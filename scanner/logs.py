"""Read the most recent run log of a job, or the current deployment log of a Service.

Rules (build brief section 4 and docs/PLAN.md):

- Jobs (Python, container, and custom scripts): only the most recent run, and
  only if it finished inside the lookback window (default 200 days).
- Services: only the current deployment's logs.
- Notebooks and workflows: no log scan. Notebook outputs are scanned from the
  notebook file instead.

Log text is held in memory, passed to the engine, and dropped. Long job logs
are read in pages with ``last_id``; reading stops at MAX_LOG_LINES.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from common.models import CivisObject, ObjectType
from scanner.inventory.base import describe_error, field_of

LOG_PAGE_SIZE = 10_000
MAX_LOG_LINES = 200_000
SERVICE_LOG_LIMIT = 10_000
JOB_TYPES = (ObjectType.PYTHON_SCRIPT, ObjectType.CONTAINER_SCRIPT, ObjectType.CUSTOM_SCRIPT)


@dataclass
class LogRead:
    lines: list[str] = field(default_factory=list)  # In memory only.
    label: str = "run log"
    note: str | None = None  # Set when the log could not be read in full.
    read: bool = False


def parse_time(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def in_lookback(obj: CivisObject, lookback_days: int, now: datetime) -> bool:
    when = parse_time(obj.last_run_at)
    return when is not None and when >= now - timedelta(days=lookback_days)


def read_log(client: Any, obj: CivisObject, deployment_id: int | None, lookback_days: int, now: datetime) -> LogRead | None:
    """Returns the log to scan, or None when this object has no log to read."""
    if obj.object_type in JOB_TYPES:
        if obj.last_run_id is None or not in_lookback(obj, lookback_days, now):
            return None
        return _read_job_log(client, obj.object_id, obj.last_run_id)
    if obj.object_type is ObjectType.SERVICE and deployment_id is not None:
        return _read_service_log(client, obj.object_id, deployment_id)
    return None


def _read_job_log(client: Any, job_id: int, run_id: int) -> LogRead:
    result = LogRead(label=f"run log (run {run_id})")
    last_id = None
    while True:
        kwargs = {"limit": LOG_PAGE_SIZE}
        if last_id is not None:
            kwargs["last_id"] = last_id
        try:
            page = list(client.jobs.list_runs_logs(job_id, run_id, **kwargs))
        except Exception as exc:
            result.note = f"The run log could not be read. {describe_error(exc)}"
            return result
        result.read = True
        for entry in page:
            message = field_of(entry, "message")
            if isinstance(message, str):
                result.lines.append(message)
        ids = [field_of(e, "id") for e in page if isinstance(field_of(e, "id"), int)]
        if len(page) < LOG_PAGE_SIZE or not ids:
            return result
        if len(result.lines) >= MAX_LOG_LINES:
            result.note = f"The run log is longer than {MAX_LOG_LINES:,} lines; only the first {MAX_LOG_LINES:,} were read."
            return result
        last_id = max(ids)


def _read_service_log(client: Any, service_id: int, deployment_id: int) -> LogRead:
    result = LogRead(label=f"deployment log (deployment {deployment_id})")
    try:
        page = list(client.services.list_deployments_logs(service_id, deployment_id, limit=SERVICE_LOG_LIMIT))
    except Exception as exc:
        result.note = f"The deployment log could not be read. {describe_error(exc)}"
        return result
    result.read = True
    result.lines = [m for m in (field_of(e, "message") for e in page) if isinstance(m, str)]
    if len(page) >= SERVICE_LOG_LIMIT:
        result.note = f"The deployment log has more than {SERVICE_LOG_LIMIT:,} lines; only {SERVICE_LOG_LIMIT:,} were read."
    return result
