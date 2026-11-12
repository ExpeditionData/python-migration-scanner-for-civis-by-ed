"""A fresh notebook link right before each download (docs/PLAN.md 17.9, Part A).

In the live test each notebooks.get returned a new S3 link that expired 300
seconds later, and the code and log scan reached the notebooks many minutes
after inventory, so every link read during inventory had expired. The synthetic
client issues a new signed link on every call, and the synthetic file host
refuses a link more than 300 seconds old, on a fake clock (no real waits).
"""

from __future__ import annotations

import copy
import io
import re
import sqlite3
from datetime import datetime, timezone

import pytest

from common.models import ObjectType, ScanStatus
from scanner import scan_log
from scanner.engine import Engine
from scanner.inventory import build_inventory
from scanner.process import ScanDeps, process_item
from scanner.ratelimit import BudgetedClient, RequestBudget
from scanner.retrieval import github as gh
from scanner.retrieval.http import SafeHttp
from scanner.retrieval.notebooks import LINK_FAILED, NO_LINK, NotebookLinks
from scanner.synthetic import FakeClient, SyntheticSession
from scanner.synthetic.links import (
    BAD_SIGNATURE_BODY,
    EXPIRED_BODY,
    SyntheticClock,
    base_of,
    check_link,
    link_issued_at,
    sign_link,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
# Python notebooks in the synthetic instance, and the ones with a file to download (403 has none: HTTP 404).
PYTHON_NOTEBOOKS = {401, 403, 404, 405}
NOTEBOOKS_WITH_FILES = {401, 404, 405}
FILE_401 = "https://example-notebooks.s3.amazonaws.com/notebook-401.ipynb"
REFUSED_403 = "The notebook file could not be downloaded. The request was refused (HTTP 403)."


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def instance() -> dict:
    return copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)


def notebook(data: dict, object_id: int) -> dict:
    return next(n for n in data["notebooks"] if n["id"] == object_id)


def scan_notebook(data: dict, object_id: int = 401, clock: SyntheticClock | None = None):
    """Inventory the synthetic instance, then process one object the way the code and log scan does, with a
    BudgetedClient (as in a real scan). Returns (result, client, session, links, budget)."""
    clock = clock or SyntheticClock()
    client = FakeClient(data, clock=clock)
    session = SyntheticSession(data, clock=clock)
    budget = RequestBudget(1000, 300.0)
    civis = BudgetedClient(client, budget)
    inventory = build_inventory(civis)
    item = next(i for i in inventory.items if i.obj.object_id == object_id)
    links = NotebookLinks(civis)
    deps = ScanDeps(engine=Engine(), client=civis, http=SafeHttp(session), repos=gh.GitHubRepos(SafeHttp(session), None, None),
                    lookback_days=200, now=NOW, notebook_links=links)
    before = len(client.calls)
    result = process_item(item, deps)
    return result, client, session, links, client.calls[before:]


def link_calls(calls) -> list:
    return [args[0] for name, args, _ in calls if name == "notebooks.get"]


# --- The synthetic links and file host -------------------------------------------------------


def test_every_notebooks_get_issues_a_new_link_that_expires_after_300_seconds():
    clock = SyntheticClock()
    data = instance()
    client = FakeClient(data, clock=clock)
    first = client.notebooks.get(401)["notebook_url"]
    second = client.notebooks.get(401)["notebook_url"]
    assert first != second and base_of(first) == base_of(second) == FILE_401
    assert link_issued_at(first) == clock.now()
    for name in ("X-Amz-Expires=300", "X-Amz-SignedHeaders=host", "X-Amz-Security-Token=", "X-Amz-Signature="):
        assert name in first
    assert check_link(first, clock.now()) == (200, b"")
    clock.advance(300)
    assert check_link(first, clock.now()) == (200, b"")  # Exactly 300 seconds old is still valid.
    clock.advance(1)
    status, body = check_link(first, clock.now())
    assert status == 403 and body == EXPIRED_BODY
    assert b"<Code>AccessDenied</Code>" in body and b"Request has expired" in body
    assert check_link(FILE_401, clock.now())[1] == BAD_SIGNATURE_BODY  # No query string: not a signed link.
    forged = re.sub(r"X-Amz-Signature=[0-9a-f]+", "X-Amz-Signature=" + "0" * 64, sign_link(FILE_401, clock.now()))
    assert check_link(forged, clock.now()) == (403, BAD_SIGNATURE_BODY)


def test_the_file_host_refuses_an_expired_link_with_403_and_access_denied():
    clock = SyntheticClock()
    data = instance()
    session = SyntheticSession(data, clock=clock)
    link = FakeClient(data, clock=clock).notebooks.get(401)["notebook_url"]
    assert session.get(link).status_code == 200
    clock.advance(320)  # The live test's 320 seconds.
    response = session.get(link)
    assert response.status_code == 403 and b"".join(response.iter_content()) == EXPIRED_BODY


# --- The dry run, with the code and log scan more than 300 seconds after inventory --------


def test_dry_run_scans_every_notebook_when_the_code_scan_starts_after_the_links_expire(tmp_path, log_stream, monkeypatch):
    """Fails if any download uses a link from inventory: those links are more than 300 seconds old by then."""
    import scanner.runner as runner
    import scanner.synthetic as synthetic
    from scanner.__main__ import dry_run

    clock = SyntheticClock()
    clients, sessions, inventory_ended = [], [], []
    real_build = runner.build_inventory

    def slow_inventory(*args, **kwargs):
        built = real_build(*args, **kwargs)
        inventory_ended.append(clock.now())
        clock.advance(301)  # The code and log scan starts more than 300 seconds after inventory.
        return built

    class RecordingClient(synthetic.FakeClient):
        def __init__(self, data):
            super().__init__(data)
            clients.append(self)

    class RecordingSession(synthetic.SyntheticSession):
        def __init__(self, data):
            super().__init__(data)
            sessions.append(self)

    monkeypatch.setattr(runner, "build_inventory", slow_inventory)
    monkeypatch.setattr(synthetic, "FakeClient", RecordingClient)
    monkeypatch.setattr(synthetic, "SyntheticSession", RecordingSession)
    dry_run(tmp_path, now=NOW, file_clock=clock)

    conn = sqlite3.connect(tmp_path / "results.sqlite")
    try:
        rows = dict(conn.execute("SELECT object_id, scan_status FROM objects WHERE object_type = 'notebook'"))
    finally:
        conn.close()
    assert set(rows) == PYTHON_NOTEBOOKS
    assert {i for i, s in rows.items() if s == "scanned"} == NOTEBOOKS_WITH_FILES
    downloads = sessions[0].notebook_downloads
    assert {base.rsplit("-", 1)[1] for base, _ in downloads} == {f"{i}.ipynb" for i in NOTEBOOKS_WITH_FILES}
    # Every download used a link issued after inventory ended, never one read during inventory.
    assert all(issued is not None and issued > inventory_ended[0] for _, issued in downloads)
    inventory_links = [url for _, url in clients[0].issued_links if link_issued_at(url) <= inventory_ended[0]]
    assert len(inventory_links) == len(PYTHON_NOTEBOOKS)
    assert all(check_link(url, clock.now())[0] == 403 for url in inventory_links)  # They had expired.
    final = log_stream.getvalue().splitlines()[-1]
    assert final.endswith(" notebook_links=4 notebook_link_retries=0")


def test_the_old_behavior_fails_the_dry_run_test():
    """The test above would catch the rc9 code: downloading the inventory link after 301 seconds is refused."""
    clock = SyntheticClock()
    data = instance()
    client = FakeClient(data, clock=clock)
    session = SyntheticSession(data, clock=clock)
    inventory_link = client.notebooks.get(401)["notebook_url"]
    clock.advance(301)
    assert session.get(inventory_link).status_code == 403


# --- Retries --------------------------------------------------------------------------------


def test_a_first_403_and_then_200_scans_the_notebook_with_one_retry():
    data = instance()
    data["notebook_download_statuses"] = {FILE_401: [403]}
    result, client, session, links, calls = scan_notebook(data)
    assert result.obj.scan_status is ScanStatus.SCANNED and result.obj.scan_note is None
    assert result.findings
    assert (links.links, links.retries) == (2, 1)
    assert link_calls(calls) == [401, 401]
    first, second = (issued for _, issued in session.notebook_downloads)
    assert first is not None and second is not None


def test_two_403s_leave_the_notebook_not_scanned():
    data = instance()
    data["notebook_download_statuses"] = {FILE_401: [403, 403]}
    result, client, session, links, calls = scan_notebook(data)
    assert result.obj.scan_status is ScanStatus.NOT_SCANNED
    assert result.obj.scan_note == REFUSED_403
    assert (links.links, links.retries) == (2, 1) and len(session.notebook_downloads) == 2


def test_an_expired_link_is_retried_with_a_new_one():
    """A real expiry on the synthetic host: the first link is already more than 300 seconds old."""
    clock = SyntheticClock()
    data = instance()
    client = FakeClient(data, clock=clock)
    session = SyntheticSession(data, clock=clock)
    issued = []

    class SlowFirstLink:
        """notebooks.get through the real client; the clock moves 301 seconds after the first link only."""

        class notebooks:
            @staticmethod
            def get(object_id):
                detail = client.notebooks.get(object_id)
                issued.append(detail["notebook_url"])
                if len(issued) == 1:
                    clock.advance(301)
                return detail

    links = NotebookLinks(SlowFirstLink())
    text = links.download(SafeHttp(session), 401)
    assert '"nbformat": 4' in text and (links.links, links.retries) == (2, 1)
    assert len(set(issued)) == 2


@pytest.mark.parametrize("statuses, scanned, retries, note", [
    ([400], True, 1, None),
    ([400, 400], False, 1, "The notebook file could not be downloaded. The request was refused (HTTP 400)."),
    ([403, 400], False, 1, "The notebook file could not be downloaded. The request was refused (HTTP 400)."),
    ([404], False, 0, "The notebook file could not be downloaded. The request was refused (HTTP 404)."),
    ([500], False, 0, "The notebook file could not be downloaded. The request was refused (HTTP 500)."),
    ([403, 404], False, 1, "The notebook file could not be downloaded. The request was refused (HTTP 404)."),
])
def test_only_400_and_403_are_retried_and_only_once(statuses, scanned, retries, note):
    data = instance()
    data["notebook_download_statuses"] = {FILE_401: list(statuses)}
    result, client, session, links, calls = scan_notebook(data)
    assert (result.obj.scan_status is ScanStatus.SCANNED) == scanned
    assert result.obj.scan_note == note
    assert (links.links, links.retries) == (1 + retries, retries)
    assert len(session.notebook_downloads) == 1 + retries


def test_a_host_the_scanner_may_not_contact_is_not_retried():
    data = instance()
    notebook(data, 401)["notebook_url"] = "https://files.example.test/notebook-401.ipynb"
    result, client, session, links, calls = scan_notebook(data)
    assert result.obj.scan_status is ScanStatus.NOT_SCANNED and "not allowed to contact" in result.obj.scan_note
    assert (links.links, links.retries) == (1, 0) and session.notebook_downloads == []


# --- A failed notebooks.get before the download --------------------------------------------


@pytest.mark.parametrize("status, reason", [
    (403, "The scanning user cannot read this object (HTTP 403)."),
    (404, "The object was not found when its details were requested (HTTP 404)."),
    (500, "The Civis API returned an error (HTTP 500)."),
])
def test_a_failed_notebooks_get_before_the_download_gives_the_not_scanned_reason(monkeypatch, status, reason):
    import scanner.process as process

    logs_read = []
    real_read_log = process.read_log

    def recording_read_log(client, obj, *args):
        logs_read.append(obj.object_id)
        return real_read_log(client, obj, *args)

    monkeypatch.setattr(process, "read_log", recording_read_log)
    data = instance()
    notebook(data, 401)["_get_errors"] = [None, status]  # Inventory's call works; the link call fails.
    result, client, session, links, calls = scan_notebook(data)
    assert result.obj.scan_status is ScanStatus.NOT_SCANNED
    assert result.obj.scan_note == f"{LINK_FAILED} {reason}"
    assert (links.links, links.retries) == (1, 0) and session.notebook_downloads == []
    assert logs_read == [401]  # Its log is still read as before (notebooks have none, so nothing is sent).
    assert not [c for c in calls if c[0] not in ("notebooks.get",)]


def test_no_link_in_the_answer_gives_the_not_scanned_reason():
    data = instance()
    notebook(data, 401)["notebook_url"] = ""
    result, client, session, links, calls = scan_notebook(data)
    assert result.obj.scan_status is ScanStatus.NOT_SCANNED and result.obj.scan_note == NO_LINK
    assert links.links == 1 and session.notebook_downloads == []


def test_a_failed_second_notebooks_get_gives_its_own_reason():
    data = instance()
    data["notebook_download_statuses"] = {FILE_401: [403]}
    notebook(data, 401)["_get_errors"] = [None, None, 503]
    result, client, session, links, calls = scan_notebook(data)
    assert result.obj.scan_note == f"{LINK_FAILED} The Civis API returned an error (HTTP 503)."
    assert (links.links, links.retries) == (2, 1) and len(session.notebook_downloads) == 1


# --- Counts and other object types ----------------------------------------------------------


def test_link_counts_match_the_calls_made_and_are_charged_to_the_budget():
    data = instance()
    data["notebook_download_statuses"] = {FILE_401: [403]}
    notebook(data, 405)["_get_errors"] = [None, 500]
    clock = SyntheticClock()
    client = FakeClient(data, clock=clock)
    session = SyntheticSession(data, clock=clock)
    budget = RequestBudget(1000, 300.0)
    civis = BudgetedClient(client, budget)
    inventory = build_inventory(civis)
    links = NotebookLinks(civis)
    deps = ScanDeps(engine=Engine(), client=civis, http=SafeHttp(session), repos=gh.GitHubRepos(SafeHttp(session), None, None),
                    lookback_days=200, now=NOW, notebook_links=links)
    before_calls, before_budget = len(client.calls), budget.requests
    for item in inventory.items:
        process_item(item, deps)
    gets = link_calls(client.calls[before_calls:])
    assert sorted(gets) == [401, 401, 403, 404, 405]
    assert (links.links, links.retries) == (len(gets), 1)
    assert budget.requests - before_budget == len(client.calls) - before_calls  # Every link call was charged.


def test_objects_that_are_not_notebooks_make_no_extra_calls():
    data = instance()
    clock = SyntheticClock()
    client = FakeClient(data, clock=clock)
    session = SyntheticSession(data, clock=clock)
    inventory = build_inventory(client)
    links = NotebookLinks(client)
    deps = ScanDeps(engine=Engine(), client=client, http=SafeHttp(session), repos=gh.GitHubRepos(SafeHttp(session), None, None),
                    lookback_days=200, now=NOW, notebook_links=links)
    others = [i for i in inventory.items if i.obj.object_type is not ObjectType.NOTEBOOK]
    assert others
    before = len(client.calls)
    for item in others:
        process_item(item, deps)
    names = {name for name, _, _ in client.calls[before:]}
    assert "notebooks.get" not in names and links.links == 0
    assert session.notebook_downloads == []


def test_scan_deps_makes_its_own_link_counter_on_the_client():
    client = FakeClient({})
    deps = ScanDeps(engine=Engine(), client=client, http=SafeHttp(SyntheticSession({})), repos=None, lookback_days=1, now=NOW)
    assert isinstance(deps.notebook_links, NotebookLinks) and deps.notebook_links.links == 0


def test_run_line_counts_and_order(log_stream):
    scan_log.run_status("run-1", "succeeded", objects_total=3, objects_scanned=3, api_calls=12, civis_git_reads=0,
                        civis_git_failures=0, notebook_links=5, notebook_link_retries=1)
    assert log_stream.getvalue().splitlines() == [
        "RUN run_id=run-1 status=succeeded objects_total=3 objects_scanned=3 api_calls=12 civis_git_reads=0 "
        "civis_git_failures=0 notebook_links=5 notebook_link_retries=1"
    ]
    assert scan_log.RUN_COUNTS[-2:] == ("notebook_links", "notebook_link_retries")


def test_a_resumed_run_counts_its_own_link_calls(tmp_path, log_stream):
    """Like api_calls, a resumed run counts only the links it asked for: one per notebook it still scans."""
    from scanner.__main__ import SimulatedStop, dry_run

    with pytest.raises(SimulatedStop):
        dry_run(tmp_path, now=NOW, stop_after=10, checkpoint_objects=5)
    run_id = re.search(r"RUN run_id=(\S+) status=running", log_stream.getvalue()).group(1)
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    try:
        done = {r[0] for r in conn.execute("SELECT object_id FROM objects WHERE object_type = 'notebook'")}
    finally:
        conn.close()
    dry_run(tmp_path, now=NOW, run_id=run_id)
    final = log_stream.getvalue().splitlines()[-1]
    assert final.endswith(f" notebook_links={len(PYTHON_NOTEBOOKS - done)} notebook_link_retries=0")
