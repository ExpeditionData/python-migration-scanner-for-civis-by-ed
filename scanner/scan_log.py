"""The scanner's own log. Structured facts only.

Every log line the scanner writes goes through these functions. They accept
only progress counts, object types, object IDs, rule IDs, and exception type
names, and they validate each one. There is no function that takes free text,
so code, run log text, notebook output, names, or exception messages cannot
reach the scanner's log by accident. A redacting filter is installed as a
second layer.

Line formats (parsed by the web app to show progress):

    INVENTORY type=container_script listed=4768 kept=12 outside_window=66 template_copies=4690 not_python=0
    PROGRESS phase=inventory objects_done=50 objects_total=420
    PROGRESS phase=code objects_done=420 objects_total=2000
    OBJECT_ERROR object_type=python_script object_id=107 error=CivisAPIError status=403
    CIVIS_GIT_FAILED object_type=python_script object_id=109 error=CivisAPIError status=403
    PARSE_ERROR object_type=notebook object_id=401 error=JSONDecodeError
    FINDINGS rule_id=CV3-USE-PANDAS count=12
    PHASE name=inventory state=done
    USERS source=active status=404
    CHECKPOINT objects_written=500
    OUTPUT_FAILED error=CivisAPIError
    FATAL error=KeyError step=code_and_logs
    FATAL error=CivisAPIError status=404 step=inventory_users
    FATAL error=SchemaNotDedicated reason=results_schema_holds_other_tables_or_views other_objects=3
    TABLE_WRITE_FAILED table=findings error=CivisAPIError status=403
    RUN run_id=run-20261013T120000Z-a1b2c3 status=running
    RUN run_id=run-20261013T120000Z-a1b2c3 status=succeeded objects_total=2000 objects_scanned=2000 api_calls=6120 github_tarball_requests=14 github_cache_peak_bytes=52428800 github_cache_evictions=0 civis_git_reads=37 civis_git_failures=2

INVENTORY: one line per listed type (the six script, notebook, and Service
lists, then workflows), logged after every list is read and before any detail
call. listed is every row the list returned; the rest add up to it: kept (rows
read with a detail call), outside_window (scripts that did not run or change
within LOOKBACK_DAYS), template_copies (custom scripts also listed under this
type, inventoried once as custom scripts), and not_python (rows whose list row
shows they cannot run Python). Detail calls can find more objects that cannot
run Python (dbt off the default image, Shiny Services); those are counted in
the coverage table's not_python rows, not here.

PROGRESS phase=inventory: during the detail calls, every 50 objects or 30
seconds, and once more when they end. objects_total is the number of kept
rows across every type, workflows included.

The final RUN line carries the run's counts. api_calls is every Civis API
request the scanner sent through its request budget (scanner/ratelimit.py),
including each page of a list call. Table writes and the CSV upload use
civis-python's own helpers and are not counted (docs/PLAN.md 12.13). The
github_* counts come from the repository file cache (scanner/retrieval/github.py).
civis_git_reads and civis_git_failures count the files read, and the reads that
failed, through the Civis git endpoints (scanner/retrieval/civis_git.py); each
failure also has a CIVIS_GIT_FAILED line.
"""

from __future__ import annotations

import logging
import re
import sys

from common.models import ObjectType
from common.redact import install_redaction

LOGGER_NAME = "scanner"
PHASES = frozenset({"inventory", "code", "logs", "output", "retention"})
PHASE_STATES = frozenset({"start", "done", "failed"})
TABLE_NAMES = frozenset({"scan_runs", "objects", "findings", "coverage", "checklist_status"})
RUN_STATUSES = frozenset({"running", "succeeded", "tables_failed", "failed"})
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RULE_ID = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)+$")
_ERROR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")

_logger = logging.getLogger(LOGGER_NAME)


def configure(stream=None) -> logging.Logger:
    """Send scanner log lines to stdout (shown in the Civis run log), with redaction."""
    if not _logger.handlers:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
    install_redaction(_logger)
    return _logger


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _object_type(value: object) -> str:
    return str(ObjectType(value))


def _error_name(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _ERROR_NAME.match(name) else "Exception"


def progress(phase: str, done: int, total: int) -> None:
    if phase not in PHASES:
        raise ValueError("unknown phase")
    _logger.info(
        "PROGRESS phase=%s objects_done=%d objects_total=%d",
        phase, _count(done, "done"), _count(total, "total"),
    )


def phase(name: str, state: str) -> None:
    if name not in PHASES or state not in PHASE_STATES:
        raise ValueError("unknown phase or state")
    _logger.info("PHASE name=%s state=%s", name, state)


def object_error(object_type: ObjectType | str, object_id: int, exc: BaseException) -> None:
    """An object could not be read. Logs the error type and HTTP status, never the message."""
    status = getattr(exc, "status_code", None)
    suffix = f" status={status}" if isinstance(status, int) and not isinstance(status, bool) else ""
    _logger.warning(
        "OBJECT_ERROR object_type=%s object_id=%d error=%s%s",
        _object_type(object_type), _count(object_id, "object_id"), _error_name(exc), suffix,
    )


def civis_git_failed(object_type: ObjectType | str, object_id: int, exc: BaseException) -> None:
    """An item's own git file could not be read through Civis. Error type and HTTP status only."""
    status = getattr(exc, "status_code", None)
    suffix = f" status={status}" if isinstance(status, int) and not isinstance(status, bool) else ""
    _logger.warning(
        "CIVIS_GIT_FAILED object_type=%s object_id=%d error=%s%s",
        _object_type(object_type), _count(object_id, "object_id"), _error_name(exc), suffix,
    )


def parse_error(object_type: ObjectType | str, object_id: int, error_type: type[BaseException] | BaseException) -> None:
    """Code or a file failed to parse. Logs the object ID and error type only, never the text."""
    name = error_type.__name__ if isinstance(error_type, type) else _error_name(error_type)
    if not _ERROR_NAME.match(name):
        name = "Exception"
    _logger.warning(
        "PARSE_ERROR object_type=%s object_id=%d error=%s",
        _object_type(object_type), _count(object_id, "object_id"), name,
    )


def parse_error_named(object_type: ObjectType | str, object_id: int, error_name: str) -> None:
    """Same as parse_error, for callers that have only the exception's type name."""
    name = error_name if isinstance(error_name, str) and _ERROR_NAME.match(error_name) else "Exception"
    _logger.warning(
        "PARSE_ERROR object_type=%s object_id=%d error=%s",
        _object_type(object_type), _count(object_id, "object_id"), name,
    )


def table_write_failed(table: str, exc: BaseException) -> None:
    if table not in TABLE_NAMES:
        raise ValueError("unknown table")
    status = getattr(exc, "status_code", None)
    suffix = f" status={status}" if isinstance(status, int) and not isinstance(status, bool) else ""
    _logger.error("TABLE_WRITE_FAILED table=%s error=%s%s", table, _error_name(exc), suffix)


RUN_COUNTS = ("objects_total", "objects_scanned", "api_calls", "github_tarball_requests",
              "github_cache_peak_bytes", "github_cache_evictions", "civis_git_reads", "civis_git_failures")


def run_status(run_id: str, status: str, **counts: int) -> None:
    """The run's status. The final line also gives the counts in RUN_COUNTS, in that order."""
    if status not in RUN_STATUSES or not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ValueError("invalid run status line")
    unknown = set(counts) - set(RUN_COUNTS)
    if unknown:
        raise ValueError("unknown run count")
    suffix = "".join(f" {name}={_count(counts[name], name)}" for name in RUN_COUNTS if counts.get(name) is not None)
    _logger.info("RUN run_id=%s status=%s%s", run_id, status, suffix)


INVENTORY_COUNTS = ("listed", "kept", "outside_window", "template_copies", "not_python")


def inventory(object_type: ObjectType | str, **counts: int) -> None:
    """One listed type's counts (INVENTORY_COUNTS, in that order). Counts only, never names."""
    if set(counts) != set(INVENTORY_COUNTS):
        raise ValueError("inventory counts must be exactly INVENTORY_COUNTS")
    values = " ".join(f"{name}={_count(counts[name], name)}" for name in INVENTORY_COUNTS)
    _logger.info("INVENTORY type=%s %s", _object_type(object_type), values)


USER_SOURCES = ("all", "active", "none")


def users(source: str, status: int | None = None) -> None:
    """Which user list owner active status came from (scanner/inventory/users.py), and the HTTP status
    of the list call that failed. No user names."""
    if source not in USER_SOURCES:
        raise ValueError("unknown users source")
    if status is None:
        _logger.info("USERS source=%s", source)
    else:
        _logger.info("USERS source=%s status=%d", source, _count(status, "status"))


def output_failed(exc: BaseException) -> None:
    """The CSV report could not be saved as a run output."""
    _logger.error("OUTPUT_FAILED error=%s", _error_name(exc))


# Where a scan can stop with FATAL (scanner/runner.py tracks the step). "setup" is before run_scan
# starts (creating the client and the sink). inventory_list_<type> names a type's list calls,
# inventory_details the detail calls of the kept scripts, notebooks, and Services, and inventory_workflows
# the workflow list and the workflows' detail calls. Only these values are logged.
INVENTORY_LIST_TYPES = tuple(t.value for t in ObjectType if t is not ObjectType.WORKFLOW)
FATAL_STEPS = (
    "setup", "schema_check", "tables", "scanning_user", "inventory_users",
    *(f"inventory_list_{t}" for t in INVENTORY_LIST_TYPES),
    "inventory_details", "inventory_workflows", "inventory_archived_jobs", "code_and_logs", "save_results",
    "output", "retention",
)


def fatal(exc: BaseException, step: str) -> None:
    """The scan stopped. Logs the error type, the HTTP status when the exception has an integer
    status_code, and the step from FATAL_STEPS; never the message, which can hold Civis content."""
    if step not in FATAL_STEPS:
        raise ValueError("unknown step")
    status = getattr(exc, "status_code", None)
    suffix = f" status={status}" if isinstance(status, int) and not isinstance(status, bool) else ""
    _logger.error("FATAL error=%s%s step=%s", _error_name(exc), suffix, step)


def schema_not_dedicated(other_objects: int) -> None:
    """The results schema holds tables or views that are not this tool's, so the scan wrote nothing.
    A fixed reason and the count only, never the names."""
    _logger.error(
        "FATAL error=SchemaNotDedicated reason=results_schema_holds_other_tables_or_views other_objects=%d",
        _count(other_objects, "other_objects"),
    )


def checkpoint(objects_written: int) -> None:
    _logger.info("CHECKPOINT objects_written=%d", _count(objects_written, "objects_written"))


def findings_count(rule_id: str, count: int) -> None:
    if not isinstance(rule_id, str) or not _RULE_ID.match(rule_id):
        raise ValueError("invalid rule ID")
    _logger.info("FINDINGS rule_id=%s count=%d", rule_id, _count(count, "count"))
