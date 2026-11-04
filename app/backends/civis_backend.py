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
    LOG_PAGE, ChecklistSaveError, JobRun, LogLine, ResultsUnavailable, StorageFacts, StorageSetup, error_token,
    normalize_row,
    plain,
)
from app.job_settings import JobSettings, parse_job, unreadable

TABLE_NAMES = frozenset(t.name for t in TABLES)
MAX_SERVICES = 1000  # The Setup page looks at no more Services than this.


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
            if not self._schema_exists():
                raise ResultsUnavailable("schema_missing")
            tables = self._existing_tables()
        except ResultsUnavailable:
            raise
        except Exception:
            raise ResultsUnavailable("error") from None
        return "ok" if SCAN_RUNS.name in tables else "no_tables"

    def _query(self, statement: str) -> None:
        self.io.query_civis(
            statement, self.database, client=self.client, credential_id=self.credential_id, hidden=True
        ).result()

    # Existence checks read pg_catalog, not information_schema. Redshift is based on PostgreSQL 8.0, and before
    # PostgreSQL 9.3 information_schema.schemata lists only schemas owned by a currently enabled role, so a schema
    # a database admin created and granted to the RESULTS_DB user would look missing. pg_namespace and pg_tables
    # list every schema and table whatever the user's privileges. Every name in this app's SQL is an unquoted
    # identifier, which the database stores in lower case, so the catalogs are searched for the lower-case name.

    def _catalog_name(self) -> str:
        return sql.string(self.schema.lower())

    def _schema_exists(self) -> bool:
        rows = self._read(f"SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname = {self._catalog_name()}")
        return bool(rows)

    def _existing_tables(self) -> frozenset:
        names = ", ".join(sql.string(t.name) for t in TABLES)
        rows = self._read(
            f"SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = {self._catalog_name()} "
            f"AND tablename IN ({names})"
        )
        return frozenset(str(r.get("tablename")) for r in rows) & TABLE_NAMES

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

    def storage_facts(self) -> StorageFacts:
        """For the Setup page: does the schema exist, which tables exist, and can the credential read them.
        SELECT statements only; one more for the read check, over all five tables at once."""
        try:
            if not self._schema_exists():
                return StorageFacts(schema_exists=False)
        except Exception as exc:
            return StorageFacts(error=error_token(exc), step="schema")
        try:
            tables = self._existing_tables()
        except Exception as exc:
            return StorageFacts(schema_exists=True, error=error_token(exc), step="tables")
        if tables != TABLE_NAMES:
            return StorageFacts(schema_exists=True, tables=tables)
        statement = " UNION ALL ".join(
            f"SELECT {sql.string(t.name)} AS table_name, COUNT(*) AS row_count FROM {sql.qualified_table(self.schema, t.name)}"
            for t in TABLES
        )
        try:
            self._read(statement)
        except Exception as exc:
            return StorageFacts(schema_exists=True, tables=tables, readable=False, error=error_token(exc), step="read")
        return StorageFacts(schema_exists=True, tables=tables, readable=True)

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


class CivisSetup:
    """The Setup page's reads (app/setup.py). Every call here is a read; none writes.

    users.list_me, credentials.get, scripts.list_containers_shares, services.list, services.get, and
    services.list_shares, all in civis 2.7.1. The results storage check uses CivisResults (SELECT only).
    """

    def __init__(self, client: Any, io_module: Any, job_id: int | None):
        self.client = client
        self.io = io_module
        self.job_id = job_id

    def me(self) -> dict:
        """The user the app runs as. The Service image's civis 2.7.1 has only users.list_me; civis 2.8 and
        later add users.get_me, and v3 removes list_me. Use get_me where it exists, so this keeps working."""
        users = self.client.users
        read_me = getattr(users, "get_me", None) or users.list_me
        return plain(read_me())

    def credential(self, credential_id: int) -> dict:
        return plain(self.client.credentials.get(credential_id))

    def job_shares(self) -> list:
        return plain(self.client.scripts.list_containers_shares(self.job_id))

    def services(self) -> list[dict]:
        found = []
        for item in self.client.services.list(iterator=True, limit=50):
            found.append(plain(item))
            if len(found) >= MAX_SERVICES:
                break
        return found

    def service(self, service_id: int) -> dict:
        return plain(self.client.services.get(service_id))

    def service_shares(self, service_id: int) -> list:
        return plain(self.client.services.list_shares(service_id))

    def storage(self, settings: JobSettings) -> StorageFacts:
        try:
            results = CivisResults(self.client, self.io, settings.database_id, settings.schema, settings.credential_id)
        except Exception as exc:
            return StorageFacts(error=error_token(exc), step="schema")
        return results.storage_facts()
