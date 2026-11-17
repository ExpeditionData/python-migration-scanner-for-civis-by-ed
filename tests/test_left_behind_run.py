"""A scan that ended tables_failed before the typed frame fix (docs/PLAN.md 17.7, Part A).

Its first checkpoint wrote findings and coverage rows, then the objects load failed, so its run ID has findings
and coverage rows, no objects rows, and only a "running" scan_runs row. Civis shows the run as failed (exit
code 3). The app must list it as Failed, never treat it as the current scan, and show every page. Since
docs/PLAN.md 17.11, Part D, Scan history links its CSV once Civis has ended the run, and the download route says
when the run has none. Made-up rows.
"""

from __future__ import annotations

import html
import re

import pytest

from common.schema import TABLES
from tests.civis_fakes import FakeCivisClient, RecordingIO

SCHEMA = "migration_results"
OLD = "run-20261101T120000Z-aaa111"
LEFT = "run-20261110T120000Z-bbb222"
SECRET = "left-behind-run-test-secret-key-long-enough-01"  # pragma: allowlist secret  gitleaks:allow
PAGES = ("/", "/scan", "/inventory", "/findings", "/api/scan-status", "/setup", "/setup/summary")


def _io(with_old_scan: bool) -> RecordingIO:
    scan_runs = [["run_id", "started_at", "finished_at", "status", "civis_run_id", "recorded_at", "parameters"]]
    objects = [["run_id", "object_id", "object_type", "name", "url", "scan_status"]]
    findings = [["run_id", "object_id", "object_type", "rule_id", "file", "line_number", "impact", "source"]]
    coverage = [["run_id", "row_kind", "object_type", "object_id", "found_count", "scanned_count", "reason"]]
    if with_old_scan:
        scan_runs.append([OLD, "2026-11-01 12:00:00", "", "running", "701", "2026-11-01 12:00:00", "{}"])
        scan_runs.append([OLD, "2026-11-01 12:00:00", "2026-11-01 12:30:00", "succeeded", "701",
                          "2026-11-01 12:30:00", "{}"])
        objects.append([OLD, "101", "python_script", "Example job", "https://platform.example.test/1", "scanned"])
        findings.append([OLD, "101", "python_script", "CV3-ML", "inline source", "3", "affected", "code"])
        coverage.append([OLD, "summary", "python_script", "", "1", "1", ""])
    # The left-behind run: the "running" row only, findings and coverage rows, and no objects rows.
    scan_runs.append([LEFT, "2026-11-10 12:00:00", "", "running", "702", "2026-11-10 12:00:00",
                      '{"lookback_days": 30}'])
    findings.append([LEFT, "202", "custom_script", "CV3-ML", "inline source", "7", "affected", "code"])
    coverage.append([LEFT, "object", "workflow", "303", "", "", "The workflow's own file could not be read."])
    return RecordingIO({
        "pg_catalog.pg_namespace": [["nspname"], [SCHEMA]],
        "pg_catalog.pg_tables": [["tablename"]] + [[t.name] for t in TABLES],
        "COUNT(*)": [["table_name", "row_count"]] + [[t.name, "1"] for t in TABLES],
        f"{SCHEMA}.scan_runs": scan_runs,
        f"{SCHEMA}.objects": objects,
        f"{SCHEMA}.findings": findings,
        f"{SCHEMA}.coverage": coverage,
    })


def _web(monkeypatch, with_old_scan: bool):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings

    client = FakeCivisClient()
    runs = [{"id": 702, "state": "failed", "started_at": "2026-11-10T12:00:00Z", "finished_at": "2026-11-10T12:26:00Z"}]
    if with_old_scan:
        runs.append({"id": 701, "state": "succeeded", "started_at": "2026-11-01T12:00:00Z",
                     "finished_at": "2026-11-01T12:31:00Z"})
    client.handlers["scripts.list_containers_runs"] = lambda id, limit=None: runs
    io = _io(with_old_scan)
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    return create_app({"SECRET_KEY": SECRET, "SERVICES": services, "TESTING": True}).test_client()


def _main_text(response) -> str:
    body = response.get_data(as_text=True)
    if "<main" not in body:
        return body
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body[body.index("<main"): body.index("</main>")])))


@pytest.mark.parametrize("with_old_scan", [True, False], ids=["after_a_completed_scan", "only_scan"])
def test_every_page_shows_with_a_left_behind_run(monkeypatch, with_old_scan):
    web = _web(monkeypatch, with_old_scan)
    for page in PAGES:
        response = web.get(page)
        assert response.status_code == 200, page
        assert "could not be read" not in _main_text(response), page


@pytest.mark.parametrize("with_old_scan", [True, False], ids=["after_a_completed_scan", "only_scan"])
def test_scan_history_lists_the_left_behind_run_as_failed(monkeypatch, with_old_scan):
    text = _main_text(_web(monkeypatch, with_old_scan).get("/scan"))
    assert re.search(r"Civis run 702 [^|]{0,80}? Failed ", text)
    if with_old_scan:
        assert re.search(r"Civis run 701 [^|]{0,80}? Completed ", text)


def test_the_overview_keeps_the_completed_scan_as_current(monkeypatch):
    text = _main_text(_web(monkeypatch, True).get("/"))
    assert "The last scan failed before it finished. The results shown are from Civis run 701" in text
    assert "No completed scan yet" not in text and "No scan yet" not in text


def test_the_overview_with_only_the_left_behind_run(monkeypatch):
    text = _main_text(_web(monkeypatch, False).get("/"))
    assert "No completed scan yet" in text and "The last scan failed before it finished." in text
    assert "The results shown are from" not in text


# --- The CSV link for a run left with only a "running" row (docs/PLAN.md 17.11, Part D) ------------------------

NO_REPORT = "This scan has no CSV report. It stopped before saving one."
CSV_LINK = re.compile(r'<a href="/download/(\d+)" data-no-busy>CSV<span class="sr-only"> report for ([^<]+)</span></a>')


def _web_with(monkeypatch, state: str = "failed", outputs=None, job=None):
    """The left-behind run 702 (only scan) with Civis run state ``state``, and its run outputs ``outputs`` (a list,
    or an exception to raise). Returns the test client and the fake civis client."""
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings

    client = FakeCivisClient(job)
    runs = [{"id": 702, "state": state, "started_at": "2026-11-10T12:00:00Z",
             "finished_at": None if state in ("queued", "running") else "2026-11-10T12:26:00Z"}]
    client.handlers["scripts.list_containers_runs"] = lambda id, limit=None: runs
    client.handlers["scripts.list_containers_runs_outputs"] = (
        lambda id, run_id: outputs if outputs is not None else [])
    client.handlers["files.get"] = lambda id: {"id": id, "file_url": "https://example-bucket.s3.amazonaws.com/r.csv"}
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client,
                              io_factory=lambda: _io(False))
    return create_app({"SECRET_KEY": SECRET, "SERVICES": services, "TESTING": True}).test_client(), client


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_scan_history_links_the_csv_of_a_run_left_running_after_civis_ended_it(monkeypatch, state):
    web, client = _web_with(monkeypatch, state)
    body = web.get("/scan").get_data(as_text=True)
    assert CSV_LINK.findall(body) == [("702", "Civis run 702")]
    assert "scripts.list_containers_runs_outputs" not in client.methods_called()  # No API call for the link.


@pytest.mark.parametrize("state", ["queued", "running"])
def test_no_csv_link_while_the_civis_run_is_active(monkeypatch, state):
    web, _ = _web_with(monkeypatch, state)
    body = web.get("/scan").get_data(as_text=True)
    assert "/download/702" not in body and CSV_LINK.findall(body) == []


def test_the_csv_downloads_when_the_run_attached_one(monkeypatch):
    csv_output = [{"object_type": "File", "object_id": 9, "name": "python-migration-scanner-run.csv"}]
    web, client = _web_with(monkeypatch, "failed", outputs=csv_output)
    response = web.get("/download/702")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://example-bucket.s3.amazonaws.com/r.csv"
    assert ("scripts.list_containers_runs_outputs", (1234, 702), {}) in client.calls


@pytest.mark.parametrize("outputs", [[], [{"object_type": "JSONValue", "object_id": 3, "name": "summary"}]])
def test_a_run_with_no_csv_output_gets_the_no_report_page(monkeypatch, outputs):
    web, _ = _web_with(monkeypatch, "failed", outputs=outputs)
    response = web.get("/download/702")
    assert response.status_code == 404 and response.mimetype == "text/html"
    text = _main_text(response)
    assert "No CSV report" in text and NO_REPORT in text and "Go to the overview" in text
    assert "Page not found" not in text


def test_another_jobs_run_outputs_are_still_refused(monkeypatch):
    from tests.civis_fakes import scanner_job

    # SCANNER_JOB_ID points at another job: no outputs are read at all.
    web, client = _web_with(monkeypatch, "failed", job=scanner_job(docker_command="python export.py"))
    response = web.get("/download/702")
    assert response.status_code == 404 and NO_REPORT not in _main_text(response)
    assert "scripts.list_containers_runs_outputs" not in client.methods_called()
    # A run ID that is not this job's: Civis refuses the outputs call, and the page is the plain 404.
    from civis.base import CivisAPIError

    class Refused(CivisAPIError):
        def __init__(self):
            Exception.__init__(self, "made-up refusal")
            self.status_code = 404

    web, _ = _web_with(monkeypatch, "failed", outputs=Refused())
    response = web.get("/download/999")
    text = _main_text(response)
    assert response.status_code == 404 and "Page not found" in text and NO_REPORT not in text
