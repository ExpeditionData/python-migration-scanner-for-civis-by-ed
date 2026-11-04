"""Every SQL statement the tool sends stays in the results schema (docs/PLAN.md 17.2, Part E).

Drives every code path that sends SQL, with a recording fake civis.io:

- the scanner's result sink: create tables, appends, resume reads, and the RETENTION_DAYS deletes
- the app's reads (every page), the checklist save, the "Set up results storage" button,
  the Setup page, and the test summary

Each statement must start with CREATE SCHEMA IF NOT EXISTS, CREATE TABLE IF NOT EXISTS, SELECT, INSERT, or
DELETE; name only the results schema's five tables, or read pg_catalog.pg_namespace or pg_catalog.pg_tables (the
existence checks; no other pg_catalog object, and no information_schema); contain no GRANT, REVOKE,
DROP, ALTER, TRUNCATE, UPDATE, COPY, or UNLOAD; and hold one statement only. Made-up data only.
"""

from __future__ import annotations

import re

import pytest

from common.schema import TABLES
from tests.civis_fakes import RecordingIO

TABLE_NAMES = {t.name for t in TABLES}
STARTS = re.compile(r"^(CREATE SCHEMA IF NOT EXISTS|CREATE TABLE IF NOT EXISTS|SELECT|INSERT|DELETE)\b")
FORBIDDEN = re.compile(r"\b(GRANT|REVOKE|DROP|ALTER|TRUNCATE|UPDATE|COPY|UNLOAD)\b", re.IGNORECASE)
TARGET = re.compile(r"\b(?:FROM|INTO|USING|JOIN|TABLE IF NOT EXISTS|SCHEMA IF NOT EXISTS)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
QUALIFIED = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
CATALOG = {"pg_namespace", "pg_tables"}  # Existence checks only.


def _without_literals(statement: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", statement)


def scope_problems(statement: str, schema: str) -> list[str]:
    problems = []
    text = _without_literals(" ".join(statement.split()))
    if not STARTS.match(text):
        problems.append("starts with a statement type that is not allowed")
    if FORBIDDEN.search(text):
        problems.append(f"contains {FORBIDDEN.search(text).group(0)}")
    if ";" in text:
        problems.append("holds more than one statement")
    for target in TARGET.findall(text):
        if "." in target:
            owner, _, name = target.partition(".")
            if owner == schema and name in TABLE_NAMES:
                continue
            if owner == "pg_catalog" and name in CATALOG:
                continue
            problems.append(f"names {target}")
        elif not (text.startswith("CREATE SCHEMA IF NOT EXISTS") and target == schema):
            problems.append(f"names {target}")
    for owner, _ in QUALIFIED.findall(text):
        if owner not in (schema, "pg_catalog", "newer"):  # "newer" is the retention DELETE's alias.
            problems.append(f"names schema {owner}")
    return problems


@pytest.mark.parametrize("statement", [
    "GRANT SELECT ON results.findings TO PUBLIC",
    "DROP TABLE results.findings",
    "SELECT * FROM results.findings; DROP TABLE results.objects",
    "SELECT * FROM other_schema.findings",
    "SELECT * FROM results.pg_user",
    "SELECT * FROM pg_catalog.pg_user",
    "UPDATE results.findings SET rule_id = 'x'",
    "COPY results.findings TO 's3://bucket'",
    "CREATE SCHEMA IF NOT EXISTS other_schema",
    "INSERT INTO results.findings SELECT * FROM other_schema.findings",
    "ALTER TABLE results.findings ADD COLUMN x INT",
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'results'",
    "SELECT usename FROM pg_catalog.pg_user",
    "SELECT * FROM pg_catalog.pg_class",
])
def test_the_checker_catches_statements_outside_the_scope(statement):
    assert scope_problems(statement, "results")


def test_the_checker_allows_the_two_catalog_reads():
    assert scope_problems("SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname = 'results'", "results") == []
    assert scope_problems("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'results' "
                          "AND tablename IN ('scan_runs')", "results") == []


def test_the_checker_allows_literals_that_look_like_sql():
    assert scope_problems("INSERT INTO results.checklist_status (changed_by) VALUES ('drop; grant')", "results") == []


def _assert_in_scope(io: RecordingIO, schema: str) -> None:
    assert io.statements
    problems = {s: scope_problems(s, schema) for s in io.statements}
    assert {s: p for s, p in problems.items() if p} == {}
    for table in io.frames:  # dataframe_to_civis writes rows as data into a named table.
        owner, _, name = table.partition(".")
        assert owner == schema and name in TABLE_NAMES, table


def test_scanner_sink_statements_stay_in_the_results_schema():
    from scanner.output import CivisSink

    io = RecordingIO({"results_scan.scan_runs": [["run_id", "started_at", "status", "recorded_at"],
                                                 ["run-1", "2026-10-01 12:00:00", "running", "2026-10-01 12:00:00"]]})
    sink = CivisSink(object(), io, 31, "results_scan", 41)
    sink.ensure_tables()
    sink.finished_object_ids("run-1")
    sink.run_rows("run-1")
    sink.latest_status("run-1")
    sink.checklist_pairs()
    sink.run_starts()
    sink.delete_runs(["run-0", "run-1"])
    _assert_in_scope(io, "results_scan")
    kinds = {s.split()[0] for s in io.statements}
    assert kinds == {"CREATE", "SELECT", "DELETE"}


def test_scanner_appends_go_only_to_results_tables():
    pytest.importorskip("pandas", reason="pandas is not in the Service image")
    from common.schema import FINDINGS
    from scanner.output import CivisSink

    io = RecordingIO()
    CivisSink(object(), io, 31, "results_scan", 41).append(FINDINGS, "run-1", [{"object_id": 1, "rule_id": "CV3-ML"}])
    assert io.frames == ["results_scan.findings"]


def test_app_statements_stay_in_the_results_schema(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.security import generate_csrf_token
    from app.services import civis_services
    from app.settings import Settings
    from tests.test_summary import MARK, _marked_client, _marked_io

    schema = f"{MARK}_schema"
    client, io = _marked_client(), _marked_io()
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    app = create_app({"SECRET_KEY": "sql-scope-test-secret-key-that-is-long-enough", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
    web = app.test_client()
    for url in ("/", "/scan", "/inventory", "/findings", "/setup", "/setup/summary", "/api/scan-status"):
        assert web.get(url).status_code == 200, url
    headers = {"Civis-User-Id": "77", "Civis-Username": "o'example"}
    with app.test_request_context("/", headers=headers):
        token = generate_csrf_token(app)
    saved = web.post("/findings/save", headers=headers,
                     data={"csrf_token": token, "s:101:CV3-ML": "ignored", "o:101:CV3-ML": "fixed"})
    assert "saved=1" in saved.headers["Location"]
    stored = web.post("/setup/storage", headers=headers, data={"csrf_token": token})
    assert stored.status_code == 302
    assert web.get(stored.headers["Location"], headers=headers).status_code == 200
    _assert_in_scope(io, schema)
    kinds = {" ".join(s.split()[:2]) for s in io.statements}
    assert {"SELECT nspname", "SELECT tablename", "INSERT INTO", "CREATE TABLE"} <= kinds

    # A missing schema: the button also sends CREATE SCHEMA IF NOT EXISTS.
    empty = RecordingIO()
    services = civis_services(Settings(scanner_job_id=1234), client_factory=_marked_client, io_factory=lambda: empty)
    app = create_app({"SECRET_KEY": "sql-scope-test-secret-key-that-is-long-enough", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
    with app.test_request_context("/"):
        token = generate_csrf_token(app)
    app.test_client().post("/setup/storage", data={"csrf_token": token})
    assert f"CREATE SCHEMA IF NOT EXISTS {schema}" in empty.statements
    _assert_in_scope(empty, schema)


def test_every_sql_sending_call_is_covered_here():
    """If a new module sends SQL through civis.io, add it to this file."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    senders = set()
    for package in ("scanner", "common", "app"):
        for path in (root / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("query_civis", "read_civis_sql", "dataframe_to_civis")):
                    senders.add(path.relative_to(root).as_posix())
    assert senders == {"scanner/output.py", "app/backends/civis_backend.py"}
