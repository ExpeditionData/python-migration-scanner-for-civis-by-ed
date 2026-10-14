"""Web app settings, read from the Service's environment variables.

| Variable | Meaning |
|---|---|
| SCANNER_JOB_ID | ID of the scanner container script the admin created. Without it, "Run scan" is off. |
| RESULTS_DATABASE | Name of the database that holds the results schema. |
| RESULTS_SCHEMA | The results schema the scanner writes to. |
| RESULTS_CREDENTIAL_ID | Optional. Database credential ID; the Service owner's default credential is used if blank. |
| CIVIS_PLATFORM_URL | Optional. Base URL for links to Civis objects. |
| CIVIS_FRAME_ANCESTORS | Optional. Origins allowed to frame the app. |

None of these are secrets; Service environment variables are visible to anyone
who can read the Service.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from common import sql


@dataclass(frozen=True)
class Settings:
    scanner_job_id: int | None = None
    results_database: str | None = None
    results_schema: str | None = None
    results_credential_id: int | None = None
    problem: str | None = None  # A setting is present but not valid.

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        problems = []
        job_id = _positive_int(env.get("SCANNER_JOB_ID"))
        if env.get("SCANNER_JOB_ID") and job_id is None:
            problems.append("SCANNER_JOB_ID must be a number.")
        credential = _positive_int(env.get("RESULTS_CREDENTIAL_ID"))
        if env.get("RESULTS_CREDENTIAL_ID") and credential is None:
            problems.append("RESULTS_CREDENTIAL_ID must be a number.")
        schema = (env.get("RESULTS_SCHEMA") or "").strip() or None
        if schema is not None:
            try:
                sql.identifier(schema)
            except sql.UnsafeValueError:
                problems.append("RESULTS_SCHEMA must be a plain schema name.")
                schema = None
        return cls(
            scanner_job_id=job_id,
            results_database=(env.get("RESULTS_DATABASE") or "").strip() or None,
            results_schema=schema,
            results_credential_id=credential,
            problem=" ".join(problems) or None,
        )

    @property
    def results_configured(self) -> bool:
        return bool(self.results_database and self.results_schema)


def _positive_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None
