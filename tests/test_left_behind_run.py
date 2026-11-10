"""A scan that ended tables_failed before the typed frame fix (docs/PLAN.md 17.7, Part A).

Its first checkpoint wrote findings and coverage rows, then the objects load failed, so its run ID has findings
and coverage rows, no objects rows, and only a "running" scan_runs row. Civis shows the run as failed (exit
code 3). The app must list it as Failed, never treat it as the current scan, and show every page. Made-up rows.
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
    run = "Run 2" if with_old_scan else "Run 1"
    assert re.search(rf"{run} [^R]*? Failed ", text)
    if with_old_scan:
        assert re.search(r"Run 1 [^R]*? Completed ", text)


def test_the_overview_keeps_the_completed_scan_as_current(monkeypatch):
    text = _main_text(_web(monkeypatch, True).get("/"))
    assert "The last scan failed before it finished. The results shown are from Run 1" in text
    assert "No completed scan yet" not in text and "No scan yet" not in text


def test_the_overview_with_only_the_left_behind_run(monkeypatch):
    text = _main_text(_web(monkeypatch, False).get("/"))
    assert "No completed scan yet" in text and "The last scan failed before it finished." in text
    assert "The results shown are from" not in text
