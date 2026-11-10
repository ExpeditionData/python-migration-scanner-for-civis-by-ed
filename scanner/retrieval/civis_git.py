"""Read a git-backed item's own file through the Civis API, with no GitHub token.

Python scripts and workflows stored in git have two read endpoints in the
civis-python 2.10 API spec:

- ``GET .../git/commits``: commits on the item's current branch
  (``scripts.list_python3_git_commits``, ``workflows.list_git_commits``). The
  spec describes one object, but civis-python lists these paths as returning an
  array, so both shapes are accepted and the newest commit is used.
- ``GET .../git/commits/{commit_hash}``: the item's file at that commit
  (``scripts.get_python3_git_commits``, ``workflows.get_git_commits``). The
  spec describes one object, but in the first live test Civis returned a JSON
  array with one object (``content`` as plain text, ``fileHash``, ``size``,
  ``type`` "file"), which civis 2.10 turns into a ``ListResponse``. Both
  shapes are accepted (docs/PLAN.md 17.7).

Civis reads the file from the repository itself, so this needs no GitHub token
and sends nothing to GitHub. Only the item's own file comes back, not the rest
of its repository. Container scripts, Services, and dbt scripts have no such
endpoints. The four method names are the same in civis 2.10 and v3
(docs/PLAN.md 17.1).

The file text stays in memory (SECURITY.md). It gets the same limit and
decoding as one file from a GitHub tarball: at most 2 MB, decoded as UTF-8 with
bad bytes replaced. A failure is logged through scan_log with the object ID,
the error type, and the HTTP status only, and returns a short reason for the
coverage note.

The ref is used as the commit only if it looks like a SHA (7 to 40 hex
characters), is not the item's branch name, and its ref type is not "tag".
Civis's ref type says only "branch" or "tag", and a branch can be named with
hex characters (for example 2024101), so the branch name is checked too.
Anything else is resolved with the commits endpoint.

A tag cannot be passed to the commits endpoint, which lists commits on the
item's branch. So a file read for a tag is read at the branch's newest commit,
and the read returns a note that it may not match the tag.
"""

from __future__ import annotations

import base64
import binascii
import re
import threading
from typing import Any

from common.models import ObjectType
from scanner import scan_log
from scanner.inventory.base import field_of
from scanner.logs import parse_time
from scanner.retrieval.github import MAX_FILE_BYTES

SHA = re.compile(r"[0-9a-fA-F]{7,40}")
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")

# Object type -> (client endpoint, commits method, file method).
ENDPOINTS = {
    ObjectType.PYTHON_SCRIPT: ("scripts", "list_python3_git_commits", "get_python3_git_commits"),
    ObjectType.WORKFLOW: ("workflows", "list_git_commits", "get_git_commits"),
}
ITEM_NAMES = {ObjectType.PYTHON_SCRIPT: "script", ObjectType.WORKFLOW: "workflow"}
TAG_READ_AT_BRANCH = (
    "The {item}'s git reference is a tag, and Civis lists commits on the branch, so the file was read "
    "at the branch's newest commit and may not match the tag."
)


class GitCommitNotFound(Exception):
    """The commits endpoint returned no commit to read."""


class GitFileEmpty(Exception):
    """Civis returned no file content."""


class GitFileTooLarge(Exception):
    """The file is over MAX_FILE_BYTES."""


class GitFileAmbiguous(Exception):
    """Civis returned more than one item, and not exactly one of them is a file with content."""


class CivisGitFiles:
    """Reads files through the Civis git endpoints and counts reads and failures. Thread-safe."""

    def __init__(self, client: Any):
        self._client = client  # A BudgetedClient in a real scan, so every call counts in api_calls.
        self._lock = threading.Lock()
        self.reads = 0
        self.failures = 0

    def read(
        self, object_type: ObjectType, object_id: int, ref: str | None, ref_type: str | None = None,
        branch: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Returns (file text, None); (file text, note) if the file was read but may not
        match the git reference; or (None, reason) if the file could not be read."""
        endpoint_name, commits_method, file_method = ENDPOINTS[object_type]
        endpoint = getattr(self._client, endpoint_name)
        try:
            commit, from_branch = _commit(endpoint, commits_method, object_id, ref, ref_type, branch)
            text = _file_text(getattr(endpoint, file_method)(object_id, commit))
        except Exception as exc:
            with self._lock:
                self.failures += 1
            scan_log.civis_git_failed(object_type, object_id, exc)
            return None, _reason(exc, ITEM_NAMES[object_type], ref_type)
        with self._lock:
            self.reads += 1
        if from_branch and _is_tag(ref_type):
            return text, TAG_READ_AT_BRANCH.format(item=ITEM_NAMES[object_type])
        return text, None


def _commit(
    endpoint: Any, commits_method: str, object_id: int, ref: str | None, ref_type: str | None, branch: str | None
) -> tuple[str, bool]:
    """The commit to read, and whether it is the newest commit on the branch.

    A commit SHA set in Civis is used as is. The branch name, a tag, and
    anything else are resolved with the commits endpoint."""
    ref = (ref or "").strip()
    if SHA.fullmatch(ref) and ref != (branch or "").strip() and not _is_tag(ref_type):
        return ref, False
    response = getattr(endpoint, commits_method)(object_id)
    commits = list(response) if isinstance(response, (list, tuple)) else [response]
    dated = []
    for commit in commits:
        sha = field_of(commit, "commit_hash")
        if isinstance(sha, str) and SHA.fullmatch(sha):
            dated.append((parse_time(field_of(commit, "date")), sha))
    if not dated:
        raise GitCommitNotFound()
    if all(when is not None for when, _ in dated):
        return max(dated, key=lambda pair: pair[0])[1], True
    return dated[0][1], True


def _is_tag(ref_type: str | None) -> bool:
    return (ref_type or "").strip().lower() == "tag"


def _file_item(response: Any) -> Any:
    """The one file in the file call's answer (docs/PLAN.md 17.7).

    The API spec describes one object, but Civis returns a JSON array with one object, which civis 2.10 turns
    into a civis.ListResponse. Both shapes are accepted: a list (or tuple) with no items is GitFileEmpty, one
    item is that item, and with more than one, the one item whose type is "file" and whose content is not blank
    is used; if there is not exactly one such item, GitFileAmbiguous."""
    if not isinstance(response, (list, tuple)):
        return response
    items = list(response)
    if not items:
        raise GitFileEmpty()
    if len(items) == 1:
        return items[0]
    files = [item for item in items if field_of(item, "type") == "file" and _has_text(field_of(item, "content"))]
    if len(files) != 1:
        raise GitFileAmbiguous()
    return files[0]


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _file_text(response: Any) -> str:
    response = _file_item(response)
    content = field_of(response, "content")
    if not isinstance(content, str) or not content.strip():
        raise GitFileEmpty()
    size = field_of(response, "size")
    if isinstance(size, int) and not isinstance(size, bool) and size > MAX_FILE_BYTES:
        raise GitFileTooLarge()
    data = _base64_bytes(content) or content.encode("utf-8", errors="replace")
    if len(data) > MAX_FILE_BYTES:
        raise GitFileTooLarge()
    return data.decode("utf-8", errors="replace")


def _base64_bytes(content: str) -> bytes | None:
    """Decoded bytes if the content is base64 (as GitHub's contents API sends it), else None.

    Code and YAML always have spaces or punctuation outside the base64
    alphabet, so they are never mistaken for it.
    """
    if " " in content or "\t" in content:
        return None
    compact = "".join(content.split())
    if len(compact) % 4 or not _BASE64.fullmatch(compact):
        return None
    try:
        data = base64.b64decode(compact, validate=True)
        data.decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    return data


def _reason(exc: Exception, item: str, ref_type: str | None) -> str:
    start = f"The {item}'s own file could not be read through Civis"
    if isinstance(exc, GitCommitNotFound):
        if _is_tag(ref_type):
            return f"{start}: its git reference is a tag, and Civis returned no commit for it."
        return f"{start}: Civis returned no commit for its git branch."
    if isinstance(exc, GitFileEmpty):
        return f"{start}: Civis returned an empty file."
    if isinstance(exc, GitFileTooLarge):
        return f"{start}: the file is over 2 MB."
    if isinstance(exc, GitFileAmbiguous):
        return f"{start}: Civis returned more than one file."
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return f"{start} (HTTP {status})."
    return f"{start} ({type(exc).__name__})."
