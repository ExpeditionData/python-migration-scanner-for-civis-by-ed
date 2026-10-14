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
from typing import Any, Protocol

from common.schema import TABLES

ACTIVE_STATES = frozenset({"queued", "running"})


class ResultsUnavailable(Exception):
    """The results cannot be read. ``kind``: not_configured, schema_missing, or error."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class ChecklistSaveError(Exception):
    """A checklist change could not be saved. ``message`` is safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


class Jobs(Protocol):
    job_id: int | None
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
        for line in lines:
            self.last_id = max(self.last_id or 0, line.id)
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
