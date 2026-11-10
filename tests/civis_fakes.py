"""Made-up stand-ins for the civis client and civis.io, for the web app's Civis data layer.

Nothing here contacts Civis. Every name, ID, and value is invented. Responses
behave like civis 2.7.1's ``Response``: attribute and key access, and
``json()`` returns snake_case keys except inside ``arguments`` and
``environment_variables``, whose keys keep their case.
"""

from __future__ import annotations

import copy
import csv
import io
import re
from typing import Any

try:
    from civis.base import CivisJobFailure, EmptyResultError
except ImportError:  # Both venvs and both test images have civis; this keeps the fakes usable without it.
    class EmptyResultError(Exception):
        """A made-up stand-in with the name of civis.base.EmptyResultError."""

    class CivisJobFailure(Exception):
        """A made-up stand-in with the name and first argument of civis.base.CivisJobFailure."""

        def __init__(self, err_msg, response=None, job_id=None, run_id=None):
            super().__init__(err_msg)
            self.error_message = err_msg


_WHOLE_NUMBER_CELL = re.compile(r"-?[0-9]+")
_BOOLEAN_CELLS = frozenset({"true", "false"})


def load_frame_like_civis(df, table: str) -> str:
    """Render ``df`` the way civis 2.10's dataframe_to_civis does (``df.to_csv(index=False)``, then a load of that
    CSV into the table), and check every cell against its column's type the way the database load would.

    ``table`` is "schema.table"; its columns come from common/schema.py. INTEGER and BIGINT cells must be an
    optional minus sign and digits, or empty. BOOLEAN cells must be true, false, or empty. Returns the CSV text.
    A bad cell, or column names that do not match the table, raises civis.base.CivisJobFailure, as the import
    job's future does when the load fails (the scan then ends tables_failed, as in the live test)."""
    from common.schema import TABLES

    name = table.rsplit(".", 1)[-1]
    spec = next((t for t in TABLES if t.name == name), None)
    if spec is None:
        raise CivisJobFailure(f"Made-up load failure: {table} is not one of the tool's tables.")
    text = df.to_csv(index=False)
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    if header != spec.column_names:
        raise CivisJobFailure(f"Made-up load failure: the columns do not match {name}.")
    types = [c.sql_type for c in spec.columns]
    for line, row in enumerate(reader, start=2):
        for column, sql_type, cell in zip(header, types, row):
            if cell == "":
                continue  # NULL.
            if sql_type in ("INTEGER", "BIGINT") and not _WHOLE_NUMBER_CELL.fullmatch(cell):
                raise CivisJobFailure(f"Made-up load failure: {name}.{column} on CSV line {line} is not a whole "
                                      f"number: {cell!r}.")
            if sql_type == "BOOLEAN" and cell not in _BOOLEAN_CELLS:
                raise CivisJobFailure(f"Made-up load failure: {name}.{column} on CSV line {line} is not a "
                                      f"boolean: {cell!r}.")
    return text


def no_output() -> EmptyResultError:
    """What civis.io.read_civis_sql raises for a query with no rows: Civis writes no file for an empty result."""
    return EmptyResultError("Query 0 returned no output.")


def civis_read_result(rows: list[list] | None) -> list[list]:
    """Return ``rows`` (a header row, then data rows) as read_civis_sql would: no data rows raises EmptyResultError."""
    if not rows or len(rows) < 2:
        raise no_output()
    return copy.deepcopy(rows)


REPO = "https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git"
ROBOT = {"id": 15, "name": "Example Robot", "username": "example_robot"}


class FakeResponse:
    """Enough of civis.response.Response for the app: .json(), [key], .get(), and attributes."""

    def __init__(self, data: dict):
        self._data = data

    def json(self) -> dict:
        return {k: v.json() if isinstance(v, FakeResponse) else copy.deepcopy(v) for k, v in self._data.items()}

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(key) from None


class FakeAPIError(Exception):
    """Like civis.base.CivisAPIError: a message that may hold names, and a status code."""

    def __init__(self, status_code: int, message: str = "made-up error text"):
        super().__init__(message)
        self.status_code = status_code


def scanner_job(**changes: Any) -> dict:
    """A made-up scanner job that matches the README."""
    job = {
        "id": 1234, "name": "Example scanner job", "author": dict(ROBOT), "running_as": dict(ROBOT),
        "docker_image_name": "civisanalytics/datascience-python", "docker_image_tag": "9.1.1",
        "repo_http_uri": REPO, "repo_ref": "v0.1.0-rc3", "git_credential_id": None,
        "docker_command": "cd /app && python -m scanner",
        "required_resources": {"cpu": 1000, "memory": 4096, "disk_space": 4.0},
        "params": [
            {"name": "RESULTS_DB", "type": "database", "required": True},
            {"name": "RESULTS_SCHEMA", "type": "string", "required": True},
        ],
        "arguments": {"RESULTS_DB": {"database": 31, "credential": 41}, "RESULTS_SCHEMA": "migration_results"},
    }
    job.update(changes)
    return job


def app_service(**changes: Any) -> dict:
    service = {
        "id": 501, "name": "Example scanner app", "user": dict(ROBOT),
        "docker_image_name": "civisanalytics/civis-services-flask", "docker_image_tag": "0.1.0",
        "git_repo_url": REPO, "git_repo_ref": "v0.1.0-rc3", "credentials": [],
        "environment_variables": {"SCANNER_JOB_ID": "1234", "CIVIS_SERVICE_HEALTH_PATH": "/healthz"},
    }
    service.update(changes)
    return service


def shares(users: list[tuple[int, str]], groups: list[tuple[int, str]]) -> dict:
    """One object with readers, writers, and owners: what scripts.list_containers_shares and
    services.list_shares return (confirmed in the first live test)."""
    return {
        "readers": {"users": [{"id": i, "name": n} for i, n in users], "groups": [{"id": i, "name": n} for i, n in groups]},
        "writers": {"users": [], "groups": []},
        "owners": {"users": [{"id": ROBOT["id"], "name": ROBOT["name"]}], "groups": []},
        "total_user_shares": len(users) + 1, "total_group_shares": len(groups),
    }


class _Endpoint:
    def __init__(self, client: "FakeCivisClient", name: str):
        self._client = client
        self._name = name

    def __getattr__(self, method):
        if f"{self._name}.{method}" not in self._client.handlers:
            raise AttributeError(method)  # Like civis 2.7.1, which has no such method.

        def call(*args, **kwargs):
            self._client.calls.append((f"{self._name}.{method}", args, kwargs))
            handler = self._client.handlers.get(f"{self._name}.{method}")
            if handler is None:
                raise AssertionError(f"unexpected civis call {self._name}.{method}")
            result = handler(*args, **kwargs)
            if isinstance(result, BaseException):
                raise result
            return _wrap(result)
        return call


def _wrap(value):
    if isinstance(value, dict):
        return FakeResponse(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


class FakeCivisClient:
    """``handlers`` maps "endpoint.method" to a function returning data, or an exception to raise."""

    def __init__(self, job: dict | Exception | None = None, **overrides):
        self.calls: list[tuple] = []
        job = scanner_job() if job is None else job
        self.handlers = {
            "scripts.get_containers": lambda id: job if isinstance(job, Exception) else copy.deepcopy(job),
            "scripts.list_containers_runs": lambda id, limit=None: [],
            "scripts.list_containers_runs_logs": lambda id, run_id, last_id=None, limit=None: [],
            "scripts.list_containers_shares": lambda id: shares([(21, "Example Admin One")], [(61, "Example Admins")]),
            "users.list_me": lambda: dict(ROBOT),
            "credentials.get": lambda id: {"id": id, "name": "Example results credential", "username": "example_robot_db",
                                           "user": dict(ROBOT), "owner": ROBOT["username"]},
            "services.list": lambda **kwargs: [app_service()],
            "services.get": lambda id: app_service(id=id),
            "services.list_shares": lambda id: shares([(21, "Example Admin One")], []),
        }
        self.handlers.update(overrides)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _Endpoint(self, name)

    def methods_called(self) -> list[str]:
        return [call[0] for call in self.calls]


class FakeFuture:
    def __init__(self, error: Exception | None = None, value: Any = None):
        self.error = error
        self.value = value

    def result(self):
        if self.error:
            raise self.error
        return self.value


def query_result(rows: list[list] | None, preview_rows: int = 10) -> FakeResponse:
    """What query_civis's future returns in civis 2.7.1: the query's Response, with ``result_columns`` and up to
    ``preview_rows`` of ``result_rows``, every value as text (or None). Zero rows is a normal result."""
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    text = [[None if v is None else str(v) for v in row] for row in body[:preview_rows]]
    return FakeResponse({"id": 1, "state": "succeeded", "result_columns": list(header), "result_rows": text})


_TABLE_NAME = re.compile(r"FROM\s+([A-Za-z_][\w]*)\.([A-Za-z_]\w*)", re.IGNORECASE)


class RecordingIO:
    """A made-up civis.io that records every statement and answers from ``tables``.

    ``tables`` maps a text found in the SQL to the list-of-lists civis.io returns
    (a header row, then rows). ``fail`` maps a text found in the SQL to an
    exception: read_civis_sql raises it, and query_civis raises it from
    ``.result()``.

    Like civis, read_civis_sql raises EmptyResultError when the result has no
    data rows, including for SQL that matches no key. query_civis answers a
    SELECT the way civis 2.7.1 does: the query's Response with
    ``result_columns`` and up to ``preview_rows`` text ``result_rows``, where
    zero rows is normal.

    ``reads`` holds every read, by either function: ``full_reads`` those by
    read_civis_sql and ``small_reads`` the SELECTs sent with query_civis.
    ``empty_reads`` holds the reads that returned zero rows. ``queries`` holds
    only the statements query_civis sent that are not reads (the writes).

    dataframe_to_civis renders the frame to CSV as civis 2.10 does and checks every typed cell
    (``load_frame_like_civis``); a bad cell makes the returned future raise CivisJobFailure. ``frames`` holds
    every table it was asked to write, ``loaded`` the (table, CSV text) of each load that passed, and
    ``failed_loads`` the tables whose load failed.
    """

    def __init__(self, tables: dict[str, list[list]] | None = None, fail: dict[str, Exception] | None = None):
        self.tables = tables or {}
        self.fail = fail or {}
        self.statements: list[str] = []
        self.reads: list[str] = []
        self.full_reads: list[str] = []
        self.small_reads: list[str] = []
        self.preview_rows: list[Any] = []
        self.empty_reads: list[str] = []  # Reads that returned zero rows.
        self.queries: list[str] = []
        self.frames: list[str] = []  # Every table dataframe_to_civis was asked to write.
        self.loaded: list[tuple[str, str]] = []  # (table, CSV text) for each load that passed the cell check.
        self.failed_loads: list[str] = []  # Tables whose load failed the cell check.
        self.databases: list[Any] = []
        self.credentials: list[Any] = []

    def read_civis_sql(self, sql, database, return_as=None, client=None, credential_id=None, hidden=True, **kwargs):
        assert return_as == "list"
        self.statements.append(sql)
        self.reads.append(sql)
        self.full_reads.append(sql)
        self.databases.append(database)
        self.credentials.append(credential_id)
        for key, error in self.fail.items():
            if key in sql:
                raise error
        try:
            return civis_read_result(self._rows(sql))
        except EmptyResultError:
            self.empty_reads.append(sql)
            raise

    def _rows(self, sql) -> list[list] | None:
        """The header row and rows for a read, or None when no key matches."""
        if "pg_catalog.pg_views" in sql:  # The dedicated-schema check: tables and views with a kind column.
            rows = [["kind", "name"]]
            namespace = self.tables.get("pg_catalog.pg_namespace", [["nspname"]])[1:]
            if "pg_catalog.pg_namespace" in sql and namespace:  # The app's catalog read adds the schema's own row.
                rows.append(["schema", namespace[0][0]])
            tables = self.tables.get("pg_catalog.pg_tables", [["tablename"]])[1:]
            views = self.tables.get("pg_catalog.pg_views", [["viewname"]])[1:]
            return rows + [["table", r[0]] for r in tables] + [["view", r[0]] for r in views]
        for key, rows in self.tables.items():
            if key in sql:
                return rows
        return None

    def query_civis(self, sql, database, client=None, credential_id=None, preview_rows=10, hidden=True, **kwargs):
        self.statements.append(sql)
        self.databases.append(database)
        self.credentials.append(credential_id)
        is_read = sql.lstrip().upper().startswith("SELECT")
        if is_read:
            self.reads.append(sql)
            self.small_reads.append(sql)
            self.preview_rows.append(preview_rows)
        else:
            self.queries.append(sql)
        for key, error in self.fail.items():
            if key in sql:
                return FakeFuture(error)
        if not is_read:
            return FakeFuture()
        result = query_result(self._rows(sql), preview_rows)
        if not result["result_rows"]:
            self.empty_reads.append(sql)
        return FakeFuture(value=result)

    def dataframe_to_civis(self, df, database, table, client=None, existing_table_rows=None, credential_id=None,
                           hidden=True, **kwargs):
        self.frames.append(table)
        self.databases.append(database)
        self.credentials.append(credential_id)
        try:
            self.loaded.append((table, load_frame_like_civis(df, table)))
        except CivisJobFailure as exc:
            self.failed_loads.append(table)
            return FakeFuture(exc)  # Like civis: the import job's future raises when the load fails.
        return FakeFuture()
