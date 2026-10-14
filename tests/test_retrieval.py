"""Phase 3: rate limiting, safe HTTP, GitHub, notebooks, inline code, logs, and per-object scanning.

Everything runs against synthetic data. No network calls.
"""

from __future__ import annotations

import io
import json
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.models import Impact, ObjectType, ScanStatus
from scanner.engine import Engine
from scanner.inventory import build_inventory
from scanner.inventory.workflows import InlineTask
from scanner.logs import in_lookback, read_log
from scanner.process import PARSE_NOTE, ScanDeps, process_item
from scanner.ratelimit import BudgetedClient, RequestBudget
from scanner.retrieval import github as gh
from scanner.retrieval.code import inline_units
from scanner.retrieval.http import CIVIS_FILE_HOSTS, GITHUB_HOSTS, FetchError, SafeHttp, host_allowed
from scanner.retrieval.notebooks import NotebookUnavailable, fetch_notebook
from scanner.synthetic import FakeAPIError, FakeClient, SyntheticSession
from scanner.synthetic.http import SyntheticResponse, build_tarball

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# --- Rate limiting -------------------------------------------------------------


def test_budget_caps_requests_per_window():
    clock = FakeClock()
    budget = RequestBudget(3, 300, clock=clock.time, sleep=clock.sleep)
    for _ in range(3):
        budget.acquire()
    assert clock.sleeps == []
    budget.acquire()  # The fourth request waits for the window to roll over.
    assert clock.sleeps == [300]
    assert budget.requests == 4


def test_budget_slows_to_half_speed_when_headroom_is_low():
    clock = FakeClock()
    budget = RequestBudget(100, 300, clock=clock.time, sleep=clock.sleep)
    budget.observe(remaining=150, limit=1000)  # 15 percent left.
    assert budget.slow
    budget.acquire()
    budget.acquire()
    assert clock.sleeps == [pytest.approx(2 * budget.interval)]
    budget.observe(remaining=900, limit=1000)
    assert not budget.slow
    budget.observe(remaining=None, limit=1000)
    assert not budget.slow


def test_budgeted_client_charges_calls_and_pages_and_reads_headers():
    class Response(list):
        calls_remaining = 50
        rate_limit = 1000

    class Endpoint:
        def get_python3(self, object_id):
            return Response([object_id])

        def list(self, iterator=False, limit=20, **kwargs):
            return iter(range(7))

    class Client:
        scripts = Endpoint()

    clock = FakeClock()
    budget = RequestBudget(1000, 300, clock=clock.time, sleep=clock.sleep)
    client = BudgetedClient(Client(), budget)
    client.scripts.get_python3(1)
    assert budget.requests == 1 and budget.slow  # 50 of 1,000 left.
    rows = list(client.scripts.list(iterator=True, limit=3))
    assert rows == list(range(7))
    assert budget.requests == 1 + 1 + 2  # First page, then one per later page.


def test_budget_is_thread_safe():
    budget = RequestBudget(10_000, 300)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: budget.acquire(), range(400)))
    assert budget.requests == 400


# --- Safe HTTP -------------------------------------------------------------------


class RecordingSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        return self.responses.pop(0)


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("api.github.com", True),
        ("codeload.github.com", True),
        ("evil-github.com", False),
        ("github.com.evil.test", False),
    ],
)
def test_github_host_allow_list(host, allowed):
    assert host_allowed(host, GITHUB_HOSTS) is allowed


@pytest.mark.parametrize(
    "host",
    [
        "s3.amazonaws.com",
        "s3.us-east-1.amazonaws.com",
        "s3.us-gov-west-1.amazonaws.com",
        "example-bucket.s3.amazonaws.com",
        "example.bucket.s3.eu-west-2.amazonaws.com",
        "files.civisanalytics.com",
        "example.civis.io",
    ],
)
def test_civis_file_hosts_allowed(host):
    assert host_allowed(host, CIVIS_FILE_HOSTS)


@pytest.mark.parametrize(
    "host",
    [
        "ec2.amazonaws.com",
        "example.amazonaws.com",
        "example.execute-api.us-east-1.amazonaws.com",
        "bucket.s3-website-us-east-1.amazonaws.com",
        "x.s3.evil.amazonaws.com",
        "s3.amazonaws.com.evil.test",
        "civisanalytics.com.evil.test",
        "example.test",
    ],
)
def test_civis_file_hosts_refused(host):
    assert not host_allowed(host, CIVIS_FILE_HOSTS)


@pytest.mark.parametrize("url", ["http://api.github.com/x", "https://example.test/x", "ftp://api.github.com/x"])
def test_disallowed_urls_are_refused_without_a_request(url):
    session = RecordingSession([])
    with pytest.raises(FetchError) as excinfo:
        SafeHttp(session).get(url, allowed_hosts=GITHUB_HOSTS, max_bytes=10)
    assert session.calls == []
    assert "example.test" not in excinfo.value.reason and "github" not in excinfo.value.reason


def test_redirects_are_checked_and_tokens_are_not_forwarded():
    session = RecordingSession(
        [
            SyntheticResponse(302, headers={"Location": "https://codeload.github.com/o/r/tar"}),
            SyntheticResponse(200, b"payload"),
        ]
    )
    body = SafeHttp(session).get(
        "https://api.github.com/repos/o/r/tarball", allowed_hosts=GITHUB_HOSTS, max_bytes=100,
        headers={"Authorization": "Bearer synthetic"},
    )
    assert body == b"payload"
    assert "Authorization" in session.calls[0][1] and "Authorization" not in session.calls[1][1]

    evil = RecordingSession([SyntheticResponse(302, headers={"Location": "https://evil.example.test/steal"})])
    with pytest.raises(FetchError):
        SafeHttp(evil).get("https://api.github.com/x", allowed_hosts=GITHUB_HOSTS, max_bytes=100)
    assert len(evil.calls) == 1


def test_size_cap_and_error_reasons_never_hold_the_url():
    big = RecordingSession([SyntheticResponse(200, b"x" * 50)])
    with pytest.raises(FetchError, match="size limit"):
        SafeHttp(big).get("https://api.github.com/x", allowed_hosts=GITHUB_HOSTS, max_bytes=10)
    refused = RecordingSession([SyntheticResponse(500)])
    with pytest.raises(FetchError) as excinfo:
        SafeHttp(refused).get("https://api.github.com/secret-path?token=abc", allowed_hosts=GITHUB_HOSTS, max_bytes=10)
    assert excinfo.value.status_code == 500 and "secret-path" not in str(excinfo.value)


# --- GitHub ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, owner, name",
    [
        ("github.com/example-org/example-etl.git", "example-org", "example-etl"),
        ("https://github.com/example-org/example-etl", "example-org", "example-etl"),
        ("https://user:secret@www.github.com/example-org/example-etl.git", "example-org", "example-etl"),  # pragma: allowlist secret
        ("git@github.com:example-org/example-etl.git", "example-org", "example-etl"),
    ],
)
def test_parse_github_url(url, owner, name):
    assert gh.parse_github_url(url) == gh.RepoAddress(owner, name)


@pytest.mark.parametrize(
    "url, reason",
    [
        ("gitlab.example.test/org/repo.git", gh.NOT_GITHUB),
        ("https://github.example-corp.test/org/repo", gh.NOT_GITHUB),
        ("github.com/only-owner", gh.BAD_ADDRESS),
        ("github.com/org/..", gh.BAD_ADDRESS),
        ("", gh.BAD_ADDRESS),
    ],
)
def test_parse_github_url_rejects(url, reason):
    with pytest.raises(gh.RepoUnavailable) as excinfo:
        gh.parse_github_url(url)
    assert excinfo.value.reason == reason


@pytest.fixture
def synthetic():
    client = FakeClient.from_fixture("synthetic_instance.json")
    return client, SyntheticSession(client.data)


def test_repo_download_is_cached_and_the_tarball_is_deleted(tmp_path, synthetic):
    client, session = synthetic
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path)
    first = repos.files("github.com/example-org/example-etl.git", "v1.2.0")
    second = repos.files("https://github.com/Example-Org/example-etl", "v1.2.0")
    assert first is second and repos.downloads == 1
    assert set(first.files) == {"etl/__init__.py", "etl/run.py", "etl/legacy.py", "Dockerfile", "pyproject.toml"}
    assert list(tmp_path.iterdir()) == []  # Tarball removed right after reading.
    api_calls = [u for u, _ in session.requests if "api.github.com" in u]
    assert api_calls == ["https://api.github.com/repos/example-org/example-etl/tarball/v1.2.0"]


def test_repo_failures_become_reasons(tmp_path, synthetic):
    _, session = synthetic
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path)
    with pytest.raises(gh.RepoUnavailable) as missing:
        repos.files("github.com/example-org/private-repo.git", "main")
    assert missing.value.reason == gh.NOT_FOUND
    no_token = gh.GitHubRepos(SafeHttp(session), None, tmp_path)
    with pytest.raises(gh.RepoUnavailable) as excinfo:
        no_token.files("github.com/example-org/example-etl.git", "main")
    assert excinfo.value.reason == gh.NO_TOKEN
    wrong_token = gh.GitHubRepos(SafeHttp(session), "wrong-token", tmp_path)
    with pytest.raises(gh.RepoUnavailable) as rejected:
        wrong_token.files("github.com/example-org/example-etl.git", "main")
    assert rejected.value.reason == gh.TOKEN_REJECTED


def test_rate_limit_stops_further_github_requests(tmp_path, synthetic):
    client, session = synthetic
    session.github["example-org/limited"] = {"rate_limited": True, "refs": {"main": {}}}
    clock = FakeClock()
    clock.now = 1_000.0
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path, clock=clock.time)
    with pytest.raises(gh.RepoUnavailable) as first:
        repos.files("github.com/example-org/limited.git", "main")
    assert first.value.reason == gh.RATE_LIMITED
    sent = len(session.requests)
    with pytest.raises(gh.RepoUnavailable) as second:
        repos.files("github.com/example-org/example-etl.git", "main")
    assert second.value.reason == gh.RATE_LIMITED
    assert len(session.requests) == sent  # No request until GitHub's reset time.


def test_temp_folder_problems_become_a_reason(tmp_path, synthetic):
    _, session = synthetic
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path / "missing-folder")
    with pytest.raises(gh.RepoUnavailable) as excinfo:
        repos.files("github.com/example-org/example-etl.git", "main")
    assert excinfo.value.reason == gh.NOT_SAVED


def test_archive_reader_skips_links_traversal_and_big_files(tmp_path, monkeypatch):
    path = tmp_path / "repo.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in [("top/ok.py", b"x = 1\n"), ("top/../evil.py", b"bad"), ("top/big.py", b"y" * 64)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("top/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    monkeypatch.setattr(gh, "MAX_FILE_BYTES", 32)
    repo = gh._read_archive(path)
    assert set(repo.files) == {"ok.py"} and repo.skipped_large == 1


def test_select_files_for_a_file_a_folder_and_the_cap():
    repo = gh.RepoFiles(
        files={
            "jobs/load.py": "", "jobs/requirements.txt": "", "requirements.txt": "", "Dockerfile": "",
            "app/a.py": "", "app/b.py": "", "app/c.py": "", "app/pyproject.toml": "", "other/x.py": "",
        }
    )
    one = gh.select_files(repo, "jobs/load.py")
    assert [p for p, _ in one.files] == ["jobs/load.py", "jobs/requirements.txt", "Dockerfile", "requirements.txt"]
    folder = gh.select_files(repo, "app/", file_cap=2)
    assert [p for p, _ in folder.files] == ["app/a.py", "app/b.py", "app/pyproject.toml", "Dockerfile", "requirements.txt"]
    assert folder.notes == ["Only the first 2 of 3 Python files were scanned."]
    missing = gh.select_files(repo, "jobs/gone.py")
    assert "not found" in missing.notes[0]


# --- Notebooks and inline code --------------------------------------------------------


def test_fetch_notebook(synthetic):
    client, session = synthetic
    url = next(n for n in client.data["notebooks"] if n["id"] == 401)["notebook_url"]
    assert json.loads(fetch_notebook(SafeHttp(session), url))["nbformat"] == 4
    with pytest.raises(NotebookUnavailable, match="HTTP 404"):
        fetch_notebook(SafeHttp(session), "https://example-notebooks.s3.amazonaws.com/missing.ipynb")
    with pytest.raises(NotebookUnavailable, match="not allowed"):
        fetch_notebook(SafeHttp(session), "https://files.example.test/notebook.ipynb")
    with pytest.raises(NotebookUnavailable, match="did not return a link"):
        fetch_notebook(SafeHttp(session), None)


def test_inline_units():
    assert inline_units(ObjectType.PYTHON_SCRIPT, {"source": "x = 1"}, git_backed=False)[0].label == "inline source"
    assert inline_units(ObjectType.PYTHON_SCRIPT, {"source": "x = 1"}, git_backed=True) == []
    (cmd,) = inline_units(ObjectType.CONTAINER_SCRIPT, {"docker_command": "pip install civis"}, git_backed=False)
    assert (cmd.kind, cmd.label) == ("shell", "docker command")
    (preview,) = inline_units(ObjectType.CUSTOM_SCRIPT, {"code_preview": "python run.py --x 1"}, git_backed=False)
    assert preview.kind == "shell"
    (backing,) = inline_units(ObjectType.CUSTOM_SCRIPT, {"code_preview": "p", "backing_source": "import civis"}, git_backed=False)
    assert backing.label == "template script source"
    tasks = [InlineTask("load", "civis.scripts.python3", True, source="import civis.ml")]
    (task,) = inline_units(ObjectType.WORKFLOW, {"inline_tasks": tasks}, git_backed=False)
    assert task.label == "workflow task load"


# --- Logs --------------------------------------------------------------------------------


def test_lookback_window():
    from common.models import CivisObject

    obj = CivisObject(1, ObjectType.PYTHON_SCRIPT, "x", "u", last_run_id=5, last_run_at="2026-04-01T00:00:00Z")
    assert in_lookback(obj, 200, NOW)
    assert not in_lookback(obj, 100, NOW)
    obj.last_run_at = None
    assert not in_lookback(obj, 200, NOW)


def test_job_logs_are_paged_and_capped(monkeypatch):
    from common.models import CivisObject
    import scanner.logs as logs

    entries = [{"id": i, "message": f"line {i}"} for i in range(1, 8)]
    client = FakeClient({"run_logs": {"1/5": entries}})
    obj = CivisObject(1, ObjectType.PYTHON_SCRIPT, "x", "u", last_run_id=5, last_run_at="2026-10-01T00:00:00Z")
    monkeypatch.setattr(logs, "LOG_PAGE_SIZE", 3)
    log = read_log(client, obj, None, 200, NOW)
    assert log.lines == [f"line {i}" for i in range(1, 8)] and log.note is None
    assert [k.get("last_id") for _, _, k in client.calls] == [None, 3, 6]
    monkeypatch.setattr(logs, "MAX_LOG_LINES", 3)
    capped = read_log(client, obj, None, 200, NOW)
    assert len(capped.lines) == 3 and "longer than" in capped.note


def test_log_errors_and_types_without_logs():
    from common.models import CivisObject

    client = FakeClient({"log_errors": {"1/5": 403}})
    job = CivisObject(1, ObjectType.CONTAINER_SCRIPT, "x", "u", last_run_id=5, last_run_at="2026-10-01T00:00:00Z")
    failed = read_log(client, job, None, 200, NOW)
    assert not failed.read and "HTTP 403" in failed.note
    notebook = CivisObject(2, ObjectType.NOTEBOOK, "n", "u", last_run_id=5, last_run_at="2026-10-01T00:00:00Z")
    assert read_log(client, notebook, None, 200, NOW) is None
    old = CivisObject(3, ObjectType.PYTHON_SCRIPT, "x", "u", last_run_id=5, last_run_at="2025-01-01T00:00:00Z")
    assert read_log(client, old, None, 200, NOW) is None


# --- Per-object scanning against the synthetic instance -----------------------------------


def scan_all(tmp_path, token="synthetic-github-token", workers=1):
    tmp_path.mkdir(parents=True, exist_ok=True)
    client = FakeClient.from_fixture("synthetic_instance.json")
    session = SyntheticSession(client.data)
    inventory = build_inventory(client)
    deps = ScanDeps(
        engine=Engine(),
        client=client,
        http=SafeHttp(session),
        repos=gh.GitHubRepos(SafeHttp(session), token, tmp_path),
        lookback_days=200,
        now=NOW,
    )
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda item: process_item(item, deps), inventory.items))
    return {r.obj.object_id: r for r in results}, client, session


def rules(result):
    return {(f.rule_id, f.source.value) for f in result.findings}


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    results, client, session = scan_all(tmp_path_factory.mktemp("repos"))
    return results


@pytest.mark.parametrize(
    "object_id, status",
    [
        (101, ScanStatus.SCANNED),
        (102, ScanStatus.SCANNED),
        (105, ScanStatus.SCANNED),
        (106, ScanStatus.SCANNED),
        (107, ScanStatus.NOT_SCANNED),
        (201, ScanStatus.SCANNED),
        (202, ScanStatus.PARTIAL),
        (205, ScanStatus.PARTIAL),
        (206, ScanStatus.PARTIAL),
        (301, ScanStatus.SCANNED),
        (302, ScanStatus.PARTIAL),
        (304, ScanStatus.SCANNED),
        (306, ScanStatus.SCANNED),
        (401, ScanStatus.SCANNED),
        (403, ScanStatus.NOT_SCANNED),
        (501, ScanStatus.SCANNED),
        (502, ScanStatus.NOT_SCANNED),
        (601, ScanStatus.SCANNED),
        (231, ScanStatus.SCANNED),
        (603, ScanStatus.NOT_SCANNED),
    ],
)
def test_every_object_ends_with_a_coverage_status(scanned, object_id, status):
    result = scanned[object_id]
    assert result.obj.scan_status is status
    if status is ScanStatus.SCANNED:
        assert result.coverage == []
    else:
        assert result.coverage and result.coverage[0].reason == result.obj.scan_note and result.obj.scan_note


def test_inline_code_and_log_findings(scanned):
    assert ("CV3-RENAME-USERS-LIST-ME", "code") in rules(scanned[101])
    assert ("CV3-RENAME-USERS-LIST-ME", "log") in rules(scanned[101])
    assert rules(scanned[105]) == {("PY315-STRPTIME-NO-YEAR", "log")}
    assert ("CV3-UNPINNED-INSTALL", "code") in rules(scanned[201])
    assert ("CV3-FEATURE-FLAGS", "code") in rules(scanned[302])
    assert rules(scanned[601]) == {("CV3-PARALLEL", "code")}


def test_git_backed_python_script_reads_the_repo_not_saved_source(scanned):
    found = {(f.rule_id, f.file) for f in scanned[106].findings}
    assert found == {
        ("CV3-PARALLEL", "jobs/load.py"),
        ("CV3-UNPINNED-INSTALL", "jobs/requirements.txt"),
        ("PY315-PINNED-PACKAGE-INSTALL", "requirements.txt"),
    }


def test_container_repo_with_unparseable_file_is_partial(scanned):
    result = scanned[202]
    files = {(f.rule_id, f.file) for f in result.findings}
    assert ("CV3-USE-PANDAS", "etl/run.py") in files
    assert ("CV3-RENAME-USERS-LIST-ME", "etl/legacy.py") in files
    assert ("CV3-UNPINNED-INSTALL", "Dockerfile") in files and ("CV3-UNPINNED-INSTALL", "pyproject.toml") in files
    assert PARSE_NOTE in result.obj.scan_note
    legacy = next(f for f in result.findings if f.file == "etl/legacy.py")
    assert legacy.confidence.value == "medium"  # Regex fallback lowers confidence.
    assert all(f.impact is Impact.ADVISORY for f in result.findings if f.rule_id != "CV3-UNPINNED-INSTALL")


def test_repo_problems_do_not_stop_the_scan(scanned):
    assert gh.NOT_GITHUB in scanned[205].obj.scan_note
    assert gh.NOT_FOUND in scanned[206].obj.scan_note
    assert scanned[206].findings == [] and scanned[205].findings == []


def test_notebook_cells_and_outputs(scanned):
    found = {(f.rule_id, f.source.value, f.file) for f in scanned[401].findings}
    assert ("CV3-UNPINNED-INSTALL", "code", "notebook file [cell 2]") in found
    assert ("CV3-USE-PANDAS", "code", "notebook file [cell 3]") in found
    assert ("CV3-USE-PANDAS", "notebook_output", "notebook file [cell 3 output]") in found
    assert "HTTP 404" in scanned[403].obj.scan_note


def test_service_repo_folder_and_current_deployment_log(scanned):
    found = {(f.rule_id, f.source.value, f.file) for f in scanned[501].findings}
    assert ("CV3-RENAME-USERS-LIST-ME", "code", "app/app.py") in found
    assert ("CV3-UNPINNED-INSTALL", "code", "app/requirements.txt") in found
    assert ("CV3-ML", "log", "deployment log (deployment 7001)") in found
    assert not any(f.file.startswith("scripts/") for f in scanned[501].findings)
    assert "no git repository" in scanned[502].obj.scan_note


def test_standalone_dbt_script_repo_and_log(scanned):
    found = {(f.rule_id, f.file) for f in scanned[231].findings}
    assert ("PY315-STRPTIME-NO-YEAR", "warehouse/models/score.py") in found


def test_dbt_template_repo(scanned):
    found = {(f.rule_id, f.file) for f in scanned[306].findings}
    assert ("PY315-STRPTIME-NO-YEAR", "warehouse/models/score.py") in found
    assert ("PY315-PINNED-PACKAGE-INSTALL", "warehouse/requirements.txt") in found


def test_missing_token_marks_repos_for_manual_review(tmp_path):
    results, _, session = scan_all(tmp_path, token=None)
    assert results[106].obj.scan_status is ScanStatus.NOT_SCANNED
    assert gh.NO_TOKEN in results[106].obj.scan_note
    assert results[202].obj.scan_status is ScanStatus.PARTIAL  # Its docker command was still scanned.
    assert not any("github.com" in url for url, _ in session.requests)


def test_concurrent_scan_matches_sequential(tmp_path):
    sequential, _, _ = scan_all(tmp_path / "a")
    parallel, _, session = scan_all(tmp_path / "b", workers=4)
    assert {k: (r.obj.scan_status, sorted(rules(r))) for k, r in sequential.items()} == {
        k: (r.obj.scan_status, sorted(rules(r))) for k, r in parallel.items()
    }
    downloads = [u for u, _ in session.requests if u.startswith("https://api.github.com")]
    assert len(downloads) == len(set(downloads))  # Each repo and ref downloaded once, even with 4 workers.


def test_no_log_or_code_text_in_results(scanned):
    text = repr([(r.obj.to_row(), [f.to_row() for f in r.findings], r.coverage) for r in scanned.values()])
    for secret in ("Synthetic-Log-Pw-7731", "synthetic.member@example.org", "make_backend_factory", "select 1"):
        assert secret not in text
