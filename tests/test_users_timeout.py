"""A request timeout on the "all" users list falls back to the active list (docs/PLAN.md 17.12, decided by Rob on
November 17, 2026), like HTTP 403 or 404. Synthetic client and dry runs only; no network calls.
"""

from __future__ import annotations

import copy
import io
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from scanner import scan_log
from scanner.__main__ import dry_run
from scanner.inventory.users import USERS_ACTIVE_NOTE, USERS_NONE_NOTE, load_users
from scanner.ratelimit import is_request_timeout
from scanner.runner import ScanFailed
from scanner.synthetic import FIXTURES, FakeClient
from scanner.synthetic.client import TIMEOUT_ERRORS, made_up_timeout

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
TIMED_OUT = " (the request timed out)"


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def synthetic(errors: dict) -> dict:
    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    data["users_list_errors"] = copy.deepcopy(errors)
    return data


def users_rows(folder) -> list[tuple]:
    conn = sqlite3.connect(folder / "results.sqlite")
    try:
        return conn.execute("SELECT reason FROM coverage WHERE row_kind = 'users'").fetchall()
    finally:
        conn.close()


# --- The timeout test ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", TIMEOUT_ERRORS)
def test_the_made_up_timeouts_are_request_timeouts(kind):
    assert is_request_timeout(made_up_timeout(kind))


def test_other_errors_are_not_request_timeouts():
    import requests

    assert not is_request_timeout(requests.exceptions.ConnectionError("made-up refused"))
    assert not is_request_timeout(ConnectionError("made-up"))
    assert not is_request_timeout(TimeoutError("made-up"))  # Python's own, not requests'.


# --- load_users -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", TIMEOUT_ERRORS)
def test_a_timeout_on_all_falls_back_to_the_active_list(kind):
    client = FakeClient(synthetic({"all": kind}))
    lookup = load_users(client)
    assert (lookup.source, lookup.status, lookup.reason) == ("active", None, "timeout")
    assert sorted(lookup.users) == [1, 2] and all(u.active is True for u in lookup.users.values())
    assert [k["account_status"] for _, k in client.called("users.list")] == ["all", "active"]
    assert lookup.note() == USERS_ACTIVE_NOTE.format(cause=TIMED_OUT)
    assert lookup.note() == (
        "Owner active status is only partly known. The scanning user could not list deactivated users (the request "
        "timed out), so owners in the list of active users are marked active and all other owners are unknown.")


@pytest.mark.parametrize("kind", TIMEOUT_ERRORS)
def test_a_timeout_on_both_lists_gives_none_with_reason_timeout(kind):
    lookup = load_users(FakeClient(synthetic({"all": kind, "active": kind})))
    assert (lookup.source, lookup.status, lookup.reason, lookup.users) == ("none", None, "timeout", {})
    assert lookup.note() == USERS_NONE_NOTE.format(cause=TIMED_OUT)


def test_a_timeout_on_all_then_a_404_on_active_gives_none_with_status_404():
    lookup = load_users(FakeClient(synthetic({"all": "read_timeout", "active": 404})))
    assert (lookup.source, lookup.status, lookup.reason) == ("none", 404, None)
    assert lookup.note() == USERS_NONE_NOTE.format(cause=" (HTTP 404)")


def test_a_404_on_all_then_a_timeout_on_active_gives_none_with_reason_timeout():
    lookup = load_users(FakeClient(synthetic({"all": 404, "active": "connect_timeout"})))
    assert (lookup.source, lookup.status, lookup.reason) == ("none", None, "timeout")


def test_a_403_or_404_keeps_its_status():
    lookup = load_users(FakeClient(synthetic({"all": 403})))
    assert (lookup.source, lookup.status, lookup.reason) == ("active", 403, None)
    assert lookup.note() == USERS_ACTIVE_NOTE.format(cause=" (HTTP 403)")


# --- The log line, the coverage note, and the scan ---------------------------------------------------------


def test_a_dry_run_with_a_timed_out_all_list_continues(tmp_path, log_stream):
    result = dry_run(tmp_path, now=NOW, data=synthetic({"all": "read_timeout", "inactive": 404}))
    assert result.status == "succeeded"
    logged = log_stream.getvalue().splitlines()
    assert "USERS source=active reason=timeout" in logged
    assert not [line for line in logged if line.startswith("FATAL")]
    assert users_rows(tmp_path) == [(USERS_ACTIVE_NOTE.format(cause=TIMED_OUT),)]


def test_a_dry_run_with_both_lists_timed_out(tmp_path, log_stream):
    assert dry_run(tmp_path, now=NOW, data=synthetic({"all": "body_read_timeout", "active": "read_timeout"})).status \
        == "succeeded"
    assert "USERS source=none reason=timeout" in log_stream.getvalue().splitlines()
    assert users_rows(tmp_path) == [(USERS_NONE_NOTE.format(cause=TIMED_OUT),)]


def test_a_dry_run_with_a_timeout_then_a_404(tmp_path, log_stream):
    dry_run(tmp_path, now=NOW, data=synthetic({"all": "read_timeout", "active": 404}))
    assert "USERS source=none status=404" in log_stream.getvalue().splitlines()


def test_a_500_on_all_still_stops_the_scan(tmp_path, log_stream):
    with pytest.raises(ScanFailed):
        dry_run(tmp_path, now=NOW, data=synthetic({"all": 500}))
    assert log_stream.getvalue().splitlines()[-1] == "FATAL error=FakeAPIError status=500 step=inventory_users"


def test_no_exception_text_reaches_any_output(tmp_path, log_stream):
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary

    result = dry_run(tmp_path, now=NOW, data=synthetic({"all": "body_read_timeout", "active": "read_timeout"}))
    logged = log_stream.getvalue()
    text = "\n".join(summary._scan_lines(summary.ScanData(
        result.run_id, "succeeded", None, None, log=summary.LogRead(logged.splitlines()))))
    assert "  users_list: none reason=timeout" in text
    outputs = {
        "log": logged,
        "tables": (tmp_path / "results.sqlite").read_bytes().decode("utf-8", errors="replace"),
        "csv": "".join(path.read_text(encoding="utf-8-sig") for path in tmp_path.glob("*.csv")),
        "summary": text,
    }
    for where, output in outputs.items():
        assert "made-up" not in output and "ReadTimeoutError" not in output, where


# --- scan_log and the support summary ----------------------------------------------------------------------


@pytest.mark.parametrize("source, status, reason, line", [
    ("active", None, "timeout", "USERS source=active reason=timeout"),
    ("none", None, "timeout", "USERS source=none reason=timeout"),
    ("active", 404, None, "USERS source=active status=404"),
])
def test_users_log_line_with_a_reason(log_stream, source, status, reason, line):
    scan_log.users(source, status, reason)
    assert log_stream.getvalue().splitlines() == [line]


@pytest.mark.parametrize("status, reason", [(None, "slow"), (404, "timeout"), (None, "")])
def test_users_log_line_rejects_other_reasons(log_stream, status, reason):
    with pytest.raises(ValueError):
        scan_log.users("active", status, reason)
    assert log_stream.getvalue() == ""


@pytest.mark.parametrize("line, expected", [
    ("USERS source=active reason=timeout", "active reason=timeout"),
    ("USERS source=none reason=timeout", "none reason=timeout"),
    ("USERS source=none status=404", "none status=404"),
    ("USERS source=active status=403", "active status=403"),
    ("USERS source=active reason=slow", "other"),
    ("USERS source=active status=404 reason=timeout", "other"),
])
def test_the_summary_users_line(line, expected):
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary

    counts = summary.count_log([line])
    assert (counts.users, counts.other_lines) == (expected, 0)


def test_the_summary_reasons_match_the_scanner():
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary

    assert summary.USER_REASONS == scan_log.USER_REASONS
