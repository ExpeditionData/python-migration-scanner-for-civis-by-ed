"""How many civis.io reads each page sends (docs/PLAN.md 17.6, Part D). Made-up data only. No Civis calls.

Reads whose result is small by design (the catalog check, Setup line 9's counts, the scan_runs rows, and Scan
history's two count reads) use query_civis preview rows; the objects, findings, coverage, and checklist rows use
read_civis_sql. A small read with the same SQL is sent once per request, and each page reads only the tables it
shows. The counts below are the "after" column of the reads table in docs/PLAN.md 17.6, except /scan's, which is
from docs/PLAN.md 17.17.
"""

from __future__ import annotations

from datetime import datetime

import re

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
    "/scan": ((0, 4), (0, 0)),  # Two completed scans: the catalog check, scan_runs, and the two history reads (docs/PLAN.md 17.17).
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


def test_small_reads_ask_for_100_preview_rows():
    io = _io()
    store = CivisResults(object(), io, 31, SCHEMA, 41)
    store.check()
    store.scan_runs()
    store.storage_facts()
    assert io.preview_rows and set(io.preview_rows) == {PREVIEW_ROWS} == {100}
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


@pytest.mark.parametrize(("scans", "read_again"), [(49, False), (50, True)])
def test_scan_runs_falls_back_to_read_civis_sql_at_50_scans(scans, read_again):
    """scan_runs holds two rows per scan (running, then the final row), so 50 scans fill the 100 preview rows."""
    rows = [["run_id", "status"]]
    for i in range(scans):
        rows += [[f"run-{i}", "running"], [f"run-{i}", "succeeded"]]
    io = RecordingIO({f"{SCHEMA}.scan_runs": rows})
    assert len(CivisResults(object(), io, 31, SCHEMA, 41).scan_runs()) == 2 * scans
    assert io.full_reads == ([f"SELECT * FROM {SCHEMA}.scan_runs"] if read_again else [])


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


# ---------------------------------------------------------------------------
# Scan history with many completed scans (docs/PLAN.md 17.17)
# ---------------------------------------------------------------------------


def _many(count: int, exact: bool = True) -> tuple[RecordingIO, list[str], FakeCivisClient]:
    """``count`` completed scans, oldest first. With ``exact``, the fake answers the two history reads with fixed
    values: scan i (from 1) has i affected and 20 + i advisory objects, and every scan but the oldest has i + 100
    fixed pairs (a fixed row comes only for the newer side of a pair). Without ``exact``, the fake runs the two
    statements on its findings rows: one affected object per scan, and nothing fixed."""
    run_ids = [f"run-202611{i:02d}T120000Z-abc{i:03d}" for i in range(1, count + 1)]
    scan_runs = [["run_id", "started_at", "finished_at", "status", "civis_run_id", "recorded_at", "parameters"]]
    for i, run in enumerate(run_ids, start=1):
        day = f"2026-11-{i:02d}"
        scan_runs.append([run, f"{day} 12:00:00", "", "running", str(700 + i), f"{day} 12:00:00", "{}"])
        scan_runs.append([run, f"{day} 12:00:00", f"{day} 12:40:00", "succeeded", str(700 + i), f"{day} 12:40:00", "{}"])
    tables = {
        "pg_catalog.pg_namespace": [["nspname"], [SCHEMA]],
        "pg_catalog.pg_tables": [["tablename"]] + [[t.name] for t in TABLES],
        f"{SCHEMA}.scan_runs": scan_runs,
        f"{SCHEMA}.objects": [["run_id", "object_id"]] + [[r, "101"] for r in run_ids],
        f"{SCHEMA}.findings": [["run_id", "object_id", "rule_id", "impact"]] + [[r, "101", "CV3-ML", "affected"] for r in run_ids],
    }
    if exact:
        tables["affected_objects"] = [["run_id", "affected_objects", "advisory_objects"]] + [
            [r, str(i), str(20 + i)] for i, r in enumerate(run_ids, start=1)]
        tables["fixed_pairs"] = [["run_id", "fixed_pairs"]] + [[r, str(100 + i)] for i, r in enumerate(run_ids, start=1) if i > 1]
    client = FakeCivisClient()
    client.handlers["scripts.list_containers_runs"] = lambda id, limit=None: [
        {"id": 700 + i, "state": "succeeded", "started_at": f"2026-11-{i:02d}T12:00:00Z",
         "finished_at": f"2026-11-{i:02d}T12:41:00Z"} for i in range(count, 0, -1)
    ]
    return RecordingIO(tables), run_ids, client


def _history_cells(body: str) -> dict[str, tuple[str, str, str]]:
    """Scan history's Affected, Advisory, and Fixed cells by run label."""
    rows = {}
    for row in re.findall(r"<tr>\s*<th scope=\"row\">(.*?)</tr>", body, flags=re.S):
        label = re.sub(r"<[^>]+>.*", "", row, flags=re.S).strip()
        cells = re.findall(r'<td class="num [a-z]+-text">([^<]*)</td>', row)
        rows[label] = tuple(cells)
    return rows


def _history_app(io: RecordingIO, client: FakeCivisClient):
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    return create_app({"SECRET_KEY": SECRET, "SERVICES": services, "TESTING": True})


def test_scan_history_with_nine_completed_scans_reads_four_small_results_once(monkeypatch):
    """rc13 read every listed scan's objects and findings, and with 7 or more completed scans every read missed the
    12-table cache. Now: 4 small reads on the first load, none on the next, and the objects table is never read."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    io, run_ids, client = _many(9)
    web = _history_app(io, client).test_client()
    first = web.get("/scan")
    assert first.status_code == 200
    assert (len(io.full_reads), len(io.small_reads)) == (0, 4)
    assert web.get("/scan").status_code == 200
    assert (len(io.full_reads), len(io.small_reads)) == (0, 4)
    assert not [s for s in io.reads if f"{SCHEMA}.objects" in s]
    cells = _history_cells(first.get_data(as_text=True))
    for i in range(1, 10):
        fixed = "n/a" if i == 1 else str(100 + i)
        assert cells[f"Civis run {700 + i}"] == (str(i), str(20 + i), fixed)


def test_scan_history_lists_counts_for_the_newest_ten_completed_scans(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    calls = []
    original = CivisResults.history_counts

    def spy(self, run_ids, pairs):
        calls.append((list(run_ids), list(pairs)))
        return original(self, run_ids, pairs)

    monkeypatch.setattr(CivisResults, "history_counts", spy)
    io, run_ids, client = _many(11, exact=False)
    web = _history_app(io, client).test_client()
    response = web.get("/scan")
    assert response.status_code == 200
    newest_ten = run_ids[1:]
    assert calls == [(newest_ten[::-1], list(zip(newest_ten, newest_ten[1:])))]
    assert len(calls[0][1]) == 9
    cells = _history_cells(response.get_data(as_text=True))
    assert cells["Civis run 701"] == ("n/a", "n/a", "n/a")  # The oldest scan is past the 10-scan limit.
    assert cells["Civis run 702"] == ("1", "0", "n/a")  # The 10th has no earlier completed scan in the list.
    for i in range(3, 12):
        assert cells[f"Civis run {700 + i}"] == ("1", "0", "0")

