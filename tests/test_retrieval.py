"""Phase 3: rate limiting, safe HTTP, GitHub, notebooks, inline code, logs, and per-object scanning.

Everything runs against synthetic data. No network calls.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.models import Impact, ObjectType, ScanStatus
from scanner import scan_log
from scanner.engine import Engine
from scanner.inventory import build_inventory
from scanner.inventory.workflows import InlineTask
from scanner.logs import in_lookback, read_log
from scanner.process import CIVIS_FILE_ONLY, PARSE_NOTE, ScanDeps, process_item
from scanner.ratelimit import BudgetedClient, RequestBudget
from scanner.retrieval import civis_git
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


# --- Repository cache cap (least recently used) ------------------------------------------------


def _cache_repos(session, sizes: dict[str, int]) -> dict[str, dict[str, str]]:
    """Add synthetic repositories, each with one Python file of ``lines`` short lines."""
    files = {}
    for name, lines in sizes.items():
        files[name] = {"pkg/mod.py": f"# {name}\n" + "x = 1\n" * lines}
        session.github[f"example-org/{name}"] = {"refs": {"main": files[name]}}
    return files


def _repo_bytes(files: dict[str, str]) -> int:
    return sum(sys.getsizeof(text) for text in files.values())


def _url(name: str) -> str:
    return f"github.com/example-org/{name}.git"


def test_cache_evicts_the_least_recently_used_repo(tmp_path, synthetic):
    _, session = synthetic
    files = _cache_repos(session, {"cache-a": 100, "cache-b": 100, "cache-c": 100})
    one = _repo_bytes(files["cache-a"])
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path, max_cache_bytes=2 * one + 10)
    repos.files(_url("cache-a"), "main")
    repos.files(_url("cache-b"), "main")
    repos.files(_url("cache-a"), "main")  # Now cache-b is the least recently used.
    repos.files(_url("cache-c"), "main")
    assert [key[1] for key in repos.cached_keys()] == ["cache-a", "cache-c"]
    assert repos.evictions == 1 and repos.downloads == 3
    assert repos.cache_bytes == repos.peak_cache_bytes == 2 * one


def test_an_evicted_repo_is_downloaded_again(tmp_path, synthetic):
    _, session = synthetic
    files = _cache_repos(session, {"cache-a": 100, "cache-b": 100})
    one = _repo_bytes(files["cache-a"])
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path, max_cache_bytes=one + 10)
    first = repos.files(_url("cache-a"), "main")
    repos.files(_url("cache-b"), "main")  # Evicts cache-a.
    again = repos.files(_url("cache-a"), "main")
    assert again is not first and again.files == first.files == files["cache-a"]
    assert repos.downloads == 3 and repos.evictions == 2
    tarballs = [u for u, _ in session.requests if u == "https://api.github.com/repos/example-org/cache-a/tarball/main"]
    assert len(tarballs) == 2


def test_eviction_never_takes_files_from_an_object_still_using_them(tmp_path, synthetic):
    _, session = synthetic
    files = _cache_repos(session, {"cache-a": 100, "cache-b": 100, "cache-c": 100})
    one = _repo_bytes(files["cache-a"])
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path, max_cache_bytes=one + 10)
    in_use = repos.files(_url("cache-a"), "main")  # An object starts scanning cache-a ...
    repos.files(_url("cache-b"), "main")  # ... while other objects evict it from the cache.
    repos.files(_url("cache-c"), "main")
    assert ("example-org", "cache-a", "main") not in repos.cached_keys()
    assert in_use.files == files["cache-a"] and in_use.text_bytes == one
    selection = gh.select_files(in_use, "pkg/mod.py")
    assert selection.files == [("pkg/mod.py", files["cache-a"]["pkg/mod.py"])]


def test_cache_bytes_never_go_over_the_cap(tmp_path, synthetic):
    _, session = synthetic
    sizes = {f"cache-{n}": lines for n, lines in enumerate([50, 400, 120, 900, 30, 250])}
    files = _cache_repos(session, sizes)
    cap = _repo_bytes(files["cache-3"]) + _repo_bytes(files["cache-1"]) // 2
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path, max_cache_bytes=cap)
    order = [3, 0, 1, 1, 5, 2, 3, 4, 0, 5, 1, 3, 2, 2, 4, 0, 3, 5, 1, 0]
    for n in order:
        repo = repos.files(_url(f"cache-{n}"), "main")
        assert repo.files == files[f"cache-{n}"]
        cached = [key[1] for key in repos.cached_keys()]
        assert repos.cache_bytes == sum(_repo_bytes(files[name]) for name in cached) <= cap
        assert cached[-1] == f"cache-{n}"  # The repo just used is the most recent.
    assert repos.peak_cache_bytes <= cap and repos.evictions > 0


def test_four_threads_share_the_capped_cache(tmp_path, synthetic):
    _, session = synthetic
    sizes = {f"cache-{n}": 60 + 40 * n for n in range(6)}
    files = _cache_repos(session, sizes)
    cap = 2 * max(_repo_bytes(f) for f in files.values())
    repos = gh.GitHubRepos(SafeHttp(session), "synthetic-github-token", tmp_path, max_cache_bytes=cap)
    names = [f"cache-{n % 6}" for n in (7 * i for i in range(120))]

    def use(name: str) -> bool:
        repo = repos.files(_url(name), "main")
        assert repos.peak_cache_bytes <= cap
        return repo.files == files[name]  # Still intact, even if evicted meanwhile.

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(use, names))
    assert repos.cache_bytes <= cap and repos.peak_cache_bytes <= cap
    assert repos.evictions > 0 and repos.downloads == repos.evictions + len(repos.cached_keys())
    assert list(tmp_path.iterdir()) == []  # Every tarball was deleted.


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


def scan_all(tmp_path, token="synthetic-github-token", workers=1, data=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    client = FakeClient(data) if data is not None else FakeClient.from_fixture("synthetic_instance.json")
    session = SyntheticSession(client.data)
    reader = civis_git.CivisGitFiles(client)
    inventory = build_inventory(client, civis_git=reader)
    deps = ScanDeps(
        engine=Engine(),
        client=client,
        http=SafeHttp(session),
        repos=gh.GitHubRepos(SafeHttp(session), token, tmp_path),
        lookback_days=200,
        now=NOW,
        civis_git=reader,
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
        (109, ScanStatus.PARTIAL),
        (110, ScanStatus.PARTIAL),
        (111, ScanStatus.NOT_SCANNED),
        (112, ScanStatus.NOT_SCANNED),
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
        (605, ScanStatus.SCANNED),
        (606, ScanStatus.PARTIAL),
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


def test_missing_token_marks_repos_for_manual_review(no_token):
    results, _, session = no_token
    assert results[106].obj.scan_status is ScanStatus.PARTIAL  # Its own file was read through Civis.
    assert results[106].obj.scan_note == f"{CIVIS_FILE_ONLY} without a GitHub token."
    assert results[202].obj.scan_status is ScanStatus.PARTIAL  # Its docker command was still scanned.
    assert gh.NO_TOKEN in results[202].obj.scan_note
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


# --- Git files read through Civis ---------------------------------------------------------

SHA_A = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"  # pragma: allowlist secret  gitleaks:allow (a made-up commit SHA)
SHA_B = "b2c3d4e5f60718293a4b5c6d7e8f9012345678a1"  # pragma: allowlist secret  gitleaks:allow (a made-up commit SHA)


@pytest.fixture
def git_log():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def git_client(entry: dict, key: str = "python3/5") -> FakeClient:
    return FakeClient({"civis_git": {key: entry}})


def test_civis_git_uses_a_sha_ref_as_is():
    client = git_client({"files": {SHA_A: "import civis\n"}})
    reader = civis_git.CivisGitFiles(client)
    assert reader.read(ObjectType.PYTHON_SCRIPT, 5, SHA_A[:7]) == ("import civis\n", None)
    assert [n for n, _, _ in client.calls] == ["scripts.get_python3_git_commits"]
    assert client.calls[0][1] == (5, SHA_A[:7])
    assert (reader.reads, reader.failures) == (1, 0)


def test_civis_git_reads_the_newest_commit_for_a_branch_or_tag():
    commits = [
        {"commit_hash": SHA_A, "author_name": "Example Analyst", "date": "2026-09-01T10:00:00Z", "message": "Older"},
        {"commit_hash": SHA_B, "author_name": "Example Analyst", "date": "2026-10-01T10:00:00Z", "message": "Newer"},
    ]
    client = git_client({"commits": commits, "files": {SHA_A: "old = 1\n", SHA_B: "new = 1\n"}})
    reader = civis_git.CivisGitFiles(client)
    assert reader.read(ObjectType.PYTHON_SCRIPT, 5, "main", "branch") == ("new = 1\n", None)
    tag_note = civis_git.TAG_READ_AT_BRANCH.format(item="script")
    assert reader.read(ObjectType.PYTHON_SCRIPT, 5, "v1.0.0", "tag") == ("new = 1\n", tag_note)
    assert tag_note.startswith("The script's git reference is a tag") and "may not match the tag" in tag_note
    assert "could not be read through Civis" not in tag_note  # TESTING 4.8 counts failures by that phrase.
    assert [n for n, _, _ in client.calls].count("scripts.list_python3_git_commits") == 2


def test_civis_git_accepts_one_commit_object_as_the_spec_describes():
    class OneCommit(FakeClient):
        def _scripts__list_python3_git_commits(self, object_id):
            return {"commit_hash": SHA_B, "author_name": "Example Analyst", "date": "2026-10-01T10:00:00Z", "message": "x"}

    client = OneCommit({"civis_git": {"python3/5": {"files": {SHA_B: "x = 1\n"}}}})
    assert civis_git.CivisGitFiles(client).read(ObjectType.PYTHON_SCRIPT, 5, "main") == ("x = 1\n", None)


def test_civis_git_workflow_endpoints():
    client = git_client({"commits": [{"commit_hash": SHA_A, "date": "2026-10-01T10:00:00Z"}],
                         "files": {SHA_A: "version: '2.0'\n"}}, key="workflows/9")
    text, reason = civis_git.CivisGitFiles(client).read(ObjectType.WORKFLOW, 9, "main")
    assert (text, reason) == ("version: '2.0'\n", None)
    assert [n for n, _, _ in client.calls] == ["workflows.list_git_commits", "workflows.get_git_commits"]


@pytest.mark.parametrize(
    "entry, ref, ref_type, logged, reason",
    [
        ({"files": {SHA_A: "x = 1"}, "file_error": 403}, SHA_A, None,
         "error=FakeAPIError status=403", " (HTTP 403)."),
        ({"commits_error": 404}, "main", "branch", "error=FakeAPIError status=404", " (HTTP 404)."),
        ({"commits": []}, "v2.0.0", "tag", "error=GitCommitNotFound",
         ": its git reference is a tag, and Civis returned no commit for it."),
        ({"commits": [{"commit_hash": "not-a-sha"}]}, "main", "branch", "error=GitCommitNotFound",
         ": Civis returned no commit for its git branch."),
        ({"files": {SHA_A: "   \n"}}, SHA_A, None, "error=GitFileEmpty", ": Civis returned an empty file."),
        ({"files": {SHA_A: {"content": "x = 1\n", "size": gh.MAX_FILE_BYTES + 1}}}, SHA_A, None,
         "error=GitFileTooLarge", ": the file is over 2 MB."),
        ({"files": {SHA_A: "x = 1\n" * (gh.MAX_FILE_BYTES // 6 + 1)}}, SHA_A, None,
         "error=GitFileTooLarge", ": the file is over 2 MB."),
    ],
)
def test_civis_git_failures_give_a_reason_and_log_the_error_type_only(git_log, entry, ref, ref_type, logged, reason):
    reader = civis_git.CivisGitFiles(git_client(entry))
    text, why = reader.read(ObjectType.PYTHON_SCRIPT, 5, ref, ref_type)
    assert text is None and why == "The script's own file could not be read through Civis" + reason
    assert (reader.reads, reader.failures) == (0, 1)
    assert git_log.getvalue() == f"CIVIS_GIT_FAILED object_type=python_script object_id=5 {logged}\n"


def base64_lines(text: str) -> str:
    raw = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return "\n".join(raw[i:i + 60] for i in range(0, len(raw), 60)) + "\n"


def test_civis_git_decodes_base64_content_like_a_github_file():
    code = "import civis\nclient = civis.APIClient()\n"
    client = git_client({"files": {SHA_A: base64_lines(code), SHA_B: "pass\n"}})
    reader = civis_git.CivisGitFiles(client)
    assert reader.read(ObjectType.PYTHON_SCRIPT, 5, SHA_A)[0] == code
    assert reader.read(ObjectType.PYTHON_SCRIPT, 5, SHA_B)[0] == "pass\n"  # Plain code is never decoded.


def test_civis_git_method_names_are_the_same_in_civis_2_10_and_v3():
    """civis-python names a GET "list_" when the response is an array. These paths are on its
    array list, which v3 keeps (only the legacy-name list is removed at v3)."""
    resources = pytest.importorskip("civis.resources._resources")
    if not hasattr(resources, "GET_PATHS_WITH_ARRAY_RESPONSE"):
        pytest.skip("this civis predates the list and get naming fix (the Service image)")
    spec = json.loads((Path(resources.__file__).parent / "civis_api_spec.json").read_text(encoding="utf-8"))
    for (endpoint, commits_method, file_method), base in (
        (civis_git.ENDPOINTS[ObjectType.PYTHON_SCRIPT], "scripts/python3/{id}/git/commits"),
        (civis_git.ENDPOINTS[ObjectType.WORKFLOW], "workflows/{id}/git/commits"),
    ):
        assert base in resources.GET_PATHS_WITH_ARRAY_RESPONSE
        assert base not in resources.LEGACY_PATHS_WITH_LIST_NAME
        assert base.split("/")[0] == endpoint
        for path, expected in ((base, commits_method), (base + "/{commit_hash}", file_method)):
            operation = spec["paths"]["/" + path]["get"]
            for legacy in (True, False):
                assert resources.parse_method_name("get", path, operation, use_legacy_names=legacy) == expected


def no_token_data(**overrides) -> dict:
    data = copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)
    data.setdefault("civis_git", {})["python3/106"] = {
        "commits": [{"commit_hash": SHA_A, "author_name": "Example Analyst", "date": "2026-10-01T10:00:00Z",
                     "message": "Example change"}],
        "files": {SHA_A: "import civis\nimport civis.parallel\n"},
        **overrides,
    }
    return data


def test_git_backed_script_without_a_token_is_read_through_civis(tmp_path):
    results, client, session = scan_all(tmp_path, token=None, data=no_token_data())
    result = results[106]
    assert result.obj.scan_status is ScanStatus.PARTIAL
    assert {(f.rule_id, f.file) for f in result.findings} == {("CV3-PARALLEL", "jobs/load.py")}
    assert result.obj.scan_note == f"{CIVIS_FILE_ONLY} without a GitHub token."
    assert [c for c in client.called("scripts.get_python3_git_commits") if c[0][0] == 106] == [((106, SHA_A), {})]
    assert not any("github.com" in url for url, _ in session.requests)


def test_git_backed_script_with_a_token_is_read_from_github_not_civis(tmp_path):
    results, client, _ = scan_all(tmp_path, data=no_token_data())
    assert results[106].obj.scan_status is ScanStatus.SCANNED
    assert not [n for n, a, _ in client.calls if "git_commits" in n and a[0] == 106]


def test_failed_civis_read_falls_back_to_the_github_reason(tmp_path):
    results, _, _ = scan_all(tmp_path, token=None, data=no_token_data(file_error=403))
    assert results[106].obj.scan_status is ScanStatus.NOT_SCANNED
    assert results[106].findings == []
    assert gh.NO_TOKEN in results[106].obj.scan_note and "(HTTP 403)" in results[106].obj.scan_note


def test_git_backed_workflow_findings_come_from_its_git_file(tmp_path):
    from tests.test_inventory import git_workflow_data
    results, _, session = scan_all(tmp_path, data=git_workflow_data())
    result = results[601]
    assert result.obj.scan_status is ScanStatus.SCANNED and result.coverage == []
    assert rules(result) == {("CV3-ML", "code")}
    assert not any("example-workflows" in url for url, _ in session.requests)  # Never read from GitHub.


def test_object_with_a_log_but_no_readable_code_is_partial(tmp_path):
    results, _, _ = scan_all(tmp_path, token=None)
    service = results[501]
    assert service.obj.scan_status is ScanStatus.PARTIAL
    assert service.obj.scan_note == f"{gh.NO_TOKEN} Only its deployment log (deployment 7001) was checked."
    assert rules(service) == {("CV3-ML", "log")}
    assert service.coverage[0].reason == service.obj.scan_note


def test_object_with_neither_code_nor_a_log_stays_not_scanned(tmp_path):
    results, _, _ = scan_all(tmp_path, token=None)
    script = results[111]  # Git-backed, Civis cannot read its file, and never run.
    assert script.obj.last_run_id is None
    assert script.obj.scan_status is ScanStatus.NOT_SCANNED and "Only its" not in script.obj.scan_note


def test_missing_token_reads_as_the_normal_setup():
    assert "normal setup" in gh.NO_TOKEN and "No GitHub token is attached" not in gh.NO_TOKEN


# --- Synthetic git-backed objects, with and without a token ----------------------------------


@pytest.fixture(scope="module")
def with_token(tmp_path_factory):
    return scan_all(tmp_path_factory.mktemp("with-token"))


@pytest.fixture(scope="module")
def no_token(tmp_path_factory):
    return scan_all(tmp_path_factory.mktemp("no-token"), token=None)


def git_calls(client, object_id: int) -> list[str]:
    return [name for name, args, _ in client.calls if "git_commits" in name and args[0] == object_id]


def test_with_a_token_github_is_read_and_civis_is_not(with_token):
    results, client, _ = with_token
    assert results[106].obj.scan_status is ScanStatus.SCANNED and git_calls(client, 106) == []


def test_with_a_token_a_github_404_is_read_through_civis(with_token):
    results, client, session = with_token
    result = results[109]
    assert any(url.startswith("https://api.github.com/repos/example-org/example-models/") for url, _ in session.requests)
    assert result.obj.scan_status is ScanStatus.PARTIAL
    assert result.obj.scan_note == f"{CIVIS_FILE_ONLY}. {gh.NOT_FOUND}"
    assert {(f.rule_id, f.file) for f in result.findings} == {("CV3-USE-PANDAS", "models/score.py")}


def test_sha_branch_and_tag_refs(with_token):
    results, client, _ = with_token
    assert git_calls(client, 109) == ["scripts.get_python3_git_commits"]  # A SHA ref needs no commits call.
    assert client.called("scripts.get_python3_git_commits")[0][0][1] == "5ca1ab1"
    assert git_calls(client, 111) == ["scripts.list_python3_git_commits", "scripts.get_python3_git_commits"]
    assert git_calls(client, 110) == ["scripts.list_python3_git_commits"]  # No commit for the tag.
    assert "its git reference is a tag, and Civis returned no commit for it." in results[110].obj.scan_note


def test_failed_civis_reads_fall_back_to_todays_behavior(with_token):
    results, _, _ = with_token
    for object_id, why in ((111, "(HTTP 403)."), (112, "the file is over 2 MB.")):
        result = results[object_id]
        assert result.obj.scan_status is ScanStatus.NOT_SCANNED and result.findings == []
        assert result.obj.scan_note.startswith(gh.NOT_FOUND) and why in result.obj.scan_note


def test_log_only_objects_are_partial(with_token, no_token):
    tag = with_token[0][110]  # Its file could not be read, but its run log was.
    assert tag.obj.scan_status is ScanStatus.PARTIAL
    assert tag.obj.scan_note.endswith("Only its run log (run 9010) was checked.")
    assert rules(tag) == {("CV3-RENAME-USERS-LIST-ME", "log")}
    assert no_token[0][501].obj.scan_status is ScanStatus.PARTIAL


def test_without_a_token_no_github_requests_and_scripts_read_through_civis(no_token):
    results, client, session = no_token
    assert not any("github.com" in url for url, _ in session.requests)
    assert {(f.rule_id, f.file) for f in results[106].findings} == {("CV3-PARALLEL", "jobs/load.py")}
    assert results[109].obj.scan_note == f"{CIVIS_FILE_ONLY} without a GitHub token."
    assert git_calls(client, 106) == ["scripts.list_python3_git_commits", "scripts.get_python3_git_commits"]


def test_git_backed_workflows_in_the_synthetic_instance(with_token):
    results, _, _ = with_token
    assert rules(results[605]) == {("CV3-RENAME-USERS-LIST-ME", "code")}
    assert rules(results[606]) == {("CV3-CONTAINERFUTURE", "code")}
    assert results[109].obj.workflow_ids == [605]


def test_script_on_a_tag_read_at_the_branch_head_is_partial_with_both_notes(tmp_path):
    data = copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)
    data["civis_git"]["python3/110"] = {
        "commits": [{"commit_hash": SHA_A, "author_name": "Example Analyst", "date": "2026-10-01T10:00:00Z",
                     "message": "Example change"}],
        "files": {SHA_A: "import civis\nimport civis.parallel\n"},
    }
    for token, github_note in ((None, " without a GitHub token."), ("synthetic-github-token", ". " + gh.NOT_FOUND)):
        results, _, _ = scan_all(tmp_path / str(bool(token)), token=token, data=copy.deepcopy(data))
        script = results[110]
        assert script.obj.scan_status is ScanStatus.PARTIAL
        assert script.obj.scan_note == (
            CIVIS_FILE_ONLY + github_note + " " + civis_git.TAG_READ_AT_BRANCH.format(item="script")
        )
        assert ("CV3-PARALLEL", "code") in rules(script)  # The read is kept.


def test_workflow_on_a_tag_read_at_the_branch_head_is_partial(tmp_path):
    from tests.test_inventory import git_workflow_data
    data = git_workflow_data()
    data["workflows_git"]["601"].update({"git_ref": "v1.0.0", "git_ref_type": "tag"})
    results, _, _ = scan_all(tmp_path, data=data)
    result = results[601]
    assert result.obj.scan_status is ScanStatus.PARTIAL
    assert result.obj.scan_note == civis_git.TAG_READ_AT_BRANCH.format(item="workflow")
    assert rules(result) == {("CV3-ML", "code")}  # Still read from the git file.


@pytest.mark.parametrize(
    "ref, ref_type, branch, commits_called, sent",
    [
        ("2024101", "branch", "2024101", True, SHA_B),  # A branch named with hex characters.
        (SHA_A[:7], "branch", "main", False, SHA_A[:7]),  # A SHA ref; Civis still says "branch".
        ("abcdef1", "tag", "main", True, SHA_B),  # A tag named with hex characters.
        ("v1.0.0", "tag", "main", True, SHA_B),
        ("main", "branch", "main", True, SHA_B),
        ("main", None, None, True, SHA_B),
    ],
)
def test_civis_git_tells_a_sha_from_a_branch_or_tag(ref, ref_type, branch, commits_called, sent):
    client = git_client({"commits": [{"commit_hash": SHA_B, "date": "2026-10-01T10:00:00Z"}],
                         "files": {SHA_A: "a = 1\n", SHA_B: "b = 1\n"}})
    text, _ = civis_git.CivisGitFiles(client).read(ObjectType.PYTHON_SCRIPT, 5, ref, ref_type, branch)
    assert bool(client.called("scripts.list_python3_git_commits")) is commits_called
    assert client.called("scripts.get_python3_git_commits") == [((5, sent), {})]
    assert text == ("a = 1\n" if sent.startswith(SHA_A[:7]) else "b = 1\n")


def test_the_branch_name_reaches_the_reader(tmp_path):
    data = copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)
    data["python3_git"]["111"].update({"git_ref": "2024101", "git_branch": "2024101"})
    data["civis_git"]["python3/111"].pop("file_error")
    results, client, _ = scan_all(tmp_path, token=None, data=data)
    assert git_calls(client, 111) == ["scripts.list_python3_git_commits", "scripts.get_python3_git_commits"]
    assert results[111].obj.scan_status is ScanStatus.PARTIAL
