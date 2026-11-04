"""Web app settings, read from the Service's environment variables.

| Variable | Meaning |
|---|---|
| SCANNER_JOB_ID | ID of the scanner container script the admin created. Without it, "Run scan" is off. |
| CIVIS_SERVICE_HEALTH_PATH | Set to /healthz. Read by Civis, not by the app. |
| CIVIS_PLATFORM_URL | Optional. Base URL for links to Civis objects. |
| CIVIS_FRAME_ANCESTORS | Optional. Origins allowed to frame the app. |
| CIVIS_FRAME_ANCESTORS_REPORT_ONLY | Optional, true or false (default false). Setup aid: report the frame rule instead of enforcing it. |

The results database, credential, and schema are not Service settings. The
app reads them from the scanner job's RESULTS_DB and RESULTS_SCHEMA parameters
(app/job_settings.py), so they are set in one place.

None of these are secrets; Service environment variables are visible to anyone
who can read the Service.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    scanner_job_id: int | None = None
    problem: str | None = None  # A setting is present but not valid.

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        job_id = _positive_int(env.get("SCANNER_JOB_ID"))
        problem = "SCANNER_JOB_ID must be a number." if env.get("SCANNER_JOB_ID") and job_id is None else None
        return cls(scanner_job_id=job_id, problem=problem)


def _positive_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None
