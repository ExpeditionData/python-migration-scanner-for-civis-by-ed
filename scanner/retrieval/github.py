"""Read code from the client's own GitHub repositories.

One REST API tarball request per repository and ref, cached for the whole scan.
The tarball is streamed to a file inside the scan's private temp directory
(SECURITY.md requirement 3), the needed files are read into memory, and the
tarball is deleted right away. Nothing is extracted to disk, so a malicious
archive cannot write outside the temp directory.

Files kept: Python files, notebooks, requirements*.txt, pyproject.toml, and
Dockerfiles. Each file is capped at 2 MB.

Every problem becomes RepoUnavailable with a clear reason, never a crash:
non-GitHub hosts, a missing token, a repo the token cannot read (404),
rejected tokens, GitHub rate limits, and network errors. After a rate limit,
no more requests are sent until GitHub's reset time.
"""

from __future__ import annotations

import itertools
import tarfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from scanner.engine import kind_for_path
from scanner.retrieval.http import GITHUB_HOSTS, FetchError, SafeHttp

TARBALL_MAX_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_REPO_TEXT_BYTES = 100 * 1024 * 1024
DEFAULT_FILE_CAP = 500
DEPENDENCY_KINDS = ("requirements", "pyproject", "dockerfile")
KEPT_KINDS = ("python", "notebook", *DEPENDENCY_KINDS)

REVIEW = "Review it manually."
NOT_GITHUB = f"The repository is not on github.com, so it was not scanned. {REVIEW}"
BAD_ADDRESS = f"The repository address could not be read, so it was not scanned. {REVIEW}"
NO_TOKEN = f"No GitHub token is attached to the scanner job, so the repository was not scanned. {REVIEW}"
NOT_FOUND = (
    f"GitHub returned 404: the repository or ref does not exist, or the GitHub token cannot read it. {REVIEW}"
)
TOKEN_REJECTED = f"GitHub rejected the token (HTTP 401). It may be expired. {REVIEW}"
FORBIDDEN = f"GitHub denied access (HTTP 403). Check the token's permissions and organization SSO. {REVIEW}"
RATE_LIMITED = f"GitHub's rate limit was reached, so the repository was not scanned. {REVIEW}"
UNREADABLE_ARCHIVE = f"The repository archive could not be read. {REVIEW}"
NOT_SAVED = f"The repository archive could not be saved to the scanner's temp folder. {REVIEW}"


class RepoUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RepoAddress:
    owner: str
    name: str


def parse_github_url(url: str | None) -> RepoAddress:
    """Accepts github.com/org/repo(.git), https://..., and git@github.com:org/repo.git."""
    text = (url or "").strip()
    if not text:
        raise RepoUnavailable(BAD_ADDRESS)
    if text.startswith("git@"):
        host, _, path = text[4:].partition(":")
    else:
        if "://" not in text:
            text = "https://" + text
        parts = urlsplit(text)
        host, path = parts.hostname or "", parts.path
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "github.com":
        raise RepoUnavailable(NOT_GITHUB)
    pieces = [p for p in path.strip("/").split("/") if p]
    if len(pieces) < 2:
        raise RepoUnavailable(BAD_ADDRESS)
    owner, name = pieces[0], pieces[1].removesuffix(".git")
    if not all(_safe_segment(s) for s in (owner, name)):
        raise RepoUnavailable(BAD_ADDRESS)
    return RepoAddress(owner, name)


def _safe_segment(value: str) -> bool:
    return bool(value) and value not in (".", "..") and all(c.isalnum() or c in "-_." for c in value)


@dataclass
class RepoFiles:
    files: dict[str, str] = field(default_factory=dict)  # In memory only.
    skipped_large: int = 0
    truncated: bool = False


@dataclass
class Selection:
    files: list[tuple[str, str]]
    notes: list[str]


class GitHubRepos:
    """Cache of repository files for one scan. Thread-safe; each repo is downloaded once."""

    def __init__(
        self,
        http: SafeHttp,
        token: str | None,
        workdir: Path,
        clock: Callable[[], float] = time.time,
    ):
        self._http = http
        self._token = token or None
        self._workdir = workdir
        self._clock = clock
        self._lock = threading.Lock()
        self._results: dict[tuple, RepoFiles | RepoUnavailable] = {}
        self._pending: dict[tuple, threading.Event] = {}
        self._blocked_until = 0.0
        self._counter = itertools.count(1)
        self.downloads = 0

    def files(self, repo_url: str | None, ref: str | None) -> RepoFiles:
        address = parse_github_url(repo_url)
        if not self._token:
            raise RepoUnavailable(NO_TOKEN)
        key = (address.owner.lower(), address.name.lower(), ref or "")
        while True:
            with self._lock:
                if key in self._results:
                    result = self._results[key]
                    break
                event = self._pending.get(key)
                if event is None:
                    event = self._pending[key] = threading.Event()
                    owner = True
                else:
                    owner = False
            if owner:
                try:
                    result = self._download(address, ref)
                except RepoUnavailable as exc:
                    result = exc
                with self._lock:
                    # Rate limits are not cached per repo, so the repo is retried after the reset.
                    if not (isinstance(result, RepoUnavailable) and result.reason == RATE_LIMITED):
                        self._results[key] = result
                    self._pending.pop(key, None)
                event.set()
                break
            event.wait()
        if isinstance(result, RepoUnavailable):
            raise result
        return result

    def _download(self, address: RepoAddress, ref: str | None) -> RepoFiles:
        if self._clock() < self._blocked_until:
            raise RepoUnavailable(RATE_LIMITED)
        url = f"https://api.github.com/repos/{quote(address.owner)}/{quote(address.name)}/tarball"
        if ref:
            url += "/" + quote(ref, safe="")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        path = self._workdir / f"repo-{next(self._counter)}.tar.gz"
        try:
            self.downloads += 1
            self._http.get(url, allowed_hosts=GITHUB_HOSTS, max_bytes=TARBALL_MAX_BYTES, headers=headers, to_path=path)
            return _read_archive(path)
        except FetchError as exc:
            raise RepoUnavailable(self._reason(exc)) from None
        except OSError:
            raise RepoUnavailable(NOT_SAVED) from None
        finally:
            path.unlink(missing_ok=True)

    def _reason(self, exc: FetchError) -> str:
        status = exc.status_code
        if status == 404:
            return NOT_FOUND
        if status == 401:
            return TOKEN_REJECTED
        if status == 429 or (status == 403 and exc.headers.get("x-ratelimit-remaining") == "0"):
            reset = exc.headers.get("x-ratelimit-reset", "")
            retry_after = exc.headers.get("retry-after", "")
            if reset.isdigit():
                self._blocked_until = float(reset)
            elif retry_after.isdigit():
                self._blocked_until = self._clock() + float(retry_after)
            else:
                self._blocked_until = self._clock() + 3600
            return RATE_LIMITED
        if status == 403:
            return FORBIDDEN
        return f"{exc.reason} The repository was not scanned. {REVIEW}"


def _read_archive(path: Path) -> RepoFiles:
    result = RepoFiles()
    total = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue  # Skips links, devices, and folders.
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2 or any(p in ("..", "") for p in parts):
                    continue
                relative = "/".join(parts[1:])  # Drop GitHub's top-level folder.
                if kind_for_path(relative) not in KEPT_KINDS:
                    continue
                if member.size > MAX_FILE_BYTES:
                    result.skipped_large += 1
                    continue
                if total + member.size > MAX_REPO_TEXT_BYTES:
                    result.truncated = True
                    break
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                data = handle.read(MAX_FILE_BYTES + 1)
                total += len(data)
                result.files[relative] = data.decode("utf-8", errors="replace")
    except (tarfile.TarError, OSError, EOFError):
        raise RepoUnavailable(UNREADABLE_ARCHIVE) from None
    return result


def select_files(repo: RepoFiles, path: str | None, file_cap: int = DEFAULT_FILE_CAP) -> Selection:
    """Pick the files to scan for one object.

    - ``path`` names a .py or .ipynb file: that file, plus dependency files in
      its folder and at the repo root.
    - ``path`` names a folder, or is empty: every Python file under it up to
      ``file_cap`` (build brief decision 4), plus dependency files in that
      folder tree and at the repo root.
    """
    notes: list[str] = []
    clean = (path or "").strip().strip("/")
    kind = kind_for_path(clean) if clean else None
    chosen: list[str] = []
    if kind in ("python", "notebook"):
        if clean not in repo.files:
            notes.append("The file set in Civis was not found in the repository.")
        else:
            chosen.append(clean)
        folder = str(PurePosixPath(clean).parent)
        scope = "" if folder == "." else folder
        chosen += _dependency_files(repo, scope, recursive=False)
    else:
        scope = clean
        under = [p for p in sorted(repo.files) if _inside(p, scope)]
        python = [p for p in under if kind_for_path(p) == "python"]
        if len(python) > file_cap:
            notes.append(f"Only the first {file_cap} of {len(python)} Python files were scanned.")
            python = python[:file_cap]
        chosen += python
        chosen += [p for p in under if kind_for_path(p) in DEPENDENCY_KINDS]
    chosen += _dependency_files(repo, "", recursive=False)
    if repo.skipped_large:
        notes.append(f"{repo.skipped_large} file(s) over 2 MB were skipped.")
    if repo.truncated:
        notes.append("The repository is too large to read in full; some files were skipped.")
    unique = list(dict.fromkeys(chosen))
    return Selection([(p, repo.files[p]) for p in unique], notes)


def _inside(path: str, folder: str) -> bool:
    return not folder or path == folder or path.startswith(folder + "/")


def _dependency_files(repo: RepoFiles, folder: str, recursive: bool) -> list[str]:
    found = []
    for p in sorted(repo.files):
        parent = str(PurePosixPath(p).parent)
        parent = "" if parent == "." else parent
        in_scope = _inside(p, folder) if recursive else parent == folder
        if in_scope and kind_for_path(p) in DEPENDENCY_KINDS:
            found.append(p)
    return found
