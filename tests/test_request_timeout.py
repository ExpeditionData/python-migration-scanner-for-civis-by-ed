"""A timeout on every Civis API request (docs/PLAN.md 17.12, Part C).

These tests run civis-python's own client (2.10 or 2.11; civis/base.py, civis/_retries.py, and
civis/resources/_resources.py are the same in both) with ``requests.adapters.HTTPAdapter.send`` stubbed: the stub
serves the API spec bundled with civis for ``/endpoints`` and made-up JSON for every other path, so no request
leaves this machine. The API endpoint is a made-up host. They skip on civis 2.7.1 (the Service image), like
tests/test_retries.py.
"""

from __future__ import annotations

import io
import json
import threading
import uuid
from pathlib import Path

import pytest

civis = pytest.importorskip("civis")
requests = pytest.importorskip("requests")
pytest.importorskip("civis._retries")  # civis 2.10 and 2.11 (the scanner image); civis 2.7.1 has no such module.

import civis.base  # noqa: E402
import civis.resources._resources as civis_resources  # noqa: E402

from scanner import ratelimit, scan_log  # noqa: E402
from scanner.ratelimit import (  # noqa: E402
    REQUEST_CONNECT_TIMEOUT_SECONDS,
    REQUEST_READ_TIMEOUT_SECONDS,
    civis_retrying,
    install_request_timeout,
    request_timeouts,
)

TIMEOUT = (REQUEST_CONNECT_TIMEOUT_SECONDS, REQUEST_READ_TIMEOUT_SECONDS)
SPEC = (Path(civis.__file__).parent / "resources" / "civis_api_spec.json").read_bytes()
ENDPOINT = "https://civis-api.example.test/"
MARK = "zq-timeout-marker-3307"


class StubAdapter:
    """Answers HTTPAdapter.send: the bundled spec for /endpoints, a made-up user for /users/me. A path in
    ``errors`` raises that error instead. Records (path, timeout) for every request."""

    def __init__(self):
        self.sent: list[tuple[str, object]] = []
        self.errors: dict[str, Exception] = {}
        self.raise_when = None  # Optional: request -> an error to raise, or None.
        self._lock = threading.Lock()

    def send(self, adapter, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        path = request.url[len(ENDPOINT) - 1:].split("?", 1)[0]
        with self._lock:
            self.sent.append((path, timeout))
        if path in self.errors:
            raise self.errors[path]
        error = self.raise_when(request) if self.raise_when else None
        if error is not None:
            raise error
        response = requests.Response()
        response.status_code = 200
        if path == "/endpoints":
            response._content = SPEC
        elif path == "/users":  # One page with one user; civis asks for pages until one comes back empty.
            rows = [{"id": 2, "name": "Example Analyst", "active": True}] if "page_num=1" in request.url else []
            response._content = json.dumps(rows).encode()
        else:
            response._content = json.dumps({"id": 7, "username": "example_robot"}).encode()
        response.headers["Content-Type"] = "application/json"
        response.encoding = "utf-8"
        response.url = request.url
        response.request = request
        return response


@pytest.fixture
def stub(monkeypatch):
    """The stubbed adapter, a made-up endpoint, and civis's open_session restored after the test."""
    adapter = StubAdapter()
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", lambda self, request, **kw: adapter.send(self, request, **kw))
    monkeypatch.setenv("CIVIS_API_ENDPOINT", ENDPOINT)
    monkeypatch.delenv("CIVIS_JOB_ID", raising=False)
    monkeypatch.setattr(civis.base, "open_session", civis.base.open_session)
    monkeypatch.setattr(civis_resources, "open_session", civis_resources.open_session)
    return adapter


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def new_client():
    # A new made-up key for each client, so civis downloads the spec again (it caches the spec per key).
    return civis.APIClient(api_key=f"fake-key-{uuid.uuid4().hex}", retries=civis_retrying())  # pragma: allowlist secret


def test_the_civis_source_matches_what_the_wrapper_expects():
    """civis 2.10 opens a session with open_session for each request and the spec download, and sends with
    sess.send(prepared) and no timeout."""
    import inspect

    from civis import _retries

    assert "open_session(" in inspect.getsource(civis.base.Endpoint._make_request)
    assert "open_session(" in inspect.getsource(civis_resources.get_api_spec)
    assert "sess.send(req)" in inspect.getsource(_retries.retry_request)


def test_every_request_carries_the_timeout(stub, log_stream):
    assert install_request_timeout() is True
    client = new_client()
    assert client.users.get_me()["id"] == 7
    assert [path for path, _ in stub.sent] == ["/endpoints", "/users/me"]
    assert all(timeout == TIMEOUT for _, timeout in stub.sent)
    assert log_stream.getvalue().splitlines() == ["REQUEST_TIMEOUT installed=yes connect=15 read=120"]


def test_without_the_wrapper_civis_sends_no_timeout(stub):
    """What the wrapper fixes: civis's own requests carry no timeout."""
    new_client().users.get_me()
    assert [timeout for _, timeout in stub.sent] == [None, None]


def test_a_callers_own_timeout_is_kept(stub):
    install_request_timeout()
    for module in (civis.base, civis_resources):
        with module.open_session("fake-key", {}) as session:  # pragma: allowlist secret
            prepared = session.prepare_request(requests.Request("GET", f"{ENDPOINT}users/me"))
            session.send(prepared, timeout=5)
            session.send(prepared)
    assert [timeout for _, timeout in stub.sent] == [5, TIMEOUT, 5, TIMEOUT]


@pytest.mark.parametrize("error", [
    requests.exceptions.ReadTimeout(MARK),
    requests.exceptions.ConnectTimeout(MARK),
    requests.exceptions.ConnectionError(type("ReadTimeoutError", (Exception,), {})(MARK)),  # While the body is read.
])
def test_a_timeout_raises_out_of_the_call_is_counted_and_releases_civiss_lock(stub, error):
    install_request_timeout()
    client = new_client()
    stub.errors["/users/me"] = error
    before = request_timeouts()
    with pytest.raises(type(error)):
        client.users.get_me()
    assert request_timeouts() == before + 1
    assert [path for path, _ in stub.sent].count("/users/me") == 1  # Not retried: civis retries only on statuses.
    assert not civis.base.Endpoint._lock.locked()

    # A second call in another thread completes.
    del stub.errors["/users/me"]
    answers = []
    worker = threading.Thread(target=lambda: answers.append(client.users.get_me()["id"]))
    worker.start()
    worker.join(timeout=30)
    assert answers == [7]


def test_other_errors_are_raised_and_not_counted(stub):
    install_request_timeout()
    client = new_client()
    stub.errors["/users/me"] = requests.exceptions.ConnectionError(MARK)
    before = request_timeouts()
    with pytest.raises(requests.exceptions.ConnectionError):
        client.users.get_me()
    assert request_timeouts() == before


def test_a_missing_open_session_installs_nothing(stub, log_stream, monkeypatch):
    original = civis_resources.open_session
    monkeypatch.delattr(civis.base, "open_session")
    assert install_request_timeout() is False
    assert civis_resources.open_session is original  # Neither module is changed.
    assert log_stream.getvalue().splitlines() == ["REQUEST_TIMEOUT installed=no"]


def test_calling_it_twice_does_not_wrap_twice(stub):
    original = civis.base.open_session
    install_request_timeout()
    install_request_timeout()
    wrapped = civis.base.open_session
    assert wrapped is not original and wrapped.__wrapped__ is original
    assert getattr(civis_resources.open_session, "__wrapped__", None) is not None
    assert not hasattr(wrapped.__wrapped__, "__wrapped__")
    new_client().users.get_me()
    assert [timeout for _, timeout in stub.sent] == [TIMEOUT, TIMEOUT]


def test_live_run_installs_the_timeout_before_making_the_client(monkeypatch):
    from scanner import __main__ as entry

    calls = []
    monkeypatch.setenv("CIVIS_JOB_ID", "1")
    monkeypatch.setenv("CIVIS_RUN_ID", "2")
    monkeypatch.setattr(ratelimit, "install_request_timeout", lambda: calls.append("timeout"))

    def client(**kwargs):
        calls.append("client")
        raise RuntimeError("stop here")

    monkeypatch.setattr(civis, "APIClient", client)
    monkeypatch.setattr(entry.ScanConfig, "from_env", classmethod(lambda cls: type("C", (), {
        "civis_job_id": 1, "civis_run_id": 2})()))
    with pytest.raises(RuntimeError):
        entry.live_run()
    assert calls == ["timeout", "client"]


def test_a_timed_out_all_users_list_falls_back_and_is_counted(stub):
    """docs/PLAN.md 17.12: with civis 2.10's own client, the "all" users list times out, is counted, and the
    active list is read instead."""
    from scanner.inventory.users import load_users

    install_request_timeout()
    client = new_client()
    stub.raise_when = lambda request: (
        requests.exceptions.ReadTimeout(MARK) if "account_status=all" in request.url else None)
    before = request_timeouts()
    lookup = load_users(client)
    assert (lookup.source, lookup.status, lookup.reason) == ("active", None, "timeout")
    assert sorted(lookup.users) == [2]
    assert request_timeouts() == before + 1
    users_calls = [timeout for path, timeout in stub.sent if path == "/users"]
    # The "all" list once (not retried), then the active list's two pages, each with the timeout.
    assert users_calls == [TIMEOUT, TIMEOUT, TIMEOUT]


# --- The RUN line and the test summary -------------------------------------------------------------------


def test_the_run_line_and_the_summary_carry_request_timeouts(tmp_path, log_stream, monkeypatch):
    """A timed-out detail call, list call, and run log read in a dry run: each path goes on, and the RUN line
    counts them. The synthetic client raises requests' ReadTimeout after counting it, as the wrapper does."""
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary
    from scanner.__main__ import dry_run
    from scanner.synthetic import FakeClient

    def timing_out(*args, **kwargs):
        ratelimit._request_timeouts.add()
        raise requests.exceptions.ReadTimeout(MARK)

    original_get = FakeClient._scripts__get_containers
    monkeypatch.setattr(FakeClient, "_scripts__get_containers",
                        lambda self, object_id: timing_out() if object_id == 203 else original_get(self, object_id))
    monkeypatch.setattr(FakeClient, "_services__list", lambda self, **kwargs: timing_out())
    original_logs = FakeClient._jobs__list_runs_logs
    monkeypatch.setattr(FakeClient, "_jobs__list_runs_logs",
                        lambda self, job_id, run_id, **kw: timing_out() if job_id == 101 else original_logs(
                            self, job_id, run_id, **kw))
    result = dry_run(tmp_path)
    assert result.status == "succeeded"
    logged = log_stream.getvalue()
    final = logged.splitlines()[-1]
    assert final.startswith(f"RUN run_id={result.run_id} status=succeeded ") and final.endswith(" request_timeouts=3")
    assert "LIST_FAILED type=service rows_read=0 error=ReadTimeout" in logged
    assert MARK not in logged

    import sqlite3

    conn = sqlite3.connect(tmp_path / "results.sqlite")
    try:
        notes = dict(conn.execute("SELECT object_id, scan_note FROM objects WHERE object_id IN (101, 203)"))
    finally:
        conn.close()
    assert notes[203] == "Reading this object failed (ReadTimeout)."
    assert "The run log could not be read. Reading this object failed (ReadTimeout)." in notes[101]

    text = "\n".join(summary._scan_lines(summary.ScanData(
        result.run_id, "succeeded", None, None, log=summary.LogRead(logged.splitlines()))))
    assert "request_timeouts=3" in text and "LIST_FAILED ReadTimeout: 1" in text and MARK not in text


def test_the_summary_does_not_count_the_request_timeout_line_as_other():
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary

    counts = summary.count_log(["REQUEST_TIMEOUT installed=yes connect=15 read=120", "REQUEST_TIMEOUT installed=no"])
    assert counts.other_lines == 0 and not counts.errors


def test_scan_log_request_timeout_validates_its_values(log_stream):
    for bad in ((None, 15, 120), (True, -1, 120), (True, 15, None), ("yes", 15, 120)):
        with pytest.raises(ValueError):
            scan_log.request_timeout(*bad)
    assert log_stream.getvalue() == ""
