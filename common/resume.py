"""Whether the next scan resumes the newest stopped scan (docs/PLAN.md 17.13).

Civis starts the scanner job with its saved parameters, and its start-run call
takes the job ID only, so the app cannot pass a run ID to one run, and setting
one on the job would edit a Civis object. So the scanner decides by itself,
when it starts, from its own scan_runs rows and one read of the stopped
scan's newest Civis run. The app runs the same decision with what it already
has (the scan_runs rows, the job's runs, and the job's settings) to say on the
Overview that Run scan will resume.

``decide`` is pure: no I/O, no civis import, no clock of its own. It runs on
Python 3.12 (the Service image) and 3.14 (the scanner image).

The rule, checked in this order (the first that applies starts a new scan):

| Reason | A new scan starts when |
|---|---|
| no_scan | there is no scan at all |
| last_scan_finished | the newest scan (by first start) has a "succeeded" or "tables_failed" row, or its newest row is not "running" or "failed" |
| run_state_unknown | no row of that scan has a Civis run ID (a dry run) |
| run_active | one of its Civis runs is still queued or running |
| run_state_unknown | its newest Civis run's end time is not known |
| too_old | its newest Civis run ended more than RESUME_WINDOW_HOURS ago |
| resumed_twice | it was already resumed MAX_RESUMES times (distinct Civis run IDs minus one) |
| version_changed | a row's tool_version is not this build's version |
| settings_changed | a row's lookback_days or github_token_attached differs from the current value |
| results_saved | it already wrote its summary rows to coverage |

Otherwise it is resumed. Only the newest scan is ever considered: an older
stopped scan is never resumed. A caller that cannot check one input passes
None for it, and that check is skipped (the app cannot see coverage rows
without a new read, so it skips results_saved and leaves it to the scanner).
The scanner adds two reasons of its own, when it cannot make the decision:
tables_unavailable and read_failed.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

RESUME_WINDOW_HOURS = 24
MAX_RESUMES = 2
FINISHED_STATUSES = ("succeeded", "tables_failed")
STOPPED_STATUSES = ("running", "failed")
ACTIVE_RUN_STATES = ("queued", "running")
# The scan parameters (scan_runs.parameters) that change results. max_concurrency, request budget, and
# retention do not, so they do not block a resume.
RESULT_PARAMETERS = ("lookback_days", "github_token_attached")
DECISION_REASONS = (
    "no_scan", "last_scan_finished", "run_state_unknown", "run_active", "too_old", "resumed_twice",
    "version_changed", "settings_changed", "results_saved",
)
# The scanner's own reasons, when it cannot make the decision at all.
SCANNER_REASONS = ("tables_unavailable", "read_failed")
REASONS = DECISION_REASONS + SCANNER_REASONS
DECISIONS = ("resume", "new")


@dataclass(frozen=True)
class CivisRun:
    """What the caller knows about one Civis run of the scanner job: its state and its end time."""

    state: str | None
    finished_at: datetime | None


@dataclass(frozen=True)
class StoppedScan:
    """The newest scan, from its scan_runs rows."""

    run_id: str
    status: str  # The newest row's status.
    finished: bool  # It has a "succeeded" or "tables_failed" row.
    civis_run_ids: tuple[int, ...]  # Distinct, in the order the rows were recorded.
    civis_job_id: int | None  # The job of its newest Civis run.
    rows: tuple[Mapping[str, Any], ...]

    @property
    def newest_civis_run_id(self) -> int | None:
        return self.civis_run_ids[-1] if self.civis_run_ids else None

    @property
    def resumes(self) -> int:
        """How many times it was resumed: its distinct Civis run IDs minus one."""
        return max(len(self.civis_run_ids) - 1, 0)


@dataclass(frozen=True)
class Decision:
    resume: bool
    run_id: str | None = None  # The scan to resume.
    reason: str | None = None  # Why a new scan starts (REASONS).
    resumes: int = 0  # The scan's resumes so far, before this one.
    window_ends: datetime | None = None  # When its newest Civis run's 24 hours end, when known (UTC).

    @classmethod
    def new(cls, reason: str, **known: Any) -> "Decision":
        if reason not in REASONS:
            raise ValueError("unknown reason")
        return cls(False, reason=reason, **known)


def moment(value: Any) -> datetime | None:
    """A timestamp as an aware UTC datetime: a datetime, or ISO text such as "2026-11-13 12:00:00" or
    "2026-11-13T12:00:00Z" (stored times are UTC without a zone). None when it cannot be read."""
    if isinstance(value, datetime):
        found = value
    elif isinstance(value, str) and value.strip():
        try:
            found = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return found.replace(tzinfo=timezone.utc) if found.tzinfo is None else found.astimezone(timezone.utc)


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _order(row: Mapping[str, Any]) -> tuple:
    """Rows oldest first: by recorded_at, a "running" row before a final row on a tie."""
    return (moment(row.get("recorded_at")) or _EARLIEST, row.get("status") != "running")


def newest_scan(rows: Iterable[Mapping[str, Any]]) -> StoppedScan | None:
    """The newest scan by its first start, or None when there is no scan."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        run_id = row.get("run_id")
        if isinstance(run_id, str) and run_id:
            grouped.setdefault(run_id, []).append(row)
    if not grouped:
        return None

    def first_start(item: tuple[str, list]) -> tuple:
        starts = [moment(r.get("started_at")) for r in item[1]]
        known = [s for s in starts if s is not None]
        return (min(known) if known else _EARLIEST, max(_order(r) for r in item[1]), item[0])

    run_id, items = max(grouped.items(), key=first_start)
    ordered = sorted(items, key=_order)
    civis_ids: list[int] = []
    job_id = None
    for row in ordered:
        civis_run = _whole(row.get("civis_run_id"))
        if civis_run is not None:
            if civis_run not in civis_ids:
                civis_ids.append(civis_run)
            job_id = _whole(row.get("civis_job_id"))
    newest = ordered[-1]
    return StoppedScan(
        run_id=run_id, status=str(newest.get("status") or ""),
        finished=any(r.get("status") in FINISHED_STATUSES for r in items),
        civis_run_ids=tuple(civis_ids), civis_job_id=job_id, rows=tuple(ordered),
    )


def _parameters(row: Mapping[str, Any]) -> dict | None:
    value = row.get("parameters")
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _settings_match(rows: Iterable[Mapping[str, Any]], lookback_days: int | None,
                    github_attached: bool | None) -> bool:
    for row in rows:
        parameters = _parameters(row)
        if parameters is None:
            return False
        if lookback_days is not None and _whole(parameters.get("lookback_days")) != lookback_days:
            return False
        if github_attached is not None:
            attached = parameters.get("github_token_attached")
            if not isinstance(attached, bool) or attached != github_attached:
                return False
    return True


def decide(
    rows: Iterable[Mapping[str, Any]],
    now: datetime,
    *,
    civis_runs: Mapping[int, CivisRun],
    version: str | None = None,
    lookback_days: int | None = None,
    github_attached: bool | None = None,
    results_saved: bool | None = None,
) -> Decision:
    """Resume the newest scan, or start a new one with a reason from DECISION_REASONS (module docstring).

    ``civis_runs`` maps Civis run IDs to what the caller knows about them; the scanner reads only the
    stopped scan's newest run, and the app passes the job's runs it already listed. ``version``,
    ``lookback_days``, ``github_attached`` (compared with the rows' github_token_attached), and
    ``results_saved`` are skipped when None."""
    scan = newest_scan(rows)
    if scan is None:
        return Decision.new("no_scan")
    if scan.finished or scan.status not in STOPPED_STATUSES:
        return Decision.new("last_scan_finished")
    known = {"resumes": scan.resumes}
    if not scan.civis_run_ids:
        return Decision.new("run_state_unknown", **known)
    if any((civis_runs.get(r) or CivisRun(None, None)).state in ACTIVE_RUN_STATES for r in scan.civis_run_ids):
        return Decision.new("run_active", **known)
    newest = civis_runs.get(scan.newest_civis_run_id)
    ended = moment(newest.finished_at) if newest is not None else None
    if ended is None:
        return Decision.new("run_state_unknown", **known)
    known["window_ends"] = ended + timedelta(hours=RESUME_WINDOW_HOURS)
    if moment(now) > known["window_ends"]:
        return Decision.new("too_old", **known)
    if scan.resumes >= MAX_RESUMES:
        return Decision.new("resumed_twice", **known)
    if version is not None and any(str(r.get("tool_version") or "") != version for r in scan.rows):
        return Decision.new("version_changed", **known)
    if not _settings_match(scan.rows, lookback_days, github_attached):
        return Decision.new("settings_changed", **known)
    if results_saved:
        return Decision.new("results_saved", **known)
    return Decision(True, run_id=scan.run_id, **known)
