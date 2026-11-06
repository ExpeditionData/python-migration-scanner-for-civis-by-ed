"""The test summary (docs/PLAN.md 17.2, Part D): counts, fixed labels, and error types only.

Made-up objects, users, groups, schema, database and credential names, log
lines, and error messages carry marker strings, and no marker may reach the
summary. No Civis calls.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The scanner image has no Flask, and importing any app module loads it. These tests run in the Service image.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import summary
from app.backends.base import LogLine
from scanner.__main__ import DRY_RUN_SCHEMA, dry_run

ROOT = Path(__file__).resolve().parent.parent
MARK = "zqmark"  # Every made-up name below contains this; none of it may reach the summary.
RUN_A = "run-20261101T120000Z-a1b2c3"
RUN_B = "run-20261102T120000Z-d4e5f6"


# --- The allow list --------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    ("v0.1.0-rc3", "v0.1.0-rc3"), ("v1.2", "v1.2"), ("v2", "v2"), ("main", "other"), ("v0.1.0-rc3-zq", "other"),
    ("v1.0.0.zqmark", "other"), ("", "other"), (None, "other"), ("V1.0", "other"),
])
def test_tool_version(value, expected):
    assert summary.tool_version(value) == expected


def test_values_outside_the_allow_list_become_other():
    assert summary._n(3) == "3" and summary._n(-1) == "other" and summary._n(True) == "other" and summary._n("3") == "other"
    assert summary._run(RUN_A) == RUN_A and summary._run("run-zqmark") == "other" and summary._run("my_run") == "other"
    assert summary._token("CivisAPIError status=403") == "CivisAPIError status=403"
    assert summary._token("zqmarkError") == "other" and summary._token("CivisAPIError status=zqm") == "other"
    assert summary._token("Error: example text") == "other"
    assert summary._label("pass", ("pass",)) == "pass" and summary._label("zqmark", ("pass",)) == "other"


def test_log_counts():
    lines = [
        f"RUN run_id={RUN_A} status=running",
        "PROGRESS phase=code objects_done=1 objects_total=2",
        "OBJECT_ERROR object_type=python_script object_id=1 error=CivisAPIError status=403",
        "OBJECT_ERROR object_type=python_script object_id=2 error=CivisAPIError status=403",
        "CIVIS_GIT_FAILED object_type=workflow object_id=3 error=GitCommitNotFound",
        "PARSE_ERROR object_type=notebook object_id=4 error=JSONDecodeError",
        f"PARSE_ERROR object_type=notebook object_id=5 error={MARK}-text status=x",
        "Some container message from Civis",
        f"RUN run_id={RUN_A} status=succeeded objects_total=2 objects_scanned=2 api_calls=9 github_tarball_requests=0 "
        "github_cache_peak_bytes=0 github_cache_evictions=0 civis_git_reads=1 civis_git_failures=1",
    ]
    counts = summary.count_log(lines)
    assert counts.run_counts["api_calls"] == 9 and counts.run_counts["civis_git_failures"] == 1
    assert counts.errors[("OBJECT_ERROR", "CivisAPIError status=403")] == 2
    assert counts.errors[("CIVIS_GIT_FAILED", "GitCommitNotFound")] == 1
    assert counts.errors[("PARSE_ERROR", "other")] == 1
    assert counts.other_lines == 1
    assert counts.users is None


@pytest.mark.parametrize("line, expected", [
    ("USERS source=all", "all"),
    ("USERS source=active status=404", "active status=404"),
    ("USERS source=none status=503", "none status=503"),
    (f"USERS source={MARK}", "other"),
    (f"USERS source=active status=404 {MARK}", "other"),
])
def test_users_line(line, expected):
    counts = summary.count_log([line])
    assert (counts.users, counts.other_lines) == (expected, 0)


def test_users_sources_match_the_scanner():
    from scanner import scan_log

    assert summary.USER_SOURCES == scan_log.USER_SOURCES


def test_fatal_steps_match_the_scanner():
    from scanner import scan_log

    assert summary.FATAL_STEPS == scan_log.FATAL_STEPS


@pytest.mark.parametrize("line, token, step", [
    ("FATAL error=CivisAPIError status=404 step=inventory_users", "CivisAPIError status=404", "inventory_users"),
    ("FATAL error=KeyError step=code_and_logs", "KeyError", "code_and_logs"),
    (f"FATAL error=KeyError step={MARK}", "KeyError", "other"),
    ("FATAL error=KeyError", "KeyError", None),  # The rc5 format.
    ("FATAL error=SchemaNotDedicated reason=results_schema_holds_other_tables_or_views other_objects=2",
     "SchemaNotDedicated", None),
])
def test_fatal_step(line, token, step):
    counts = summary.count_log([line])
    assert counts.errors == {("FATAL", token): 1}
    assert counts.fatal_step == step


class PagedJobs:
    """Every page is full, so reading stops at the page cap."""

    def __init__(self):
        self.calls = []

    def logs(self, run_id, last_id=None):
        self.calls.append(last_id)
        start = (last_id or 0) + 1
        return [LogLine(i, f"{MARK} line {i}") for i in range(start, start + 1000)]


def test_log_reading_stops_at_50_pages_and_says_so():
    jobs = PagedJobs()
    read = summary.read_log(jobs, 7)
    assert len(jobs.calls) == 50 and jobs.calls[:2] == [None, 1000]
    assert not read.complete and len(read.lines) == 50_000
    text = "\n".join(summary._scan_lines(summary.ScanData(RUN_A, "succeeded", None, None, log=read)))
    assert "log_read: log partly read lines_read=50000" in text and MARK not in text


def test_a_short_page_ends_the_log():
    class Jobs:
        def logs(self, run_id, last_id=None):
            return [LogLine(1, "RUN run_id=x status=running")] if last_id is None else []

    read = summary.read_log(Jobs(), 7)
    assert read.complete and read.lines == ["RUN run_id=x status=running"]


# --- TESTING.md item 4.8 ---------------------------------------------------------------------


def _testing_query() -> str:
    text = (ROOT / "TESTING.md").read_text(encoding="utf-8")
    block = re.search(r"```sql\n(.*?)```", text, re.DOTALL).group(1)
    return " ".join(block.split())


def test_4_8_counts_match_the_testing_query_on_the_same_data(tmp_path):
    result = dry_run(tmp_path, github_token=None)  # The synthetic instance, no token: Civis git reads and failures.
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ? AS results_schema", (str(tmp_path / "results.sqlite"),))
    query = _testing_query().replace("<run_id>", result.run_id).rstrip(";")
    from_sql = {row[0]: {"git_backed": row[1], "read_through_civis": row[2], "civis_read_failed": row[3]}
                for row in conn.execute(query)}
    rows = conn.execute("SELECT * FROM results_schema.objects WHERE run_id = ?", (result.run_id,))
    names = [d[0] for d in rows.description]
    from app.backends.base import normalize_row

    objects = [normalize_row("objects", dict(zip(names, r))) for r in rows.fetchall()]
    assert from_sql and summary.testing_4_8(objects) == from_sql
    assert sum(v["read_through_civis"] for v in from_sql.values()) > 0
    assert sum(v["civis_read_failed"] for v in from_sql.values()) > 0
    assert DRY_RUN_SCHEMA  # The dry run's own schema name is not used by the query.


# --- The page, with marker strings everywhere --------------------------------------------------


def _marked_client():
    from tests.civis_fakes import FakeAPIError, FakeCivisClient, app_service, scanner_job, shares

    person = {"id": 15, "name": f"{MARK} Robot Person", "username": f"{MARK}_robot"}
    job = scanner_job(
        name=f"{MARK} scanner job", author=dict(person), running_as=dict(person), repo_ref=f"{MARK}-branch",
        docker_image_tag=f"{MARK}-tag",  # The identity fields (repository, command, image name) stay the scanner's.
        arguments={"RESULTS_DB": {"database": 31, "credential": 41}, "RESULTS_SCHEMA": f"{MARK}_schema",
                   "EXTRA": f"{MARK} value"},
    )
    job["params"].append({"name": f"{MARK.upper()}_PARAM", "type": "string"})
    client = FakeCivisClient(job)
    client.handlers.update({
        "users.list_me": lambda: dict(person),
        "credentials.get": lambda id: FakeAPIError(403, f"{MARK} credential {MARK}_db_user"),
        "scripts.list_containers_shares": lambda id: shares([(21, f"{MARK} Admin")], [(61, f"{MARK} Group")]),
        "services.list": lambda **kw: [app_service(name=f"{MARK} service", user=dict(person))],
        "services.get": lambda id: app_service(name=f"{MARK} service", current_url=f"https://{MARK}.example.com"),
        "services.list_shares": lambda id: shares([(22, f"{MARK} Viewer")], []),
        "scripts.list_containers_runs": lambda id, limit=None: [
            {"id": 702, "state": "succeeded", "started_at": "2026-11-02T12:00:00Z", "finished_at": "2026-11-02T12:41:00Z"},
            {"id": 701, "state": "failed"},
        ],
        "scripts.list_containers_runs_logs": lambda id, run_id, last_id=None, limit=None: [] if last_id else [
            {"id": 1, "message": f"RUN run_id={RUN_B} status=running"},
            {"id": 2, "message": f"{MARK} password=hunter2 from a library warning"},  # pragma: allowlist secret
            {"id": 3, "message": "OBJECT_ERROR object_type=python_script object_id=101 error=CivisAPIError status=404"},
            {"id": 4, "message": f"OBJECT_ERROR object_type=python_script object_id=102 error={MARK}.Error status=404"},
            {"id": 5, "message": f"CIVIS_GIT_FAILED object_type=workflow object_id=103 error=CivisAPIError status={MARK}"},
            {"id": 6, "message": f"FATAL error=KeyError {MARK} trailing text"},
            {"id": 7, "message": f"RUN run_id={RUN_B} status=succeeded objects_total=3 objects_scanned=3 api_calls=12 "
                                 "github_tarball_requests=0 github_cache_peak_bytes=0 github_cache_evictions=0 "
                                 "civis_git_reads=1 civis_git_failures=1"},
        ],
    })
    return client


def _marked_io():
    from common.schema import TABLES
    from tests.civis_fakes import RecordingIO

    schema = f"{MARK}_schema"
    scan_runs = [["run_id", "started_at", "finished_at", "status", "scanning_user_name", "civis_run_id", "recorded_at",
                  "parameters"]]
    for run, civis_run, start, end, status in ((RUN_A, "701", "2026-11-01 12:00:00", "", "running"),
                                               (RUN_B, "702", "2026-11-02 12:00:00", "", "running"),
                                               (RUN_B, "702", "2026-11-02 12:00:00", "2026-11-02 12:41:00", "succeeded"),
                                               (f"{MARK}-run", "", "2026-10-01 12:00:00", "", "failed")):
        scan_runs.append([run, start, end, status, f"{MARK}_robot", civis_run, end or start, f'{{"note": "{MARK}"}}'])
    objects = [["run_id", "object_id", "object_type", "name", "url", "owner_name", "git_repo", "git_path",
                "scan_status", "scan_note"],
               [RUN_B, "101", "python_script", f"{MARK} job", f"https://{MARK}.example.com/1", f"{MARK} Owner",
                f"https://github.com/{MARK}/repo", f"{MARK}.py", "partial",
                f"{MARK}: Its own file was checked, read through Civis. {MARK}"],
               [RUN_B, "102", "workflow", f"{MARK} flow", "", f"{MARK} Owner", f"https://github.com/{MARK}/flow", "",
                "partial", f"The file could not be read through Civis ({MARK})."],
               [RUN_B, "103", f"{MARK}_type", f"{MARK} other", "", "", "", "", "not_scanned", f"{MARK}"]]
    findings = [["run_id", "object_id", "object_type", "rule_id", "file", "line_number", "impact"],
                [RUN_B, "101", "python_script", "CV3-ML", f"{MARK}.py", "3", "affected"],
                [RUN_B, "102", "workflow", "CV3-ML", f"{MARK}.yml", "4", "advisory"]]
    checklist = [["run_id", "object_id", "rule_id", "status", "changed_by", "changed_at"],
                 [RUN_B, "101", "CV3-ML", "fixed", f"{MARK}_admin", "2026-11-03 10:00:00"],
                 [RUN_B, "102", "CV3-ML", "open", "scanner", "2026-11-02 12:40:00"]]
    return RecordingIO({
        "pg_catalog.pg_namespace": [["nspname"], [schema]],
        "pg_catalog.pg_tables": [["tablename"]] + [[t.name] for t in TABLES],
        "COUNT(*)": [["table_name", "row_count"], ["scan_runs", "4"]],
        f"{schema}.scan_runs": scan_runs,
        f"{schema}.objects": objects,
        f"{schema}.findings": findings,
        f"{schema}.checklist_status": checklist,
        f"{schema}.coverage": [["run_id"]],
    })


@pytest.fixture
def marked_app(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.setenv(f"{MARK.upper()}_PASSWORD", f"{MARK}-secret-value")  # pragma: allowlist secret
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings

    client, io = _marked_client(), _marked_io()
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    app = create_app({"SECRET_KEY": "summary-test-secret-key-that-is-long-enough", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
    return app, client, io


def _summary_text(response) -> str:
    body = response.get_data(as_text=True)
    return html.unescape(body[body.index(">", body.index("<textarea")) + 1: body.index("</textarea>")])


def test_no_marker_reaches_the_summary(marked_app):
    app, client, io = marked_app
    response = app.test_client().get("/setup/summary")
    assert response.status_code == 200
    text = _summary_text(response)
    assert MARK not in text.lower() and "hunter2" not in text and "example.com" not in text
    assert f"scan: {RUN_B}" in text and "tool_version: other" in text
    assert "  status: succeeded" in text and "minutes_start_to_finish: 41" in text
    assert "run_line: objects_total=3 objects_scanned=3 api_calls=12" in text
    assert "OBJECT_ERROR CivisAPIError status=404: 1" in text and "OBJECT_ERROR other: 1" in text
    assert "CIVIS_GIT_FAILED other: 1" in text and "FATAL KeyError: 1" in text
    assert "log_other_lines: 1" in text and "log_read: complete lines_read=7" in text
    assert "users_list: not_found" in text
    assert "python_script: scanned=0 partly_scanned=1 not_scanned=0" in text and "other: scanned=0" in text
    assert "findings: affected_objects=1 advisory_objects=1 total_findings=2" in text
    assert "python_script: git_backed=1 read_through_civis=1 civis_read_failed=0" in text
    assert "workflow: git_backed=1 read_through_civis=0 civis_read_failed=1" in text
    assert "checklist_rows_by_people: 1" in text and "runs_in_scan_runs: 3" in text
    assert "sharing_job: users=2 groups=1" in text and "service_lookup: single_match" in text
    assert "database_parameter_shape: object" in text
    assert re.search(r"^  5 one_owner: (review|fail)$", text, re.MULTILINE)


def test_the_summary_counts_both_shares_shapes_and_names_an_unrecognized_one(marked_app):
    from tests.civis_fakes import shares

    app, client, _ = marked_app
    client.handlers["scripts.list_containers_shares"] = lambda id: [shares([(21, f"{MARK} A")], []),
                                                                     shares([(22, f"{MARK} B")], [(61, f"{MARK} G")])]
    client.handlers["services.list_shares"] = lambda id: {"id": 7, "name": f"{MARK} not a shares object"}
    text = _summary_text(app.test_client().get("/setup/summary"))
    assert "sharing_job: users=3 groups=1" in text  # A list of share objects.
    assert "sharing_service: shape_not_recognized" in text
    assert MARK not in text.lower()


def test_every_summary_line_is_built_from_the_allow_list(marked_app):
    """After the two heading lines, every word is an integer, a run ID, a release tag, a lowercase label, or a
    class-name-like token: nothing that could be free text."""
    app, _, _ = marked_app
    text = _summary_text(app.test_client().get("/setup/summary"))
    lines = text.splitlines()
    assert lines[:2] == ["Python Migration Scanner test summary", "Counts, fixed labels, and error types only."]
    word = re.compile(r"\d+|run-\d{8}T\d{6}Z-[0-9a-f]{6}|v\d+(\.\d+)*(-rc\d+)?|[a-z][a-z0-9_]*|[A-Z][A-Za-z0-9_]*")
    for line in lines[2:]:
        for item in re.split(r"[\s=:]+", line.strip()):
            assert item == "" or word.fullmatch(item), (line, item)


def test_an_earlier_scan_can_be_chosen(marked_app):
    app, _, _ = marked_app
    body = app.test_client().get("/setup/summary").get_data(as_text=True)
    assert f'<option value="{RUN_A}"' in body and f'<option value="{RUN_B}" selected' in body
    assert f"{MARK}-run" not in body  # Run IDs that do not match the generated pattern are not offered.
    text = _summary_text(app.test_client().get(f"/setup/summary?run={RUN_A}"))
    assert f"scan: {RUN_A}" in text and "status: running" in text
    text = _summary_text(app.test_client().get(f"/setup/summary?run={MARK}"))
    assert f"scan: {RUN_B}" in text


def test_the_summary_is_a_read_only_get(marked_app):
    app, client, io = marked_app
    assert app.test_client().post("/setup/summary").status_code in (400, 405)
    app.test_client().get("/setup/summary")
    assert io.queries == [] and io.frames == []
    assert all(s.lstrip().upper().startswith("SELECT") for s in io.reads)
    assert not any(m.startswith(("scripts.post", "services.post", "services.put", "services.patch"))
                   for m in client.methods_called())


def test_the_setup_page_links_to_the_summary_and_the_copy_button_is_vendored(marked_app):
    app, _, _ = marked_app
    assert 'href="/setup/summary">Build test summary</a>' in app.test_client().get("/setup").get_data(as_text=True)
    body = app.test_client().get("/setup/summary").get_data(as_text=True)
    assert 'src="/static/js/copy-summary.js"' in body and 'id="copy-summary"' in body and " hidden>" in body
    assert app.test_client().get("/static/js/copy-summary.js").status_code == 200


def test_the_summary_renders_in_every_preview_state(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.backends.local_backend import STATES
    from app.services import local_services
    from scanner.synthetic.preview import HOSTILE_NAME, build_preview

    build_preview(tmp_path)
    for state in STATES:
        app = create_app({"SECRET_KEY": "summary-test-secret-key-that-is-long-enough",  # pragma: allowlist secret  gitleaks:allow
                          "SERVICES": local_services(tmp_path, state)})
        response = app.test_client().get("/setup/summary")
        assert response.status_code == 200, state
        text = _summary_text(response)
        assert "Example" not in text and HOSTILE_NAME not in text and "example" not in text, state


def test_the_default_scan_is_the_newest_finished_one(monkeypatch):
    """A running scan has no RUN line counts yet, so the summary starts with the newest finished scan."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings

    run_c = "run-20261103T120000Z-0a1b2c"
    io = _marked_io()
    io.tables[f"{MARK}_schema.scan_runs"].append(
        [run_c, "2026-11-03 12:00:00", "", "running", "x", "703", "2026-11-03 12:00:00", "{}"])
    client = _marked_client()
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    app = create_app({"SECRET_KEY": "summary-test-secret-key-that-is-long-enough", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
    body = app.test_client().get("/setup/summary").get_data(as_text=True)
    assert f'<option value="{RUN_B}" selected' in body and f'<option value="{run_c}"' in body
    assert f"scan: {RUN_B}" in _summary_text(app.test_client().get("/setup/summary"))
    running = _summary_text(app.test_client().get(f"/setup/summary?run={run_c}"))
    assert f"scan: {run_c}" in running and "status: running" in running


# --- Inventory counts and the lookback window (docs/PLAN.md 17.6, Part C) -------------------------------


def test_inventory_lines_are_counted_per_type_and_are_not_other_lines():
    lines = [
        "INVENTORY type=container_script listed=4768 kept=12 outside_window=66 template_copies=4690 not_python=0",
        "INVENTORY type=custom_script listed=5553 kept=221 outside_window=5277 template_copies=0 not_python=55",
        f"INVENTORY type={MARK} listed=1 kept=1 outside_window=0 template_copies=0 not_python=0",
        f"INVENTORY type=notebook listed=68 kept=68 outside_window=0 template_copies=0 not_python=0 {MARK}",
        "PROGRESS phase=inventory objects_done=50 objects_total=301",
    ]
    counts = summary.count_log(lines)
    assert counts.other_lines == 0
    assert counts.inventory == {
        "container_script": {"listed": 4768, "kept": 12, "outside_window": 66, "template_copies": 4690, "not_python": 0},
        "custom_script": {"listed": 5553, "kept": 221, "outside_window": 5277, "template_copies": 0, "not_python": 55},
        "other": {},
    }
    text = "\n".join(summary._scan_lines(summary.ScanData(
        RUN_A, "succeeded", None, None, log=summary.LogRead(lines), parameters={"lookback_days": 30})))
    assert "  lookback_days: 30" in text
    assert ("    container_script: listed=4768 kept=12 outside_window=66 template_copies=4690 not_python=0"
            in text)
    assert "    other: not_recognized" in text and MARK not in text


def test_inventory_counts_match_the_scanner():
    from scanner import scan_log

    assert summary.INVENTORY_COUNTS == scan_log.INVENTORY_COUNTS


@pytest.mark.parametrize("parameters, expected", [
    ({"lookback_days": 400}, "400"), ({"lookback_days": f"{MARK}"}, "other"), ({"lookback_days": -1}, "other"),
    ({}, "not_recorded"), (None, "not_recorded"),
])
def test_lookback_days_from_the_scan_runs_parameters(parameters, expected):
    text = "\n".join(summary._scan_lines(summary.ScanData(RUN_A, "succeeded", None, None, parameters=parameters)))
    assert f"  lookback_days: {expected}" in text


def test_the_summary_reports_the_dry_runs_inventory(tmp_path):
    """The summary's INVENTORY counts and lookback_days for a dry run of the synthetic instance."""
    import io as io_module
    import logging

    from scanner import scan_log

    stream = io_module.StringIO()
    handler = logging.StreamHandler(stream)
    logger = scan_log.configure()
    logger.addHandler(handler)
    try:
        dry_run(tmp_path, now=datetime(2026, 10, 13, 12, tzinfo=timezone.utc))
    finally:
        logger.removeHandler(handler)
    counts = summary.count_log(stream.getvalue().splitlines())
    assert counts.inventory["python_script"] == {
        "listed": 10, "kept": 8, "outside_window": 1, "template_copies": 1, "not_python": 0}
    assert counts.inventory["workflow"]["kept"] == counts.inventory["workflow"]["listed"] == 5
    for c in counts.inventory.values():
        assert c["listed"] == c["kept"] + c["outside_window"] + c["template_copies"] + c["not_python"]
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    parameters = json.loads(conn.execute("SELECT parameters FROM scan_runs LIMIT 1").fetchone()[0])
    assert parameters["lookback_days"] == 400
