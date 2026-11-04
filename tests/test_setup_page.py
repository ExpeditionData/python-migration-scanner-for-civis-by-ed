"""The Setup page: ten read-only checks (docs/PLAN.md 17.2, Part C).

Made-up job, Service, users, groups, and credentials only. No Civis calls.
"""

from __future__ import annotations

import html
import logging
import re

import pytest

# The scanner image has no Flask, and importing any app module loads it. These tests run in the Service image.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import setup
from app.backends.base import StorageFacts
from app.job_settings import parse_job, unreadable
from common import release
from common.schema import TABLES
from tests.civis_fakes import ROBOT, FakeAPIError, app_service, scanner_job, shares

ALL_TABLES = frozenset(t.name for t in TABLES)
OTHER = {"id": 12, "name": "Example Owner Casey", "username": "example_owner_casey"}


class Source:
    """A made-up setup source. ``fail`` maps a method name to an exception to raise."""

    def __init__(self, services=None, service=None, credential=None, storage=None, fail=None, me=None):
        self._services = [app_service()] if services is None else services
        self._service = service or app_service()
        self._credential = credential or {"id": 41, "username": "example_robot_db", "user": dict(ROBOT)}
        self._storage = storage or StorageFacts(schema_exists=True, tables=ALL_TABLES, readable=True)
        self._fail = fail or {}
        self._me = me or dict(ROBOT)

    def _check(self, name):
        if name in self._fail:
            raise self._fail[name]

    def me(self):
        self._check("me")
        return self._me

    def credential(self, credential_id):
        self._check("credential")
        return self._credential

    def job_shares(self):
        self._check("job_shares")
        return shares([(21, "Example Admin One"), (22, "Example Admin Two")], [(61, "Example Admins")])

    def services(self):
        self._check("services")
        return self._services

    def service(self, service_id):
        self._check("service")
        return self._service

    def service_shares(self, service_id):
        return shares([], [])

    def storage(self, settings):
        return self._storage


def lines_for(job=None, source=None, *, report_only=False, latest="succeeded", env=None, settings=None):
    settings = settings if settings is not None else parse_job(job if job is not None else scanner_job())
    facts = setup.gather(source if source is not None else Source(), 1234, settings, report_only=report_only,
                         latest=latest, env=env or {})
    return {line.key: line for line in setup.evaluate(facts)}


def test_a_setup_that_matches_the_readme_passes_every_line_but_sharing():
    lines = lines_for()
    assert [line.number for line in lines.values()] == list(range(1, 11))
    assert tuple(lines) == setup.LINE_KEYS
    assert {k: v.result for k, v in lines.items() if v.result != "pass"} == {"sharing": "review"}


def test_the_pinned_images_come_from_the_requirements_files():
    assert release.pinned_image("scanner") == ("civisanalytics/datascience-python", "9.1.1")
    assert release.pinned_image("service") == ("civisanalytics/civis-services-flask", "0.1.0")
    readme = (release.ROOT / "README.md").read_text(encoding="utf-8")
    for part in ("scanner", "service"):
        name, tag = release.pinned_image(part)
        assert f"`{name}:{tag}`" in readme
    assert release.REPOSITORY_URL in readme and f"`{release.SCANNER_COMMAND}`" in readme


@pytest.mark.parametrize("changes, result, words", [
    ({"docker_image_tag": "latest"}, "fail", "Docker Image Tag is not 9.1.1"),
    ({"docker_image_name": "example/other"}, "fail", "Docker Image Name is not"),
    ({"repo_http_uri": "https://github.com/example-org/fork.git"}, "fail", "not this repository"),
    ({"repo_http_uri": "github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed"}, "pass", ""),
    ({"repo_ref": "main"}, "fail", "not a release tag"),
    ({"repo_ref": "feature-branch"}, "fail", "not a release tag"),
    ({"repo_ref": ""}, "fail", "not a release tag"),
    ({"repo_ref": "v-test"}, "warning", "starts with v but is not a release tag"),
    ({"repo_ref": "v1.2.0"}, "pass", ""),
    ({"docker_command": "python -m scanner"}, "fail", "Command is not"),
    ({"docker_command": "cd /app  &&  python -m scanner"}, "pass", ""),
    ({"required_resources": {"cpu": 500, "memory": 4096, "disk_space": 4}}, "fail", "CPU is below 1000"),
    ({"required_resources": {"cpu": 2000, "memory": 8192, "disk_space": 10}}, "warning", "Memory is larger than"),
    ({"required_resources": {"cpu": 1000, "memory": 4096, "disk_space": 2}}, "fail", "Disk Space is below 4"),
    ({"required_resources": None}, "fail", "CPU is below"),
])
def test_job_settings_line(changes, result, words):
    line = lines_for(scanner_job(**changes))["job_settings"]
    assert line.result == result and words in line.reason


def test_more_threads_need_more_memory():
    job = scanner_job()
    job["arguments"]["MAX_CONCURRENCY"] = "8"
    job["params"].append({"name": "MAX_CONCURRENCY", "type": "integer"})
    line = lines_for(job)["job_settings"]
    assert line.result == "fail" and "Memory is below 6144 MB" in line.reason


def test_git_credential_line():
    assert lines_for()["job_git_credential"].result == "pass"
    assert lines_for(scanner_job(git_credential_id=77))["job_git_credential"].result == "warning"


@pytest.mark.parametrize("change, result, words", [
    (lambda j: j["params"].pop(0), "fail", "RESULTS_DB is missing"),
    (lambda j: j["params"][0].update(type="string"), "fail", "RESULTS_DB is not the Database type"),
    (lambda j: j["arguments"].pop("RESULTS_DB"), "fail", "RESULTS_DB has no value"),
    (lambda j: j["arguments"].update(RESULTS_DB="example-database"), "fail", "shape of the RESULTS_DB value was not recognized"),
    (lambda j: j["arguments"].update(RESULTS_SCHEMA="bad schema"), "fail", "RESULTS_SCHEMA is not a plain schema name"),
    (lambda j: j["arguments"].pop("RESULTS_SCHEMA"), "fail", "RESULTS_SCHEMA has no value"),
    (lambda j: j["params"].append({"name": "GITHUB", "type": "string"}), "warning", "not the Custom Credential type"),
    (lambda j: j["params"].append({"name": "GITHUB", "type": "credential_custom"}), "pass", ""),
    (lambda j: j["params"].append({"name": "LOOKBAK_DAYS", "type": "integer"}), "warning", "1 parameter name not in the README"),
    (lambda j: (j["params"].append({"name": "RUN_ID", "type": "string"}), j["arguments"].update(RUN_ID="run-x")),
     "warning", "RUN_ID has a value"),
])
def test_parameters_line(change, result, words):
    job = scanner_job()
    change(job)
    line = lines_for(job)["job_parameters"]
    assert line.result == result and words in line.reason


def test_parameters_line_names_unknown_parameters_and_the_shape_in_details_only():
    job = scanner_job()
    job["params"].append({"name": "LOOKBAK_DAYS", "type": "integer"})
    line = lines_for(job)["job_parameters"]
    assert "LOOKBAK_DAYS" not in line.reason
    assert "Parameter names not in the README: LOOKBAK_DAYS" in line.details
    assert "RESULTS_DB value shape: object" in line.details


@pytest.mark.parametrize("job_changes, source, result, roles", [
    ({}, Source(), "pass", ""),
    ({"author": dict(OTHER)}, Source(), "fail", "job owner"),
    ({"running_as": dict(OTHER)}, Source(), "fail", "job runs as"),
    ({}, Source(credential={"id": 41, "user": dict(OTHER)}), "fail", "credential owner"),
    ({}, Source(credential={"id": 41, "owner": "example_robot"}), "pass", ""),  # Compared by user name.
    ({}, Source(credential={"id": 41, "owner": "someone_else"}), "fail", "credential owner"),
    ({"author": {"username": "example_robot"}}, Source(), "pass", ""),  # No ID on one side: by user name.
    ({}, Source(fail={"credential": FakeAPIError(403)}), "review", "error type: FakeAPIError status=403"),
    ({}, Source(fail={"me": FakeAPIError(500)}), "review", "The app's own user could not be read"),
])
def test_one_owner_line_names_roles_never_users(job_changes, source, result, roles):
    line = lines_for(scanner_job(**job_changes), source)["one_owner"]
    assert line.result == result and roles in line.reason
    assert "Example" not in line.reason and "example_" not in line.reason


def test_one_owner_without_a_credential_in_the_parameter():
    job = scanner_job(arguments={"RESULTS_DB": {"database": 31}, "RESULTS_SCHEMA": "migration_results"})
    line = lines_for(job)["one_owner"]
    assert line.result == "review" and "no credential in RESULTS_DB" in line.reason


def test_sharing_line_counts_users_and_groups_and_lists_names_in_details():
    line = lines_for()["sharing"]
    assert line.result == "review"
    assert "Scanner job: 3 users and 1 group, including the owner" in line.reason
    assert "Service: 1 user and 0 groups" in line.reason
    assert "Example" not in line.reason
    assert "Scanner job users: Example Admin One, Example Admin Two, Example Robot" in line.details
    assert setup.sharing_counts(setup.gather(Source(), 1, parse_job(scanner_job()), report_only=False,
                                             latest="none", env={})) == {"job": (3, 1), "service": (1, 0)}


@pytest.mark.parametrize("source, env, result, words, method", [
    (Source(), {}, "pass", "Same release tag", "single_match"),
    (Source(service=app_service(git_repo_ref="v0.1.0-rc2")), {}, "fail", "Branch is not the scanner job's release tag", "single_match"),
    (Source(service=app_service(docker_image_tag="latest")), {}, "fail", "image is not", "single_match"),
    (Source(service=app_service(credentials=[{"id": 9, "name": "x"}])), {}, "fail", "1 credential attached", "single_match"),
    (Source(), {"EXAMPLE_PASSWORD": "x", "OTHER_password": "y"}, "fail", "2 environment variables", "single_match"),
    (Source(services=[app_service(), app_service(id=502)]), {}, "review", "2 Services use this repository", "several_matches"),
    (Source(services=[app_service(git_repo_url="https://github.com/example-org/other.git")]), {}, "review",
     "No Service", "none"),
    (Source(services=[]), {"A_PASSWORD": "x"}, "fail", "1 environment variable in the app end in _PASSWORD", "none"),
    (Source(fail={"services": FakeAPIError(403)}), {}, "review", "could not be listed", "list_failed"),
    (Source(fail={"service": FakeAPIError(404)}), {}, "review", "error type: FakeAPIError status=404", "single_match"),
])
def test_service_line(source, env, result, words, method):
    settings = parse_job(scanner_job())
    facts = setup.gather(source, 1234, settings, report_only=False, latest="none", env=env)
    line = {l.key: l for l in setup.evaluate(facts)}["service"]
    assert facts.service_method == method and method in setup.SERVICE_METHODS
    assert line.result == result and words in line.reason
    assert "EXAMPLE_PASSWORD" not in line.reason


def test_password_count_never_holds_names_or_values():
    assert setup.password_env_count({"A_PASSWORD": "secret-value", "B": "x", "c_Password": "y"}) == 2  # pragma: allowlist secret


def test_frame_rule_line():
    assert lines_for()["frame_rule"].result == "pass"
    line = lines_for(report_only=True)["frame_rule"]
    assert line.result == "warning" and "README \"Create the Service\", step 10" in line.fix


@pytest.mark.parametrize("storage, result, words", [
    (StorageFacts(schema_exists=False), "fail", "does not exist"),
    (StorageFacts(schema_exists=True, tables=frozenset({"scan_runs"})), "warning", "4 of the 5 tables"),
    (StorageFacts(schema_exists=True, tables=ALL_TABLES, readable=False, error="FakeAPIError status=403", step="read"),
     "fail", "step read (error type: FakeAPIError status=403)"),
    (StorageFacts(error="FakeAPIError status=500", step="schema"), "fail", "step schema"),
    (StorageFacts(schema_exists=True, tables=ALL_TABLES, readable=True), "pass", "can read them"),
])
def test_results_storage_line(storage, result, words):
    line = lines_for(source=Source(storage=storage))["results_storage"]
    assert line.result == result and words in line.reason


@pytest.mark.parametrize("latest, result", [
    ("none", "review"), ("queued", "review"), ("running", "review"), ("succeeded", "pass"), ("failed", "fail"),
    ("cancelled", "warning"), ("tables_failed", "fail"), ("anything else", "review"),
])
def test_latest_scan_line(latest, result):
    assert lines_for(latest=latest)["latest_scan"].result == result


def test_an_unreadable_job_marks_the_job_lines_not_checked():
    lines = lines_for(settings=unreadable("FakeAPIError status=404"))
    assert lines["job_found"].result == "fail" and "FakeAPIError status=404" in lines["job_found"].reason
    for key in ("job_settings", "job_git_credential", "job_parameters", "one_owner", "results_storage"):
        assert lines[key].result == "review"
    assert lines["service"].result == "review"  # The job's tag cannot be compared.


def test_no_job_id_still_checks_the_service():
    facts = setup.gather(Source(), None, None, report_only=False, latest="none", env={})
    lines = {l.key: l for l in setup.evaluate(facts)}
    assert lines["job_found"].result == "fail" and "SCANNER_JOB_ID is not set" in lines["job_found"].reason
    assert facts.service_method == "single_match"


# --- The page inside the app ------------------------------------------------------------------


@pytest.fixture
def civis_app(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings
    from tests.civis_fakes import FakeCivisClient, RecordingIO

    def build(client=None, io=None, **config):
        client = client or FakeCivisClient()
        io = io or RecordingIO({
            "pg_catalog.pg_namespace": [["nspname"], ["migration_results"]],
            "pg_catalog.pg_tables": [["tablename"]] + [[t.name] for t in TABLES],
            "COUNT(*)": [["table_name", "row_count"], ["scan_runs", "0"]],
        })
        services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
        app = create_app({"SECRET_KEY": "setup-page-test-secret-key-long-enough-1", "SERVICES": services, **config})  # pragma: allowlist secret  gitleaks:allow
        return app, client, io
    return build


READ_METHODS = {
    "scripts.get_containers", "scripts.list_containers_runs", "scripts.list_containers_runs_logs",
    "scripts.list_containers_shares", "users.list_me", "credentials.get", "services.list", "services.get",
    "services.list_shares",
}


def _text(body: str) -> str:
    main = body[body.index("<main"): body.index("</main>")]
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", main)))


def test_the_setup_page_reads_only_and_logs_nothing(civis_app, caplog):
    app, client, io = civis_app()
    with caplog.at_level(logging.DEBUG):
        response = app.test_client().get("/setup")
    assert response.status_code == 200
    assert set(client.methods_called()) <= READ_METHODS
    assert {"users.list_me", "credentials.get", "scripts.list_containers_shares", "services.list", "services.get",
            "services.list_shares"} <= set(client.methods_called())
    assert io.queries == [] and io.frames == []
    assert all(s.lstrip().upper().startswith("SELECT") for s in io.reads)
    assert caplog.records == []
    page = _text(response.get_data(as_text=True))
    assert "1 Scanner job found and readable job_found Pass" in page
    assert "9 Results storage: schema, five tables, and read access results_storage Pass" in page


def test_the_setup_page_reads_the_job_fresh(civis_app):
    app, client, _ = civis_app()
    app.test_client().get("/")
    before = client.methods_called().count("scripts.get_containers")
    app.test_client().get("/setup")
    assert client.methods_called().count("scripts.get_containers") == before + 1


def test_names_on_the_setup_page_are_escaped(civis_app):
    from tests.civis_fakes import FakeCivisClient

    hostile = "<script>alert('share')</script>"
    client = FakeCivisClient()
    client.handlers["scripts.list_containers_shares"] = lambda id: shares([(21, hostile)], [])
    app, _, _ = civis_app(client)
    body = app.test_client().get("/setup").get_data(as_text=True)
    assert hostile not in body and "&lt;script&gt;alert(&#39;share&#39;)&lt;/script&gt;" in body


def test_errors_on_the_setup_page_show_the_type_never_the_message(civis_app):
    from tests.civis_fakes import FakeCivisClient

    client = FakeCivisClient()
    client.handlers["users.list_me"] = lambda: FakeAPIError(500, "example_secret_detail")
    client.handlers["services.list"] = lambda **kw: FakeAPIError(403, "example_secret_detail")
    app, _, _ = civis_app(client)
    body = app.test_client().get("/setup").get_data(as_text=True)
    assert "example_secret_detail" not in body and "FakeAPIError status=500" in body


def test_report_only_is_a_warning_on_the_setup_page(civis_app):
    app, _, _ = civis_app(FRAME_ANCESTORS_REPORT_ONLY="true")
    page = _text(app.test_client().get("/setup").get_data(as_text=True))
    assert "8 Frame rule enforced frame_rule Warning" in page


def test_setup_is_in_the_navigation(civis_app):
    app, _, _ = civis_app()
    body = app.test_client().get("/").get_data(as_text=True)
    assert '<a href="/setup">Setup</a>' in body
    assert 'class="active" aria-current="page">Setup</a>' in app.test_client().get("/setup").get_data(as_text=True)


def test_every_preview_state_renders_the_setup_page(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.backends.local_backend import STATES
    from app.services import local_services
    from scanner.synthetic.preview import build_preview

    build_preview(tmp_path)
    results = {}
    for state in STATES:
        app = create_app({"SECRET_KEY": "setup-page-test-secret-key-long-enough-1",  # pragma: allowlist secret  gitleaks:allow
                          "SERVICES": local_services(tmp_path, state)})
        response = app.test_client().get("/setup")
        assert response.status_code == 200, state
        results[state] = _text(response.get_data(as_text=True))
    assert "1 Scanner job found and readable job_found Pass" in results["normal"]
    failures = results["setup-failures"]
    for words in ("job_settings Fail", "job_git_credential Warning", "job_parameters Warning", "one_owner Fail",
                  "2 Services use this repository"):
        assert words in failures
    assert "results_storage Fail The results schema does not exist." in results["schema-missing"]
    assert "job_found Fail SCANNER_JOB_ID is not set" in results["no-job-id"]
