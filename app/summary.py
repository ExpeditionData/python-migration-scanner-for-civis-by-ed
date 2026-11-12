"""The test summary: counts an admin can share with whoever supports the setup.

Built from an allow list. Every value written into the summary is one of:

- an integer (``_n``)
- a run ID that matches the scanner's generated run ID pattern (``_run``)
- a fixed label from a known set (``_label``)
- an error token: an exception class name with an optional ``status=NNN`` (``_token``)

Anything else becomes "other". All other text is fixed in this file. So the
summary never holds object, user, group, database, schema, or credential
names, URLs, hosts, code, log text, or SQL error text. The Setup page's
reasons and details are not used, only each line's number, short ID, and
result.

The scan's log is read with the same paging as the progress view
(``Jobs.logs`` with ``last_id``), at most ``MAX_LOG_PAGES`` pages.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.backends.base import ERROR_TOKEN, LOG_PAGE, LogLine
from common import release
from common.models import ObjectType

# The scanner's generated run IDs: run-<UTC time>-<6 hex characters> (scanner/runner.py, new_run_id).
RUN_ID = re.compile(r"^run-\d{8}T\d{6}Z-[0-9a-f]{6}$")
MAX_LOG_PAGES = 50
OTHER = "other"
# Line types the scanner writes (scanner/scan_log.py). Any other log line is counted as "other".
SCANNER_PREFIXES = (
    "RUN", "PHASE", "PROGRESS", "CHECKPOINT", "FINDINGS", "OBJECT_ERROR", "CIVIS_GIT_FAILED", "PARSE_ERROR",
    "TABLE_WRITE_FAILED", "OUTPUT_FAILED", "FATAL", "USERS", "INVENTORY",
)
ERROR_LINES = ("OBJECT_ERROR", "CIVIS_GIT_FAILED", "PARSE_ERROR", "TABLE_WRITE_FAILED", "OUTPUT_FAILED", "FATAL")
# The counts on the scanner's final RUN line, in order (scanner/scan_log.py, RUN_COUNTS).
RUN_COUNTS = (
    "objects_total", "objects_scanned", "api_calls", "github_tarball_requests", "github_cache_peak_bytes",
    "github_cache_evictions", "civis_git_reads", "civis_git_failures", "notebook_links", "notebook_link_retries",
)
SCAN_STATUSES = ("running", "succeeded", "tables_failed", "failed")
FINAL_STATUSES = ("succeeded", "tables_failed", "failed")  # The default scan is the newest with one of these.
RESULTS_STATES = ("ok", "no_tables", "schema_missing", "not_configured", "job_unreadable", "error")
OBJECT_TYPES = tuple(t.value for t in ObjectType)
GIT_TYPES = ("python_script", "workflow")
# The same texts as the TESTING.md item 4.8 query (LIKE '%...%').
READ_THROUGH_CIVIS = "own file was checked, read through Civis"
CIVIS_READ_FAILED = "could not be read through Civis"

_PREFIX = re.compile(r"^(" + "|".join(SCANNER_PREFIXES) + r")\b")
_ERROR_LINE = re.compile(r"^(" + "|".join(ERROR_LINES) + r")\b.*?\berror=(\S+)(?: status=(\S+))?")
_RUN_LINE = re.compile(r"^RUN\b")
# Which user list owner active status came from (scanner/scan_log.py, USER_SOURCES).
USER_SOURCES = ("all", "active", "none")
_USERS_LINE = re.compile(r"^USERS source=(\w+)(?: status=(\d{3}))?$")
# Where a scan stopped (scanner/scan_log.py, FATAL_STEPS), from the end of a FATAL line.
FATAL_STEPS = (
    "setup", "schema_check", "tables", "scanning_user", "inventory_users",
    *(f"inventory_list_{t.value}" for t in ObjectType if t is not ObjectType.WORKFLOW),
    "inventory_details", "inventory_workflows", "inventory_archived_jobs", "code_and_logs", "save_results",
    "output", "retention",
)
# One INVENTORY line per listed type (scanner/scan_log.py, INVENTORY_COUNTS), counts only.
INVENTORY_COUNTS = ("listed", "kept", "outside_window", "template_copies", "not_python")
_INVENTORY_LINE = re.compile(
    r"^INVENTORY type=(\w+) " + " ".join(rf"{name}=(\d+)" for name in INVENTORY_COUNTS) + r"$")
_FATAL_STEP = re.compile(r"^FATAL .*\sstep=([a-z0-9_]+)$")
_PAIR = re.compile(r"\b(\w+)=(\S+)")


# --- The allow list ---------------------------------------------------------------------------


def _n(value: Any) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else OTHER


def _label(value: Any, allowed: Iterable[str]) -> str:
    return value if isinstance(value, str) and value in tuple(allowed) else OTHER


def _run(value: Any) -> str:
    return value if isinstance(value, str) and RUN_ID.match(value) else OTHER


def _token(value: Any) -> str:
    return value if isinstance(value, str) and ERROR_TOKEN.match(value) else OTHER


def is_run_id(value: Any) -> bool:
    return isinstance(value, str) and bool(RUN_ID.match(value))


def tool_version(ref: Any) -> str:
    """The job's git reference if it is a release tag (v, numbers and dots, optional -rcN), otherwise "other"."""
    return ref.strip() if release.is_release_tag(ref) else OTHER


# --- Reading the scan's log ------------------------------------------------------------------


@dataclass
class LogRead:
    lines: list[str] = field(default_factory=list)
    complete: bool = True
    error: str | None = None


def read_log(jobs: Any, civis_run_id: int, page_size: int = LOG_PAGE, max_pages: int = MAX_LOG_PAGES) -> LogRead:
    """Read a scanner run's log with Jobs.logs paging, at most ``max_pages`` pages."""
    from app.backends.base import error_token

    result = LogRead()
    last_id = None
    for _ in range(max_pages):
        try:
            page: list[LogLine] = jobs.logs(civis_run_id, last_id)
        except Exception as exc:
            result.error = error_token(exc)
            result.complete = False
            return result
        result.lines.extend(line.message for line in page)
        if len(page) < page_size or not page:
            return result
        last_id = max(line.id for line in page)
    result.complete = False
    return result


@dataclass
class LogCounts:
    run_counts: dict[str, int] | None
    errors: Counter
    other_lines: int
    users: str | None = None  # "<source>" or "<source> status=NNN", from the USERS line.
    fatal_step: str | None = None  # The step on the last FATAL line that has one.
    # Object type (or "other") -> the INVENTORY counts, from each INVENTORY line. A line that does not match the
    # format is counted under "other" with no counts.
    inventory: dict[str, dict[str, int]] = field(default_factory=dict)


def count_log(lines: list[str]) -> LogCounts:
    run_counts = None
    errors: Counter = Counter()
    other = 0
    users = None
    fatal_step = None
    inventory: dict[str, dict[str, int]] = {}
    for raw in lines:
        line = (raw or "").strip()
        if not _PREFIX.match(line):
            other += 1
            continue
        match = _ERROR_LINE.match(line)
        if match:
            status = match.group(3)
            token = match.group(2) + (f" status={status}" if status else "")
            errors[(match.group(1), _token(token))] += 1
            step = _FATAL_STEP.match(line) if match.group(1) == "FATAL" else None
            if step:
                fatal_step = _label(step.group(1), FATAL_STEPS)
        elif _RUN_LINE.match(line):
            pairs = dict(_PAIR.findall(line))
            if "objects_total" in pairs:
                run_counts = {k: int(pairs[k]) for k in RUN_COUNTS if pairs.get(k, "").isdigit()}
        elif line.startswith("USERS"):
            match = _USERS_LINE.match(line)
            source = _label(match.group(1), USER_SOURCES) if match else OTHER
            users = source if source == OTHER or not match.group(2) else f"{source} status={match.group(2)}"
        elif line.startswith("INVENTORY"):
            match = _INVENTORY_LINE.match(line)
            if match and _label(match.group(1), OBJECT_TYPES) != OTHER:
                inventory[match.group(1)] = {name: int(match.group(i + 2)) for i, name in enumerate(INVENTORY_COUNTS)}
            else:
                inventory.setdefault(OTHER, {})
    return LogCounts(run_counts, errors, other, users, fatal_step, inventory)


# --- Counts from the result tables ------------------------------------------------------------


def testing_4_8(objects: list[dict]) -> dict[str, dict[str, int]]:
    """TESTING.md item 4.8, computed the same way as its SQL query, per object type."""
    counts: dict[str, dict[str, int]] = {}
    for row in objects:
        object_type = row.get("object_type")
        if object_type not in GIT_TYPES or row.get("git_repo") is None:
            continue
        note = row.get("scan_note") or ""
        entry = counts.setdefault(object_type, {"git_backed": 0, "read_through_civis": 0, "civis_read_failed": 0})
        entry["git_backed"] += 1
        entry["read_through_civis"] += READ_THROUGH_CIVIS in note
        entry["civis_read_failed"] += CIVIS_READ_FAILED in note
    return counts


def scan_counts(objects: list[dict], findings: list[dict]) -> dict:
    by_type: dict[str, Counter] = {}
    for row in objects:
        status = row.get("scan_status")
        key = {"scanned": "scanned", "partial": "partial", "not_scanned": "not_scanned", "pending": "not_scanned"}.get(status)
        if key:
            by_type.setdefault(_label(row.get("object_type"), OBJECT_TYPES), Counter())[key] += 1
    impact: dict[Any, str] = {}
    for row in findings:
        if row.get("impact") == "affected":
            impact[row.get("object_id")] = "affected"
        else:
            impact.setdefault(row.get("object_id"), "advisory")
    return {
        "by_type": by_type,
        "affected": sum(1 for v in impact.values() if v == "affected"),
        "advisory": sum(1 for v in impact.values() if v == "advisory"),
        "findings": len(findings),
    }


# --- The summary text -------------------------------------------------------------------------


@dataclass
class ScanData:
    run_id: str
    status: str | None
    started_at: datetime | None
    finished_at: datetime | None
    objects: list[dict] | None = None
    findings: list[dict] | None = None
    log: LogRead | None = None
    parameters: dict | None = None  # The scan's scan_runs.parameters (JSON written by the scanner).


@dataclass
class SummaryInputs:
    tool_ref: Any
    lines: list  # app.setup.Line
    database_shape: str | None
    service_method: str
    sharing: dict[str, tuple[int, int] | str | None]  # app.setup.sharing_counts
    results_state: str
    repo_token_present: bool = False
    schema_dedicated: str = "unknown"
    scan: ScanData | None = None
    checklist: list[dict] | None = None
    scan_run_rows: list[dict] | None = None


def build(inputs: SummaryInputs) -> str:
    from app.job_settings import DATABASE_SHAPES
    from app.setup import LINE_KEYS, RESULTS, SERVICE_METHODS, SHAPE_NOT_RECOGNIZED

    out = [
        "Python Migration Scanner test summary",
        "Counts, fixed labels, and error types only.",
        f"tool_version: {tool_version(inputs.tool_ref)}",
        "setup_lines:",
    ]
    for line in inputs.lines:
        out.append(f"  {_n(line.number)} {_label(line.key, LINE_KEYS)}: {_label(line.result, RESULTS)}")
    out.append(f"database_parameter_shape: {_label(inputs.database_shape, DATABASE_SHAPES) if inputs.database_shape else 'not_read'}")
    out.append(f"service_lookup: {_label(inputs.service_method, SERVICE_METHODS)}")
    out.append(f"repo_http_token_present: {'yes' if inputs.repo_token_present is True else 'no'}")
    for part in ("job", "service"):
        counts = inputs.sharing.get(part)
        if counts is None:
            out.append(f"sharing_{part}: not_read")
        elif not isinstance(counts, tuple):  # Read, but the shape was not recognized.
            out.append(f"sharing_{part}: {_label(counts, (SHAPE_NOT_RECOGNIZED,))}")
        else:
            out.append(f"sharing_{part}: users={_n(counts[0])} groups={_n(counts[1])}")
    out.append(f"results_state: {_label(inputs.results_state, RESULTS_STATES)}")
    out.append(f"results_schema_dedicated: {_label(inputs.schema_dedicated, ('yes', 'no', 'unknown'))}")
    out += _scan_lines(inputs.scan)
    if inputs.checklist is None:
        out.append("checklist_rows_by_people: not_read")
    else:
        people = sum(1 for row in inputs.checklist if row.get("changed_by") not in (None, "scanner"))
        out.append(f"checklist_rows_by_people: {_n(people)}")
    if inputs.scan_run_rows is None:
        out.append("runs_in_scan_runs: not_read")
    else:
        out.append(f"runs_in_scan_runs: {_n(len({r.get('run_id') for r in inputs.scan_run_rows if r.get('run_id')}))}")
    return "\n".join(out) + "\n"


def _scan_lines(scan: ScanData | None) -> list[str]:
    if scan is None:
        return ["scan: none"]
    out = [f"scan: {_run(scan.run_id)}", f"  status: {_label(scan.status, SCAN_STATUSES)}"]
    if scan.started_at and scan.finished_at:
        out.append(f"  minutes_start_to_finish: {_n(max(int((scan.finished_at - scan.started_at).total_seconds() // 60), 0))}")
    else:
        out.append("  minutes_start_to_finish: not_finished")
    lookback = (scan.parameters or {}).get("lookback_days")
    out.append(f"  lookback_days: {_n(lookback) if lookback is not None else 'not_recorded'}")
    if scan.log is None:
        out.append("  log: not_read")
    else:
        counts = count_log(scan.log.lines)
        if counts.run_counts is None:
            out.append("  run_line: not_found")
        else:
            out.append("  run_line: " + " ".join(f"{k}={_n(counts.run_counts.get(k))}" for k in RUN_COUNTS
                                                if k in counts.run_counts))
        out.append("  log_errors:" + ("" if counts.errors else " none"))
        for (kind, token), count in sorted(counts.errors.items()):
            out.append(f"    {_label(kind, ERROR_LINES)} {_token(token)}: {_n(count)}")
        out.append(f"  log_other_lines: {_n(counts.other_lines)}")
        out.append(f"  users_list: {counts.users or 'not_found'}")
        out.append("  inventory:" + ("" if counts.inventory else " not_found"))
        for object_type in OBJECT_TYPES + (OTHER,):
            if object_type in counts.inventory:
                c = counts.inventory[object_type]
                out.append(f"    {object_type}:" + ("".join(f" {name}={_n(c.get(name))}" for name in INVENTORY_COUNTS)
                                                     if c else " not_recognized"))
        if counts.fatal_step:
            out.append(f"  fatal_step: {counts.fatal_step}")
        if scan.log.error:
            out.append(f"  log_read: failed error={_token(scan.log.error)} lines_read={_n(len(scan.log.lines))}")
        elif scan.log.complete:
            out.append(f"  log_read: complete lines_read={_n(len(scan.log.lines))}")
        else:
            out.append(f"  log_read: log partly read lines_read={_n(len(scan.log.lines))}")
    if scan.objects is None:
        out.append("  objects: not_read")
    else:
        counts = scan_counts(scan.objects, scan.findings or [])
        out.append("  objects_by_type:" + ("" if counts["by_type"] else " none"))
        for object_type in OBJECT_TYPES + (OTHER,):
            if object_type in counts["by_type"]:
                c = counts["by_type"][object_type]
                out.append(f"    {object_type}: scanned={_n(c['scanned'])} partly_scanned={_n(c['partial'])} "
                           f"not_scanned={_n(c['not_scanned'])}")
        out.append(f"  findings: affected_objects={_n(counts['affected'])} advisory_objects={_n(counts['advisory'])} "
                   f"total_findings={_n(counts['findings'])}")
        git = testing_4_8(scan.objects)
        out.append("  testing_4_8:" + ("" if git else " none"))
        for object_type in GIT_TYPES:
            if object_type in git:
                g = git[object_type]
                out.append(f"    {object_type}: git_backed={_n(g['git_backed'])} "
                           f"read_through_civis={_n(g['read_through_civis'])} civis_read_failed={_n(g['civis_read_failed'])}")
    return out
