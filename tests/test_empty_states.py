"""The Overview's empty state when no scan has completed (docs/PLAN.md 17.7, Part D).

"No scan yet" only when there is no scan at all. When at least one scan exists but none completed, the title is
"No completed scan yet", with a body for how the newest one ended. Made-up runs only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("flask")

from app import create_app  # noqa: E402
from app.backends.base import JobRun  # noqa: E402
from app.pages import empty_state  # noqa: E402
from app.services import Services  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.views import ScanRun, Situation  # noqa: E402

SECRET = "empty-states-test-secret-key-long-enough-0001"  # pragma: allowlist secret  gitleaks:allow
WHEN = "2026-11-10T12:00:00Z"
CANCELLED = "The last scan was cancelled before it finished. Run the scan again."
FAILED = "The last scan failed before it finished."
OTHER = "No scan has finished with results to show yet."


def _scan(status: str, civis_run_id: int = 702) -> ScanRun:
    return ScanRun(run_id="run-20261110T120000Z-abc123", status=status,
                   started_at=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc), finished_at=None,
                   civis_run_id=civis_run_id, scanning_user=None, parameters={}, first_civis_run_id=civis_run_id)


def _run(state: str, run_id: int = 702) -> JobRun:
    return JobRun(run_id, state, WHEN, WHEN, None if state in ("queued", "running") else WHEN)


def _empty(results_state: str = "ok", runs=(), scans=()) -> dict | None:
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": Services(Settings(), None, None), "TESTING": True})
    situation = Situation(results_state=results_state, job_configured=True, runs=list(runs), scans=list(scans))
    with app.test_request_context("/"):
        return empty_state(situation)


@pytest.mark.parametrize("results_state", ["ok", "no_tables"])
def test_no_scan_at_all_is_no_scan_yet(results_state):
    assert _empty(results_state)["title"] == "No scan yet"


@pytest.mark.parametrize("runs, scans, body", [
    ([_run("failed")], [_scan("running")], FAILED),
    ([_run("failed")], [_scan("failed")], FAILED),
    ([_run("failed")], [], FAILED),
    ([_run("cancelled")], [_scan("running")], CANCELLED),
    ([_run("cancelled")], [], CANCELLED),
    # Civis lists the run as finished in another state, and the scan has no final row (or none was read).
    ([_run("succeeded")], [_scan("running")], OTHER),
    ([_run("idle")], [], OTHER),
    # The scan has a status row but Civis lists no run for it (for example, the run list was cut).
    ([], [_scan("running", civis_run_id=None)], OTHER),
], ids=["failed_running_row", "failed_row", "failed_no_row", "cancelled_running_row", "cancelled_no_row",
        "civis_succeeded_no_final_row", "other_state", "row_without_civis_run"])
def test_a_scan_that_did_not_complete(runs, scans, body):
    empty = _empty("ok", runs, scans)
    assert empty["title"] == "No completed scan yet" and empty["body"].startswith(body)


def test_a_cancelled_first_scan_before_the_tables_keeps_the_setup_button():
    empty = _empty("no_tables", [_run("cancelled")])
    assert empty["title"] == "No completed scan yet" and empty["body"] == CANCELLED and empty["storage_button"]


def test_the_tables_failed_text_is_unchanged():
    empty = _empty("ok", [_run("failed")], [_scan("tables_failed")])
    assert empty["title"] == "The last scan saved a CSV report only"


def test_a_running_first_scan_is_unchanged():
    assert _empty("ok", [_run("running")], [_scan("running")])["title"] == "The first scan is running"


def test_a_completed_scan_shows_results_not_an_empty_state():
    assert _empty("ok", [_run("cancelled", 703), _run("succeeded")], [_scan("succeeded")]) is None
