"""The app's database settings, read from the scanner job's own parameters.

The admin sets the results database, credential, and schema once, on the
scanner job (README, "Create the scanner job"). The app reads them from the
job with ``scripts.get_containers(SCANNER_JOB_ID)`` (app/backends/civis_backend.py),
so the Service needs no copy of them. This module only parses the job's
settings; it calls nothing.

The Database parameter value. Civis's "Script Parameters" help describes a
Database argument as "a dictionary containing the keys 'database' and
'credential'", whose values are the database ID and the database credential
ID, and its API example stores the same object as JSON text. The civis 2.10
API spec types ``arguments`` only as an object. Accepted shapes, each recorded
as a label (never the values):

| Label | Value |
|---|---|
| object | {"database": <id>, "credential": <id>} |
| object_without_credential | {"database": <id>} (the app user's default credential is used) |
| json_text | the same object as JSON text |
| object_id_keys | {"database_id": <id>, "credential_id": <id>}, or camelCase keys |
| database_id_only | a database ID alone, as a number or digits (default credential) |
| missing | no value |
| not_recognized | anything else |

The live test confirms which shape Civis returns (docs/PLAN.md 17.2).

LOOKBACK_DAYS (optional Integer): the window the next scan will use, shown
next to Run scan. The job's saved argument, the parameter's fixed value, or
the parameter's default (Civis passes a default when no argument is set), else
400 days (``common/release.DEFAULT_LOOKBACK_DAYS``, the scanner's default).
Civis's start-run call takes only the job ID, so Run scan cannot pass another
window (docs/PLAN.md 17.6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from common import release, sql

DATABASE_PARAMETER = "RESULTS_DB"
SCHEMA_PARAMETER = "RESULTS_SCHEMA"
DATABASE_SHAPES = (
    "object", "object_without_credential", "json_text", "object_id_keys", "database_id_only", "missing",
    "not_recognized",
)
# Why the app cannot use the job's settings. None means it can.
PROBLEMS = ("job_unreadable", "not_scanner_job", "database_missing", "database_unrecognized", "schema_missing",
            "schema_invalid")


@dataclass(frozen=True)
class JobSettings:
    database_id: int | None = None
    credential_id: int | None = None
    schema: str | None = None
    database_shape: str = "missing"
    problem: str | None = None
    error_type: str | None = None  # Set when the job could not be read: an exception class name, maybe with a status.
    job: dict | None = field(default=None, repr=False, compare=False)  # The job's settings, for the Setup page.
    # The window the next scan will use: the saved LOOKBACK_DAYS argument, the parameter's fixed value or default, else
    # 400. None when that value is not a whole number from 1 to 3650 (the scan would stop with a configuration problem).
    lookback_days: int | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None

    @property
    def target(self) -> tuple:
        """What the results cache depends on. When it changes, cached results are dropped."""
        return (self.database_id, self.credential_id, self.schema)


def unreadable(error_type: str) -> JobSettings:
    return JobSettings(problem="job_unreadable", error_type=error_type)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _from_mapping(value: dict) -> tuple[int | None, int | None, str]:
    keys = {str(k): v for k, v in value.items()}
    if "database" in keys:
        database, credential = _positive_int(keys["database"]), keys.get("credential")
        label = "object" if credential not in (None, "") else "object_without_credential"
    elif "database_id" in keys or "databaseId" in keys:
        database = _positive_int(keys.get("database_id", keys.get("databaseId")))
        credential = keys.get("credential_id", keys.get("credentialId"))
        label = "object_id_keys"
    else:
        return None, None, "not_recognized"
    if database is None:
        return None, None, "not_recognized"
    if credential in (None, ""):
        return database, None, label
    credential_id = _positive_int(credential)
    if credential_id is None:
        return None, None, "not_recognized"
    return database, credential_id, label


def database_value(value: Any) -> tuple[int | None, int | None, str]:
    """(database ID, credential ID or None, shape label) from a Database parameter value."""
    if value is None or value == "" or value == {}:
        return None, None, "missing"
    if isinstance(value, dict):
        return _from_mapping(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                decoded = json.loads(text)
            except ValueError:
                return None, None, "not_recognized"
            if not isinstance(decoded, dict):
                return None, None, "not_recognized"
            database, credential, label = _from_mapping(decoded)
            return (database, credential, "json_text") if database is not None else (None, None, "not_recognized")
    database = _positive_int(value)
    if database is not None:
        return database, None, "database_id_only"
    return None, None, "not_recognized"


def parameters(job: dict) -> list[dict]:
    items = job.get("params") or []
    return [p for p in items if isinstance(p, dict)]


def parameter_value(job: dict, name: str) -> Any:
    """A parameter's value: the job's saved argument, or a fixed value set on the parameter itself."""
    arguments = job.get("arguments")
    if isinstance(arguments, dict) and arguments.get(name) not in (None, ""):
        return arguments[name]
    for param in parameters(job):
        if param.get("name") == name and param.get("value") not in (None, ""):
            return param["value"]
    return None


def _not_set(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def lookback_days(job: dict) -> int | None:
    """The LOOKBACK_DAYS the scanner will get, or None when it is not a whole number in the scanner's range, which
    stops a scan before it starts (scanner/config.py).

    In order: the job's saved argument, the parameter's fixed value ("value"), the parameter's "default", then 400
    (``common/release.DEFAULT_LOOKBACK_DAYS``). Civis's API spec says a parameter's default is used when no argument
    is set, and the scanner gets it as its LOOKBACK_DAYS environment variable. None and blank text count as not set
    at each step. (RESULTS_DB and RESULTS_SCHEMA are required parameters, which Civis does not allow a default on,
    so ``parameter_value`` does not read defaults.)"""
    candidates: list[Any] = []
    arguments = job.get("arguments")
    if isinstance(arguments, dict):
        candidates.append(arguments.get(release.LOOKBACK_PARAMETER))
    param = next((p for p in parameters(job) if p.get("name") == release.LOOKBACK_PARAMETER), None)
    if param is not None:
        candidates += [param.get("value"), param.get("default")]
    value = next((c for c in candidates if not _not_set(c)), None)
    if value is None:
        return release.DEFAULT_LOOKBACK_DAYS
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    low, high = release.LOOKBACK_DAYS_RANGE
    return value if isinstance(value, int) and low <= value <= high else None


def parse_job(job: dict) -> JobSettings:
    """The results database, credential, and schema from the scanner job's settings."""
    database, credential, shape = database_value(parameter_value(job, DATABASE_PARAMETER))
    raw_schema = parameter_value(job, SCHEMA_PARAMETER)
    schema = raw_schema.strip() if isinstance(raw_schema, str) else None
    problem = None
    if shape == "missing":
        problem = "database_missing"
    elif shape == "not_recognized":
        problem = "database_unrecognized"
    if not schema:
        problem = problem or "schema_missing"
        schema = None
    else:
        try:
            sql.identifier(schema)  # Every schema name goes through sql.identifier before any SQL.
        except sql.UnsafeValueError:
            problem = problem or "schema_invalid"
            schema = None
    if not release.is_scanner_job(job):
        # SCANNER_JOB_ID names some other job. Its parameters are never used to read or create anything.
        problem = "not_scanner_job"
    return JobSettings(database, credential, schema, shape, problem, None, job, lookback_days=lookback_days(job))
