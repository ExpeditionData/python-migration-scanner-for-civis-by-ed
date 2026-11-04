"""Tests for the security and privacy requirements in SECURITY.md.

All secrets, emails, and names below are fake and made up for these tests.
"""

from __future__ import annotations

import ast
import copy
import csv
import io
import json
import logging
import os
import re
import sqlite3
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

try:
    from flask import render_template_string

    from app import create_app
    from app.security import (
        csrf_token_is_valid,
        frame_ancestors,
        frame_ancestors_report_only,
        generate_csrf_token,
        safe_href,
    )
except ModuleNotFoundError:  # The scanner image has no Flask. The web app tests run in the Service image.
    create_app = None
from common import retention, schema, sql
from common.models import CivisObject, Impact, ObjectType, WorkflowRef
from common.redact import REDACTED, RedactingFilter, redact, safe_error
from scanner import scan_log
from scanner.engine import Engine
from scanner.retrieval.tempdir import private_tempdir

ROOT = Path(__file__).resolve().parent.parent
needs_flask = pytest.mark.skipif(create_app is None, reason="Flask is not in this image (the scanner image)")

FAKE_PASSWORD = "Tr0ub4dor-fake-pw-9431"  # pragma: allowlist secret  gitleaks:allow
FAKE_EMAIL = "pat.donor@example.org"
FAKE_API_KEY = "fakecivisapikey0123456789abcdef"  # pragma: allowlist secret  gitleaks:allow
FAKE_GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # pragma: allowlist secret  gitleaks:allow
SECRETS = (FAKE_PASSWORD, FAKE_EMAIL, FAKE_API_KEY, FAKE_GITHUB_TOKEN)


def _every_output(findings, log_text: str) -> str:
    """Everything the scanner could write: table rows, CSV rows, JSON, and its own log."""
    rows = [f.to_row() for f in findings]
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return "\n".join([buffer.getvalue(), json.dumps(rows), repr(findings), log_text])


@pytest.fixture
def scanner_log():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def _obj():
    return CivisObject(
        101, ObjectType.PYTHON_SCRIPT, "Example job", "https://example.test/101",
        uses_default_image=True, impact=Impact.AFFECTED, workflows=[WorkflowRef(601, "Example pipeline")],
    )


# --- 1. Sensitive content from Civis -----------------------------------------


def test_log_with_password_and_email_leaves_no_trace(scanner_log):
    run_log = "\n".join(
        [
            f"Connecting as etl_user with password={FAKE_PASSWORD}",
            f"Loaded member record for {FAKE_EMAIL}",
            "/app/job.py:4: FutureWarning: The method name ``<client>.users.list_me`` is deprecated "
            "and will be removed at civis-python v3.0.0 (scheduled for February 2027).",
            f"DeprecationWarning: Parsing dates involving a day of month without a year specified is ambiguous ({FAKE_EMAIL})",
        ]
    )
    engine = Engine()
    findings = engine.to_findings(_obj(), engine.scan_log(run_log, "run log"))
    assert {f.rule_id for f in findings} == {"CV3-RENAME-USERS-LIST-ME", "PY315-STRPTIME-NO-YEAR"}
    for finding in findings:
        scan_log.findings_count(finding.rule_id, 1)
    output = _every_output(findings, scanner_log.getvalue())
    for secret in (FAKE_PASSWORD, FAKE_EMAIL):
        assert secret not in output


def test_code_and_notebook_output_text_never_reach_findings(scanner_log):
    code = f"import civis\nPASSWORD = '{FAKE_PASSWORD}'\nflags = civis.APIClient().feature_flags  # {FAKE_EMAIL}\n"
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": [f"email = '{FAKE_EMAIL}'\n"],
                "outputs": [{"output_type": "stream", "name": "stdout", "text": [f"{FAKE_EMAIL} {FAKE_PASSWORD}\n"]}],
            }
        ]
    }
    engine = Engine()
    matches = engine.scan_python(code, "jobs/load.py").matches
    matches += engine.scan_notebook(json.dumps(notebook), "analysis.ipynb").matches
    output = _every_output(engine.to_findings(_obj(), matches), scanner_log.getvalue())
    for secret in (FAKE_PASSWORD, FAKE_EMAIL):
        assert secret not in output


def test_git_files_read_through_civis_never_reach_any_output(tmp_path, scanner_log):
    """Files read through the Civis git endpoints are scanned in memory only: no text from them
    reaches the findings, the coverage notes, the CSV report, or the scanner's log."""
    from scanner.__main__ import dry_run
    from scanner.synthetic import FakeClient as SyntheticClient

    data = copy.deepcopy(SyntheticClient.from_fixture("synthetic_instance.json").data)
    planted = f"PASSWORD = '{FAKE_PASSWORD}'  # {FAKE_EMAIL}\n"
    for key, entry in data["civis_git"].items():
        for sha, file in list(entry.get("files", {}).items()):
            if isinstance(file, str):
                entry["files"][sha] = file + (planted if key.startswith("python3/") else "# " + planted)
    markers = (FAKE_PASSWORD, FAKE_EMAIL, "make_backend_factory", "'analytics'")
    for token in (None, "synthetic-github-token"):
        out = tmp_path / ("token" if token else "no-token")
        dry_run(out, data=copy.deepcopy(data), github_token=token)
        conn = sqlite3.connect(out / "results.sqlite")
        try:
            read = {r[0] for r in conn.execute("SELECT object_id FROM findings WHERE rule_id = 'CV3-USE-PANDAS'")}
        finally:
            conn.close()
        assert 109 in read  # The planted file was read and scanned.
        outputs = [(out / "results.sqlite").read_bytes(), *(path.read_bytes() for path in out.glob("*.csv"))]
        for marker in markers:
            assert not any(marker.encode() in blob for blob in outputs)
            assert marker not in scanner_log.getvalue()
    assert "CIVIS_GIT_FAILED object_type=python_script object_id=111 error=FakeAPIError status=403" in scanner_log.getvalue()


def test_parse_failures_log_object_id_and_error_type_only(scanner_log):
    broken = f"print 'old style' {FAKE_PASSWORD} {FAKE_EMAIL}\n"
    result = Engine().scan_python(broken, "jobs/legacy.py")
    assert not result.fully_parsed
    try:
        compile(broken, "jobs/legacy.py", "exec")
    except SyntaxError as exc:
        scan_log.parse_error(ObjectType.PYTHON_SCRIPT, 107, exc)
    try:
        json.loads(f"{{not json {FAKE_EMAIL}")
    except ValueError as exc:
        scan_log.parse_error(ObjectType.NOTEBOOK, 401, exc)
    logged = scanner_log.getvalue()
    assert "PARSE_ERROR object_type=python_script object_id=107 error=SyntaxError" in logged
    assert "PARSE_ERROR object_type=notebook object_id=401 error=JSONDecodeError" in logged
    for secret in (FAKE_PASSWORD, FAKE_EMAIL):
        assert secret not in logged and secret not in " ".join(result.notes)


# --- 2. The scanner's own log --------------------------------------------------


def test_scanner_log_lines_are_structured(scanner_log):
    class FakeAPIError(Exception):
        status_code = 403

    scan_log.progress("code", 420, 2000)
    scan_log.phase("inventory", "done")
    scan_log.object_error(ObjectType.CONTAINER_SCRIPT, 201, FakeAPIError(f"denied for {FAKE_EMAIL} key {FAKE_API_KEY}"))
    scan_log.run_status("run-1", "running")
    scan_log.run_status("run-1", "succeeded", objects_total=2000, objects_scanned=1500, api_calls=6120,
                        civis_git_reads=4, civis_git_failures=1)
    lines = scanner_log.getvalue().splitlines()
    assert lines == [
        "PROGRESS phase=code objects_done=420 objects_total=2000",
        "PHASE name=inventory state=done",
        "OBJECT_ERROR object_type=container_script object_id=201 error=FakeAPIError status=403",
        "RUN run_id=run-1 status=running",
        "RUN run_id=run-1 status=succeeded objects_total=2000 objects_scanned=1500 api_calls=6120 "
        "civis_git_reads=4 civis_git_failures=1",
    ]


@pytest.mark.parametrize(
    "call",
    [
        lambda: scan_log.progress(f"code {FAKE_PASSWORD}", 1, 2),
        lambda: scan_log.progress("code", "1", 2),
        lambda: scan_log.findings_count(f"CV3-X {FAKE_EMAIL}", 1),
        lambda: scan_log.object_error("not_a_type", 1, ValueError()),
        lambda: scan_log.object_error(ObjectType.NOTEBOOK, f"{FAKE_EMAIL}", ValueError()),
        lambda: scan_log.run_status("run-1", "succeeded", api_calls=f"{FAKE_PASSWORD}"),
        lambda: scan_log.run_status("run-1", "succeeded", api_calls=-1),
        lambda: scan_log.run_status("run-1", "succeeded", objects_total=True),
        lambda: scan_log.run_status(f"run-1 {FAKE_EMAIL}", "succeeded", api_calls=1),
        lambda: scan_log.run_status("run-1", "succeeded", note=1),
    ],
)
def test_scanner_log_rejects_free_text(call):
    with pytest.raises((ValueError, TypeError)):
        call()


# --- 3. Temp directories --------------------------------------------------------


def test_private_tempdir_is_owner_only_and_removed_on_failure():
    created = None
    with pytest.raises(RuntimeError):
        with private_tempdir() as path:
            created = path
            if os.name != "nt":
                assert stat.S_IMODE(path.stat().st_mode) == 0o700
            locked = path / "pack" / "objects.pack"
            locked.parent.mkdir()
            locked.write_text("synthetic")
            locked.chmod(stat.S_IREAD)  # git marks object files read-only
            raise RuntimeError("scan failed")
    assert created is not None and not created.exists()


# --- 4. SQL identifiers and values ----------------------------------------------

MALICIOUS_SCHEMAS = [
    "results; DROP TABLE users; --",
    'results"; DROP TABLE users; --',
    "results--",
    "public.results",
    "results schema",
    "",
    "1results",
]


@pytest.mark.parametrize("bad_schema", MALICIOUS_SCHEMAS)
def test_malicious_schema_name_is_rejected_everywhere(bad_schema):
    for build in (
        lambda: schema.FINDINGS.create_sql(bad_schema),
        lambda: sql.qualified_table(bad_schema, "findings"),
        lambda: schema.checklist_insert_sql(bad_schema, "run1", [(1, "CV3-ML", "open")], "user", datetime(2026, 10, 13)),
        lambda: retention.delete_runs_sql(bad_schema, ["run1"]),
    ):
        with pytest.raises(sql.UnsafeValueError):
            build()


def test_malicious_table_and_run_ids_are_rejected():
    with pytest.raises(sql.UnsafeValueError):
        sql.qualified_table("results", "findings; DROP TABLE x")
    with pytest.raises(sql.UnsafeValueError):
        retention.delete_runs_sql("results", ["run1', 'x'); DROP TABLE x; --"])


# --- 5. Credentials -------------------------------------------------------------------


def test_fake_key_in_exception_is_redacted(monkeypatch):
    monkeypatch.setenv("CIVIS_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("GITHUB_TOKEN_PASSWORD", FAKE_GITHUB_TOKEN)
    stream = io.StringIO()
    logger = logging.getLogger("test.redaction")
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    try:
        try:
            raise RuntimeError(
                f"GET https://api.example.test/users failed: Authorization: Bearer {FAKE_API_KEY} "
                f"token={FAKE_GITHUB_TOKEN} url=https://user:{FAKE_PASSWORD}@github.com/org/repo.git"
            )
        except RuntimeError:
            logger.exception("Request failed with key %s", FAKE_API_KEY)
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert "Request failed" in output and "Traceback" in output
    for secret in (FAKE_API_KEY, FAKE_GITHUB_TOKEN, FAKE_PASSWORD):
        assert secret not in output
    assert REDACTED in output


@pytest.mark.parametrize(
    "text, secret",
    [
        (f"db password: {FAKE_PASSWORD}", FAKE_PASSWORD),
        (f"https://files.example.test/x.ipynb?X-Amz-Signature={FAKE_API_KEY}&a=1", FAKE_API_KEY),
        (f"clone https://x-access-token:{FAKE_GITHUB_TOKEN}@github.com/o/r", FAKE_GITHUB_TOKEN),
        (f"using {FAKE_GITHUB_TOKEN}", FAKE_GITHUB_TOKEN),
        (f'{{"api_key": "{FAKE_API_KEY}"}}', FAKE_API_KEY),
    ],
)
def test_redact_patterns(text, secret):
    assert secret not in redact(text, env={})


def test_redact_uses_attached_credential_env_values():
    env = {"DATABASE_CREDENTIAL_PASSWORD": FAKE_PASSWORD, "SCHEMA_NAME": "results"}
    assert redact(f"failed with {FAKE_PASSWORD} on results", env=env) == f"failed with {REDACTED} on results"


def test_safe_error_never_includes_the_message():
    class FakeAPIError(Exception):
        status_code = 401

    assert safe_error(FakeAPIError(FAKE_API_KEY)) == "FakeAPIError (HTTP 401)"
    assert safe_error(ValueError(FAKE_PASSWORD)) == "ValueError"


# --- 7. The GitHub token belongs to the scanner job only --------------------------------


def test_app_never_reads_a_github_token():
    """The app reads no GitHub token. It never names the token's environment variables (GITHUB_PASSWORD,
    GITHUB_USERNAME, and GITHUB_ID, which Civis sets for the GITHUB parameter), GH_TOKEN, or REPO_HTTP_TOKEN (a
    token Civis may insert into a Service). The summary's github_* counts from the scanner's RUN line are fine.
    The bare parameter name GITHUB appears only where the Setup page checks the parameter's type and in the
    preview's made-up job, and never on a line that reads the environment. "GitHub" in Civis field labels
    (GitHub Repository URL) is fine. The one exception for REPO_HTTP_TOKEN is the constant in app/setup.py that
    Setup line 7 uses to check whether the variable is set (never its value), and the summary's
    repo_http_token_present label (tests/test_setup_page.py checks the value never reaches any output)."""
    token_names = re.compile(r"GITHUB_(PASSWORD|USERNAME|ID|TOKEN)\b|GH_TOKEN|REPO_HTTP_TOKEN|github_token", re.IGNORECASE)
    parameter = re.compile(r"\bGITHUB\b")
    allowed = {"app/setup.py", "app/backends/local_backend.py"}
    offenders = []
    for p in (ROOT / "app").rglob("*"):
        if not (p.is_file() and p.suffix in (".py", ".html", ".js")):
            continue
        rel = p.relative_to(ROOT).as_posix()
        for number, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if rel == "app/setup.py" and line == 'REPO_TOKEN_VARIABLE = "REPO_HTTP_TOKEN"':
                continue
            line = line.replace("repo_http_token_present", "")
            if token_names.search(line):
                offenders.append(f"{rel}:{number}")
            elif parameter.search(line) and (rel not in allowed or re.search(r"environ|getenv", line)):
                offenders.append(f"{rel}:{number}")
    assert offenders == []


# --- By design: what the tool writes and contacts (SECURITY.md) ---------------------------

CODE_PACKAGES = ("scanner", "app", "common")
# The made-up Civis client and HTTP session used by dry runs and tests; they make no calls.
FAKES = ("scanner/synthetic/",)


def _code_files():
    for package in CODE_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith(FAKES):
                yield rel, ast.parse(path.read_text(encoding="utf-8"))


def test_civis_writes_are_limited_to_the_scanner_job_and_the_results_schema():
    """Reads Civis objects and does not modify them; writes only to the results schema and the
    scanner job's run outputs. Every Civis API write and every civis.io write is listed here."""
    allowed = {
        # The web app starts runs of the scanner job the admin created.
        ("post_containers_runs", "app/backends/civis_backend.py"),
        # The scanner attaches its CSV report to its own run.
        ("post_containers_runs_outputs", "scanner/output.py"),
        ("file_to_civis", "scanner/output.py"),
        # Result tables in the schema the admin chose (create, insert, retention deletes).
        ("query_civis", "scanner/output.py"),
        ("dataframe_to_civis", "scanner/output.py"),
        # The web app, in the results schema only: the checklist save (one INSERT), and the "Set up results
        # storage" button (CREATE SCHEMA IF NOT EXISTS, then CREATE TABLE IF NOT EXISTS for the five tables).
        ("query_civis", "app/backends/civis_backend.py"),
        # RETENTION_DAYS: the scanner's own result sink deletes expired runs from the results
        # schema, using the DELETE statements common/retention.py builds for that schema.
        ("delete_runs", "scanner/runner.py"),
        ("delete_runs_sql", "scanner/output.py"),
    }
    write = re.compile(r"^(post|put|patch|delete)_|^(query_civis|dataframe_to_civis|file_to_civis|civis_to_file)$")
    found = set()
    for rel, tree in _code_files():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and write.search(node.func.attr):
                found.add((node.func.attr, rel))
    assert found == allowed


def test_only_safe_http_sends_http_requests_and_only_to_the_allowed_hosts():
    """Network calls go only to the Civis API (through civis-python), Civis file storage, and GitHub.
    No other module imports an HTTP or socket client, so there is no path for telemetry."""
    clients = {"requests", "httpx", "aiohttp", "urllib3", "http.client", "urllib.request", "socket", "smtplib", "ftplib"}
    importers = set()
    hosts = set()
    for rel, tree in _code_files():
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            if any(name in clients or name.split(".")[0] in clients - {"http", "urllib"} for name in names):
                importers.add(rel)
            if isinstance(node, ast.keyword) and node.arg == "allowed_hosts" and isinstance(node.value, ast.Name):
                hosts.add(node.value.id)
    assert importers == {"scanner/retrieval/http.py"}
    assert hosts == {"GITHUB_HOSTS", "CIVIS_FILE_HOSTS"}


def test_security_page_states_the_fixed_by_design_text():
    text = " ".join(re.sub(r"\*\*|\[|\]\([^)]*\)", "", (ROOT / "SECURITY.md").read_text(encoding="utf-8")).split())
    for sentence in (
        "By design, this tool:",
        "Reads Civis objects and does not modify them. The web app starts runs of the scanner job you created.",
        "Writes only to the schema you specify and to its own job run outputs",
        "Makes network calls only to the Civis API, to Civis file storage through the time-limited links the "
        "Civis API provides (to download notebook files), and, if you provide a token, to GitHub",
        "Sends no telemetry and no data to Expedition Data LLC",
        "Reports object IDs, rule IDs, file names, and line numbers, not code contents",
        "These statements describe intended behavior. The full source code is available for your review. "
        "See LICENSE for the warranty disclaimer.",
        "Reports go privately to Expedition Data LLC.",
    ):
        assert sentence in text, sentence


def test_every_test_named_in_the_security_page_exists():
    named = set(re.findall(r"`(test_[a-z0-9_]+)`", (ROOT / "SECURITY.md").read_text(encoding="utf-8")))
    defined = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_[a-z0-9_]+)\(", path.read_text(encoding="utf-8"), re.MULTILINE))
    assert named and named <= defined, sorted(named - defined)


# --- 8. The scanner never grants access -------------------------------------------------


def test_no_code_runs_grant_statements():
    pattern = re.compile(r"\b(GRANT|REVOKE)\s", re.IGNORECASE)
    offenders = []
    for package in ("scanner", "common", "app"):
        for path in (ROOT / package).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if pattern.search(line) and not line.lstrip().startswith("#") and "grants or changes" not in line:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert offenders == []


# --- 9. Retention ------------------------------------------------------------------------


def test_retention_keeps_everything_by_default_and_never_the_newest_run():
    now = datetime(2026, 10, 13)
    runs = [("run-a", now - timedelta(days=400)), ("run-b", now - timedelta(days=100)), ("run-c", now - timedelta(days=500))]
    assert retention.expired_run_ids(runs, None, now) == []
    assert retention.expired_run_ids(runs, 0, now) == []
    assert retention.expired_run_ids(runs, 365, now) == ["run-a", "run-c"]
    assert retention.expired_run_ids([("only", now - timedelta(days=900))], 30, now) == []


def test_retention_deletes_from_all_five_tables_and_keeps_current_checklist_status():
    statements = retention.delete_runs_sql("results", ["run-1", "run-2"])
    tables = [re.search(r"DELETE FROM results\.(\w+)", s).group(1) for s in statements]
    assert sorted(tables) == sorted(t.name for t in schema.TABLES)
    assert tables[-1] == "scan_runs"
    checklist = next(s for s in statements if "checklist_status" in s)
    assert "newer.changed_at > results.checklist_status.changed_at" in checklist
    assert all("'run-1', 'run-2'" in s for s in statements)


# --- 10, 11, 12. Web app ----------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    application = create_app({"SECRET_KEY": "test-secret-key-that-is-long-enough-123", "TESTING": True})  # pragma: allowlist secret  gitleaks:allow

    @application.post("/test-post")
    def _test_post():
        return "saved"

    return application


SCRIPT_NAME = "<script>alert('owned')</script><img src=x onerror=alert(1)>"


@needs_flask
def test_object_named_with_script_tag_renders_as_text(app):
    with app.test_request_context("/"):
        html = render_template_string(
            "{% from 'macros.html' import object_link %}{{ object_link(o) }}",
            o={"name": SCRIPT_NAME, "url": "javascript:alert(1)"},
        )
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;alert(&#39;owned&#39;)&lt;/script&gt;" in html
    assert 'href="#"' in html


@needs_flask
def test_values_from_civis_are_escaped_in_json(app):
    with app.app_context():
        body = app.json.dumps({"name": SCRIPT_NAME, "owner": "a & b"})
        response = app.json.response({"name": SCRIPT_NAME})
    assert "<" not in body and ">" not in body and "&" not in body and "'" not in body
    assert json.loads(body)["name"] == SCRIPT_NAME
    assert b"<script>" not in response.get_data()


@needs_flask
@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://platform.civisanalytics.com/spa/#/scripts/containers/1", "https://platform.civisanalytics.com/spa/#/scripts/containers/1"),
        ("javascript:alert(1)", "#"),
        ("JaVaScRiPt:alert(1)", "#"),
        ("data:text/html,<script>", "#"),
        ("", "#"),
    ],
)
def test_safe_href(url, expected):
    assert safe_href(url) == expected


@needs_flask
def test_security_headers(app):
    response = app.test_client().get("/")
    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp and "style-src 'self'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "frame-ancestors 'self' https://platform.civisanalytics.com" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Frame-Options" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert "For Civis admins only" in response.get_data(as_text=True)


@needs_flask
def test_frame_ancestors_setting_rejects_unsafe_values():
    assert frame_ancestors("https://platform.civisanalytics.com https://*.civisanalytics.com").startswith("'self' ")
    for bad in ("*", "http://example.test", "https://x.test; script-src *", "'unsafe-inline'"):
        with pytest.raises(ValueError):
            frame_ancestors(bad)


OTHER_DIRECTIVES = [
    "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:", "font-src 'self'",
    "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'",
]


def _headers_with(monkeypatch, **env):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    for name in ("CIVIS_FRAME_ANCESTORS", "CIVIS_FRAME_ANCESTORS_REPORT_ONLY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app({"SECRET_KEY": "x" * 40, "TESTING": True})
    return app.test_client().get("/healthz").headers


@needs_flask
@pytest.mark.parametrize("env", [{}, {"CIVIS_FRAME_ANCESTORS_REPORT_ONLY": ""},
                                 {"CIVIS_FRAME_ANCESTORS_REPORT_ONLY": "false"}])
def test_frame_rule_is_enforced_by_default(monkeypatch, env):
    headers = _headers_with(monkeypatch, **env)
    assert headers["Content-Security-Policy"].endswith("; frame-ancestors 'self' https://platform.civisanalytics.com")
    assert "Content-Security-Policy-Report-Only" not in headers


@needs_flask
@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_report_only_moves_only_the_frame_rule(monkeypatch, value):
    headers = _headers_with(monkeypatch, CIVIS_FRAME_ANCESTORS_REPORT_ONLY=value,
                            CIVIS_FRAME_ANCESTORS="https://platform.civisanalytics.com https://*.civisanalytics.com")
    enforced = headers["Content-Security-Policy"]
    assert enforced.split("; ") == OTHER_DIRECTIVES
    assert "frame-ancestors" not in enforced
    assert headers["Content-Security-Policy-Report-Only"] == (
        "frame-ancestors 'self' https://platform.civisanalytics.com https://*.civisanalytics.com"
    )
    assert "report-uri" not in headers["Content-Security-Policy-Report-Only"]
    assert "report-to" not in headers["Content-Security-Policy-Report-Only"]
    assert "X-Frame-Options" not in headers


@needs_flask
@pytest.mark.parametrize("bad", ["1", "0", "yes", "no", "on", "off", "enabled", "true; script-src *", "t"])
def test_report_only_setting_rejects_anything_but_true_or_false(monkeypatch, bad):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.setenv("CIVIS_FRAME_ANCESTORS_REPORT_ONLY", bad)
    with pytest.raises(ValueError, match=r"^CIVIS_FRAME_ANCESTORS_REPORT_ONLY must be true or false$"):
        create_app({"SECRET_KEY": "x" * 40, "TESTING": True})
    assert not frame_ancestors_report_only("false") and frame_ancestors_report_only("true")


@needs_flask
def test_csrf_is_required_on_every_post(app):
    client = app.test_client()
    viewer = {"Civis-User-Id": "5"}
    assert client.post("/test-post", headers=viewer).status_code == 400
    with app.test_request_context("/", headers=viewer):
        token = generate_csrf_token(app)
    assert client.post("/test-post", data={"csrf_token": token}, headers=viewer).status_code == 200
    assert client.post("/test-post", headers={**viewer, "X-CSRF-Token": token}).status_code == 200
    # A token issued to one viewer does not work for another.
    assert client.post("/test-post", data={"csrf_token": token}, headers={"Civis-User-Id": "6"}).status_code == 400
    # A cross-site Origin is refused even with a valid token.
    cross = {**viewer, "Origin": "https://attacker.example.test"}
    assert client.post("/test-post", data={"csrf_token": token}, headers=cross).status_code == 403


@needs_flask
def test_csrf_tokens_expire(app):
    with app.test_request_context("/", headers={"Civis-User-Id": "5"}):
        old = generate_csrf_token(app, now=1_000_000)
        assert csrf_token_is_valid(app, old, now=1_000_000 + 60)
        assert not csrf_token_is_valid(app, old, now=1_000_000 + 9 * 3600)
        assert not csrf_token_is_valid(app, "garbage", now=1_000_000)


@needs_flask
def test_debug_mode_is_always_off(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    app = create_app({"SECRET_KEY": "x" * 40, "DEBUG": True})
    assert app.debug is False
    monkeypatch.setenv("FLASK_DEBUG", "1")
    with pytest.raises(RuntimeError, match="debug mode is not allowed"):
        create_app({"SECRET_KEY": "x" * 40})


@needs_flask
def test_secret_key_is_derived_from_the_service_api_key(monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("CIVIS_API_KEY", FAKE_API_KEY)
    app = create_app()
    assert app.config["SECRET_KEY"] and FAKE_API_KEY not in app.config["SECRET_KEY"]
    monkeypatch.delenv("CIVIS_API_KEY")
    with pytest.raises(RuntimeError, match="No secret key"):
        create_app()


def test_templates_and_static_files_have_no_inline_code_or_cdn_links():
    offenders = []
    for path in list((ROOT / "app" / "templates").rglob("*.html")) + list((ROOT / "app" / "static").rglob("*")):
        if not path.is_file() or path.suffix not in (".html", ".css", ".js"):
            continue  # Font files and their license are not code.
        # The only external address allowed: the two links to the Expedition Data website.
        text = path.read_text(encoding="utf-8").replace('href="https://www.expedition-data.com/"', "")
        for pattern in (r"<script(?![^>]*\bsrc=)", r"<style", r"\sstyle=", r"\son[a-z]+=", r"https?://", r"//cdn"):
            if re.search(pattern, text, re.IGNORECASE):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}: {pattern}")
    assert offenders == []
