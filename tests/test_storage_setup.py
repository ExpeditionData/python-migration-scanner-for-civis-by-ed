"""The "Set up results storage" button (docs/PLAN.md 17.2, Part A).

Made-up job settings, credentials, and SQL errors only. No Civis calls.
"""

from __future__ import annotations

import html
import re

import pytest

pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import create_app
from app.security import generate_csrf_token
from app.services import civis_services, local_services
from app.settings import Settings
from common import sql
from common.schema import TABLES, admin_schema_sql
from scanner.synthetic.preview import build_preview
from tests.civis_fakes import FakeAPIError, FakeCivisClient, RecordingIO, scanner_job

SECRET = "storage-setup-test-secret-key-long-enough-1"  # pragma: allowlist secret  gitleaks:allow
SCHEMA = "migration_results"
SQL_ERROR_TEXT = "permission denied for database example_secret_db owned by example_person"
SCHEMA_ROWS = {"information_schema.schemata": [["schema_name"], [SCHEMA]]}


def build(client=None, io=None):
    client = client or FakeCivisClient()
    io = io if io is not None else RecordingIO()
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": services})
    return app, client, io


def page_text(response) -> str:
    body = response.get_data(as_text=True)
    main = body[body.index("<main"): body.index("</main>")]
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", main)))


def post(app, url="/setup/storage"):
    with app.test_request_context("/"):
        token = generate_csrf_token(app)
    return app.test_client().post(url, data={"csrf_token": token})


@pytest.fixture(autouse=True)
def _no_debug(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)


# --- Statements ------------------------------------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("example_robot_db", "example_robot_db"),
    ("Example-Robot", '"Example-Robot"'),
    ('odd"name', '"odd""name"'),
    ("robot@example", '"robot@example"'),
    ("back\\slash", None),
    ("line\nbreak", None),
    ("", None),
    (None, None),
    ("x" * 200, None),
])
def test_database_user_is_validated_or_quoted(name, expected):
    assert sql.quoted_identifier(name) == expected


def test_admin_statement_uses_a_placeholder_when_the_user_cannot_be_written_safely():
    assert admin_schema_sql(SCHEMA, "example_robot_db") == f"CREATE SCHEMA {SCHEMA} AUTHORIZATION example_robot_db;"
    assert admin_schema_sql(SCHEMA, "a\\b") == f"CREATE SCHEMA {SCHEMA} AUTHORIZATION <database user>;"
    with pytest.raises(sql.UnsafeValueError):
        admin_schema_sql("bad schema", "example_robot_db")


# --- Inside Civis ----------------------------------------------------------------------------


def test_nothing_runs_on_a_get_and_the_button_is_a_csrf_post():
    app, _, io = build()
    for url in ("/", "/scan", "/inventory", "/findings", "/setup/storage"):
        app.test_client().get(url)
    assert io.queries == [] and all(s.lstrip().upper().startswith("SELECT") for s in io.reads)
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'action="/setup/storage"' in body and 'name="csrf_token"' in body and "Set up results storage" in body
    assert app.test_client().post("/setup/storage").status_code == 400
    assert io.queries == []


def test_missing_schema_is_created_then_the_five_tables():
    app, client, io = build()
    page = page_text(post(app))
    assert io.queries == [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"] + [t.create_sql(SCHEMA) for t in TABLES]
    assert set(io.databases) == {31} and set(io.credentials) == {41}  # From the job's RESULTS_DB parameter.
    assert "Results storage is set up. The results schema and its five tables are ready." in page
    assert "scripts.get_containers" in client.methods_called()


def test_an_existing_schema_is_not_created_again():
    """The database may refuse CREATE SCHEMA, even IF NOT EXISTS, to a user who cannot create schemas,
    after a database admin created the schema for that user. So the app checks first."""
    app, _, io = build(io=RecordingIO(SCHEMA_ROWS))
    page = page_text(post(app))
    assert io.queries == [t.create_sql(SCHEMA) for t in TABLES]
    assert "The results schema already existed, and its five tables are ready." in page


def test_a_refused_schema_shows_the_statement_for_a_database_admin():
    app, client, io = build(io=RecordingIO(fail={"CREATE SCHEMA": FakeAPIError(403, SQL_ERROR_TEXT)}))
    response = post(app)
    page = page_text(response)
    assert io.queries == [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"]  # No table statements after a failure.
    assert "(step: schema)" in page and "cannot create schemas" in page
    assert f"CREATE SCHEMA {SCHEMA} AUTHORIZATION example_robot_db;" in page
    assert "Error type: FakeAPIError status=403" in page
    assert SQL_ERROR_TEXT not in response.get_data(as_text=True) and "example_secret_db" not in page
    assert ("credentials.get", (41,), {}) in client.calls
    assert "Replace <database user>" not in page


@pytest.mark.parametrize("username", ["back\\slash", "tab\tname", None])
def test_a_user_name_that_cannot_be_shown_safely_becomes_a_placeholder(username):
    client = FakeCivisClient()
    client.handlers["credentials.get"] = lambda id: {"id": id, "username": username}
    app, _, _ = build(client, RecordingIO(fail={"CREATE SCHEMA": FakeAPIError(403)}))
    page = page_text(post(app))
    assert f"CREATE SCHEMA {SCHEMA} AUTHORIZATION <database user>;" in page
    assert "Replace <database user> with the user name saved on the database credential" in page


def test_without_a_credential_in_the_parameter_the_user_is_a_placeholder():
    job = scanner_job(arguments={"RESULTS_DB": {"database": 31}, "RESULTS_SCHEMA": SCHEMA})
    client = FakeCivisClient(job)
    app, _, _ = build(client, RecordingIO(fail={"CREATE SCHEMA": FakeAPIError(403)}))
    page = page_text(post(app))
    assert "AUTHORIZATION <database user>;" in page
    assert "credentials.get" not in client.methods_called()


def test_a_failed_table_shows_the_step_and_error_type_only():
    io = RecordingIO(SCHEMA_ROWS, fail={"CREATE TABLE IF NOT EXISTS migration_results.findings":
                                        FakeAPIError(500, SQL_ERROR_TEXT)})
    app, _, _ = build(io=io)
    response = post(app)
    page = page_text(response)
    assert "(step: tables)" in page and "Error type: FakeAPIError status=500" in page
    assert SQL_ERROR_TEXT not in response.get_data(as_text=True)
    assert len(io.queries) == 3  # scan_runs, objects, then findings failed.


def test_unusable_job_settings_create_nothing():
    app, _, io = build(FakeCivisClient(FakeAPIError(404)))
    page = page_text(post(app))
    assert "Nothing was created" in page and io.queries == []


def test_the_button_reads_the_job_settings_fresh_and_clears_the_results_cache():
    app, client, io = build()
    app.test_client().get("/")
    reads_before = len(io.reads)
    gets_before = client.methods_called().count("scripts.get_containers")
    post(app)
    assert client.methods_called().count("scripts.get_containers") == gets_before + 1
    assert len(io.reads) > reads_before + 1  # The schema check ran again after setup, not from the cache.


def test_run_scan_stays_off_while_the_schema_is_missing():
    app, _, _ = build()
    response = app.test_client().get("/")
    body = response.get_data(as_text=True)
    assert 'disabled aria-describedby="run-scan-off">Run scan</button>' in body
    assert "Run scan is turned off because the results schema does not exist yet. Click Set up results storage" \
        in page_text(response)


def test_with_no_tables_run_scan_stays_on_and_the_button_shows():
    app, _, _ = build(io=RecordingIO(SCHEMA_ROWS))
    body = app.test_client().get("/").get_data(as_text=True)
    assert "Set up results storage" in body and ">Run scan</button>" in body and "run-scan-off" not in body


# --- Local preview ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def preview(tmp_path_factory):
    folder = tmp_path_factory.mktemp("storage-preview")
    build_preview(folder)
    return folder


@pytest.mark.parametrize("state, after", [
    ("schema-missing", "Results storage is set up. The results schema and its five tables are ready."),
    ("tables-missing", "The results schema already existed, and its five tables are ready."),
    ("schema-refused", "CREATE SCHEMA dry_run_results AUTHORIZATION example_robot_db;"),
])
def test_preview_storage_states(preview, state, after):
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": local_services(preview, state)})
    before = page_text(app.test_client().get("/"))
    assert "Set up results storage" in before
    page = page_text(post(app))
    assert after in page
    if state != "schema-refused":
        assert "No scan yet" in page and "Set up results storage" not in page.replace(after, "")
