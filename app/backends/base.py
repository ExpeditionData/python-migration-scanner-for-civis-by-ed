"""What the web app needs from Civis, and helpers shared by both implementations.

Two interfaces:

- ``Results`` reads the five result tables and saves checklist changes.
- ``Jobs`` lists the scanner job's runs, reads their log lines, starts a run,
  and finds a run's CSV report.

``app/backends/civis_backend.py`` implements them with civis 2.7.1 (the
Service image). ``app/backends/local_backend.py`` implements them over the
dry-run preview files, for local preview only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from common.schema import TABLES

if TYPE_CHECKING:
    from app.job_settings import JobSettings

ACTIVE_STATES = frozenset({"queued", "running"})
_ERROR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")


def plain(value: Any) -> Any:
    """A civis Response (or a list of them) as plain dicts and lists.

    civis 2.7.1's ``Response.json()`` gives snake_case keys, except inside
    ``arguments`` and ``environmentVariables``, whose keys keep their case
    (RESULTS_DB stays RESULTS_DB)."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    to_json = getattr(value, "json", None)
    if callable(to_json) and not isinstance(value, (str, bytes)):
        return plain(to_json())
    return value


def error_token(exc: BaseException) -> str:
    """An exception's class name, plus its HTTP status if it has one. Never its message."""
    name = type(exc).__name__
    name = name if _ERROR_NAME.match(name) else "Exception"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return f"{name} status={status}"
    return name


class ResultsUnavailable(Exception):
    """The results cannot be read. ``kind``: not_configured, job_unreadable, schema_missing, or error."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class ChecklistSaveError(Exception):
    """A checklist change could not be saved. ``message`` is safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class StorageSetup:
    """What the "Set up results storage" button did.

    ``outcome``: created, schema_failed, tables_failed, or not_ready (the job's settings cannot be used).
    ``step`` names the step that failed (schema or tables). ``error_type`` is an exception class name with an
    optional HTTP status, never the SQL error text. ``admin_statement`` is shown when the schema step failed.
    """

    outcome: str
    step: str | None = None
    error_type: str | None = None
    schema_existed: bool = False
    admin_statement: str | None = None
    user_known: bool = True


@dataclass(frozen=True)
class JobRun:
    id: int
    state: str  # queued, running, succeeded, failed, cancelled
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES


@dataclass(frozen=True)
class LogLine:
    id: int
    message: str
    created_at: str | None = None


class Results(Protocol):
    def check(self) -> str: ...  # "ok" or "no_tables"; raises ResultsUnavailable
    def scan_runs(self) -> list[dict]: ...
    def rows(self, table: str, run_id: str) -> list[dict]: ...
    def checklist(self) -> list[dict]: ...
    def save_checklist(self, run_id: str, entries: list[tuple[int, str, str]], changed_by: str, when: datetime) -> None: ...
    def setup_storage(self) -> StorageSetup: ...  # Creates the schema if missing, then the five tables.


class Jobs(Protocol):
    job_id: int | None
    def settings(self) -> "JobSettings": ...  # The results database, credential, and schema from the job.
    def runs(self, limit: int = 20) -> list[JobRun]: ...
    def logs(self, run_id: int, last_id: int | None = None) -> list[LogLine]: ...
    def start(self) -> JobRun: ...
    def report(self, run_id: int) -> tuple[str, Any] | None: ...  # ("url", link) or ("file", path)


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------

_COLUMN_TYPES = {t.name: {c.name: c.sql_type for c in t.columns} for t in TABLES}
_TRUE = {"t", "true", "1", "yes"}
_FALSE = {"f", "false", "0", "no"}


def normalize_row(table: str, row: dict) -> dict:
    """Give every value its column's type. read_civis_sql returns text; SQLite returns ints for booleans."""
    types = _COLUMN_TYPES[table]
    result = {}
    for name, value in row.items():
        sql_type = types.get(name, "")
        if value == "" or value is None:
            result[name] = None
        elif sql_type in ("BIGINT", "INTEGER"):
            try:
                result[name] = int(value)
            except (TypeError, ValueError):
                result[name] = None
        elif sql_type == "BOOLEAN":
            text = str(value).strip().lower()
            result[name] = True if text in _TRUE else False if text in _FALSE else None
        else:
            result[name] = str(value)
    return result


# ---------------------------------------------------------------------------
# Progress from the scanner's structured log lines (scanner/scan_log.py)
# ---------------------------------------------------------------------------

_LINE = re.compile(r"^(PROGRESS|PHASE|RUN|TABLE_WRITE_FAILED|OUTPUT_FAILED|FATAL|CHECKPOINT)\b(.*)$")
_PAIR = re.compile(r"(\w+)=([\w.-]+)")


@dataclass
class Progress:
    scan_run_id: str | None = None
    status: str | None = None
    phases: dict[str, str] = field(default_factory=dict)  # phase -> start | done | failed
    current_phase: str | None = None
    done: int | None = None
    total: int | None = None
    inventory_total: int | None = None
    code_started_at: str | None = None
    last_at: str | None = None
    tables_failed: bool = False
    last_id: int | None = None

    def feed(self, lines: list[LogLine]) -> None:
        """Apply new log lines, oldest first. Lines already fed are skipped, so two
        requests polling at once cannot apply the same lines twice or move progress
        back to an older line."""
        for line in sorted(lines, key=lambda item: item.id):
            if self.last_id is not None and line.id <= self.last_id:
                continue
            self.last_id = line.id
            match = _LINE.match((line.message or "").strip())
            if not match:
                continue
            kind, values = match.group(1), dict(_PAIR.findall(match.group(2)))
            if line.created_at:
                self.last_at = line.created_at
            if kind == "RUN":
                self.scan_run_id = values.get("run_id", self.scan_run_id)
                self.status = values.get("status", self.status)
            elif kind == "PHASE" and values.get("name"):
                self.phases[values["name"]] = values.get("state", "")
                if values.get("state") == "start":
                    self.current_phase = values["name"]
                    if values["name"] == "code":
                        self.code_started_at = line.created_at
            elif kind == "PROGRESS":
                done, total = _int(values.get("objects_done")), _int(values.get("objects_total"))
                if values.get("phase") == "inventory":
                    self.inventory_total = total
                else:
                    self.done, self.total = done, total
            elif kind == "TABLE_WRITE_FAILED":
                self.tables_failed = True


def _int(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None
