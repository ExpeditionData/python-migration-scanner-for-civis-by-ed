"""Made-up stand-ins for the civis client and civis.io, for the web app's Civis data layer.

Nothing here contacts Civis. Every name, ID, and value is invented. Responses
behave like civis 2.7.1's ``Response``: attribute and key access, and
``json()`` returns snake_case keys except inside ``arguments`` and
``environment_variables``, whose keys keep their case.
"""

from __future__ import annotations

import copy
import re
from typing import Any

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


def shares(users: list[tuple[int, str]], groups: list[tuple[int, str]]) -> list[dict]:
    return [{
        "readers": {"users": [{"id": i, "name": n} for i, n in users], "groups": [{"id": i, "name": n} for i, n in groups]},
        "writers": {"users": [], "groups": []},
        "owners": {"users": [{"id": ROBOT["id"], "name": ROBOT["name"]}], "groups": []},
        "total_user_shares": len(users) + 1, "total_group_shares": len(groups),
    }]


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
    def __init__(self, error: Exception | None = None):
        self.error = error

    def result(self):
        if self.error:
            raise self.error
        return None


_TABLE_NAME = re.compile(r"FROM\s+([A-Za-z_][\w]*)\.([A-Za-z_]\w*)", re.IGNORECASE)


class RecordingIO:
    """A made-up civis.io that records every statement and answers from ``tables``.

    ``tables`` maps a text found in the SQL to the list-of-lists civis.io returns
    (a header row, then rows). ``fail`` maps a text found in the SQL to an
    exception for query_civis to raise from ``.result()``.
    """

    def __init__(self, tables: dict[str, list[list]] | None = None, fail: dict[str, Exception] | None = None):
        self.tables = tables or {}
        self.fail = fail or {}
        self.statements: list[str] = []
        self.reads: list[str] = []
        self.queries: list[str] = []
        self.frames: list[str] = []
        self.databases: list[Any] = []
        self.credentials: list[Any] = []

    def read_civis_sql(self, sql, database, return_as=None, client=None, credential_id=None, hidden=True, **kwargs):
        assert return_as == "list"
        self.statements.append(sql)
        self.reads.append(sql)
        self.databases.append(database)
        self.credentials.append(credential_id)
        for key, error in self.fail.items():
            if key in sql:
                raise error
        for key, rows in self.tables.items():
            if key in sql:
                return copy.deepcopy(rows)
        return []

    def query_civis(self, sql, database, client=None, credential_id=None, hidden=True, **kwargs):
        self.statements.append(sql)
        self.queries.append(sql)
        self.databases.append(database)
        self.credentials.append(credential_id)
        for key, error in self.fail.items():
            if key in sql:
                return FakeFuture(error)
        return FakeFuture()

    def dataframe_to_civis(self, df, database, table, client=None, existing_table_rows=None, credential_id=None,
                           hidden=True, **kwargs):
        self.frames.append(table)
        self.databases.append(database)
        self.credentials.append(credential_id)
        return FakeFuture()
