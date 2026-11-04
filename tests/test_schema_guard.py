"""The results schema must hold only this tool's tables (docs/PLAN.md 17.3, Part B).

If RESULTS_SCHEMA named a schema that holds the organization's own tables, CREATE TABLE IF NOT EXISTS would add
our tables there, and if one of their tables had one of our five names, inserts and retention deletes would run
against it. The app's setup button, Setup line 9, and the scanner refuse such a schema before writing anything.
Made-up schemas and names only. No Civis calls.
"""

from __future__ import annotations

import html
import io as io_module
import logging
import re
import sqlite3

import pytest

from common.schema import SCAN_RUNS, TABLES, other_object_count, schema_objects_sql
from scanner import scan_log
from scanner.config import ScanConfig
from scanner.output import CivisRunOutputs, CivisSink, SchemaNotDedicated
from scanner.runner import run_scan
from tests.civis_fakes import RecordingIO

MARK = "zqmark"  # The organization's own table and view names below; never shown or logged.
SCHEMA = "migration_results"
OURS = [[t.name] for t in TABLES]


def catalog(tables=(), views=()):
    return {"pg_catalog.pg_namespace": [["nspname"], [SCHEMA]],
            "pg_catalog.pg_tables": [["tablename"]] + [[t] for t in tables],
            "pg_catalog.pg_views": [["viewname"]] + [[v] for v in views]}


# --- The shared check --------------------------------------------------------------------------


def test_the_check_sql():
    assert schema_objects_sql("Migration_Results") == (
        "SELECT 'table' AS kind, tablename AS name FROM pg_catalog.pg_tables WHERE schemaname = 'migration_results' "
        "UNION ALL SELECT 'view' AS kind, viewname AS name FROM pg_catalog.pg_views WHERE schemaname = 'migration_results'"
    )
    with pytest.raises(Exception):
        schema_objects_sql("x'; DROP SCHEMA y; --")


@pytest.mark.parametrize("rows, count", [
    ([], 0),
    ([{"kind": "table", "name": t.name} for t in TABLES], 0),
    ([{"kind": "table", "name": "scan_runs"}], 0),
    ([{"kind": "table", "name": "scan_runs"}, {"kind": "table", "name": f"{MARK}_their_table"}], 1),
    ([{"kind": "view", "name": "findings"}], 1),  # A view with one of our names is not ours.
    ([{"kind": "view", "name": f"{MARK}_v"}, {"kind": "table", "name": f"{MARK}_t"}], 2),
])
def test_other_object_count(rows, count):
    assert other_object_count(rows) == count


# --- The scanner -------------------------------------------------------------------------------


class NoCalls:
    """A civis client that fails the test if the scanner calls anything."""

    def __getattr__(self, name):
        raise AssertionError(f"the scanner called the Civis API ({name}) before checking the schema")


class RecordingOutputs:
    def __init__(self):
        self.saved = []

    def save_csv(self, name, data):
        self.saved.append(name)


def _config():
    return ScanConfig(database=31, schema=SCHEMA, credential_id=41, civis_job_id=5, civis_run_id=9)


@pytest.mark.parametrize("tables, views", [(["scan_runs", f"{MARK}_their_table"], []), ([], [f"{MARK}_their_view"])])
def test_the_scanner_stops_before_any_write(tables, views):
    io = RecordingIO(catalog(tables, views))
    outputs = RecordingOutputs()
    with pytest.raises(SchemaNotDedicated) as excinfo:
        run_scan(_config(), client=NoCalls(), http_session=None, sink=CivisSink(NoCalls(), io, 31, SCHEMA, 41),
                 outputs=outputs)
    assert excinfo.value.count == 1
    assert io.queries == [] and io.frames == [] and outputs.saved == []
    assert [s.split()[0] for s in io.statements] == ["SELECT"]  # Only the check ran.


def test_the_scanner_logs_one_fatal_line_and_exits_2(monkeypatch):
    import scanner.__main__ as entry

    io = RecordingIO(catalog(["objects", f"{MARK}_their_table", f"{MARK}_other"]))
    files = []
    io.file_to_civis = lambda *a, **k: files.append(a)  # Must never be called.

    def live_run():
        sink = CivisSink(NoCalls(), io, 31, SCHEMA, 41)
        return run_scan(_config(), client=NoCalls(), http_session=None, sink=sink,
                        outputs=CivisRunOutputs(NoCalls(), io, 5, 9))

    monkeypatch.setattr(entry, "live_run", live_run)
    stream = io_module.StringIO()
    logger = scan_log.configure()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        assert entry.main([]) == 2
    finally:
        logger.removeHandler(handler)
    lines = stream.getvalue().splitlines()
    assert lines == ["FATAL error=SchemaNotDedicated reason=results_schema_holds_other_tables_or_views other_objects=2"]
    assert files == [] and io.queries == [] and io.frames == []


def _make_sqlite(folder, extra_sql=None, ours=()):
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "results.sqlite")
    for table in ours:
        conn.execute(table.create_sql("main"))
    if extra_sql:
        conn.execute(extra_sql)
    conn.commit()
    conn.close()


@pytest.mark.parametrize("extra", [f"CREATE TABLE {MARK}_their_table (x INTEGER)",
                                   f"CREATE VIEW {MARK}_their_view AS SELECT 1 AS x"])
def test_a_dry_run_into_a_shared_schema_writes_nothing_and_exits_2(tmp_path, extra):
    from scanner.__main__ import main

    _make_sqlite(tmp_path, extra)
    assert main(["--dry-run", "--out", str(tmp_path)]) == 2
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()
    assert not names & {t.name for t in TABLES}  # None of our tables were created.
    assert not list(tmp_path.glob("python-migration-scanner-*.csv"))  # No CSV either.


@pytest.mark.parametrize("ours", [(), (SCAN_RUNS,), tuple(TABLES)])
def test_a_missing_empty_or_partly_set_up_schema_works_as_before(tmp_path, ours):
    from scanner.__main__ import dry_run

    _make_sqlite(tmp_path, ours=ours)
    assert dry_run(tmp_path, github_token=None).status == "succeeded"


def test_a_check_that_cannot_run_writes_no_tables_but_keeps_the_csv(tmp_path, monkeypatch):
    from scanner.__main__ import dry_run
    from scanner.output import SqliteSink

    def broken(self):
        raise sqlite3.OperationalError("made-up")

    monkeypatch.setattr(SqliteSink, "other_objects", broken)
    result = dry_run(tmp_path, github_token=None)
    assert result.status == "tables_failed" and not result.tables_ok and result.report_saved
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    assert not {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()


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
        return create_app({"SECRET_KEY": "schema-guard-test-secret-key-long-enough", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
    return build


def _post(app):
    from app.security import generate_csrf_token

    with app.test_request_context("/"):
        token = generate_csrf_token(app)
    web = app.test_client()
    response = web.post("/setup/storage", data={"csrf_token": token})
    return response, web.get(response.headers["Location"])


def _text(response) -> str:
    body = response.get_data(as_text=True)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body[body.index("<main"): body.index("</main>")])))


@pytest.mark.parametrize("tables, views, words", [
    (["scan_runs", f"{MARK}_their_table"], [], "already holds 1 other table or view"),
    ([], [f"{MARK}_v1", f"{MARK}_v2"], "already holds 2 other tables or views"),
])
def test_the_button_refuses_a_schema_that_holds_other_objects(civis_app, tables, views, words):
    io = RecordingIO(catalog(tables, views))
    response, page = _post(civis_app(io))
    assert "storage=not_dedicated" in response.headers["Location"]
    text = _text(page)
    assert words in text and "so the tool will not use it. Nothing was created." in text
    assert "Choose a new schema name in the scanner job's RESULTS_SCHEMA parameter." in text
    assert io.queries == [] and MARK not in page.get_data(as_text=True)


@pytest.mark.parametrize("tables", [[], ["scan_runs"], [t.name for t in TABLES]])
def test_the_button_still_works_on_our_own_tables(civis_app, tables):
    io = RecordingIO(catalog(tables))
    response, page = _post(civis_app(io))
    assert "storage=created_existing" in response.headers["Location"]
    assert io.queries == [t.create_sql(SCHEMA) for t in TABLES]


def test_setup_line_9_and_the_summary(civis_app):
    shared = civis_app(RecordingIO({**catalog(["scan_runs", f"{MARK}_their_table"], [f"{MARK}_v"]),
                                    "COUNT(*)": [["table_name", "row_count"]]}))
    page = _text(shared.test_client().get("/setup"))
    assert ("results_storage Fail The results schema holds 2 other tables or views, so the tool will not use it. "
            "Fix: Choose a new schema name in the scanner job's RESULTS_SCHEMA parameter.") in page
    assert MARK not in page
    summary = shared.test_client().get("/setup/summary").get_data(as_text=True)
    assert "results_schema_dedicated: no" in summary and MARK not in summary
    ours = civis_app(RecordingIO({**catalog([t.name for t in TABLES]), "COUNT(*)": [["table_name", "row_count"]]}))
    assert "results_schema_dedicated: yes" in ours.test_client().get("/setup/summary").get_data(as_text=True)
    missing = civis_app(RecordingIO())
    assert "results_schema_dedicated: unknown" in missing.test_client().get("/setup/summary").get_data(as_text=True)


def test_preview_schema_shared_state(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import local_services
    from scanner.synthetic.preview import build_preview

    build_preview(tmp_path)
    app = create_app({"SECRET_KEY": "schema-guard-test-secret-key-long-enough",  # pragma: allowlist secret  gitleaks:allow
                      "SERVICES": local_services(tmp_path, "schema-shared")})
    _, page = _post(app)
    assert "already holds 3 other tables or views" in _text(page)
    assert "results_storage Fail The results schema holds 3 other tables or views" in _text(app.test_client().get("/setup"))
