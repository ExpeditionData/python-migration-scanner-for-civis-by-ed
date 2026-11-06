"""How many civis.io reads each page sends (docs/PLAN.md 17.6, Part D). Made-up data only. No Civis calls.

Reads whose result is small by design (the catalog check, Setup line 9's counts, and the scan_runs rows) use
query_civis preview rows; the objects, findings, coverage, and checklist rows use read_civis_sql. A small read with
the same SQL is sent once per request, and each page reads only the tables it shows. The counts below are the
"after" column of the reads table in docs/PLAN.md 17.6.
"""

from __future__ import annotations

from datetime import datetime

import pytest

# The scanner image has no Flask, and importing any app module loads it. These tests run in the Service image.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import create_app
from app.backends.base import ResultsUnavailable
from app.backends.civis_backend import PREVIEW_ROWS, CivisResults
from app.services import civis_services
from app.settings import Settings
from common.schema import TABLES
from tests.civis_fakes import FakeAPIError, FakeCivisClient, RecordingIO

SCHEMA = "migration_results"
RUN_A, RUN_B = "run-20261101T120000Z-a1b2c3", "run-20261102T120000Z-d4e5f6"
SECRET = "read-count-test-secret-key-that-is-long-enough"  # pragma: allowlist secret  gitleaks:allow


def _io() -> RecordingIO:
    scan_runs = [["run_id", "started_at", "finished_at", "status", "civis_run_id", "recorded_at", "parameters"]]
    for run, civis_run, day in ((RUN_A, "701", "01"), (RUN_B, "702", "02")):
        scan_runs.append([run, f"2026-11-{day} 12:00:00", "", "running", civis_run, f"2026-11-{day} 12:00:00",
                          '{"lookback_days": 400}'])
        scan_runs.append([run, f"2026-11-{day} 12:00:00", f"2026-11-{day} 12:40:00", "succeeded", civis_run,
                          f"2026-11-{day} 12:40:00", '{"lookback_days": 400}'])
    objects = [["run_id", "object_id", "object_type", "name", "url", "scan_status"]]
    findings = [["run_id", "object_id", "object_type", "rule_id", "file", "line_number", "impact", "source"]]
    for run in (RUN_A, RUN_B):
        objects.append([run, "101", "python_script", "Example job", "https://platform.example.test/1", "scanned"])
        findings.append([run, "101", "python_script", "CV3-ML", "inline source", "3", "affected", "code"])
    return RecordingIO({
        "pg_catalog.pg_namespace": [["nspname"], [SCHEMA]],
        "pg_catalog.pg_tables": [["tablename"]] + [[t.name] for t in TABLES],
        "COUNT(*)": [["table_name", "row_count"]] + [[t.name, "2"] for t in TABLES],
        f"{SCHEMA}.scan_runs": scan_runs,
        f"{SCHEMA}.objects": objects,
        f"{SCHEMA}.findings": findings,
        f"{SCHEMA}.coverage": [["run_id", "row_kind", "object_type", "found_count"], [RUN_B, "summary", "python_script", "1"]],
        f"{SCHEMA}.checklist_status": [["run_id", "object_id", "rule_id", "status", "changed_by", "changed_at"],
                                       [RUN_B, "101", "CV3-ML", "open", "scanner", "2026-11-02 12:40:00"]],
    })


def _app(io: RecordingIO):
    client = FakeCivisClient()
    client.handlers["scripts.list_containers_runs"] = lambda id, limit=None: [
        {"id": 702, "state": "succeeded", "started_at": "2026-11-02T12:00:00Z", "finished_at": "2026-11-02T12:41:00Z"},
        {"id": 701, "state": "succeeded", "started_at": "2026-11-01T12:00:00Z", "finished_at": "2026-11-01T12:40:00Z"},
    ]
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    return create_app({"SECRET_KEY": SECRET, "SERVICES": services, "TESTING": True})


# (read_civis_sql, query_civis) per page: cold cache, then the same page again with a warm cache.
EXPECTED = {
    "/": ((4, 2), (0, 0)),
    "/inventory": ((2, 2), (0, 0)),
    "/findings": ((3, 2), (0, 0)),
    "/scan": ((4, 2), (0, 0)),  # Two completed scans: objects and findings for each.
    "/setup": ((0, 3), (0, 2)),  # Line 9 reads the storage fresh: the catalog and the row counts.
    "/setup/summary": ((3, 3), (0, 2)),
    "/api/scan-status": ((0, 2), (0, 0)),
}


@pytest.mark.parametrize("page", list(EXPECTED))
def test_reads_per_page_cold_and_warm(monkeypatch, page):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    io = _io()
    web = _app(io).test_client()
    counts = []
    for _ in range(2):
        before = (len(io.full_reads), len(io.small_reads))
        assert web.get(page).status_code == 200
        counts.append((len(io.full_reads) - before[0], len(io.small_reads) - before[1]))
    assert tuple(counts) == EXPECTED[page]


def test_no_small_read_is_sent_twice_in_one_request(monkeypatch):
    """The Overview's check and Setup line 9 share one catalog read."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    io = _io()
    web = _app(io).test_client()
    for page in ("/setup", "/setup/summary"):
        before = len(io.small_reads)
        web.get(page)
        sent = io.small_reads[before:]
        assert len(sent) == len(set(sent)), page


def test_small_reads_ask_for_the_most_preview_rows():
    io = _io()
    store = CivisResults(object(), io, 31, SCHEMA, 41)
    store.check()
    store.scan_runs()
    store.storage_facts()
    assert io.preview_rows and set(io.preview_rows) == {PREVIEW_ROWS} == {1000}
    assert not io.full_reads


def test_preview_text_is_converted_by_column_type():
    """The Queries API returns preview rows as text; scan_runs rows get their column types."""
    store = CivisResults(object(), _io(), 31, SCHEMA, 41)
    rows = store.scan_runs()
    assert rows[0]["civis_run_id"] == 701 and isinstance(rows[0]["civis_run_id"], int)
    assert rows[0]["finished_at"] is None  # An empty value is no value.


def test_a_result_that_fills_the_preview_limit_is_read_again_in_full():
    many = [["run_id", "status"]] + [[f"run-{i}", "succeeded"] for i in range(PREVIEW_ROWS + 5)]
    io = RecordingIO({f"{SCHEMA}.scan_runs": many})
    rows = CivisResults(object(), io, 31, SCHEMA, 41).scan_runs()
    assert len(rows) == PREVIEW_ROWS + 5
    assert io.small_reads == io.full_reads == [f"SELECT * FROM {SCHEMA}.scan_runs"]


def test_a_small_read_with_zero_rows_is_normal():
    io = RecordingIO({"pg_catalog.pg_namespace": [["nspname"], [SCHEMA]], "pg_catalog.pg_tables": [["tablename"]]})
    store = CivisResults(object(), io, 31, SCHEMA, 41)
    assert store.check() == "no_tables"
    assert store.scan_runs() == []
    assert not io.full_reads


def test_the_request_memo_sends_a_small_read_once_and_a_write_clears_it():
    io = _io()
    memo: dict = {}
    first = CivisResults(object(), io, 31, SCHEMA, 41, memo=memo)
    second = CivisResults(object(), io, 31, SCHEMA, 41, memo=memo)
    first.check()
    second.storage_facts()  # Same request: the catalog read is not sent again.
    assert len([s for s in io.small_reads if "pg_catalog" in s]) == 1
    first.save_checklist(RUN_B, [(101, "CV3-ML", "fixed")], "example_admin", datetime(2026, 11, 3))
    second.check()  # After a write, the catalog is read again.
    assert len([s for s in io.small_reads if "pg_catalog" in s]) == 2
    other = CivisResults(object(), io, 32, SCHEMA, 41, memo=memo)  # Another database: its own read.
    other.check()
    assert len([s for s in io.small_reads if "pg_catalog" in s]) == 3


def test_a_failed_small_read_is_an_error_not_zero_rows():
    io = RecordingIO({"pg_catalog.pg_namespace": [["nspname"], [SCHEMA]]}, fail={"pg_catalog": FakeAPIError(500)})
    with pytest.raises(ResultsUnavailable) as raised:
        CivisResults(object(), io, 31, SCHEMA, 41).check()
    assert raised.value.kind == "error"
    facts = CivisResults(object(), io, 31, SCHEMA, 41).storage_facts()
    assert (facts.step, facts.error) == ("schema", "FakeAPIError status=500")
