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

from app.backends.base import LOG_PAGE, LogLine
from common import release
from common.models import ObjectType

# The scanner's generated run IDs: run-<UTC time>-<6 hex characters> (scanner/runner.py, new_run_id).
RUN_ID = re.compile(r"^run-\d{8}T\d{6}Z-[0-9a-f]{6}$")
ERROR_TOKEN = re.compile(r"^[A-Z][A-Za-z0-9_]{0,99}( status=\d{3})?$")  # A class name, maybe a status.
MAX_LOG_PAGES = 50
OTHER = "other"
# Line types the scanner writes (scanner/scan_log.py). Any other log line is counted as "other".
SCANNER_PREFIXES = (
    "RUN", "PHASE", "PROGRESS", "CHECKPOINT", "FINDINGS", "OBJECT_ERROR", "CIVIS_GIT_FAILED", "PARSE_ERROR",
    "TABLE_WRITE_FAILED", "OUTPUT_FAILED", "FATAL",
)
ERROR_LINES = ("OBJECT_ERROR", "CIVIS_GIT_FAILED", "PARSE_ERROR", "TABLE_WRITE_FAILED", "OUTPUT_FAILED", "FATAL")
# The counts on the scanner's final RUN line, in order (scanner/scan_log.py, RUN_COUNTS).
RUN_COUNTS = (
    "objects_total", "objects_scanned", "api_calls", "github_tarball_requests", "github_cache_peak_bytes",
    "github_cache_evictions", "civis_git_reads", "civis_git_failures",
)
SCAN_STATUSES = ("running", "succeeded", "tables_failed", "failed")
RESULTS_STATES = ("ok", "no_tables", "schema_missing", "not_configured", "job_unreadable", "error")
OBJECT_TYPES = tuple(t.value for t in ObjectType)
GIT_TYPES = ("python_script", "workflow")
# The same texts as the TESTING.md item 4.8 query (LIKE '%...%').
READ_THROUGH_CIVIS = "own file was checked, read through Civis"
CIVIS_READ_FAILED = "could not be read through Civis"

_PREFIX = re.compile(r"^(" + "|".join(SCANNER_PREFIXES) + r")\b")
_ERROR_LINE = re.compile(r"^(" + "|".join(ERROR_LINES) + r")\b.*?\berror=(\S+)(?: status=(\S+))?")
_RUN_LINE = re.compile(r"^RUN\b")
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


def count_log(lines: list[str]) -> LogCounts:
    run_counts = None
    errors: Counter = Counter()
    other = 0
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
        elif _RUN_LINE.match(line):
            pairs = dict(_PAIR.findall(line))
            if "objects_total" in pairs:
                run_counts = {k: int(pairs[k]) for k in RUN_COUNTS if pairs.get(k, "").isdigit()}
    return LogCounts(run_counts, errors, other)


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


@dataclass
class SummaryInputs:
    tool_ref: Any
    lines: list  # app.setup.Line
    database_shape: str | None
    service_method: str
    sharing: dict[str, tuple[int, int] | None]
    results_state: str
    scan: ScanData | None = None
    checklist: list[dict] | None = None
    scan_run_rows: list[dict] | None = None


def build(inputs: SummaryInputs) -> str:
    from app.job_settings import DATABASE_SHAPES
    from app.setup import LINE_KEYS, RESULTS, SERVICE_METHODS

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
    for part in ("job", "service"):
        counts = inputs.sharing.get(part)
        if counts is None:
            out.append(f"sharing_{part}: not_read")
        else:
            out.append(f"sharing_{part}: users={_n(counts[0])} groups={_n(counts[1])}")
    out.append(f"results_state: {_label(inputs.results_state, RESULTS_STATES)}")
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
