"""Scan one inventoried object: its code, its notebook file or repository, and its log.

Every object ends with a coverage status:

- scanned: all of its code was read and checked.
- partial: some code was checked, but something was skipped (the reason says
  what). Also an object whose code could not be read but whose most recent run
  log or deployment log was read and checked.
- not_scanned: neither its code nor a log could be read (the reason says why
  and asks for a manual review).

A git-backed Python script is read from GitHub when a token is attached. If
there is no token, or GitHub cannot be read, the script's own file is read
through the Civis API instead (scanner/retrieval/civis_git.py). That covers the
one file, not the rest of the repository, so the script is "partial".

Code and log text stay in memory for the length of this function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.models import CivisObject, CoverageItem, Finding, ObjectType, ScanStatus
from scanner import scan_log
from scanner.engine import Engine, Match, ScanResult
from scanner.inventory.base import InventoryItem
from scanner.logs import read_log
from scanner.retrieval.civis_git import CivisGitFiles
from scanner.retrieval.code import inline_units
from scanner.retrieval.github import DEFAULT_FILE_CAP, NO_TOKEN, GitHubRepos, RepoUnavailable, select_files
from scanner.retrieval.http import SafeHttp
from scanner.retrieval.notebooks import NotebookUnavailable, fetch_notebook

MAX_NOTE_CHARS = 1000
PARSE_NOTE = "Some code could not be parsed as Python 3, so it was checked with simpler text rules."
CIVIS_FILE_ONLY = (
    "Only the script's own file was checked, read through Civis. Other files in its repository "
    "(other modules, requirements.txt, pyproject.toml, Dockerfile) were not checked"
)


@dataclass
class ScanDeps:
    engine: Engine
    client: Any  # A BudgetedClient in a real scan.
    http: SafeHttp
    repos: GitHubRepos
    lookback_days: int
    now: datetime
    file_cap: int = DEFAULT_FILE_CAP
    civis_git: CivisGitFiles | None = None  # None: never read git files through Civis.


@dataclass
class ObjectResult:
    obj: CivisObject
    findings: list[Finding] = field(default_factory=list)
    coverage: list[CoverageItem] = field(default_factory=list)


def process_item(item: InventoryItem, deps: ScanDeps) -> ObjectResult:
    obj = item.obj
    if obj.scan_status is ScanStatus.NOT_SCANNED:
        # Inventory could not read the object at all.
        return ObjectResult(obj, [], [CoverageItem(obj.object_type, obj.object_id, obj.scan_note or "Not scanned.")])

    matches: list[Match] = []
    problems: list[str] = []
    needed = False
    scanned = False
    parsed_ok = True

    def take(result: ScanResult) -> None:
        nonlocal parsed_ok
        matches.extend(result.matches)
        if not result.fully_parsed:
            parsed_ok = False
            for error_type in result.parse_errors or ["SyntaxError"]:
                scan_log.parse_error_named(obj.object_type, obj.object_id, error_type)

    # Inline code.
    git_backed_python = obj.object_type is ObjectType.PYTHON_SCRIPT and bool(obj.git_repo)
    for unit in inline_units(obj.object_type, item.code, git_backed=git_backed_python):
        needed = True
        if unit.kind == "python":
            take(deps.engine.scan_python(unit.text, unit.label))
        else:
            take(deps.engine.scan_shell(unit.text, unit.label))
        scanned = True

    # Notebook file.
    if obj.object_type is ObjectType.NOTEBOOK:
        needed = True
        try:
            text = fetch_notebook(deps.http, item.code.get("notebook_url"))
            take(deps.engine.scan_notebook(text, "notebook file"))
            del text
            scanned = True
        except NotebookUnavailable as exc:
            problems.append(exc.reason)

    # Repository files. Notebooks are read from the notebook file instead, and a git-backed
    # workflow's definition was read through Civis during inventory.
    if obj.object_type is ObjectType.SERVICE and not obj.git_repo:
        needed = True
        problems.append("The Service has no git repository set, so its code is inside its Docker image. Review it manually.")
    if obj.object_type is ObjectType.DBT_SCRIPT and not obj.git_repo:
        needed = True
        problems.append("The dbt script has no git repository set, so its project could not be read. Review it manually.")
    if obj.git_repo and obj.object_type not in (ObjectType.NOTEBOOK, ObjectType.WORKFLOW):
        needed = True
        try:
            repo = deps.repos.files(obj.git_repo, obj.git_ref)
            selection = select_files(repo, obj.git_path, deps.file_cap)
            problems.extend(selection.notes)
            if not selection.files:
                problems.append("No Python or dependency files were found at the repository path set in Civis.")
            for path, text in selection.files:
                result = deps.engine.scan_file(path, text)
                if result is not None:
                    take(result)
                    scanned = True
        except RepoUnavailable as exc:
            text, civis_problem = _own_file_through_civis(item, deps)
            if text is not None:
                take(deps.engine.scan_python(text, obj.git_path or "script file"))
                del text
                scanned = True
                problems.append(civis_file_note(exc.reason))
                if civis_problem:
                    problems.append(civis_problem)  # The file was read for a tag at the branch's newest commit.
            else:
                problems.append(exc.reason)
                if civis_problem:
                    problems.append(civis_problem)

    # Most recent run log, or the Service's current deployment log.
    log = read_log(deps.client, obj, item.code.get("deployment_id"), deps.lookback_days, deps.now)
    log_read = False
    if log is not None:
        if log.read:
            matches.extend(deps.engine.scan_log(log.lines, log.label))
            log_read = True
        if log.note:
            problems.append(log.note)
        log.lines.clear()

    if not parsed_ok:
        problems.append(PARSE_NOTE)
    if needed and not scanned and log_read:
        problems.append(f"Only its {log.label} was checked.")

    findings = deps.engine.to_findings(obj, matches)
    earlier = [obj.scan_note] if obj.scan_note else []
    note = " ".join(dict.fromkeys(earlier + problems)) or None
    if note and len(note) > MAX_NOTE_CHARS:
        note = note[: MAX_NOTE_CHARS - 3] + "..."

    if needed and not scanned and not log_read:
        obj.scan_status = ScanStatus.NOT_SCANNED
    elif problems or obj.scan_status is ScanStatus.PARTIAL:
        obj.scan_status = ScanStatus.PARTIAL
    else:
        obj.scan_status = ScanStatus.SCANNED
    obj.scan_note = note

    coverage = []
    if obj.scan_status is not ScanStatus.SCANNED:
        coverage.append(CoverageItem(obj.object_type, obj.object_id, note or "Not fully scanned."))
    return ObjectResult(obj, findings, coverage)


def _own_file_through_civis(item: InventoryItem, deps: ScanDeps) -> tuple[str | None, str | None]:
    """A git-backed Python script's own file, read through the Civis API. Other types have no endpoint."""
    obj = item.obj
    if deps.civis_git is None or obj.object_type is not ObjectType.PYTHON_SCRIPT:
        return None, None
    return deps.civis_git.read(
        obj.object_type, obj.object_id, obj.git_ref, item.code.get("git_ref_type"), item.code.get("git_branch")
    )


def civis_file_note(github_reason: str) -> str:
    """The note for a script read through Civis: what was not checked, and why GitHub was not used."""
    if github_reason == NO_TOKEN:
        return f"{CIVIS_FILE_ONLY} without a GitHub token."
    return f"{CIVIS_FILE_ONLY}. {github_reason}"
