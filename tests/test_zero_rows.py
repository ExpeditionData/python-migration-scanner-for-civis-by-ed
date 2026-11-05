"""Reads that return zero rows (docs/PLAN.md 17.4).

civis.io.read_civis_sql raises civis.base.EmptyResultError for a query with no rows, because Civis writes no
file for an empty result (found in the first live test). The app and the scanner treat that error as zero rows.
The fakes raise it the same way, and each test below checks that the path it covers really read zero rows.
Made-up schemas, jobs, and objects only. No Civis calls.
"""

from __future__ import annotations

import html
import io as io_module
import json
import logging
import re

import pytest

from common.civis_errors import is_empty_result
from common.schema import TABLES
from scanner import scan_log
from scanner.config import ScanConfig
from scanner.output import CivisSink
from scanner.runner import run_scan
from tests.civis_fakes import EmptyResultError, RecordingIO, no_output

SCHEMA = "migration_results"
RUN = "run-20261105T120000Z-abc123"
NAMESPACE = {"pg_catalog.pg_namespace": [["nspname"], [SCHEMA]]}
OUR_TABLES = {"pg_catalog.pg_tables": [["tablename"]] + [[t.name] for t in TABLES]}
# Setup line 9's read check returns one row per table, even when the tables are empty.
COUNTS = {"COUNT(*)": [["table_name", "row_count"]] + [[t.name, "0"] for t in TABLES]}


def _empty_read(io: RecordingIO, text: str) -> bool:
    return any(text in statement for statement in io.empty_reads)


# --- The shared check --------------------------------------------------------------------------


def test_only_empty_result_error_means_zero_rows():
    assert is_empty_result(no_output())
    assert not is_empty_result(RuntimeError("no output"))
    assert not is_empty_result(ValueError())


def test_the_fake_raises_like_civis_for_zero_rows():
    io = RecordingIO({"header_only": [["x"]]})
    for statement in ("SELECT 1 AS x WHERE 1 = 0", "SELECT x FROM header_only"):
        with pytest.raises(EmptyResultError):
            io.read_civis_sql(statement, 31, return_as="list")
    assert io.empty_reads == ["SELECT 1 AS x WHERE 1 = 0", "SELECT x FROM header_only"]


def test_other_read_errors_are_not_zero_rows():
    from app.backends.civis_backend import CivisResults

    class Broken(RecordingIO):
        def read_civis_sql(self, *args, **kwargs):
            raise RuntimeError("made-up error")

    with pytest.raises(RuntimeError):
        CivisResults(object(), Broken(), 31, SCHEMA, 41)._read("SELECT 1")
    with pytest.raises(RuntimeError):
        CivisSink(object(), Broken(), 31, SCHEMA, 41)._read("SELECT 1")


# --- The scanner -------------------------------------------------------------------------------


@pytest.mark.parametrize("method, args, expected", [
    ("other_objects", (), 0),
    ("finished_object_ids", (RUN,), set()),
    ("run_rows", (RUN,), {"objects": [], "findings": [], "coverage": []}),
    ("latest_status", (RUN,), None),
    ("checklist_pairs", (), set()),
    ("run_starts", (), []),
])
def test_scanner_reads_of_zero_rows(method, args, expected):
    io = RecordingIO()
    sink = CivisSink(object(), io, 31, SCHEMA, 41)
    assert getattr(sink, method)(*args) == expected
    assert io.empty_reads and io.empty_reads == io.reads


@pytest.mark.parametrize("tables", [{}, NAMESPACE], ids=["missing_schema", "new_empty_schema"])
def test_the_scanners_dedicated_check_passes_a_missing_or_new_empty_schema(tables):
    io = RecordingIO(tables)
    assert CivisSink(object(), io, 31, SCHEMA, 41).other_objects() == 0
    assert _empty_read(io, "pg_catalog.pg_views")


class _Outputs:
    def __init__(self):
        self.saved = []

    def save_csv(self, name, data):
        self.saved.append(name)


def _scan(io, **config):
    pytest.importorskip("pandas")  # CivisSink.append builds a DataFrame; the scanner image has pandas.
    from scanner.synthetic import FIXTURES, FakeClient, SyntheticSession

    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    stream = io_module.StringIO()
    logger = scan_log.configure()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    outputs = _Outputs()
    try:
        result = run_scan(
            ScanConfig(database=31, schema=SCHEMA, credential_id=41, request_budget=1000, **config),
            client=FakeClient(data), http_session=SyntheticSession(data),
            sink=CivisSink(object(), io, 31, SCHEMA, 41), outputs=outputs,
        )
    finally:
        logger.removeHandler(handler)
    return result, stream.getvalue().splitlines(), outputs


def test_a_first_scan_into_a_new_empty_schema_writes_all_five_tables():
    io = RecordingIO(NAMESPACE)  # The schema exists and holds nothing: every read returns zero rows.
    result, log, outputs = _scan(io, retention_days=30)
    assert result.status == "succeeded" and result.tables_ok and result.report_saved and outputs.saved
    assert not [line for line in log if line.startswith(("TABLE_WRITE_FAILED", "FATAL"))]
    assert set(io.frames) == {f"{SCHEMA}.{t.name}" for t in TABLES}
    assert io.queries == [t.create_sql(SCHEMA) for t in TABLES]  # No retention DELETE: no earlier runs.
    assert _empty_read(io, "pg_catalog.pg_views")  # The dedicated-schema check.
    assert _empty_read(io, f"{SCHEMA}.checklist_status")  # checklist_pairs on the first scan.
    assert _empty_read(io, "MIN(started_at)")  # Retention's run_starts.


def test_a_resume_with_no_saved_rows_starts_from_the_beginning():
    io = RecordingIO(NAMESPACE)
    result, log, _ = _scan(io, run_id=RUN)
    assert result.status == "succeeded" and result.tables_ok
    assert not [line for line in log if line.startswith(("TABLE_WRITE_FAILED", "FATAL"))]
    for text in ("SELECT status, recorded_at", "SELECT DISTINCT object_id FROM", "MIN(started_at)"):
        assert _empty_read(io, text), text  # latest_status, finished_object_ids, and run_starts.
    assert _empty_read(io, f"FROM {SCHEMA}.findings WHERE run_id")  # run_rows.


# --- The app -----------------------------------------------------------------------------------


@pytest.fixture
def civis_app(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings
    from tests.civis_fakes import FakeCivisClient

    def build(io):
        client = FakeCivisClient()
        services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
        return create_app({"SECRET_KEY": "zero-rows-test-secret-key-long-enough-01", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
    return build


def _main_text(response) -> str:
    body = response.get_data(as_text=True)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body[body.index("<main"): body.index("</main>")])))


def _post_setup(app):
    from app.security import generate_csrf_token

    with app.test_request_context("/"):
        token = generate_csrf_token(app)
    response = app.test_client().post("/setup/storage", data={"csrf_token": token})
    return response.headers["Location"]


def test_a_missing_schema_shows_the_setup_button_with_run_scan_off(civis_app):
    io = RecordingIO()
    response = civis_app(io).test_client().get("/")
    body, text = response.get_data(as_text=True), _main_text(response)
    assert "The results schema does not exist yet" in text and "Results could not be read" not in text
    assert "Set up results storage" in body
    assert 'disabled aria-describedby="run-scan-off">Run scan</button>' in body
    assert _empty_read(io, "pg_catalog.pg_namespace")


def test_a_schema_without_our_tables_shows_no_scan_yet_with_run_scan_on(civis_app):
    io = RecordingIO(NAMESPACE)
    response = civis_app(io).test_client().get("/")
    body, text = response.get_data(as_text=True), _main_text(response)
    assert "No scan yet" in text and "Results could not be read" not in text
    assert "Set up results storage" in body and ">Run scan</button>" in body and "run-scan-off" not in body
    assert _empty_read(io, "pg_catalog.pg_tables")


def test_the_button_creates_the_schema_when_the_lookup_reads_zero_rows(civis_app):
    io = RecordingIO()
    assert "storage=created" in _post_setup(civis_app(io))
    assert io.queries == [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"] + [t.create_sql(SCHEMA) for t in TABLES]
    assert _empty_read(io, "pg_catalog.pg_namespace")


def test_the_button_treats_a_new_empty_schema_as_dedicated(civis_app):
    io = RecordingIO(NAMESPACE)
    assert "storage=created_existing" in _post_setup(civis_app(io))
    assert io.queries == [t.create_sql(SCHEMA) for t in TABLES]
    assert _empty_read(io, "pg_catalog.pg_views")


def test_empty_result_tables_read_as_no_rows(civis_app):
    from app.backends.civis_backend import CivisResults

    io = RecordingIO({**NAMESPACE, **OUR_TABLES})
    results = CivisResults(object(), io, 31, SCHEMA, 41)
    assert results.check() == "ok"
    assert results.checklist() == [] and results.scan_runs() == [] and results.rows("objects", RUN) == []
    assert _empty_read(io, f"{SCHEMA}.checklist_status") and _empty_read(io, f"{SCHEMA}.scan_runs")
    web = civis_app(RecordingIO({**NAMESPACE, **OUR_TABLES})).test_client()
    for path in ("/", "/scan", "/inventory", "/findings"):
        response = web.get(path)
        assert response.status_code == 200, path
        assert "could not be read" not in _main_text(response), path
    assert "No scan yet" in _main_text(web.get("/"))


@pytest.mark.parametrize("tables, words", [
    ({}, "results_storage Fail The results schema does not exist. Fix: Click Set up results storage"),
    (NAMESPACE, "results_storage Warning 5 of the 5 tables do not exist yet."),
    ({**NAMESPACE, **OUR_TABLES, **COUNTS}, "results_storage Pass The schema holds only this tool's five tables"),
], ids=["missing_schema", "new_empty_schema", "empty_tables"])
def test_setup_line_9_with_zero_row_reads(civis_app, tables, words):
    io = RecordingIO(tables)
    text = _main_text(civis_app(io).test_client().get("/setup"))
    assert words in text and "The check failed at step" not in text
    assert io.empty_reads


@pytest.mark.parametrize("tables, expected", [
    ({}, ["results_state: schema_missing", "results_schema_dedicated: unknown", "checklist_rows_by_people: not_read",
          "scan: none"]),
    (NAMESPACE, ["results_state: no_tables", "results_schema_dedicated: yes", "scan: none"]),
    ({**NAMESPACE, **OUR_TABLES, **COUNTS}, [
        "results_state: ok", "results_schema_dedicated: yes", "checklist_rows_by_people: 0", "runs_in_scan_runs: 0",
        "scan: none"]),
], ids=["missing_schema", "new_empty_schema", "empty_tables"])
def test_the_summary_with_zero_row_reads(civis_app, tables, expected):
    io = RecordingIO(tables)
    text = civis_app(io).test_client().get("/setup/summary").get_data(as_text=True)
    for line in expected:
        assert line in text, line
    assert "  9 results_storage: fail" in text if not tables else "  9 results_storage: fail" not in text
    assert io.empty_reads
