"""The app reads its results database, credential, and schema from the scanner job (docs/PLAN.md 17.2, Part B).

Made-up job settings only. No Civis calls.
"""

from __future__ import annotations

import json

import pytest

# The scanner image has no Flask, and importing any app module loads it. These tests run in the Service image.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app.backends.base import error_token, plain
from app.job_settings import DATABASE_SHAPES, database_value, parse_job
from tests.civis_fakes import FakeAPIError, FakeCivisClient, FakeResponse, RecordingIO, scanner_job


@pytest.mark.parametrize("value, expected", [
    ({"database": 31, "credential": 41}, (31, 41, "object")),
    ({"database": "31", "credential": "41"}, (31, 41, "object")),
    ({"database": 31}, (31, None, "object_without_credential")),
    ({"database": 31, "credential": None}, (31, None, "object_without_credential")),
    (json.dumps({"database": 31, "credential": 41}), (31, 41, "json_text")),
    ({"database_id": 31, "credential_id": 41}, (31, 41, "object_id_keys")),
    ({"databaseId": 31, "credentialId": 41}, (31, 41, "object_id_keys")),
    (31, (31, None, "database_id_only")),
    ("31", (31, None, "database_id_only")),
    (None, (None, None, "missing")),
    ("", (None, None, "missing")),
    ({}, (None, None, "missing")),
    ("example-database-name", (None, None, "not_recognized")),
    ({"database": "example-database-name", "credential": 41}, (None, None, "not_recognized")),
    ({"database": 31, "credential": "not a number"}, (None, None, "not_recognized")),
    ({"name": "example"}, (None, None, "not_recognized")),
    ("{not json", (None, None, "not_recognized")),
    ("[31, 41]", (None, None, "not_recognized")),
    (True, (None, None, "not_recognized")),
    (-3, (None, None, "not_recognized")),
    ([31, 41], (None, None, "not_recognized")),
])
def test_database_parameter_shapes(value, expected):
    assert database_value(value) == expected
    assert expected[2] in DATABASE_SHAPES


def test_parse_job_reads_both_parameters():
    settings = parse_job(scanner_job())
    assert (settings.database_id, settings.credential_id, settings.schema) == (31, 41, "migration_results")
    assert settings.database_shape == "object" and settings.usable


def test_a_fixed_parameter_value_is_used_when_there_is_no_argument():
    job = scanner_job(arguments={}, params=[
        {"name": "RESULTS_DB", "type": "database", "value": json.dumps({"database": 7, "credential": 8})},
        {"name": "RESULTS_SCHEMA", "type": "string", "value": "fixed_schema"},
    ])
    settings = parse_job(job)
    assert (settings.database_id, settings.credential_id, settings.schema, settings.database_shape) == (
        7, 8, "fixed_schema", "json_text")


@pytest.mark.parametrize("arguments, problem", [
    ({"RESULTS_SCHEMA": "results"}, "database_missing"),
    ({"RESULTS_DB": "example-database-name", "RESULTS_SCHEMA": "results"}, "database_unrecognized"),
    ({"RESULTS_DB": {"database": 1, "credential": 2}}, "schema_missing"),
    ({"RESULTS_DB": {"database": 1, "credential": 2}, "RESULTS_SCHEMA": "results; DROP SCHEMA x"}, "schema_invalid"),
    ({"RESULTS_DB": {"database": 1, "credential": 2}, "RESULTS_SCHEMA": "Results Schema"}, "schema_invalid"),
])
def test_unusable_settings_name_the_problem_and_never_keep_a_bad_schema(arguments, problem):
    settings = parse_job(scanner_job(arguments=arguments))
    assert settings.problem == problem and not settings.usable
    assert settings.schema is None or settings.schema == "results"


def test_plain_keeps_the_case_of_argument_keys():
    response = FakeResponse({"arguments": {"RESULTS_DB": FakeResponse({"database": 1})}, "repo_ref": "v1"})
    assert plain(response) == {"arguments": {"RESULTS_DB": {"database": 1}}, "repo_ref": "v1"}
    assert plain([response])[0]["repo_ref"] == "v1"


def test_error_token_is_the_type_and_status_only():
    assert error_token(FakeAPIError(403, "example_schema owned by example_user")) == "FakeAPIError status=403"
    assert error_token(ValueError("example_schema")) == "ValueError"
    assert error_token(FakeAPIError(True)) == "FakeAPIError"


def test_civis_jobs_read_the_settings_from_get_containers():
    from app.backends.civis_backend import CivisJobs

    client = FakeCivisClient()
    settings = CivisJobs(client, 1234).settings()
    assert settings.database_id == 31 and settings.schema == "migration_results"
    assert client.calls == [("scripts.get_containers", (1234,), {})]


def test_an_unreadable_job_records_only_the_error_type():
    from app.backends.civis_backend import CivisJobs

    settings = CivisJobs(FakeCivisClient(FakeAPIError(404, "no job named example")), 1234).settings()
    assert settings.problem == "job_unreadable" and settings.error_type == "FakeAPIError status=404"
    assert "example" not in repr(settings)


# --- Inside the app ----------------------------------------------------------------------


@pytest.fixture
def civis_app(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    from app import create_app
    from app.services import civis_services
    from app.settings import Settings

    def build(client, io=None, now=None):
        io = io or RecordingIO({
            "pg_catalog.pg_namespace": [["nspname"], ["migration_results"]],
            "pg_catalog.pg_tables": [["tablename"], ["scan_runs"]],
        })
        services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
        if now is not None:
            services.clock = now
        app = create_app({"SECRET_KEY": "job-settings-test-secret-key-long-enough-01", "SERVICES": services})  # pragma: allowlist secret  gitleaks:allow
        return app, services, io
    return build


def test_the_app_reads_results_with_the_job_database_id_and_credential(civis_app):
    client = FakeCivisClient()
    app, _, io = civis_app(client)
    assert app.test_client().get("/").status_code == 200
    assert io.reads and set(io.databases) == {31} and set(io.credentials) == {41}
    assert all("migration_results" in s for s in io.reads if "pg_catalog" not in s)


def test_job_settings_are_cached_for_five_minutes_and_read_fresh_on_request(civis_app):
    client = FakeCivisClient()
    now = {"t": 0.0}
    app, services, _ = civis_app(client, now=lambda: now["t"])
    with app.test_request_context("/"):
        services.job_settings()
        services.job_settings()
        assert client.methods_called().count("scripts.get_containers") == 1
        now["t"] = 299.0
        services.job_settings()
        assert client.methods_called().count("scripts.get_containers") == 1
        now["t"] = 301.0
        services.job_settings()
        assert client.methods_called().count("scripts.get_containers") == 2
        services.job_settings(fresh=True)
        assert client.methods_called().count("scripts.get_containers") == 3


def test_a_failed_job_read_is_retried_sooner(civis_app):
    client = FakeCivisClient(FakeAPIError(503))
    now = {"t": 0.0}
    app, services, _ = civis_app(client, now=lambda: now["t"])
    with app.test_request_context("/"):
        assert services.job_settings().problem == "job_unreadable"
        now["t"] = 21.0
        services.job_settings()
        assert client.methods_called().count("scripts.get_containers") == 2


def test_new_job_settings_clear_the_results_cache(civis_app):
    job = scanner_job()
    client = FakeCivisClient()
    client.handlers["scripts.get_containers"] = lambda id: job
    app, services, _ = civis_app(client)
    with app.test_request_context("/"):
        services.job_settings()
        services.results._short["check"] = (0.0, "ok")
        services.job_settings(fresh=True)
        assert "check" in services.results._short  # Same settings: the cache stays.
        job["arguments"] = {"RESULTS_DB": {"database": 32, "credential": 41}, "RESULTS_SCHEMA": "migration_results"}
        services.job_settings(fresh=True)
        assert "check" not in services.results._short


@pytest.mark.parametrize("job, expected", [
    (FakeAPIError(404), "The scanner job could not be read"),
    (scanner_job(arguments={"RESULTS_SCHEMA": "migration_results"}), "The results database is not set"),
    (scanner_job(arguments={"RESULTS_DB": "example-database-name", "RESULTS_SCHEMA": "s"}),
     "The scanner job's database setting could not be read"),
    (scanner_job(arguments={"RESULTS_DB": {"database": 1, "credential": 2}}), "The results schema is not set"),
    (scanner_job(arguments={"RESULTS_DB": {"database": 1, "credential": 2}, "RESULTS_SCHEMA": "a b"}),
     "The results schema name is not valid"),
])
def test_unusable_job_settings_show_a_configuration_message(civis_app, job, expected):
    import html
    import re

    app, _, io = civis_app(FakeCivisClient(job))
    body = app.test_client().get("/").get_data(as_text=True)
    assert expected in html.unescape(re.sub(r"\s+", " ", body))
    assert io.statements == []  # No SQL without usable settings.


def test_an_unreadable_job_turns_off_run_scan(civis_app):
    app, _, _ = civis_app(FakeCivisClient(FakeAPIError(403)))
    body = app.test_client().get("/").get_data(as_text=True)
    assert "Run scan is turned off because the scanner job could not be read" in body
    assert 'disabled aria-describedby="run-scan-off">Run scan</button>' in body
