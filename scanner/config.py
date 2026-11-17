"""Scanner job parameters, read from the environment variables Civis sets.

Civis exposes each container script parameter as an environment variable
(docs: "Container Script Parameters"). Parameter names used by this tool:

| Parameter | Type | Environment variables read |
|---|---|---|
| RESULTS_DB | Database | RESULTS_DB_NAME (or RESULTS_DB_ID), RESULTS_DB_CREDENTIAL_ID |
| RESULTS_SCHEMA | String | RESULTS_SCHEMA |
| GITHUB | Custom credential (optional) | GITHUB_PASSWORD (the token) |
| LOOKBACK_DAYS | Integer, default 400 | LOOKBACK_DAYS |
| MAX_CONCURRENCY | Integer, default 4 | MAX_CONCURRENCY |
| REQUEST_BUDGET | Integer, default 500 per 5 minutes | REQUEST_BUDGET |
| RETENTION_DAYS | Integer, blank keeps every run | RETENTION_DAYS |

Civis also sets CIVIS_JOB_ID and CIVIS_RUN_ID for the running job. "DATABASE"
is reserved by Civis, so the Database parameter is called RESULTS_DB.

RUN_ID is no longer read (docs/PLAN.md 17.13): the scanner decides by itself
whether to resume the newest stopped scan (common/resume.py). A job set up on
an earlier release may still have it; ``run_id_left_over`` tells the live run
to log RUN_ID_IGNORED. ``ScanConfig.run_id`` is set only by a dry run's
``--run-id`` (local resume tests) and by the runner when it resumes a scan.

Error messages name the parameter, never its value.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from common import sql
from common.release import DEFAULT_LOOKBACK_DAYS, LOOKBACK_DAYS_RANGE
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_REQUEST_BUDGET = 500


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ScanConfig:
    database: str | int
    schema: str
    credential_id: int | None = None
    github_token: str | None = field(default=None, repr=False)
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    request_budget: int = DEFAULT_REQUEST_BUDGET
    retention_days: int | None = None
    run_id: str | None = None
    civis_job_id: int | None = None
    civis_run_id: int | None = None

    def parameters_json(self) -> str:
        """Parameters for the scan_runs table. Never includes the token or credentials."""
        return json.dumps(
            {
                "lookback_days": self.lookback_days,
                "max_concurrency": self.max_concurrency,
                "request_budget_per_5_minutes": self.request_budget,
                "retention_days": self.retention_days,
                "github_token_attached": bool(self.github_token),
                "resumed_run_id": self.run_id,
            },
            sort_keys=True,
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ScanConfig":
        env = os.environ if env is None else env

        database: str | int | None = (env.get("RESULTS_DB_NAME") or "").strip() or None
        if database is None:
            database = _int(env, "RESULTS_DB_ID", None, 1, None)
        if database is None:
            raise ConfigError("Set the RESULTS_DB parameter to the database for the results.")

        schema = (env.get("RESULTS_SCHEMA") or "").strip()
        try:
            sql.identifier(schema)
        except sql.UnsafeValueError:
            raise ConfigError(
                "RESULTS_SCHEMA must be a plain schema name: letters, numbers, and underscores, "
                "starting with a letter or underscore."
            ) from None

        retention = _int(env, "RETENTION_DAYS", None, 0, 36500)
        return cls(
            database=database,
            schema=schema,
            credential_id=_int(env, "RESULTS_DB_CREDENTIAL_ID", None, 1, None),
            github_token=(env.get("GITHUB_PASSWORD") or "").strip() or None,
            lookback_days=_int(env, "LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS, *LOOKBACK_DAYS_RANGE),
            max_concurrency=_int(env, "MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY, 1, 16),
            request_budget=_int(env, "REQUEST_BUDGET", DEFAULT_REQUEST_BUDGET, 10, 1000),
            retention_days=retention or None,
            civis_job_id=_int(env, "CIVIS_JOB_ID", None, 1, None),
            civis_run_id=_int(env, "CIVIS_RUN_ID", None, 1, None),
        )


def run_id_left_over(env: Mapping[str, str] | None = None) -> bool:
    """True when the environment still has a non-empty RUN_ID, from a job set up on an earlier release."""
    env = os.environ if env is None else env
    return bool((env.get("RUN_ID") or "").strip())


def _int(env: Mapping[str, str], name: str, default: int | None, low: int, high: int | None) -> int | None:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise ConfigError(f"{name} must be a whole number.")
    value = int(raw)
    if value < low or (high is not None and value > high):
        limit = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise ConfigError(f"{name} must be {limit}.")
    return value
