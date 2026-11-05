"""The scanner's retries policy for civis.APIClient (docs/PLAN.md 17.5, Part C).

The policy runs through civis-python's own retry_request with a made-up session,
so civis keeps its rules for which status codes and verbs are retried. No
network calls and no Civis instance.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

tenacity = pytest.importorskip("tenacity")
civis_retries = pytest.importorskip("civis._retries")

from scanner import __main__ as entry
from scanner.ratelimit import RETRY_MAX_SECONDS, RETRY_MAX_TRIES, RETRY_MAX_WAIT_SECONDS, civis_retrying


class FakeResponse:
    def __init__(self, status_code: int, retry_after: str | None = None):
        self.status_code = status_code
        self.headers = {"Retry-After": retry_after} if retry_after else {}


class FakeSession:
    """Answers every request with the next status code, the last one repeating."""

    def __init__(self, *statuses: int, retry_after: str | None = None):
        self.statuses = list(statuses)
        self.retry_after = retry_after
        self.sent = 0

    def send(self, request):
        status = self.statuses[min(self.sent, len(self.statuses) - 1)]
        self.sent += 1
        return FakeResponse(status, self.retry_after)


def no_sleep_policy(waits: list[float] | None = None):
    """The scanner's policy with tenacity's sleep replaced, so tests do not wait."""
    return civis_retrying().copy(sleep=(waits.append if waits is not None else lambda seconds: None))


def test_the_policy_values():
    policy = civis_retrying()
    assert isinstance(policy, tenacity.Retrying) and policy.reraise is True
    assert (RETRY_MAX_TRIES, RETRY_MAX_SECONDS, RETRY_MAX_WAIT_SECONDS) == (4, 120, 30)
    stop = policy.stop
    assert not stop(SimpleNamespace(attempt_number=3, seconds_since_start=60.0))
    assert stop(SimpleNamespace(attempt_number=4, seconds_since_start=5.0))
    assert stop(SimpleNamespace(attempt_number=1, seconds_since_start=121.0))
    for attempt in range(1, 12):
        assert 0 <= policy.wait(SimpleNamespace(attempt_number=attempt)) <= RETRY_MAX_WAIT_SECONDS


def test_a_get_that_keeps_failing_with_503_stops_after_four_tries_and_returns_the_last_response():
    session, waits = FakeSession(503), []
    response = civis_retries.retry_request("GET", object(), session, no_sleep_policy(waits))
    assert session.sent == RETRY_MAX_TRIES and response.status_code == 503  # civis then raises CivisAPIError.
    assert len(waits) == RETRY_MAX_TRIES - 1 and all(0 <= w <= RETRY_MAX_WAIT_SECONDS for w in waits)


def test_a_retry_that_succeeds_returns_the_success():
    session = FakeSession(502, 504, 200)
    assert civis_retries.retry_request("GET", object(), session, no_sleep_policy()).status_code == 200
    assert session.sent == 3


@pytest.mark.parametrize("method, status", [("GET", 404), ("GET", 403), ("GET", 500), ("POST", 502), ("PATCH", 503)])
def test_civis_keeps_its_own_rules_for_what_is_retried(method, status):
    session = FakeSession(status)
    assert civis_retries.retry_request(method, object(), session, no_sleep_policy()).status_code == status
    assert session.sent == 1


def test_civis_still_waits_at_least_the_retry_after_header():
    session, waits = FakeSession(429, 200, retry_after="45"), []
    civis_retries.retry_request("GET", object(), session, no_sleep_policy(waits))
    assert waits == [45.0]


def test_live_run_gives_the_client_the_policy(monkeypatch):
    """live_run builds civis.APIClient with the scanner's policy. A made-up civis module; nothing is contacted."""
    made = {}

    class FakeAPIClient:
        def __init__(self, **kwargs):
            made.update(kwargs)

    fake_civis = types.ModuleType("civis")
    fake_civis.APIClient = FakeAPIClient
    fake_civis.io = types.ModuleType("civis.io")
    monkeypatch.setitem(sys.modules, "civis", fake_civis)
    for name, value in {"CIVIS_JOB_ID": "11", "CIVIS_RUN_ID": "22", "RESULTS_DB_NAME": "example_db",
                        "RESULTS_SCHEMA": "example_schema"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "run_scan", lambda config, **kwargs: "ran")
    assert entry.live_run() == "ran"
    policy = made["retries"]
    assert isinstance(policy, tenacity.Retrying) and policy.reraise is True
    assert policy.stop(SimpleNamespace(attempt_number=RETRY_MAX_TRIES, seconds_since_start=1.0))
