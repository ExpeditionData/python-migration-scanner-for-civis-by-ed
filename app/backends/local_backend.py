"""The data layer for local preview: the dry-run preview files on this machine.

``python -m scanner --dry-run --preview --out <folder>`` writes:

- ``results.sqlite``: the five tables, under the dry-run schema name.
- ``local_jobs.json``: made-up scanner job runs and their log lines.
- One CSV report per completed run.

Nothing here touches Civis or the network. Checklist saves use real bound
parameters. ``state`` simulates the app's empty and error states for review.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.schema import CHECKLIST_STATUS, SCAN_RUNS, TABLES
from common import sql
from app.backends.base import ChecklistSaveError, JobRun, LogLine, ResultsUnavailable, normalize_row

LOCAL_SCHEMA = "dry_run_results"
TABLE_NAMES = {t.name for t in TABLES}
STATES = ("normal", "no-scan", "first-scan-running", "failed", "tables-failed", "schema-missing", "no-job-id")


class LocalResults:
    def __init__(self, folder: Path, state: str = "normal"):
        self.path = Path(folder) / "results.sqlite"
        self.state = state
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def check(self) -> str:
        if self.state == "schema-missing":
            raise ResultsUnavailable("schema_missing")
        if self.state in ("no-scan", "first-scan-running") or not self.path.exists():
            return "no_tables"
        return "ok"

    def scan_runs(self) -> list[dict]:
        rows = self._select(SCAN_RUNS.name, None)
        if self.state == "tables-failed":
            # Show the newest completed scan as one whose tables could not be written.
            done = [r for r in rows if r["status"] == "succeeded"]
            if done:
                newest = max(done, key=lambda r: r["recorded_at"] or "")
                rows.append(dict(newest, status="tables_failed", recorded_at=_later(newest["recorded_at"])))
        return rows

    def rows(self, table: str, run_id: str) -> list[dict]:
        return self._select(table, run_id)

    def checklist(self) -> list[dict]:
        return self._select(CHECKLIST_STATUS.name, None)

    def _select(self, table: str, run_id: str | None) -> list[dict]:
        if table not in TABLE_NAMES:
            raise ValueError("unknown table")
        if self.check() != "ok":
            return []
        statement = f"SELECT * FROM {sql.identifier(table)}"
        params: tuple = ()
        if run_id is not None:
            statement += " WHERE run_id = ?"
            params = (run_id,)
        with self._lock, self._connect() as conn:
            return [normalize_row(table, dict(r)) for r in conn.execute(statement, params).fetchall()]

    def save_checklist(self, run_id: str, entries: list[tuple[int, str, str]], changed_by: str, when: datetime) -> None:
        # Same validation as in Civis, then real bound parameters.
        from common.schema import checklist_insert_sql

        try:
            checklist_insert_sql(LOCAL_SCHEMA, run_id, entries, changed_by, when)
        except sql.UnsafeValueError as exc:
            raise ChecklistSaveError(f"The changes could not be saved: {exc}") from None
        columns = ", ".join(CHECKLIST_STATUS.column_names)
        stamp = when.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connect() as conn:
            conn.executemany(
                f"INSERT INTO {CHECKLIST_STATUS.name} ({columns}) VALUES (?, ?, ?, ?, ?, ?)",
                [(run_id, oid, rule, status, changed_by, stamp) for oid, rule, status in entries],
            )
            conn.commit()


def _later(stamp: str | None) -> str:
    try:
        moment = datetime.fromisoformat(stamp or "")
    except ValueError:
        return stamp or ""
    return (moment.replace(second=min(moment.second + 1, 59))).strftime("%Y-%m-%d %H:%M:%S")


class LocalJobs:
    def __init__(self, folder: Path, state: str = "normal"):
        self.folder = Path(folder)
        self.state = state
        data = _load_jobs(self.folder / "local_jobs.json")
        self.job_id = None if state == "no-job-id" else data.get("job_id", 1)
        self._runs = [JobRun(**{k: r.get(k) for k in ("id", "state", "created_at", "started_at", "finished_at")})
                      for r in data.get("runs", [])]
        self._logs = data.get("logs", {})
        self._outputs = data.get("outputs", {})
        self._lock = threading.Lock()
        self._apply_state()

    def _apply_state(self) -> None:
        if self.state == "no-scan":
            self._runs = []
        elif self.state == "first-scan-running":
            self._runs = [r for r in self._runs if r.state == "running"]
        elif self.state in ("failed", "tables-failed"):
            # Drop the running scan, and show the newest finished run as failed. With
            # tables-failed, that run's scan also has status tables_failed (exit code 3).
            self._runs = sorted((r for r in self._runs if r.state != "running"), key=lambda r: r.id, reverse=True)
            if self._runs:
                newest = self._runs[0]
                self._runs[0] = JobRun(newest.id, "failed", newest.created_at, newest.started_at, newest.finished_at)

    def runs(self, limit: int = 20) -> list[JobRun]:
        with self._lock:
            return sorted(self._runs, key=lambda r: r.id, reverse=True)[:limit]

    def logs(self, run_id: int, last_id: int | None = None) -> list[LogLine]:
        entries = self._logs.get(str(run_id), [])
        lines = [LogLine(e["id"], e.get("message", ""), e.get("created_at")) for e in entries]
        return [line for line in lines if last_id is None or line.id > last_id]

    def start(self) -> JobRun:
        with self._lock:
            new_id = max([r.id for r in self._runs], default=0) + 1
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            run = JobRun(new_id, "queued", stamp)
            self._runs.append(run)
            return run

    def report(self, run_id: int) -> tuple[str, Any] | None:
        name = self._outputs.get(str(run_id))
        if not name:
            return None
        path = self.folder / name
        return ("file", path) if path.exists() else None


def _load_jobs(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
