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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common.schema import CHECKLIST_STATUS, SCAN_RUNS, TABLES
from common import sql
from app.backends.base import (
    ChecklistSaveError, JobRun, LogLine, ResultsUnavailable, StorageFacts, StorageSetup, normalize_row,
)
from common import release
from common.schema import admin_schema_sql
from app.job_settings import JobSettings, parse_job, unreadable

LOCAL_SCHEMA = "dry_run_results"
TABLE_NAMES = {t.name for t in TABLES}
STATES = (
    "normal", "no-scan", "first-scan-running", "first-scan-cancelled", "failed", "tables-failed", "schema-missing",
    "no-job-id",
    "job-unreadable", "database-unrecognized", "schema-refused", "tables-missing", "setup-failures",
    "not-scanner-job", "schema-shared",
)
# States that look like a new install: no scans yet, and the results storage is not set up.
FRESH_STATES = ("schema-missing", "schema-refused", "tables-missing", "schema-shared")
PREVIEW_OTHER_OBJECTS = 3  # schema-shared: the made-up schema holds three tables or views of its own.
PREVIEW_DATABASE_USER = "example_robot_db"
# Made-up IDs for the preview's scanner job settings.
PREVIEW_DATABASE_ID = 3
PREVIEW_CREDENTIAL_ID = 4
PREVIEW_ROBOT = {"id": 15, "name": "Example Robot", "username": "example_robot"}
PREVIEW_OTHER_USER = {"id": 12, "name": "Example Owner Casey", "username": "example_owner_casey"}
PREVIEW_TAG = "v0.1.0-rc3"
PREVIEW_SERVICE_ID = 9200


def preview_job(job_id: int, state: str = "normal") -> dict:
    """Made-up scanner job settings, shaped like civis's get_containers response (snake_case keys).
    They match the README, except in the setup-failures state."""
    database = {"database": PREVIEW_DATABASE_ID, "credential": PREVIEW_CREDENTIAL_ID}
    if state == "database-unrecognized":
        database = "example-database-name"
    image = release.pinned_image("scanner") or ("", "")
    job = {
        "id": job_id, "name": "Example scanner job", "author": dict(PREVIEW_ROBOT), "running_as": dict(PREVIEW_ROBOT),
        "docker_image_name": image[0], "docker_image_tag": image[1], "repo_http_uri": release.REPOSITORY_URL,
        "repo_ref": PREVIEW_TAG, "git_credential_id": None, "docker_command": release.SCANNER_COMMAND,
        "required_resources": {"cpu": release.SCANNER_CPU, "memory": release.SCANNER_MEMORY_MB,
                               "disk_space": float(release.SCANNER_DISK_GB)},
        "params": [
            {"name": "RESULTS_DB", "type": "database", "required": True},
            {"name": "RESULTS_SCHEMA", "type": "string", "required": True},
            {"name": "GITHUB", "type": "credential_custom", "required": False},
            {"name": "LOOKBACK_DAYS", "type": "integer", "required": False, "default": "400"},
        ],
        "arguments": {"RESULTS_DB": database, "RESULTS_SCHEMA": LOCAL_SCHEMA},
    }
    if state == "not-scanner-job":
        job.update(name="Example nightly export", repo_http_uri="https://github.com/example-org/etl-jobs.git",
                   docker_command="python export.py")
    if state == "setup-failures":
        job.update(author=dict(PREVIEW_OTHER_USER), running_as=dict(PREVIEW_OTHER_USER), git_credential_id=77,
                   repo_ref="main", required_resources={"cpu": 1000, "memory": 8192, "disk_space": 4.0})
        job["params"] = job["params"] + [{"name": "RESULTS_SHEMA", "type": "string"}, {"name": "RUN_ID", "type": "string"}]
        job["arguments"] = dict(job["arguments"], RUN_ID="run-20261001T120000Z-abc123")
    return job


def preview_service(service_id: int, state: str = "normal") -> dict:
    image = release.pinned_image("service") or ("", "")
    return {
        "id": service_id, "name": "Example scanner app", "user": dict(PREVIEW_ROBOT),
        "docker_image_name": image[0], "docker_image_tag": image[1], "git_repo_url": release.REPOSITORY_URL,
        "git_repo_ref": PREVIEW_TAG, "credentials": [],
        "environment_variables": {"SCANNER_JOB_ID": "9100", "CIVIS_SERVICE_HEALTH_PATH": "/healthz"},
    }


def _preview_shares(users: list[dict], groups: list[dict]) -> dict:
    """One object with readers, writers, and owners: the shape Civis returns for both shares calls."""
    return {"readers": {"users": users, "groups": groups}, "writers": {"users": [], "groups": []},
            "owners": {"users": [{"id": PREVIEW_ROBOT["id"], "name": PREVIEW_ROBOT["name"]}], "groups": []}}


class LocalSetup:
    """Made-up answers for the Setup page in local preview. Nothing here touches Civis."""

    def __init__(self, folder: Path, state: str, results: "LocalResults"):
        self.state = state
        self.results = results

    def me(self) -> dict:
        return dict(PREVIEW_ROBOT)

    def credential(self, credential_id: int) -> dict:
        return {"id": credential_id, "name": "Example results credential", "username": PREVIEW_DATABASE_USER,
                "user": dict(PREVIEW_ROBOT)}

    def job_shares(self) -> dict:
        users = [{"id": 21, "name": "Example Admin Avery"}]
        if self.state == "setup-failures":
            users.append({"id": 22, "name": "Example Analyst <b>Blake</b>"})
        return _preview_shares(users, [{"id": 61, "name": "Example Civis Admins"}])

    def services(self) -> list[dict]:
        found = [preview_service(PREVIEW_SERVICE_ID, self.state)]
        if self.state == "setup-failures":
            found.append(preview_service(PREVIEW_SERVICE_ID + 1, self.state))
        return found

    def service(self, service_id: int) -> dict:
        return preview_service(service_id, self.state)

    def service_shares(self, service_id: int) -> dict:
        return _preview_shares([{"id": 21, "name": "Example Admin Avery"}], [])

    def storage(self, settings) -> StorageFacts:
        if self.results.storage == "missing_schema":
            return StorageFacts(schema_exists=False)
        if self.state == "schema-shared":
            return StorageFacts(schema_exists=True, other_objects=PREVIEW_OTHER_OBJECTS)
        if self.results.storage == "missing_tables":
            return StorageFacts(schema_exists=True, other_objects=0)
        return StorageFacts(schema_exists=True, other_objects=0, tables=frozenset(TABLE_NAMES), readable=True)


class LocalResults:
    def __init__(self, folder: Path, state: str = "normal"):
        self.path = Path(folder) / "results.sqlite"
        self.state = state
        self._lock = threading.Lock()
        # missing_schema, missing_tables, or ready. The "Set up results storage" button changes it.
        self.storage = ("missing_schema" if state in ("schema-missing", "schema-refused")
                        else "missing_tables" if state in ("tables-missing", "no-scan", "schema-shared")
                        else "ready")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def check(self) -> str:
        if self.storage == "missing_schema":
            raise ResultsUnavailable("schema_missing")
        if self.storage == "missing_tables" or not self.path.exists():
            return "no_tables"
        return "ok"

    def admin_statement(self, kind: str) -> tuple[str, bool]:
        from common.schema import admin_owner_sql

        build = admin_owner_sql if kind == "owner" else admin_schema_sql
        return build(LOCAL_SCHEMA, PREVIEW_DATABASE_USER), True

    def setup_storage(self) -> StorageSetup:
        """Made-up: schema-refused shows the statement for a database admin; the others succeed."""
        with self._lock:
            existed = self.storage != "missing_schema"
            if self.state == "schema-shared":
                return StorageSetup("not_dedicated", "schema", schema_existed=True, other_objects=PREVIEW_OTHER_OBJECTS)
            if self.state == "schema-refused" and not existed:
                return StorageSetup("schema_failed", "schema", "CivisJobFailure",
                                    admin_statement=admin_schema_sql(LOCAL_SCHEMA, PREVIEW_DATABASE_USER))
            self.storage = "ready"
            return StorageSetup("created", schema_existed=existed)

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
        if self.check() != "ok" or self.state in FRESH_STATES + ("no-scan",):
            return []
        statement = f"SELECT * FROM {sql.identifier(table)}"
        params: tuple = ()
        if run_id is not None:
            statement += " WHERE run_id = ?"
            params = (run_id,)
        with self._lock, self._connect() as conn:
            rows = [normalize_row(table, dict(r)) for r in conn.execute(statement, params).fetchall()]
            if self.state in ("first-scan-running", "first-scan-cancelled"):
                # Only the newest scan exists yet: the newest run with no final status row (running, or cancelled
                # before it wrote one).
                first = self._running_run_id(conn)
                rows = [r for r in rows if r.get("run_id") == first]
            return rows

    @staticmethod
    def _running_run_id(conn: sqlite3.Connection) -> str | None:
        rows = conn.execute(f"SELECT run_id, status, started_at FROM {SCAN_RUNS.name}").fetchall()
        final = {r["run_id"] for r in rows if r["status"] != "running"}
        running = [r for r in rows if r["run_id"] not in final]
        return max(running, key=lambda r: r["started_at"] or "")["run_id"] if running else None

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


def _minutes_later(stamp: str | None, minutes: int) -> str | None:
    try:
        moment = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return stamp
    return (moment + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class PreviewUnreadable(Exception):
    """Made-up: what civis raises when the scanner job cannot be read (job-unreadable state)."""

    status_code = 404


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

    def settings(self) -> JobSettings:
        if self.state == "job-unreadable":
            return unreadable("PreviewUnreadable status=404")
        return parse_job(preview_job(self.job_id, self.state))

    def _apply_state(self) -> None:
        if self.state in ("no-scan",) + FRESH_STATES:
            self._runs = []
        elif self.state == "first-scan-running":
            self._runs = [r for r in self._runs if r.state == "running"]
        elif self.state == "first-scan-cancelled":
            # The first scan was cancelled: Civis lists its run as cancelled, and it wrote no final status row.
            self._runs = [JobRun(r.id, "cancelled", r.created_at, r.started_at, _minutes_later(r.started_at, 12))
                          for r in self._runs if r.state == "running"]
        elif self.state in ("failed", "tables-failed"):
            # Drop the running scan, and show the newest finished run as failed. With
            # tables-failed, that run's scan also has status tables_failed (exit code 3).
            self._runs = sorted((r for r in self._runs if r.state != "running"), key=lambda r: r.id, reverse=True)
            if self._runs:
                newest = self._runs[0]
                self._runs[0] = JobRun(newest.id, "failed", newest.created_at, newest.started_at, newest.finished_at)

    def _check_readable(self) -> None:
        if self.state == "job-unreadable":
            raise PreviewUnreadable("made-up error")

    def runs(self, limit: int = 20) -> list[JobRun]:
        self._check_readable()
        with self._lock:
            return sorted(self._runs, key=lambda r: r.id, reverse=True)[:limit]

    def logs(self, run_id: int, last_id: int | None = None) -> list[LogLine]:
        self._check_readable()
        entries = self._logs.get(str(run_id), [])
        lines = [LogLine(e["id"], e.get("message", ""), e.get("created_at")) for e in entries]
        return [line for line in lines if last_id is None or line.id > last_id]

    def start(self) -> JobRun:
        self._check_readable()
        with self._lock:
            new_id = max([r.id for r in self._runs], default=0) + 1
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            run = JobRun(new_id, "queued", stamp)
            self._runs.append(run)
            return run

    def report(self, run_id: int) -> tuple[str, Any] | None:
        self._check_readable()
        name = self._outputs.get(str(run_id))
        if not name:
            return None
        path = self.folder / name
        return ("file", path) if path.exists() else None


def _load_jobs(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
