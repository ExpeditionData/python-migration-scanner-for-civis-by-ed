"""Run one scan from start to finish.

Steps:

1. Create the tables if they are missing, and record the run as "running".
   On a rerun with the same run ID, read which objects are already done.
2. Inventory every object (see scanner/inventory).
3. Scan each remaining object in a thread pool (MAX_CONCURRENCY), writing a
   checkpoint every 500 objects or 10 minutes, with PROGRESS log lines.
4. Write coverage summary rows and new "open" checklist rows.
5. Save the CSV report as a run output (with no expiration).
6. Apply the retention setting, then record the final run status. The final
   log line also gives the object count and the number of Civis API calls, so
   calls per object can be measured (docs/PLAN.md section 5).

If the tables cannot be written, the scan keeps going, still saves the CSV
report, and ends with status "tables_failed". Every API call to Civis goes
through the shared request budget.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from common.disclaimer import DISCLAIMER
from common.models import CoverageItem, ObjectType, ScanStatus
from common.retention import expired_run_ids
from common.schema import CHECKLIST_STATUS, COVERAGE, SCAN_RUNS
from common.version import __version__
from scanner import scan_log
from scanner.checkpoint import EVERY_OBJECTS, EVERY_SECONDS, Checkpointer
from scanner.config import ScanConfig
from scanner.engine import Engine
from scanner.inventory import build_inventory
from scanner.inventory.users import scanning_user
from scanner.output import ResultSink, TableWriteError, report_csv
from scanner.process import ObjectResult, ScanDeps, process_item
from scanner.ratelimit import BudgetedClient, RequestBudget
from scanner.retrieval.github import GitHubRepos
from scanner.retrieval.http import SafeHttp
from scanner.retrieval.tempdir import private_tempdir

PROGRESS_EVERY_SECONDS = 30.0
PROGRESS_EVERY_OBJECTS = 50
SCANNER_USER = "scanner"


class AlreadyFinished(Exception):
    """A rerun was asked for a run ID that already finished."""


@dataclass
class RunResult:
    run_id: str
    status: str
    objects: int = 0
    findings: int = 0
    skipped_as_done: int = 0
    api_calls: int = 0
    tables_ok: bool = True
    report_saved: bool = False
    statuses: dict[str, int] = field(default_factory=dict)


def new_run_id(now: datetime) -> str:
    return f"run-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


class _Tables:
    """Table writes that stop after the first failure, so the scan can finish with a CSV."""

    def __init__(self, sink: ResultSink | None):
        self.sink = sink
        self.ok = sink is not None

    def run(self, table: str, action: Callable[[], Any]) -> Any:
        if not self.ok:
            return None
        try:
            return action()
        except TableWriteError as exc:
            self._fail(exc.table, exc.__cause__ or exc)
        except Exception as exc:
            self._fail(table, exc)
        return None

    def _fail(self, table: str, exc: BaseException) -> None:
        self.ok = False
        scan_log.table_write_failed(table if table in scan_log.TABLE_NAMES else "scan_runs", exc)


def run_scan(
    config: ScanConfig,
    *,
    client: Any,
    http_session: Any,
    sink: ResultSink | None,
    outputs: Any,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    checkpoint_objects: int = EVERY_OBJECTS,
    checkpoint_seconds: float = EVERY_SECONDS,
    budget: RequestBudget | None = None,
) -> RunResult:
    started = now()
    run_id = config.run_id or new_run_id(started)
    scan_log.run_status(run_id, "running")
    tables = _Tables(sink)
    tables.run("scan_runs", sink.ensure_tables if sink else lambda: None)

    finished: set[int] = set()
    previous: dict[str, list[dict]] = {"objects": [], "findings": [], "coverage": []}
    if config.run_id and tables.ok:
        if tables.run("scan_runs", lambda: sink.latest_status(run_id)) == "succeeded":
            raise AlreadyFinished(run_id)
        finished = tables.run("objects", lambda: sink.finished_object_ids(run_id)) or set()
        previous = tables.run("objects", lambda: sink.run_rows(run_id)) or previous
        first_start = [s for r, s in (tables.run("scan_runs", sink.run_starts) or []) if r == run_id]
        if first_start:
            started = first_start[0].replace(tzinfo=timezone.utc) if first_start[0].tzinfo is None else first_start[0]

    budget = budget or RequestBudget(config.request_budget, 300.0)
    civis = BudgetedClient(client, budget)
    user_id, user_name = scanning_user(civis)

    def status_row(status: str, finished_at: datetime | None) -> dict:
        return {
            "started_at": _naive(started), "finished_at": _naive(finished_at), "status": status,
            "scanning_user_id": user_id, "scanning_user_name": user_name, "tool_version": __version__,
            "parameters": config.parameters_json(), "civis_job_id": config.civis_job_id,
            "civis_run_id": config.civis_run_id, "disclaimer": DISCLAIMER, "recorded_at": _naive(now()),
        }

    tables.run("scan_runs", lambda: sink.append(SCAN_RUNS, run_id, [status_row("running", None)]))

    scan_log.phase("inventory", "start")
    inventory = build_inventory(civis)
    total = len(inventory.items)
    scan_log.progress("inventory", total, total)
    scan_log.phase("inventory", "done")

    todo = [item for item in inventory.items if item.obj.object_id not in finished]
    result = RunResult(run_id=run_id, status="running", skipped_as_done=total - len(todo))
    engine = Engine()
    results: list[ObjectResult] = []

    scan_log.phase("code", "start")
    checkpointer = Checkpointer(sink if tables.ok else None, run_id, checkpoint_objects, checkpoint_seconds, monotonic)
    with private_tempdir() as workdir:
        http = SafeHttp(http_session)
        deps = ScanDeps(
            engine=engine,
            client=civis,
            http=http,
            repos=GitHubRepos(http, config.github_token, workdir),
            lookback_days=config.lookback_days,
            now=now(),
        )
        done = result.skipped_as_done
        last_progress = monotonic()
        scan_log.progress("code", done, total)
        with ThreadPoolExecutor(max_workers=config.max_concurrency) as pool:
            futures = {pool.submit(_safe_process, item, deps): item for item in todo}
            for future in as_completed(futures):
                object_result = future.result()
                results.append(object_result)
                if tables.ok:
                    tables.run("objects", lambda r=object_result: checkpointer.add(r))
                done += 1
                if done % PROGRESS_EVERY_OBJECTS == 0 or monotonic() - last_progress >= PROGRESS_EVERY_SECONDS:
                    scan_log.progress("code", done, total)
                    last_progress = monotonic()
        if tables.ok:
            tables.run("objects", checkpointer.flush)
        scan_log.progress("code", done, total)
    scan_log.phase("code", "done")

    scan_log.phase("output", "start")
    current_objects = [r.obj.to_row() for r in results]
    current_findings = [f.to_row() for r in results for f in r.findings]
    all_objects = previous.get("objects", []) + current_objects
    all_findings = _dedupe_findings(previous.get("findings", []) + current_findings)

    summary = _summary_rows(inventory, all_objects)
    extra = _inventory_coverage_rows(inventory, results, previous.get("coverage", []))
    tables.run("coverage", lambda: sink.append(COVERAGE, run_id, summary + extra))
    tables.run("checklist_status", lambda: _new_checklist_rows(sink, run_id, all_findings, now()))
    for rule_id, count in sorted(_count_by_rule(all_findings).items()):
        scan_log.findings_count(rule_id, count)

    try:
        outputs.save_csv(f"civis-python-version-search-{run_id}.csv", report_csv(all_objects, all_findings, engine.rules))
        result.report_saved = True
    except Exception as exc:
        scan_log.output_failed(exc)
    scan_log.phase("output", "done")

    if config.retention_days and tables.ok:
        scan_log.phase("retention", "start")
        starts = tables.run("scan_runs", sink.run_starts) or []
        expired = [r for r in expired_run_ids(starts, config.retention_days, _naive(now())) if r != run_id]
        if expired:
            tables.run("scan_runs", lambda: sink.delete_runs(expired))
        scan_log.phase("retention", "done")

    if not result.report_saved:
        final = "failed"
    elif tables.ok:
        final = "succeeded"
    else:
        final = "tables_failed"
    tables.run("scan_runs", lambda: sink.append(SCAN_RUNS, run_id, [status_row(final, now())]))
    if final == "succeeded" and not tables.ok:
        final = "tables_failed"
    result.api_calls = budget.requests
    scan_log.run_status(run_id, final, objects_total=total, objects_scanned=len(todo), api_calls=budget.requests)

    result.status = final
    result.tables_ok = tables.ok
    result.objects = len(all_objects)
    result.findings = len(all_findings)
    for row in all_objects:
        result.statuses[row["scan_status"]] = result.statuses.get(row["scan_status"], 0) + 1
    return result


def _safe_process(item, deps: ScanDeps) -> ObjectResult:
    """One object's unexpected error must not stop the scan."""
    try:
        return process_item(item, deps)
    except Exception as exc:
        scan_log.object_error(item.obj.object_type, item.obj.object_id, exc)
        item.obj.scan_status = ScanStatus.NOT_SCANNED
        item.obj.scan_note = f"The scanner hit an unexpected error ({type(exc).__name__}) on this object. Review it manually."
        return ObjectResult(item.obj, [], [CoverageItem(item.obj.object_type, item.obj.object_id, item.obj.scan_note)])


def _naive(value: datetime | None) -> datetime | None:
    """Timestamps are stored in UTC without a zone, which both Redshift and SQLite accept."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _dedupe_findings(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        key = (int(row["object_id"]), row["rule_id"], row["file"], row["line_number"], row["source"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _count_by_rule(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in findings:
        counts[row["rule_id"]] = counts.get(row["rule_id"], 0) + 1
    return counts


def _summary_rows(inventory, all_objects: list[dict]) -> list[dict]:
    scanned: dict[str, int] = {}
    for row in all_objects:
        if row["scan_status"] == str(ScanStatus.SCANNED):
            scanned[row["object_type"]] = scanned.get(row["object_type"], 0) + 1
    rows = []
    for object_type in ObjectType:
        counts = inventory.count(object_type)
        rows.append(
            {
                "row_kind": "summary", "object_type": str(object_type), "found_count": counts.found,
                "scanned_count": scanned.get(str(object_type), 0), "hidden_skipped_count": counts.hidden_skipped,
                "archived_included_count": counts.archived_included,
            }
        )
        for language, count in sorted(counts.not_python.items()):
            rows.append(
                {"row_kind": "not_python", "object_type": str(object_type), "language": language, "found_count": count}
            )
    return rows


def _inventory_coverage_rows(inventory, results: list[ObjectResult], previous: list[dict]) -> list[dict]:
    """Inventory notes not tied to one scanned object (for example, workflow references)."""
    written = {(str(c.object_type), c.object_id, c.reason) for r in results for c in r.coverage}
    written |= {(row.get("object_type"), row.get("object_id"), row.get("reason")) for row in previous}
    rows = []
    for item in inventory.coverage:
        key = (str(item.object_type), item.object_id, item.reason)
        if key in written:
            continue
        written.add(key)
        rows.append({"row_kind": "object", "object_type": str(item.object_type), "object_id": item.object_id, "reason": item.reason})
    return rows


def _new_checklist_rows(sink: ResultSink, run_id: str, findings: list[dict], when: datetime) -> None:
    """Insert an "open" row for each object and rule pair that has no row yet. Never overwrite."""
    existing = sink.checklist_pairs()
    pairs = sorted({(int(f["object_id"]), f["rule_id"]) for f in findings} - existing)
    rows = [
        {"object_id": o, "rule_id": r, "status": "open", "changed_by": SCANNER_USER, "changed_at": _naive(when)}
        for o, r in pairs
    ]
    sink.append(CHECKLIST_STATUS, run_id, rows)
