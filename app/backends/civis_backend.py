"""The data layer inside Civis, using only civis 2.7.1 calls (the Service image).

Reads use ``civis.io.read_civis_sql``. The checklist save and the "Set up
results storage" button use ``civis.io.query_civis``: one INSERT, or CREATE
SCHEMA IF NOT EXISTS and CREATE TABLE IF NOT EXISTS for the five tables. No
GRANT, DROP, or ALTER. Neither supports bound parameters, so every value in
the SQL text is validated and escaped by ``common/sql.py`` and every name is
validated as an identifier. Each read starts a query run in Civis, so the app
caches results (see app/backends/cache.py).

Scanner job calls: scripts.get_containers (the job's settings, which hold the
results database, credential, and schema), list_containers_runs,
list_containers_runs_logs, post_containers_runs, list_containers_runs_outputs,
and files.get.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common import sql
from common.schema import CHECKLIST_STATUS, SCAN_RUNS, TABLES, admin_schema_sql, checklist_insert_sql, create_schema_sql
from app.backends.base import (
    ChecklistSaveError, JobRun, LogLine, ResultsUnavailable, StorageSetup, error_token, normalize_row, plain,
)
from app.job_settings import JobSettings, parse_job, unreadable

TABLE_NAMES = {t.name for t in TABLES}
LOG_PAGE = 1000


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class CivisResults:
    """``database`` is a database ID (from the scanner job's RESULTS_DB parameter) or a name;
    civis.io accepts either."""

    def __init__(self, client: Any, io_module: Any, database: int | str, schema: str, credential_id: int | None):
        self.client = client
        self.io = io_module
        self.database = database
        self.schema = sql.identifier(schema)
        self.credential_id = credential_id

    def _read(self, statement: str) -> list[dict]:
        rows = self.io.read_civis_sql(
            statement, self.database, return_as="list", client=self.client,
            credential_id=self.credential_id, hidden=True,
        )
        if not rows:
            return []
        header, body = rows[0], rows[1:]
        return [dict(zip(header, r)) for r in body]

    def check(self) -> str:
        try:
            schemas = self._read(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = " + sql.string(self.schema)
            )
            if not schemas:
                raise ResultsUnavailable("schema_missing")
            tables = self._read(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = " + sql.string(self.schema)
                + " AND table_name = " + sql.string(SCAN_RUNS.name)
            )
        except ResultsUnavailable:
            raise
        except Exception:
            raise ResultsUnavailable("error") from None
        return "ok" if tables else "no_tables"

    def _query(self, statement: str) -> None:
        self.io.query_civis(
            statement, self.database, client=self.client, credential_id=self.credential_id, hidden=True
        ).result()

    def _schema_exists(self) -> bool:
        rows = self._read(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = " + sql.string(self.schema)
        )
        return bool(rows)

    def database_user(self) -> str | None:
        """The user name on the RESULTS_DB credential (credentials.get). None if there is no credential or it
        cannot be read. Shown only to admins, in the statement for a database admin."""
        if self.credential_id is None:
            return None
        try:
            credential = plain(self.client.credentials.get(self.credential_id))
        except Exception:
            return None
        name = credential.get("username") if isinstance(credential, dict) else None
        return name if isinstance(name, str) and name else None

    def setup_storage(self) -> StorageSetup:
        """Create the results schema if it is missing, then the five tables (IF NOT EXISTS).

        The schema is checked first, because the database may refuse CREATE SCHEMA, even with IF NOT
        EXISTS, to a user who cannot create schemas, after a database admin has created it for them."""
        try:
            existed = self._schema_exists()
        except Exception as exc:
            return StorageSetup("schema_failed", "schema", error_token(exc))  # Could not even check: no statement.
        if not existed:
            try:
                self._query(create_schema_sql(self.schema))
            except Exception as exc:
                user = self.database_user()
                return StorageSetup("schema_failed", "schema", error_token(exc),
                                    admin_statement=admin_schema_sql(self.schema, user),
                                    user_known=sql.quoted_identifier(user) is not None)
        for table in TABLES:
            try:
                self._query(table.create_sql(self.schema))
            except Exception as exc:
                return StorageSetup("tables_failed", "tables", error_token(exc), schema_existed=existed)
        return StorageSetup("created", schema_existed=existed)

    def scan_runs(self) -> list[dict]:
        return self._table_rows(SCAN_RUNS.name, None)

    def rows(self, table: str, run_id: str) -> list[dict]:
        return self._table_rows(table, run_id)

    def checklist(self) -> list[dict]:
        return self._table_rows(CHECKLIST_STATUS.name, None)

    def _table_rows(self, table: str, run_id: str | None) -> list[dict]:
        if table not in TABLE_NAMES:
            raise ValueError("unknown table")
        statement = f"SELECT * FROM {sql.qualified_table(self.schema, table)}"
        if run_id is not None:
            statement += f" WHERE run_id = {sql.run_id(run_id)}"
        try:
            return [normalize_row(table, r) for r in self._read(statement)]
        except Exception:
            raise ResultsUnavailable("error") from None

    def save_checklist(self, run_id: str, entries: list[tuple[int, str, str]], changed_by: str, when: datetime) -> None:
        try:
            statement = checklist_insert_sql(self.schema, run_id, entries, changed_by, when)
        except sql.UnsafeValueError as exc:
            raise ChecklistSaveError(f"The changes could not be saved: {exc}") from None
        try:
            self._query(statement)
        except Exception:
            raise ChecklistSaveError("The changes could not be saved because the database write failed.") from None


class CivisJobs:
    def __init__(self, client: Any, job_id: int | None):
        self.client = client
        self.job_id = job_id

    def settings(self) -> JobSettings:
        """The results database, credential, and schema from the job's parameters. Reads only."""
        try:
            job = plain(self.client.scripts.get_containers(self.job_id))
        except Exception as exc:
            return unreadable(error_token(exc))
        if not isinstance(job, dict):
            return unreadable("Exception")
        return parse_job(job)

    def runs(self, limit: int = 20) -> list[JobRun]:
        rows = self.client.scripts.list_containers_runs(self.job_id, limit=limit)
        runs = [
            JobRun(
                id=int(_get(r, "id")), state=str(_get(r, "state") or "").lower(),
                created_at=_get(r, "created_at"), started_at=_get(r, "started_at"), finished_at=_get(r, "finished_at"),
            )
            for r in rows
        ]
        return sorted(runs, key=lambda r: r.id, reverse=True)

    def logs(self, run_id: int, last_id: int | None = None) -> list[LogLine]:
        kwargs = {"limit": LOG_PAGE}
        if last_id is not None:
            kwargs["last_id"] = last_id
        rows = self.client.scripts.list_containers_runs_logs(self.job_id, run_id, **kwargs)
        lines = [
            LogLine(id=int(_get(r, "id")), message=str(_get(r, "message") or ""), created_at=_get(r, "created_at"))
            for r in rows
        ]
        return sorted(lines, key=lambda line: line.id)

    def start(self) -> JobRun:
        run = self.client.scripts.post_containers_runs(self.job_id)
        return JobRun(id=int(_get(run, "id")), state=str(_get(run, "state") or "queued").lower(),
                      created_at=_get(run, "created_at"))

    def report(self, run_id: int) -> tuple[str, Any] | None:
        outputs = self.client.scripts.list_containers_runs_outputs(self.job_id, run_id)
        for output in outputs:
            name = str(_get(output, "name") or "")
            if _get(output, "object_type") == "File" and name.endswith(".csv"):
                file = self.client.files.get(_get(output, "object_id"))
                url = _get(file, "file_url")
                return ("url", url) if url else None
        return None
