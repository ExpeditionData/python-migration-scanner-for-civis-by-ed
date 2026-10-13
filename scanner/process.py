"""Scan one inventoried object: its code, its notebook file or repository, and its log.

Every object ends with a coverage status:

- scanned: all of its code was read and checked.
- partial: some code was checked, but something was skipped (the reason says what).
- not_scanned: none of its code could be read (the reason says why and asks
  for a manual review).

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
from scanner.retrieval.code import inline_units
from scanner.retrieval.github import DEFAULT_FILE_CAP, GitHubRepos, RepoUnavailable, select_files
from scanner.retrieval.http import SafeHttp
from scanner.retrieval.notebooks import NotebookUnavailable, fetch_notebook

MAX_NOTE_CHARS = 1000
PARSE_NOTE = "Some code could not be parsed as Python 3, so it was checked with simpler text rules."


@dataclass
class ScanDeps:
    engine: Engine
    client: Any  # A BudgetedClient in a real scan.
    http: SafeHttp
    repos: GitHubRepos
    lookback_days: int
    now: datetime
    file_cap: int = DEFAULT_FILE_CAP


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

    # Repository files (notebooks are read from the notebook file instead).
    if obj.object_type is ObjectType.SERVICE and not obj.git_repo:
        needed = True
        problems.append("The Service has no git repository set, so its code is inside its Docker image. Review it manually.")
    if obj.git_repo and obj.object_type is not ObjectType.NOTEBOOK:
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
            problems.append(exc.reason)

    # Most recent run log, or the Service's current deployment log.
    log = read_log(deps.client, obj, item.code.get("deployment_id"), deps.lookback_days, deps.now)
    if log is not None:
        if log.read:
            matches.extend(deps.engine.scan_log(log.lines, log.label))
        if log.note:
            problems.append(log.note)
        log.lines.clear()

    if not parsed_ok:
        problems.append(PARSE_NOTE)

    findings = deps.engine.to_findings(obj, matches)
    earlier = [obj.scan_note] if obj.scan_note else []
    note = " ".join(dict.fromkeys(earlier + problems)) or None
    if note and len(note) > MAX_NOTE_CHARS:
        note = note[: MAX_NOTE_CHARS - 3] + "..."

    if needed and not scanned:
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
