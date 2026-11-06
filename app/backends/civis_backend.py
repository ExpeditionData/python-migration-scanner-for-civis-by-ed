"""The data layer inside Civis, using only civis 2.7.1 calls (the Service image).

Reads whose result is small by design (the catalog check of the schema and
its tables, Setup line 9's row counts, and the scan_runs rows) use
``civis.io.query_civis`` and take the result from the query's preview rows,
asking for 100. The civis 2.10 API spec gives 1,000 as the maximum, but civis
2.7.1's own ``query_civis`` docstring says "No more than 100 rows can be
returned at once", and the first live test never asked for more than 10, so
100 is safe either way. It returns the rows in the query's own response, so
no export file is written and read: in the first live test it took about 2
seconds a read, against 4 to 6 seconds for ``read_civis_sql``. The API
returns preview values as text, so values are converted by column type
(``normalize_row``) where it matters. A result with 100 or more preview rows
may be cut, so it is read again with ``read_civis_sql``. ``scan_runs`` holds
two rows per scan, so after 50 scans its read falls back to
``read_civis_sql`` every time; that is expected. A zero-row result is normal
there. The objects, findings, coverage, and checklist rows, which can be
large, use ``read_civis_sql`` (docs/PLAN.md 17.6, Part D).

Within one request, a small read with the same SQL is sent once (a request
memo, cleared by any write), so the Overview check and Setup line 9 share one
catalog read. The 5-minute job settings cache and the results cache in
app/backends/cache.py are unchanged.

The checklist save and the "Set up results storage" button use
``civis.io.query_civis``: one INSERT, or CREATE SCHEMA IF NOT EXISTS and
CREATE TABLE IF NOT EXISTS for the five tables. No GRANT, DROP, or ALTER.
Neither supports bound parameters, so every value in the SQL text is
validated and escaped by ``common/sql.py`` and every name is validated as an
identifier. Each read starts a query run in Civis, so the app caches results
(see app/backends/cache.py).

Scanner job calls: scripts.get_containers (the job's settings, which hold the
results database, credential, and schema), list_containers_runs,
list_containers_runs_logs, post_containers_runs, list_containers_runs_outputs,
and files.get.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common import sql
from common.civis_errors import is_empty_result
from common.schema import (
    CHECKLIST_STATUS, SCAN_RUNS, TABLES, admin_owner_sql, admin_schema_sql, checklist_insert_sql, create_schema_sql,
    other_object_count, schema_objects_sql,
)
from app.backends.base import (
    LOG_PAGE, ChecklistSaveError, JobRun, LogLine, ResultsUnavailable, StorageFacts, StorageSetup, error_token,
    normalize_row,
    plain,
)
from app.job_settings import JobSettings, parse_job, unreadable

TABLE_NAMES = frozenset(t.name for t in TABLES)
MAX_SERVICES = 1000  # The Setup page looks at no more Services than this.
# Preview rows asked for. civis 2.7.1's query_civis docstring: "No more than 100 rows can be returned at once".
# A result with this many rows or more is read again with read_civis_sql (scan_runs: after 50 scans).
PREVIEW_ROWS = 100


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class CivisResults:
    """``database`` is a database ID (from the scanner job's RESULTS_DB parameter) or a name;
    civis.io accepts either."""

    def __init__(self, client: Any, io_module: Any, database: int | str, schema: str, credential_id: int | None,
                 memo: dict | None = None):
        self.client = client
        self.io = io_module
        self.database = database
        self.schema = sql.identifier(schema)
        self.credential_id = credential_id
        # Small reads already sent in this request, by (database, credential, SQL). Shared by every CivisResults
        # made for one request (app/services.py), and cleared by any write.
        self.memo = memo if memo is not None else {}

    def _read(self, statement: str) -> list[dict]:
        """Rows as dicts. read_civis_sql raises EmptyResultError when a query returns no rows (Civis writes no
        file for an empty result), so that error means zero rows. Every other error is raised as is."""
        try:
            rows = self.io.read_civis_sql(
                statement, self.database, return_as="list", client=self.client,
                credential_id=self.credential_id, hidden=True,
            )
        except Exception as exc:
            if is_empty_result(exc):
                return []
            raise
        if not rows:
            return []
        header, body = rows[0], rows[1:]
        return [dict(zip(header, r)) for r in body]

    def _small(self, statement: str) -> list[dict]:
        """Rows of a read whose result is small by design, from query_civis's preview rows (values as text, or
        None). A result with PREVIEW_ROWS rows or more may be cut, so it is read again with read_civis_sql. The same
        SQL is sent once per request (``memo``)."""
        key = (self.database, self.credential_id, statement)
        if key in self.memo:
            return self.memo[key]
        response = self.io.query_civis(
            statement, self.database, client=self.client, credential_id=self.credential_id,
            preview_rows=PREVIEW_ROWS, hidden=True,
        ).result()
        columns = [str(c) for c in (_get(response, "result_columns") or [])]
        body = list(_get(response, "result_rows") or [])
        if len(body) >= PREVIEW_ROWS:
            rows = self._read(statement)
        else:
            rows = [dict(zip(columns, r)) for r in body]
        self.memo[key] = rows
        return rows

    def check(self) -> str:
        try:
            catalog = self._catalog()
        except Exception:
            raise ResultsUnavailable("error") from None
        if not _schema_found(catalog):
            raise ResultsUnavailable("schema_missing")
        return "ok" if SCAN_RUNS.name in _tables(catalog) else "no_tables"

    def _query(self, statement: str) -> None:
        self.memo.clear()  # A write can change what a small read returns.
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

    def _catalog(self) -> list[dict]:
        """One read for the existence and dedicated-schema checks: a "schema" row if the schema exists, then every
        table and view in it (common/schema.schema_objects_sql, the check the scanner runs too)."""
        return self._small(
            f"SELECT 'schema' AS kind, nspname AS name FROM pg_catalog.pg_namespace WHERE nspname = "
            f"{self._catalog_name()} UNION ALL {schema_objects_sql(self.schema)}"
        )

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
            catalog = self._catalog()
        except Exception as exc:
            return StorageSetup("schema_failed", "schema", error_token(exc))  # Could not even check: no statement.
        existed = _schema_found(catalog)
        if existed:
            # The tool uses only a schema that holds nothing but its own tables.
            other = other_object_count(_objects(catalog))
            if other:
                return StorageSetup("not_dedicated", "schema", schema_existed=True, other_objects=other)
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
                if not existed:  # The click created the schema, so its owner is the RESULTS_DB user already.
                    return StorageSetup("tables_failed", "tables", error_token(exc))
                # The schema existed before the click: most likely the RESULTS_DB user cannot create tables in it.
                user = self.database_user()
                return StorageSetup("tables_failed", "tables", error_token(exc), schema_existed=True,
                                    admin_statement=admin_owner_sql(self.schema, user),
                                    user_known=sql.quoted_identifier(user) is not None)
        return StorageSetup("created", schema_existed=existed)

    def admin_statement(self, kind: str) -> tuple[str, bool]:
        """Rebuild the statement for a database admin after the setup redirect: "schema" for CREATE SCHEMA
        ... AUTHORIZATION, "owner" for ALTER SCHEMA ... OWNER TO. Returns it and whether the user name is in it."""
        user = self.database_user()
        build = admin_owner_sql if kind == "owner" else admin_schema_sql
        return build(self.schema, user), sql.quoted_identifier(user) is not None

    def storage_facts(self) -> StorageFacts:
        """For the Setup page: does the schema exist, which tables exist, and can the credential read them.
        SELECT statements only; one more for the read check, over all five tables at once."""
        try:
            catalog = self._catalog()
        except Exception as exc:
            return StorageFacts(error=error_token(exc), step="schema")
        if not _schema_found(catalog):
            return StorageFacts(schema_exists=False)
        other = other_object_count(_objects(catalog))
        tables = _tables(catalog)
        if other or tables != TABLE_NAMES:
            return StorageFacts(schema_exists=True, other_objects=other, tables=tables)
        statement = " UNION ALL ".join(
            f"SELECT {sql.string(t.name)} AS table_name, COUNT(*) AS row_count FROM {sql.qualified_table(self.schema, t.name)}"
            for t in TABLES
        )
        try:
            self._small(statement)
        except Exception as exc:
            return StorageFacts(schema_exists=True, other_objects=0, tables=tables, readable=False,
                                error=error_token(exc), step="read")
        return StorageFacts(schema_exists=True, other_objects=0, tables=tables, readable=True)

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
        # scan_runs holds two rows per scan, so it is small by design. The others can be large.
        read = self._small if table == SCAN_RUNS.name else self._read
        try:
            return [normalize_row(table, r) for r in read(statement)]
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


def _schema_found(catalog: list[dict]) -> bool:
    return any(r.get("kind") == "schema" for r in catalog)


def _objects(catalog: list[dict]) -> list[dict]:
    """The schema's tables and views (the dedicated-schema check's rows), without the schema row."""
    return [r for r in catalog if r.get("kind") in ("table", "view")]


def _tables(catalog: list[dict]) -> frozenset:
    return frozenset(str(r.get("name")) for r in catalog if r.get("kind") == "table") & TABLE_NAMES


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

    def __init__(self, client: Any, io_module: Any, job_id: int | None, memo: dict | None = None):
        self.client = client
        self.io = io_module
        self.job_id = job_id
        self.memo = memo  # The request's small-read memo, shared with the results reads (CivisResults).

    def me(self) -> dict:
        """The user the app runs as. The Service image's civis 2.7.1 has only users.list_me; civis 2.8 and
        later add users.get_me, and v3 removes list_me. Use get_me where it exists, so this keeps working."""
        users = self.client.users
        read_me = getattr(users, "get_me", None) or users.list_me
        return plain(read_me())

    def credential(self, credential_id: int) -> dict:
        return plain(self.client.credentials.get(credential_id))

    # Both shares calls return one object with readers, writers, and owners (confirmed in the first live test).
    # app/setup.share_members also accepts a list of such objects.

    def job_shares(self) -> Any:
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

    def service_shares(self, service_id: int) -> Any:
        return plain(self.client.services.list_shares(service_id))

    def storage(self, settings: JobSettings) -> StorageFacts:
        try:
            results = CivisResults(self.client, self.io, settings.database_id, settings.schema, settings.credential_id,
                                   memo=self.memo)
        except Exception as exc:
            return StorageFacts(error=error_token(exc), step="schema")
        return results.storage_facts()
