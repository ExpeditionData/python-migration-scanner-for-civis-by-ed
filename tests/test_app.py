"""Web app route tests on the made-up preview data. No Civis calls, no network.

Covers every page and API, CSRF on every POST, escaping of hostile object
names, security headers, the concurrent-run guard, the batched checklist save,
the empty and error states, the Civis data layer (civis 2.7.1 calls only),
and local preview mode.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The scanner image has no Flask. These tests run in the Service image instead.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import create_app
from app import local as local_mode
from app import views
from app.backends.base import JobRun, LogLine, Progress, ResultsUnavailable, normalize_row
from app.backends.cache import CachedResults
from app.backends.civis_backend import CivisJobs, CivisResults
from app.security import generate_csrf_token
from app.services import Services, local_services
from app.settings import Settings
from common.disclaimer import NON_AFFILIATION
from common.rule_loader import load_rules
from scanner.synthetic.preview import HOSTILE_NAME, build_preview
from tests.civis_fakes import civis_read_result, no_output

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
SECRET = "app-test-secret-key-that-is-long-enough-01"  # pragma: allowlist secret  gitleaks:allow
PAGES = ["/", "/scan", "/inventory", "/findings"]


@pytest.fixture(scope="module")
def preview(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("preview")
    build_preview(folder, now=NOW)
    return folder


def make_app(preview: Path, state: str = "normal", monkeypatch=None):
    services = local_services(preview, state)
    return create_app({"SECRET_KEY": SECRET, "SERVICES": services, "TESTING": True})


@pytest.fixture
def app(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    return make_app(preview)


def csrf(app, user_id: str = "anonymous") -> str:
    headers = {"Civis-User-Id": user_id} if user_id != "anonymous" else {}
    with app.test_request_context("/", headers=headers):
        return generate_csrf_token(app)


def text_of(response) -> str:
    body = response.get_data(as_text=True)
    main = body[body.index("<main"): body.index("</main>")]
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", main)))


# --- Every page --------------------------------------------------------------------------


@pytest.mark.parametrize("url, heading, nav", [
    ("/", "Overview", "Overview"),
    ("/scan", "Scan in progress", "Scan history"),
    ("/inventory", "Inventory", "Inventory"),
    ("/findings", "Findings and checklist", "Findings"),
])
def test_pages_render_with_navigation_banner_and_footer(app, url, heading, nav):
    response = app.test_client().get(url)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f"<h1>{heading}</h1>" in body
    assert f'class="active" aria-current="page">{nav}</a>' in body
    assert "It is not a complete audit." in body and "For Civis admins only." in body
    assert "Designed to run inside your Civis environment." in body
    assert "No data leaves Civis" not in body
    assert 'href="#main"' in body  # Skip link for keyboard users.


@pytest.mark.parametrize("url", PAGES + ["/api/scan-status", "/healthz", "/static/css/app.css", "/no-such-page"])
def test_security_headers_on_every_response(app, url):
    response = app.test_client().get(url)
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe-inline" not in csp
    assert "frame-ancestors 'self' https://platform.civisanalytics.com" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Frame-Options" not in response.headers
    assert "Set-Cookie" not in response.headers


def test_pages_have_no_inline_scripts_or_styles(app):
    for url in PAGES:
        body = app.test_client().get(url).get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", body)
        assert "<style" not in body and ' style="' not in body
        assert "https://fonts." not in body and "cdn" not in body.lower()


def test_static_assets_are_vendored(app):
    client = app.test_client()
    for path in ("css/app.css", "js/scan-status.js", "js/findings.js", "js/local-time.js",
                 "fonts/carlito-latin-400-normal.woff2", "fonts/ibm-plex-mono-latin-400-normal.woff2",
                 "img/expedition-data-logo.svg"):
        assert client.get(f"/static/{path}").status_code == 200


def test_overview_numbers(app):
    page = text_of(app.test_client().get("/"))
    assert "Python objects found 96" in page
    assert "Checklist progress" in page and "Most common issues" in page
    assert "Last scan: October 7, 2026" in page and "Run 3." in page
    assert "A scan is running." in page


def test_overview_shows_the_users_note(app):
    """The preview follows the live test: the users list refuses "all", so the scan used the active list."""
    from scanner.inventory.users import USERS_ACTIVE_NOTE

    page = text_of(app.test_client().get("/"))
    assert USERS_ACTIVE_NOTE.format(status=" (HTTP 404)") in page


HIDDEN_SENTENCE = ("Hidden objects (workflow-created copies and Civis's own query and import jobs) "
                   "are not scanned or counted.")


def test_overview_says_hidden_objects_are_not_counted(app):
    page = text_of(app.test_client().get("/"))
    assert HIDDEN_SENTENCE in page
    assert "Hidden objects skipped" not in page  # New scans write NULL.


def test_hidden_count_shows_only_above_zero():
    rules = load_rules()
    summary = {"row_kind": "summary", "object_type": "python_script", "found_count": 1}
    assert views.overview([], [], [{**summary, "hidden_skipped_count": None}], [], rules)["hidden_skipped"] == 0
    assert views.overview([], [], [{**summary, "hidden_skipped_count": 0}], [], rules)["hidden_skipped"] == 0
    assert views.overview([], [], [{**summary, "hidden_skipped_count": 4}], [], rules)["hidden_skipped"] == 4


def test_users_note_comes_only_from_users_rows():
    rules = load_rules()
    coverage = [{"row_kind": "object", "reason": "Not a users note."},
                {"row_kind": "users", "reason": "Owner active status is not known."}]
    assert views.overview([], [], coverage, [], rules)["users_note"] == "Owner active status is not known."
    assert views.overview([], [], coverage[:1], [], rules)["users_note"] is None


def test_overview_counts_partly_scanned_objects(app):
    html = app.test_client().get("/").get_data(as_text=True)
    page = text_of(app.test_client().get("/"))
    assert "Partly scanned" in page and "more partly scanned. Review the not scanned and partly scanned objects manually." in page
    assert "/inventory?status=not_scanned" in html and "/inventory?status=partial" in html


def test_scan_page_progress_and_history(app):
    page = text_of(app.test_client().get("/scan"))
    assert "Scanning code and logs: 40 of 96 objects" in page
    assert "1. Inventory" in page and "2. Code and log scan" in page and "3. Save results" in page
    for status in ("In progress", "Completed", "Failed"):
        assert status in page
    history = page[page.index("Fixed since last run"):]
    assert re.search(r"Run 3 .* Completed \d+ \d+ \d+", history)  # Fixed since Run 1 is a number.


def test_scan_status_api(app):
    response = app.test_client().get("/api/scan-status")
    data = response.get_json()
    assert response.mimetype == "application/json"
    assert data["state"] == "running" and data["percent"] == 41 and data["can_start"] is False
    assert [s["state"] for s in data["steps"]] == ["Done", "Running", "Waiting"]


def test_inventory_filters_reasons_and_workflows(app):
    client = app.test_client()
    everything = text_of(client.get("/inventory"))
    assert "Showing 1 to 50 of 96 objects" in everything
    partial = text_of(client.get("/inventory?status=partial"))
    assert "Scanned the code preview instead." in partial
    # The preview uses the normal setup, with no GitHub token.
    assert "Only the script's own file was checked, read through Civis." in partial
    assert "Only its deployment log (deployment 7001) was checked." in partial
    assert "may not match the file in git" in partial
    notebooks = text_of(client.get("/inventory?type=notebook"))
    assert "Notebook" in notebooks and "Python script" not in notebooks.split("Run by workflows")[1]
    search = text_of(client.get("/inventory?q=nightly+load"))
    assert "Showing 1 to 1 of 1 objects" in search and "Example Analyst" in search
    run_by = text_of(client.get("/inventory?q=member+file+load+01"))
    assert "Example nightly pipeline" in run_by
    assert "No objects match" in text_of(client.get("/inventory?q=zzzz-no-match"))
    page_two = text_of(client.get("/inventory?page=2"))
    assert "Showing 51 to 96 of 96 objects" in page_two


def test_findings_filters_show_location_never_code(app):
    client = app.test_client()
    page = text_of(client.get("/findings?status=all"))
    assert ", line " in page
    assert "make_backend_factory" not in page and "read_civis_sql(" not in page and "QUERY" not in page
    fixed = text_of(client.get("/findings?status=fixed"))
    assert "By example_admin" in fixed
    advisory = text_of(client.get("/findings?status=all&impact=advisory"))
    assert "Advisory" in advisory and "Affected in February Advisory" in advisory  # Filter labels plus rows.
    py315 = client.get("/findings?status=all&ruleset=python315").get_data(as_text=True)
    assert "PY315-" in py315 and "CV3-USE-PANDAS</div>" not in py315


def test_hostile_object_names_render_as_text(app):
    client = app.test_client()
    for url in ("/inventory?q=quoted", "/findings?status=all&q=x"):
        body = client.get(url).get_data(as_text=True)
        assert "<script>alert" not in body and "<b>job</b>" not in body
    body = client.get("/inventory?q=quoted").get_data(as_text=True)
    assert "Example &lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt; &amp; &#34;quoted&#34; &lt;b&gt;job&lt;/b&gt;" in body


def test_csv_download(app):
    client = app.test_client()
    response = client.get("/download/latest")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    assert client.get("/download/502").status_code == 404  # The failed run has no report.


# --- CSRF and the concurrent-run guard -----------------------------------------------------


@pytest.mark.parametrize("url", ["/scan/start", "/findings/save"])
def test_every_post_needs_a_csrf_token(app, url):
    client = app.test_client()
    assert client.post(url).status_code == 400
    assert client.post(url, data={"csrf_token": "123.bad"}).status_code == 400
    token = csrf(app)
    cross = client.post(url, data={"csrf_token": token}, headers={"Origin": "https://attacker.example.test"})
    assert cross.status_code == 403


def test_run_scan_refuses_while_a_run_is_active(app):
    client = app.test_client()
    jobs = app.extensions["pmsc"].jobs()
    before = len(jobs.runs())
    response = client.post("/scan/start", data={"csrf_token": csrf(app)})
    assert response.status_code == 302 and response.headers["Location"].endswith("/scan?notice=busy")
    assert len(jobs.runs()) == before
    assert "already queued or running" in text_of(client.get("/scan?notice=busy"))
    assert 'disabled>Scan running</button>' in client.get("/").get_data(as_text=True)


def test_run_scan_starts_once_then_refuses_while_queued(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    app = make_app(preview, "failed")
    client = app.test_client()
    first = client.post("/scan/start", data={"csrf_token": csrf(app)})
    assert first.headers["Location"].endswith("/scan?notice=started")
    assert app.extensions["pmsc"].jobs().runs()[0].state == "queued"
    second = client.post("/scan/start", data={"csrf_token": csrf(app)})
    assert second.headers["Location"].endswith("/scan?notice=busy")


# --- The batched checklist save -----------------------------------------------------------


def _form_rows(app, query="status=all"):
    body = app.test_client().get(f"/findings?{query}").get_data(as_text=True)
    return re.findall(r'name="o:(\d+):([A-Z0-9-]+)" value="(\w+)"', body)


def test_checklist_save_is_one_batched_write(app, monkeypatch):
    results = app.extensions["pmsc"].results
    calls = []
    original = results.save_checklist

    def spy(run_id, entries, changed_by, when):
        calls.append((run_id, list(entries), changed_by))
        return original(run_id, entries, changed_by, when)

    monkeypatch.setattr(results, "save_checklist", spy)
    rows = [r for r in _form_rows(app, "status=open")][:3]
    data = {"csrf_token": csrf(app), "f_status": "open"}
    for object_id, rule_id, status in rows:
        data[f"o:{object_id}:{rule_id}"] = status
        data[f"s:{object_id}:{rule_id}"] = status
    data[f"s:{rows[0][0]}:{rows[0][1]}"] = "fixed"
    data[f"s:{rows[1][0]}:{rows[1][1]}"] = "ignored"
    response = app.test_client().post("/findings/save", data=data)
    assert response.status_code == 302 and "saved=2" in response.headers["Location"]
    assert "status=open" in response.headers["Location"]
    assert len(calls) == 1
    run_id, entries, changed_by = calls[0]
    assert changed_by == "local preview user"
    assert sorted(entries) == sorted([(int(rows[0][0]), rows[0][1], "fixed"), (int(rows[1][0]), rows[1][1], "ignored")])
    fixed_page = text_of(app.test_client().get("/findings?status=fixed"))
    assert "By local preview user" in fixed_page
    assert "Saved 2 status changes." in text_of(app.test_client().get("/findings?saved=2"))


def test_checklist_save_rejects_values_that_are_not_allowed(app, monkeypatch):
    results = app.extensions["pmsc"].results
    monkeypatch.setattr(results, "save_checklist", lambda *a, **k: pytest.fail("must not save"))
    object_id, rule_id, status = _form_rows(app)[0]
    for bad in ({f"s:{object_id}:{rule_id}": "deleted"}, {f"s:{object_id}:NOT-A-RULE": "fixed"},
                {"s:abc:CV3-ML": "fixed"}):
        response = app.test_client().post("/findings/save", data={"csrf_token": csrf(app), **bad})
        assert "error=invalid" in response.headers["Location"]
    unchanged = {"csrf_token": csrf(app), f"o:{object_id}:{rule_id}": status, f"s:{object_id}:{rule_id}": status}
    assert "saved=0" in app.test_client().post("/findings/save", data=unchanged).headers["Location"]


# --- Empty and error states ----------------------------------------------------------------


@pytest.mark.parametrize("state, expected", [
    ("no-scan", "No scan yet"),
    ("first-scan-running", "The first scan is running"),
    ("first-scan-cancelled", "No completed scan yet The last scan was cancelled before it finished. Run the scan again."),
    ("schema-missing", "The results schema does not exist yet"),
    ("failed", "The last scan failed before it finished."),
    ("tables-failed", "could not save its results tables"),
    ("no-job-id", "Run scan is turned off because SCANNER_JOB_ID is not set"),
    ("job-unreadable", "Run scan is turned off because the scanner job could not be read"),
    ("database-unrecognized", "The scanner job's database setting could not be read"),
])
def test_empty_and_error_states(preview, monkeypatch, state, expected):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    app = make_app(preview, state)
    for url in PAGES:
        response = app.test_client().get(url)
        assert response.status_code == 200
    assert expected in text_of(app.test_client().get("/"))


def test_no_job_id_turns_off_run_scan(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    app = make_app(preview, "no-job-id")
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'disabled aria-describedby="run-scan-off">Run scan</button>' in body
    response = app.test_client().post("/scan/start", data={"csrf_token": csrf(app)})
    assert response.headers["Location"].endswith("/scan?notice=no-job")


def test_no_job_id_in_civis_mode(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": Services(Settings(), None, None)})
    page = text_of(app.test_client().get("/"))
    assert "The scanner job is not set" in page and "SCANNER_JOB_ID is not set" in page


def test_saving_needs_a_civis_user(monkeypatch):
    """Inside Civis the user name comes from the Civis-Username header; without it nothing is saved."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    services = Services(Settings(), None, None)
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": services})
    response = app.test_client().post("/findings/save", data={"csrf_token": csrf(app), "s:1:CV3-ML": "fixed"})
    assert "error=viewer" in response.headers["Location"]


# --- The Civis data layer (civis 2.7.1 calls only) -----------------------------------------


from tests.civis_fakes import RecordingIO as FakeCivisIO  # noqa: E402  Reads like civis 2.7.1, writes recorded.


def test_civis_results_reads_escaped_sql_and_normalizes_text():
    fake = FakeCivisIO({
        "pg_catalog.pg_namespace": [["nspname"], ["results"]],
        "pg_catalog.pg_tables": [["tablename"], ["scan_runs"]],
        "results.objects": [["run_id", "object_id", "owner_active", "name"], ["run-1", "101", "f", "Example"]],
    })
    store = CivisResults(object(), fake, "analytics", "results", 77)
    assert store.check() == "ok"
    rows = store.rows("objects", "run-1")
    assert rows == [{"run_id": "run-1", "object_id": 101, "owner_active": False, "name": "Example"}]
    assert "WHERE run_id = 'run-1'" in fake.reads[-1]
    with pytest.raises(Exception):
        store.rows("objects", "run-1'; DROP TABLE x; --")
    with pytest.raises(ValueError):
        store.rows("pg_user", "run-1")


def test_civis_results_reports_a_missing_schema():
    store = CivisResults(object(), FakeCivisIO({}), "analytics", "results", None)
    with pytest.raises(ResultsUnavailable) as excinfo:
        store.check()
    assert excinfo.value.kind == "schema_missing"


def test_civis_checklist_save_is_one_escaped_insert():
    fake = FakeCivisIO({})
    store = CivisResults(object(), fake, "analytics", "results", None)
    store.save_checklist("run-1", [(101, "CV3-ML", "fixed"), (102, "CV3-PARALLEL", "ignored")], "o'neil",
                         datetime(2026, 10, 14, 12, 0))
    assert len(fake.queries) == 1
    statement = fake.queries[0]
    assert statement.startswith("INSERT INTO results.checklist_status")
    assert "'o''neil'" in statement and "(101, 'CV3-ML', 'fixed'" in statement.replace("'run-1', ", "")
    from app.backends.base import ChecklistSaveError

    with pytest.raises(ChecklistSaveError):
        store.save_checklist("run-1", [(101, "CV3-ML", "fixed")], "user\\'); DROP TABLE x; --", datetime(2026, 10, 14))
    assert len(fake.queries) == 1


class FakeScripts:
    def __init__(self):
        self.calls = []

    def list_containers_runs(self, id, limit=None):
        self.calls.append(("list_containers_runs", id, limit))
        return [{"id": 7, "state": "succeeded"}, {"id": 8, "state": "running", "started_at": "2026-10-14T12:00:00Z"}]

    def list_containers_runs_logs(self, id, run_id, last_id=None, limit=None):
        self.calls.append(("list_containers_runs_logs", id, run_id, last_id))
        return [{"id": 2, "message": "PHASE name=inventory state=done"}, {"id": 1, "message": "RUN run_id=run-1 status=running"}]

    def post_containers_runs(self, id):
        self.calls.append(("post_containers_runs", id))
        return {"id": 9, "state": "queued"}

    def list_containers_runs_outputs(self, id, run_id):
        return [{"object_type": "File", "object_id": 55, "name": "report.csv"}]


class FakeFiles:
    def __init__(self, url):
        self.url = url

    def get(self, id):
        return {"id": id, "file_url": self.url}


class FakeClient:
    def __init__(self, url="https://example-bucket.s3.amazonaws.com/report.csv?X-Amz-Signature=synthetic"):
        self.scripts = FakeScripts()
        self.files = FakeFiles(url)


def test_civis_jobs_use_the_scanner_job():
    client = FakeClient()
    jobs = CivisJobs(client, 1234)
    runs = jobs.runs()
    assert [r.id for r in runs] == [8, 7] and runs[0].active
    lines = jobs.logs(8, last_id=0)
    assert [line.id for line in lines] == [1, 2]
    assert jobs.start().state == "queued"
    assert jobs.report(7) == ("url", "https://example-bucket.s3.amazonaws.com/report.csv?X-Amz-Signature=synthetic")
    assert all(call[1] == 1234 for call in client.scripts.calls)


def test_download_redirects_only_to_http_links(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    for url, status in (("https://example-bucket.s3.amazonaws.com/r.csv", 302), ("javascript:alert(1)", 404)):
        client = FakeClient(url)
        services = Services(Settings(scanner_job_id=1234), None, lambda c=client: CivisJobs(c, 1234))
        app = create_app({"SECRET_KEY": SECRET, "SERVICES": services})
        assert app.test_client().get("/download/7").status_code == status


def test_cache_reads_each_finished_run_once():
    class Store:
        def __init__(self):
            self.calls = 0

        def rows(self, table, run_id):
            self.calls += 1
            return [{"run_id": run_id}]

        def checklist(self):
            self.calls += 1
            return []

        def save_checklist(self, *args):
            pass

    store = Store()
    clock = {"now": 0.0}
    cache = CachedResults(lambda: store, clock=lambda: clock["now"])
    for _ in range(3):
        cache.rows("objects", "run-1")
        cache.checklist()
    assert store.calls == 2
    cache.save_checklist("run-1", [], "x", NOW)
    cache.checklist()
    assert store.calls == 3  # Saving clears the checklist cache at once.


# --- Thread safety (gunicorn gthread workers) -----------------------------------------------


def test_concurrent_first_loads_start_one_query_per_table():
    started = threading.Event()
    release = threading.Event()

    class SlowStore:
        def __init__(self):
            self.calls = 0

        def rows(self, table, run_id):
            self.calls += 1
            started.set()
            release.wait(5)
            return [{"run_id": run_id, "table": table}]

    store = SlowStore()
    cache = CachedResults(lambda: store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cache.rows, "findings", "run-1") for _ in range(4)]
        assert started.wait(5)
        release.set()
        results = [f.result(5) for f in futures]
    assert store.calls == 1 and cache.reads == 1
    assert all(r is results[0] for r in results)


def test_a_checklist_load_from_before_a_save_is_not_kept():
    loading = threading.Event()
    release = threading.Event()

    class Store:
        def __init__(self):
            self.version = "before save"
            self.slow = True

        def checklist(self):
            value = [self.version]
            if self.slow:
                self.slow = False
                loading.set()
                release.wait(5)
            return value

        def save_checklist(self, *args):
            self.version = "after save"

    store = Store()
    cache = CachedResults(lambda: store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        early = pool.submit(cache.checklist)  # Starts reading before the save ...
        assert loading.wait(5)
        cache.save_checklist("run-1", [], "x", NOW)  # ... and finishes after it.
        release.set()
        assert early.result(5) == ["before save"]
    assert cache.checklist() == ["after save"]  # The older read was not cached.


def test_progress_ignores_lines_it_has_already_seen():
    progress = Progress()
    newer = [LogLine(n, f"PROGRESS phase=code objects_done={n * 10} objects_total=100", None) for n in (1, 2, 3, 4)]
    older = newer[:2]
    progress.feed(list(reversed(newer)))  # Order does not matter.
    progress.feed(older)  # A slower request applies older lines afterward.
    assert (progress.done, progress.last_id) == (40, 4)


def test_concurrent_run_scan_posts_in_one_worker_start_one_scan(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    app = make_app(preview, "failed")
    jobs = app.extensions["pmsc"].jobs()
    original_runs = jobs.runs

    def slow_runs(*args, **kwargs):
        result = original_runs(*args, **kwargs)
        time.sleep(0.2)  # Widens the window between the check and the start.
        return result

    monkeypatch.setattr(type(jobs), "runs", lambda self, *a, **k: slow_runs(*a, **k))
    token = csrf(app)

    def post(_):
        return app.test_client().post("/scan/start", data={"csrf_token": token}).headers["Location"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        notices = sorted(location.rsplit("=", 1)[1] for location in pool.map(post, range(4)))
    assert notices == ["busy", "busy", "busy", "started"]


# --- Pure helpers ----------------------------------------------------------------------------


def test_month_first_dates_and_durations():
    assert views.format_datetime("2026-10-12T14:14:00Z") == "October 12, 2026 at 2:14 PM UTC"
    assert views.format_datetime("2026-10-12 09:05:00") == "October 12, 2026 at 9:05 AM UTC"
    assert views.format_date("2026-04-02T00:00:00Z") == "Apr 2, 2026"
    assert views.format_duration("2026-10-12T14:00:00Z", "2026-10-12T14:41:30Z") == "41 min"
    assert views.format_duration("2026-10-12T14:00:00Z", "2026-10-12T16:05:00Z") == "2 hr 5 min"


def test_progress_parsing_and_view():
    progress = Progress()
    progress.feed([
        LogLine(1, "RUN run_id=run-9 status=running", "2026-10-14T12:00:00Z"),
        LogLine(2, "PHASE name=inventory state=done", "2026-10-14T12:01:00Z"),
        LogLine(3, "PROGRESS phase=inventory objects_done=200 objects_total=200", "2026-10-14T12:01:00Z"),
        LogLine(4, "PHASE name=code state=start", "2026-10-14T12:01:00Z"),
        LogLine(5, "PROGRESS phase=code objects_done=50 objects_total=200", "2026-10-14T12:11:00Z"),
        LogLine(6, "a line of job output that is not a scanner line", "2026-10-14T12:11:00Z"),
    ])
    assert (progress.scan_run_id, progress.done, progress.total, progress.last_id) == ("run-9", 50, 200, 6)
    view = views.progress_view(JobRun(9, "running", "2026-10-14T12:00:00Z"), progress)
    assert view["headline"] == "Scanning code and logs: 50 of 200 objects" and view["percent"] == 25
    assert view["eta"] == "About 31 minutes left"


def test_checklist_changes_from_form():
    rules = set(load_rules().ids())
    form = [("o:1:CV3-ML", "open"), ("s:1:CV3-ML", "fixed"), ("o:2:CV3-ML", "open"), ("s:2:CV3-ML", "open"), ("csrf_token", "x")]
    assert views.checklist_changes(form, rules) == [(1, "CV3-ML", "fixed")]
    with pytest.raises(ValueError):
        views.checklist_changes([("s:1:CV3-ML", "removed")], rules)


def test_normalize_row_types():
    row = normalize_row("findings", {"object_id": "12", "line_number": "", "rule_id": "CV3-ML", "workflows": None})
    assert row == {"object_id": 12, "line_number": None, "rule_id": "CV3-ML", "workflows": None}


def test_settings_from_env():
    settings = Settings.from_env({"SCANNER_JOB_ID": "77"})
    assert (settings.scanner_job_id, settings.problem) == (77, None)
    bad = Settings.from_env({"SCANNER_JOB_ID": "abc"})
    assert bad.scanner_job_id is None and "SCANNER_JOB_ID" in bad.problem


def test_the_service_has_no_results_settings_of_its_own():
    """The results database, credential, and schema come from the scanner job only (docs/PLAN.md 17.2)."""
    removed = ("RESULTS_DATABASE", "RESULTS_CREDENTIAL_ID")
    root = Path(__file__).resolve().parent.parent
    for path in [*(root / "app").rglob("*.py"), root / "README.md", root / "TESTING.md", root / ".env.example"]:
        if not path.exists():  # .dockerignore keeps .env files out of the test images.
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(name in text for name in removed), path.name
    assert not hasattr(Settings(), "results_schema")


# --- Accessibility: text contrast ----------------------------------------------------------


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i: i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_text_contrast_is_at_least_4_5_to_1():
    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text(encoding="utf-8")
    colors = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6});", css))
    colors["white"] = "#ffffff"
    pairs = [
        # Body text and muted text on every background they appear on.
        ("text", "bg"), ("text", "surface"), ("text", "stripe"), ("text", "row-border"),
        ("muted", "bg"), ("muted", "surface"), ("muted", "stripe"),
        # Navy: headings, labels, links, nav, tile numbers, step states, disabled buttons.
        ("primary", "bg"), ("primary", "surface"), ("primary", "stripe"), ("primary", "nav-active-bg"),
        ("primary", "disabled-bg"), ("primary", "info-bg"), ("primary-hover", "surface"), ("primary-hover", "stripe"),
        # White on navy: primary buttons, table headers, skip link.
        ("white", "primary"), ("white", "primary-hover"),
        # Semantic colors, unchanged.
        ("warn-text", "warn-bg"), ("error-text", "error-bg"), ("fixed", "success-bg"), ("fixed", "surface"),
        ("fixed", "stripe"), ("affected", "affected-bg"), ("affected", "surface"), ("affected", "stripe"),
        ("advisory", "advisory-bg"), ("advisory", "surface"), ("advisory", "stripe"),
    ]
    low = [(a, b, round(_contrast(colors[a], colors[b]), 2)) for a, b in pairs if _contrast(colors[a], colors[b]) < 4.5]
    assert low == []
    # Orange is decoration only: it fails text contrast, so it must never color text or focus outlines.
    assert _contrast(colors["accent"], colors["surface"]) < 4.5
    assert not re.search(r"(?<![-\w])color:\s*var\(--accent\)", css)
    assert "outline: 3px solid var(--primary)" in css


def test_input_borders_meet_3_to_1():
    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text(encoding="utf-8")
    colors = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6});", css))
    for background in ("surface", "bg"):
        assert _contrast(colors["input-border"], colors[background]) >= 3.0, background


LOGO_LINK = re.compile(
    r'<a class="brand-logo" href="https://www\.expedition-data\.com/" target="_blank" rel="noopener noreferrer">'
    r'<img src="(/static/img/expedition-data-logo\.svg)" alt="Expedition Data website \(opens in a new tab\)"'
)


@pytest.mark.parametrize("url", PAGES + ["/no-such-page"])
def test_every_page_has_the_logo_link_and_only_local_sources(app, url):
    body = app.test_client().get(url).get_data(as_text=True)
    match = LOGO_LINK.search(body)
    assert match, url
    assert 'aria-label' not in body[match.start(): body.index("</a>", match.start())]
    assert app.test_client().get(match.group(1)).status_code == 200
    # Footer link to the same site, opened the same way.
    assert re.search(r'Built by <a href="https://www\.expedition-data\.com/" target="_blank" rel="noopener noreferrer">'
                     r"Expedition Data LLC", body)
    # Every src stays on the app. The only external hrefs in the page itself are the two
    # Expedition Data links; the rest are links to objects in Civis Platform, built from scan data.
    assert all(src.startswith("/static/") for src in re.findall(r'\ssrc="([^"]*)"', body))
    external = [h for h in re.findall(r'\shref="([^"]*)"', body) if re.match(r"(?i)(https?:)?//", h)]
    brand = [h for h in external if h == "https://www.expedition-data.com/"]
    assert len(brand) == 2
    assert all(h.startswith("https://platform.civisanalytics.com/spa/#/") for h in external if h not in brand)
    assert '<span class="brand-divider" aria-hidden="true"></span>' in body
    assert re.search(
        r'<a class="brand-name" href="/"><span class="brand-title">Python Migration Scanner</span> '
        r'<span class="brand-descriptor">for Civis Platform</span></a>',
        body,
    )


@pytest.mark.parametrize("url, status", [(url, 200) for url in PAGES] + [("/no-such-page", 404)])
def test_every_page_footer_has_the_non_affiliation_statement(app, url, status):
    response = app.test_client().get(url)
    assert response.status_code == status
    body = response.get_data(as_text=True)
    footer = body[body.index('<footer class="site-footer">'): body.index("</footer>")]
    notice = re.search(r'<p class="footer-notice">(.*?)</p>', footer, re.DOTALL)
    assert notice and " ".join(html.unescape(notice.group(1)).split()) == NON_AFFILIATION


def test_logo_svg_is_inert_and_served_safely(app):
    svg = (Path(__file__).resolve().parent.parent / "app/static/img/expedition-data-logo.svg").read_text(encoding="utf-8")
    for forbidden in ("<script", "foreignobject", "<image", "href"):
        assert forbidden not in svg.lower(), forbidden
    assert not re.search(r"\son[a-z]+\s*=", svg, re.IGNORECASE)  # No event handler attributes.
    # No metadata (such as content credentials) and no namespace other than SVG.
    svg_ns = "http://www.w3.org/2000/svg"
    assert "<metadata" not in svg.lower()
    declarations = re.findall(r"""\sxmlns(?::([\w.-]+))?\s*=\s*["']([^"']*)["']""", svg)
    assert declarations and all(prefix == "" and uri == svg_ns for prefix, uri in declarations), declarations
    from xml.etree import ElementTree

    root = ElementTree.fromstring(svg)
    assert all(el.tag.startswith("{" + svg_ns + "}") for el in root.iter())
    assert all(":" not in name.split("}")[-1] and not name.startswith("{") for el in root.iter() for name in el.attrib)
    response = app.test_client().get("/static/img/expedition-data-logo.svg")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_fonts_and_logo_are_served_with_the_right_types(app):
    client = app.test_client()
    assert client.get("/static/fonts/carlito-latin-400-normal.woff2").mimetype == "font/woff2"
    assert client.get("/static/fonts/ibm-plex-mono-latin-400-normal.woff2").mimetype == "font/woff2"
    assert client.get("/static/img/expedition-data-logo.svg").mimetype == "image/svg+xml"


def test_fonts_are_carlito_and_plex_mono_only():
    fonts = Path(__file__).resolve().parent.parent / "app/static/fonts"
    names = sorted(p.name for p in fonts.iterdir())
    assert names == [
        "Carlito-LICENSE.txt", "IBM-Plex-LICENSE.txt",
        "carlito-latin-400-italic.woff2", "carlito-latin-400-normal.woff2", "carlito-latin-700-normal.woff2",
        "ibm-plex-mono-latin-400-normal.woff2", "ibm-plex-mono-latin-500-normal.woff2",
    ]
    assert "SIL Open Font License" in (fonts / "Carlito-LICENSE.txt").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text(encoding="utf-8")
    assert "--sans: Calibri, Carlito, system-ui, sans-serif;" in css
    # Carlito has no 500 or 600 weights; only the Plex Mono font face may use 500.
    weights = re.findall(r"font-weight:\s*(\d+)", css.split("--mono")[0].split(":root")[1] + css.split(":root")[1])
    assert set(weights) <= {"400", "700"}


def test_phone_width_rules_exist():
    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text(encoding="utf-8")
    assert "@media (max-width: 640px)" in css and ".table-wrap { overflow-x: auto; }" in css


# --- Local preview mode -----------------------------------------------------------------------


def test_local_mode_refuses_when_a_civis_key_is_set(preview, monkeypatch):
    monkeypatch.setenv("CIVIS_API_KEY", "x" * 40)  # pragma: allowlist secret  gitleaks:allow
    with pytest.raises(SystemExit, match="refuses to start while CIVIS_API_KEY is set"):
        local_mode.build(preview)


def test_local_mode_binds_to_localhost_only(preview, monkeypatch):
    monkeypatch.delenv("CIVIS_API_KEY", raising=False)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    seen = {}

    def fake_run(self, host=None, port=None, debug=None, use_reloader=None):
        seen.update(host=host, debug=debug)

    monkeypatch.setattr("flask.Flask.run", fake_run)
    assert local_mode.main(["--results", str(preview), "--port", "8051"]) == 0
    assert seen == {"host": "127.0.0.1", "debug": False}


def test_local_mode_needs_preview_data(tmp_path, monkeypatch):
    monkeypatch.delenv("CIVIS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="No preview data"):
        local_mode.build(tmp_path)


def test_hostile_name_is_in_the_preview(preview):
    import sqlite3

    conn = sqlite3.connect(preview / "results.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM objects WHERE name = ?", (HOSTILE_NAME,)).fetchone()[0] > 0
    json.loads((preview / "local_jobs.json").read_text(encoding="utf-8"))


# --- LOOKBACK_DAYS on the Overview (docs/PLAN.md 17.6) ------------------------------------------------


def test_overview_says_which_window_a_scan_uses_and_how_many_scripts_were_left_out(app):
    page = text_of(app.test_client().get("/"))
    assert "A scan checks scripts that ran or changed in the last 400 days (LOOKBACK_DAYS on the scanner job)." in page
    # 12 retired reports, plus one Python, one container, and one custom script in the synthetic instance.
    assert ("15 scripts that have not run or changed in the last 400 days were left out. To include them, raise "
            "LOOKBACK_DAYS on the scanner job.") in page


@pytest.mark.parametrize("lookback, read, expected", [
    (30, True, "A scan checks scripts that ran or changed in the last 30 days (LOOKBACK_DAYS on the scanner job)."),
    (1, True, "A scan checks scripts that ran or changed in the last 1 day (LOOKBACK_DAYS on the scanner job)."),
    (3650, True, "A scan checks scripts that ran or changed in the last 3,650 days (LOOKBACK_DAYS on the scanner job)."),
    (None, True, "The scanner job's LOOKBACK_DAYS is not a whole number from 1 to 3,650, so a scan would stop "
                 "before it starts. See line 4 on the Setup page."),
    (400, False, None),  # The job was not read, or is not this tool's scanner job: nothing is said.
])
def test_next_window_text(lookback, read, expected):
    situation = views.Situation(results_state="ok", job_configured=True, job_lookback=lookback, job_lookback_read=read)
    assert views.next_window_text(situation) == expected


def test_outside_window_text():
    assert views.outside_window_text(0, 400) is None
    assert views.outside_window_text(1, 30) == (
        "1 script that has not run or changed in the last 30 days was left out. To include it, raise LOOKBACK_DAYS "
        "on the scanner job.")
    assert views.outside_window_text(1234, 400) == (
        "1,234 scripts that have not run or changed in the last 400 days were left out. To include them, raise "
        "LOOKBACK_DAYS on the scanner job.")
    assert "the scan's LOOKBACK_DAYS window" in views.outside_window_text(2, None)


def test_the_scan_page_window_defaults_to_400_days(app):
    assert "Lookback window: 400 days." in text_of(app.test_client().get("/scan"))


# --- Inventory progress (docs/PLAN.md 17.6, Part C) -----------------------------------------------------


def test_progress_view_shows_inventory_n_of_m_while_it_runs():
    progress = Progress()
    progress.feed([
        LogLine(1, "RUN run_id=run-9 status=running", "2026-10-14T12:00:00Z"),
        LogLine(2, "PHASE name=inventory state=start", "2026-10-14T12:00:01Z"),
        LogLine(3, "INVENTORY type=python_script listed=426 kept=58 outside_window=295 template_copies=73 "
                   "not_python=0", "2026-10-14T12:05:00Z"),
        LogLine(4, "PROGRESS phase=inventory objects_done=0 objects_total=301", "2026-10-14T12:05:00Z"),
        LogLine(5, "PROGRESS phase=inventory objects_done=50 objects_total=301", "2026-10-14T12:05:30Z"),
    ])
    assert (progress.inventory_done, progress.inventory_total) == (50, 301)
    view = views.progress_view(JobRun(9, "running", "2026-10-14T12:00:00Z"), progress)
    assert view["headline"] == "Inventory: 50 of 301 objects read"
    assert view["steps"][0] == {"name": "1. Inventory", "state": "Running", "detail": "50 of 301 objects"}
    assert view["percent"] == 0

    progress.feed([
        LogLine(6, "PROGRESS phase=inventory objects_done=301 objects_total=301", "2026-10-14T12:08:00Z"),
        LogLine(7, "PHASE name=inventory state=done", "2026-10-14T12:08:00Z"),
        LogLine(8, "PHASE name=code state=start", "2026-10-14T12:08:00Z"),
        LogLine(9, "PROGRESS phase=code objects_done=0 objects_total=280", "2026-10-14T12:08:00Z"),
    ])
    view = views.progress_view(JobRun(9, "running", "2026-10-14T12:00:00Z"), progress)
    assert view["steps"][0] == {"name": "1. Inventory", "state": "Done", "detail": "280 Python objects listed"}
    assert view["headline"] == "Scanning code and logs: 0 of 280 objects"


def test_scan_history_and_the_status_api_show_inventory_progress(monkeypatch):
    from app.services import civis_services
    from tests.civis_fakes import FakeCivisClient, RecordingIO

    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    client = FakeCivisClient()
    client.handlers.update({
        "scripts.list_containers_runs": lambda id, limit=None: [
            {"id": 801, "state": "running", "created_at": "2026-11-06T12:00:00Z", "started_at": "2026-11-06T12:00:00Z"}],
        "scripts.list_containers_runs_logs": lambda id, run_id, last_id=None, limit=None: [] if last_id else [
            {"id": 1, "message": "RUN run_id=run-20261106T120000Z-abc123 status=running"},
            {"id": 2, "message": "PHASE name=inventory state=start"},
            {"id": 3, "message": "PROGRESS phase=inventory objects_done=100 objects_total=301"},
        ],
    })
    io = RecordingIO({"pg_catalog.pg_namespace": [["nspname"], ["migration_results"]],
                      "pg_catalog.pg_tables": [["tablename"]] + [[t] for t in
                                                                ("scan_runs", "objects", "findings", "coverage",
                                                                 "checklist_status")]})
    services = civis_services(Settings(scanner_job_id=1234), client_factory=lambda: client, io_factory=lambda: io)
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": services, "TESTING": True})
    data = app.test_client().get("/api/scan-status").get_json()
    assert data["state"] == "running" and data["headline"] == "Inventory: 100 of 301 objects read"
    assert data["steps"][0] == {"name": "1. Inventory", "state": "Running", "detail": "100 of 301 objects"}
    page = text_of(app.test_client().get("/scan"))
    assert "1. Inventory" in page and "100 of 301 objects" in page


# --- Templates (docs/PLAN.md 17.6, Part F) ----------------------------------------------------------------


def _template_rows():
    objects = [
        {"object_id": 108, "object_type": "python_script", "name": "Example backing", "url": "https://civis.test/108"},
        {"object_id": 301, "object_type": "custom_script", "name": "Custom A", "template_id": 31,
         "backing_script_id": 211, "url": "https://civis.test/301"},
        {"object_id": 304, "object_type": "custom_script", "name": "Custom B", "template_id": 33,
         "backing_script_id": 108, "url": "https://civis.test/304"},
        {"object_id": 305, "object_type": "custom_script", "name": "Custom C", "template_id": 33,
         "backing_script_id": 108, "url": "https://civis.test/305"},
        {"object_id": 306, "object_type": "custom_script", "name": "Custom D", "template_id": 34,
         "backing_script_id": None, "url": "https://civis.test/306"},
    ]
    findings = [{"object_id": i, "rule_id": "CV3-ML", "impact": "affected", "file": "f", "line_number": 1}
                for i in (304, 305, 301)]
    return objects, findings


def test_template_info_links_the_backing_script_only_when_the_scan_stored_its_link():
    objects, _ = _template_rows()
    by_id = {o["object_id"]: o for o in objects}
    assert views.template_info(by_id[304], by_id) == {"template_id": 33, "backing_id": 108,
                                                       "backing_url": "https://civis.test/108"}
    # 211 is not in the scan: its type, and so its link, is not known. The IDs are shown as text.
    assert views.template_info(by_id[301], by_id) == {"template_id": 31, "backing_id": 211, "backing_url": None}
    assert views.template_info(by_id[306], by_id)["backing_id"] is None
    assert views.template_info(by_id[108], by_id) is None  # Not a custom script.


def test_the_overview_line_counts_templates_and_custom_scripts_with_findings():
    objects, findings = _template_rows()
    assert views.template_findings_text(objects, findings) == (
        "2 templates, used by 3 custom scripts, have findings.")
    assert views.template_findings_text(objects, findings[:1]) == "1 template, used by 1 custom script, has findings."
    assert views.template_findings_text(objects, []) == "0 templates, used by 0 custom scripts, have findings."
    assert views.template_findings_text(objects[:1], findings) is None  # No custom script from a template.


@pytest.mark.parametrize("value, expected", [
    ("33", [304, 305]), (" 31 ", [301]), ("", [108, 301, 304, 305, 306]), ("99", []),
    ("33 OR 1=1", []), ('"><script>alert(1)</script>', []), ("-1", []), ("1" * 40, []),
])
def test_the_template_filter(value, expected):
    objects, findings = _template_rows()
    rows = views.inventory(objects, findings, {"template": value} if value else {})
    assert sorted(r["object_id"] for r in rows) == expected
    found = views.findings_rows(objects, findings, [], load_rules(), {"template": value, "status": "all"})
    assert sorted(r["object_id"] for r in found) == [i for i in expected if i in (301, 304, 305)]


def test_inventory_and_findings_show_templates_and_the_sentence(app):
    client = app.test_client()
    body = client.get("/inventory?type=custom_script").get_data(as_text=True)
    text = text_of(client.get("/inventory?type=custom_script"))
    assert "Template 33, backing script 108" in text and "Template 31, backing script 211" in text
    assert re.search(r'backing script <a href="https://platform\.civisanalytics\.com/spa/#/scripts/python3/108" '
                     r'target="_blank" rel="noopener noreferrer">108<span class="sr-only">', body)
    assert 'backing script 211</div>' in body  # Not in the scan: text, no link.
    assert views.TEMPLATE_SENTENCE in text
    findings = text_of(client.get("/findings?status=all&template=33"))
    assert "Template 33, backing script 108" in findings and views.TEMPLATE_SENTENCE in findings
    assert "Template 31" not in findings
    assert views.TEMPLATE_SENTENCE not in text_of(client.get("/findings?status=all&type=x&template=99999"))
    overview = text_of(client.get("/"))
    assert re.search(r"\d+ templates?, used by \d+ custom scripts?, (has|have) findings\. See findings", overview)


def test_a_hostile_template_filter_is_escaped_and_matches_nothing(app):
    client = app.test_client()
    hostile = '"><script>alert(1)</script>'
    for page in ("/inventory", "/findings"):
        response = client.get(page, query_string={"template": hostile})
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "<script>alert(1)</script>" not in body
        assert 'value="&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in body
        assert ("No objects match these filters." if page == "/inventory" else "No findings match these filters.") in body


def test_a_hostile_backing_script_link_is_not_followed(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    objects = [
        {"object_id": 108, "object_type": "python_script", "name": "<b>x</b>", "url": "javascript:alert(1)"},
        {"object_id": 304, "object_type": "custom_script", "name": "<img src=x onerror=alert(1)>", "template_id": 33,
         "backing_script_id": 108, "url": "https://civis.test/304"},
    ]
    from flask import render_template_string

    info = views.template_info(objects[1], {o["object_id"]: o for o in objects})
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": Services(Settings(), None, None), "TESTING": True})
    with app.test_request_context("/"):
        html_text = render_template_string('{% from "macros.html" import template_line %}{{ template_line(info) }}',
                                           info=info)
    assert 'href="#"' in html_text and "javascript:" not in html_text


def test_the_findings_save_keeps_the_template_filter(app):
    with app.test_request_context("/", headers={"Civis-User-Id": "77"}):
        token = generate_csrf_token(app)
    response = app.test_client().post(
        "/findings/save", headers={"Civis-User-Id": "77", "Civis-Username": "example_admin"},
        data={"csrf_token": token, "f_template": "33", "f_status": "all"})
    assert "template=33" in response.headers["Location"]


def test_the_csv_report_has_no_template_columns():
    """The CSV gains no columns (docs/PLAN.md 17.6, Part F): it had no template_id before."""
    from scanner.output import REPORT_COLUMNS

    assert "template_id" not in REPORT_COLUMNS and "backing_script_id" not in REPORT_COLUMNS
