"""Run scan starts only this tool's scanner job (docs/PLAN.md 17.3, Part A).

If SCANNER_JOB_ID named one of the organization's own container scripts, Run scan would run it, and that job
could change their data. The app reads the job fresh inside the POST, under the start lock, and starts it only
if its repository, command, and image are this tool's. Made-up jobs only. No Civis calls.
"""

from __future__ import annotations

import html
import re

import pytest

pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import create_app
from app.security import generate_csrf_token
from app.services import civis_services
from app.settings import Settings
from common import release
from tests.civis_fakes import FakeAPIError, FakeCivisClient, RecordingIO, scanner_job

SECRET = "start-guard-test-secret-key-long-enough-01"  # pragma: allowlist secret  gitleaks:allow
NOT_THE_SCANNER = [
    {"repo_http_uri": "https://github.com/example-org/etl-jobs.git"},
    {"docker_command": "python export.py"},
    {"docker_command": "cd /app && python -m scanner && python export.py"},
    {"docker_command": "cd /app  &&  python -m scanner"},
    {"docker_image_name": "example-org/custom-image"},
    {"repo_http_uri": None},
]


@pytest.fixture(autouse=True)
def _no_debug(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)


def build(job):
    client = FakeCivisClient(job)
    client.handlers["scripts.post_containers_runs"] = lambda id: {"id": 900, "state": "queued"}
    io = RecordingIO()
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    return create_app({"SECRET_KEY": SECRET, "SERVICES": services}), client, io


def start(app):
    with app.test_request_context("/"):
        token = generate_csrf_token(app)
    return app.test_client().post("/scan/start", data={"csrf_token": token})


def text(response) -> str:
    body = response.get_data(as_text=True)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body[body.index("<main"): body.index("</main>")])))


# --- The identity check ------------------------------------------------------------------------


@pytest.mark.parametrize("changes, expected", [
    ({}, ()),
    ({"repo_http_uri": "github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed/"}, ()),
    ({"repo_http_uri": "https://github.com/expeditiondata/python-migration-scanner-for-civis-by-ed"}, ()),
    ({"docker_command": "  cd /app && python -m scanner\n"}, ()),
    ({"repo_http_uri": "https://github.com/example-org/etl-jobs.git"}, ("repository",)),
    ({"docker_command": "python export.py"}, ("command",)),
    ({"docker_image_name": "example-org/custom-image"}, ("image",)),
    ({"docker_image_tag": "latest", "repo_ref": "main", "required_resources": None}, ()),  # Setup checks only.
])
def test_scanner_job_mismatches(changes, expected):
    assert release.scanner_job_mismatches(scanner_job(**changes)) == expected


def test_anything_but_a_job_is_not_the_scanner():
    assert release.scanner_job_mismatches(None) == ("repository", "command", "image")


# --- The start path ----------------------------------------------------------------------------


@pytest.mark.parametrize("changes", NOT_THE_SCANNER)
def test_another_job_is_never_started(changes):
    app, client, io = build(scanner_job(**changes))
    response = start(app)
    assert response.headers["Location"].endswith("/scan?notice=not-scanner")
    assert "scripts.post_containers_runs" not in client.methods_called()
    page = text(app.test_client().get("/scan?notice=not-scanner"))
    assert "The scan was not started, because SCANNER_JOB_ID does not point to this tool's scanner job" in page


def test_the_scanner_job_still_starts():
    app, client, _ = build(scanner_job())
    assert start(app).headers["Location"].endswith("/scan?notice=started")
    assert client.methods_called().count("scripts.post_containers_runs") == 1


def test_a_read_failure_starts_nothing():
    app, client, _ = build(FakeAPIError(503))
    assert start(app).headers["Location"].endswith("/scan?notice=start-failed")
    assert "scripts.post_containers_runs" not in client.methods_called()


def test_the_post_reads_the_job_fresh_even_when_the_cache_says_it_is_the_scanner():
    job = scanner_job()
    app, client, _ = build(job)
    client.handlers["scripts.get_containers"] = lambda id: job
    app.test_client().get("/")  # Caches the scanner job's settings.
    job["docker_command"] = "python export.py"  # SCANNER_JOB_ID now names another job.
    start(app)
    assert "scripts.post_containers_runs" not in client.methods_called()


# --- Nothing else uses another job -------------------------------------------------------------


def test_another_job_turns_run_scan_off_and_is_never_read_or_used():
    app, client, io = build(scanner_job(docker_command="python export.py"))
    web = app.test_client()
    body = web.get("/").get_data(as_text=True)
    page = text(web.get("/"))
    assert 'disabled aria-describedby="run-scan-off">Run scan</button>' in body
    assert "Run scan is turned off because SCANNER_JOB_ID does not point to this tool's scanner job" in page
    assert "See lines 1 and 2 on the Setup page" in page
    with app.test_request_context("/"):
        token = generate_csrf_token(app)
    web.post("/setup/storage", data={"csrf_token": token})
    assert web.get("/download/700").status_code == 404
    setup_page = text(web.get("/setup"))
    assert "1 Scanner job found and readable job_found Fail Job 1234 is not this tool's scanner job: its command" \
        in setup_page
    assert "2 Scanner job settings match the README job_settings Fail Command is not" in setup_page
    assert "latest_scan Review The newest scan is not known" in setup_page
    assert io.statements == []  # No results read and nothing created with that job's settings.
    called = set(client.methods_called())
    assert not called & {"scripts.list_containers_runs", "scripts.list_containers_runs_logs",
                         "scripts.list_containers_runs_outputs", "scripts.post_containers_runs"}


def test_preview_state_for_another_job(tmp_path):
    from app.services import local_services
    from scanner.synthetic.preview import build_preview

    build_preview(tmp_path)
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": local_services(tmp_path, "not-scanner-job")})
    page = text(app.test_client().get("/"))
    assert "Run scan is turned off because SCANNER_JOB_ID does not point to this tool's scanner job" in page
    assert "A scan is running" not in page
    assert start(app).headers["Location"].endswith("/scan?notice=not-scanner")
