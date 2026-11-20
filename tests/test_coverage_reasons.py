"""Fixed reasons for partly scanned and not scanned objects (docs/PLAN.md 17.19, Part D).

Every note the scanner can store in ``objects.scan_note`` is built here from
the scanner's own constants and functions, with made-up values for the
variable parts (counts, a run ID, a deployment ID, HTTP statuses, an exception
class), and must map to its expected label and never to "other". The dry runs
of the synthetic instance and the preview are checked the same way. No Civis
calls, synthetic data only.
"""

from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from common import coverage_reasons
from common.coverage_reasons import LABEL_PATTERN, LABELS, OTHER, REASONS, reasons_for


class StatusError(Exception):
    def __init__(self, status: int):
        super().__init__("made-up message that must not matter")
        self.status_code = status


class MadeUpError(Exception):
    pass


def _raise(exc):
    def call(*args, **kwargs):
        raise exc
    return call


# --- The module itself ------------------------------------------------------------------------


def test_every_label_is_fixed_and_unique():
    assert len(set(LABELS)) == len(LABELS) and OTHER not in LABELS
    assert all(LABEL_PATTERN.match(label) and re.fullmatch(r"^[a-z0-9_]+$", label) for label in LABELS)
    assert all(reason.fragments and all(f.strip() for f in reason.fragments) for reason in REASONS)


def test_empty_and_unknown_notes():
    assert reasons_for(None) == [] and reasons_for("") == [] and reasons_for("   ") == []
    assert reasons_for("A note the scanner never writes.") == [OTHER]


def test_the_module_imports_nothing_from_the_scanner_or_the_app():
    source = inspect.getsource(coverage_reasons)
    assert not re.search(r"^\s*(from|import)\s+(scanner|app)\b", source, re.MULTILINE)


# --- Every note text the scanner builds -------------------------------------------------------


def _describe_errors():
    from scanner.inventory.base import describe_error

    return {
        401: (describe_error(StatusError(401)), "object_unreadable"),
        403: (describe_error(StatusError(403)), "object_unreadable"),
        404: (describe_error(StatusError(404)), "object_not_found"),
        500: (describe_error(StatusError(500)), "api_error"),
        None: (describe_error(MadeUpError()), "read_failed"),
    }


def test_object_detail_errors():
    """mark_not_scanned stores describe_error's text alone (an object's own detail call failed)."""
    for note, label in _describe_errors().values():
        assert reasons_for(note) == [label], note


def _github_notes():
    from scanner.retrieval import github
    from scanner.retrieval.http import FetchError

    repos = github.GitHubRepos(http=None, token="made-up", workdir=Path("."), clock=lambda: 0.0)
    generic = [repos._reason(FetchError(reason, status)) for reason, status in (
        ("The request was refused (HTTP 500).", 500), ("The request failed (ConnectionError).", None),
        ("The link points to a host the scanner is not allowed to contact.", None), ("Too many redirects.", None),
        ("The file is larger than the scanner's size limit.", None), ("A redirect had no location.", 302))]
    return [
        (github.NO_TOKEN, ["no_github_token"]),
        (github.NOT_FOUND, ["github_not_found"]),
        (repos._reason(FetchError("x", 404)), ["github_not_found"]),
        (github.TOKEN_REJECTED, ["github_token_rejected"]),
        (repos._reason(FetchError("x", 401)), ["github_token_rejected"]),
        (github.FORBIDDEN, ["github_forbidden"]),
        (repos._reason(FetchError("x", 403)), ["github_forbidden"]),
        (github.RATE_LIMITED, ["github_rate_limited"]),
        (repos._reason(FetchError("x", 429)), ["github_rate_limited"]),
        (github.UNREADABLE_ARCHIVE, ["github_archive_unreadable"]),
        (github.NOT_SAVED, ["github_archive_not_saved"]),
        (github.NOT_GITHUB, ["repo_not_on_github"]),
        (github.BAD_ADDRESS, ["repo_address_unreadable"]),
    ] + [(note, ["github_request_failed"]) for note in generic]


def _selection_notes():
    from scanner.retrieval.github import RepoFiles, select_files

    repo = RepoFiles(files={f"pkg/m{i}.py": "x = 1\n" for i in range(5)}, skipped_large=3, truncated=True)
    missing = select_files(RepoFiles(files={"a.py": ""}), "made_up/missing.py").notes
    capped = select_files(repo, None, file_cap=2).notes
    assert len(missing) == 1 and len(capped) == 3
    return [
        (missing[0], ["repo_path_file_not_found"]),
        (capped[0], ["repo_file_cap"]),
        (capped[1], ["repo_large_files_skipped"]),
        (capped[2], ["repo_too_large"]),
    ]


def _process_literals():
    """Notes written as literals in scanner/process.py: each must be in its source."""
    from scanner import process

    source = inspect.getsource(process)
    literals = [
        ("The Service has no git repository set, so its code is inside its Docker image. Review it manually.",
         ["service_no_git_repo"]),
        ("The dbt script has no git repository set, so its project could not be read. Review it manually.",
         ["dbt_no_git_repo"]),
        ("No Python or dependency files were found at the repository path set in Civis.", ["repo_no_python_files"]),
    ]
    for text, _ in literals:
        assert text in source, text
    assert 'f"Only its {log.label} was checked."' in source
    return literals + [
        (f"Only its run log (run {run_id}) was checked.", ["only_log_checked"]) for run_id in (1, 98765)
    ] + [
        ("Only its deployment log (deployment 4321) was checked.", ["only_log_checked"]),
        (process.PARSE_NOTE, ["parse_fallback"]),
    ]


def _civis_file_notes():
    from scanner.process import civis_file_note
    from scanner.retrieval import github

    return [
        (civis_file_note(github.NO_TOKEN), ["no_github_token", "own_file_through_civis_only"]),
        (civis_file_note(github.NOT_FOUND), ["own_file_through_civis_only", "github_not_found"]),
        (civis_file_note(github.RATE_LIMITED), ["own_file_through_civis_only", "github_rate_limited"]),
        (civis_file_note(github.NOT_GITHUB), ["own_file_through_civis_only", "repo_not_on_github"]),
    ]


def _civis_git_notes():
    from scanner.retrieval import civis_git as cg

    notes = []
    for item in ("script", "workflow"):
        for exc, ref_type in ((cg.GitCommitNotFound(), "tag"), (cg.GitCommitNotFound(), "branch"),
                              (cg.GitFileEmpty(), None), (cg.GitFileTooLarge(), None), (cg.GitFileAmbiguous(), None),
                              (StatusError(403), None), (StatusError(404), None), (StatusError(500), None),
                              (MadeUpError(), None)):
            notes.append((cg._reason(exc, item, ref_type), ["civis_git_read_failed"]))
        notes.append((cg.TAG_READ_AT_BRANCH.format(item=item), ["git_tag_read_at_branch"]))
    return notes


def _log_notes(monkeypatch):
    from scanner import logs

    notes = []
    for status, (text, label) in _describe_errors().items():
        exc = StatusError(status) if status else MadeUpError()
        client = SimpleNamespace(jobs=SimpleNamespace(list_runs_logs=_raise(exc)),
                                 services=SimpleNamespace(list_deployments_logs=_raise(exc)))
        job = logs._read_job_log(client, 11, 22)
        service = logs._read_service_log(client, 33, 44)
        assert text in job.note and text in service.note
        notes.append((job.note, ["run_log_unreadable", label]))
        notes.append((service.note, ["deployment_log_unreadable", label]))

    monkeypatch.setattr(logs, "LOG_PAGE_SIZE", 2)
    monkeypatch.setattr(logs, "MAX_LOG_LINES", 4)
    monkeypatch.setattr(logs, "SERVICE_LOG_LIMIT", 2)
    ids = iter(range(1, 100))
    page = lambda *a, **k: [{"id": next(ids), "message": "made-up line"} for _ in range(2)]  # noqa: E731
    client = SimpleNamespace(jobs=SimpleNamespace(list_runs_logs=page),
                             services=SimpleNamespace(list_deployments_logs=page))
    long_job = logs._read_job_log(client, 11, 22).note
    long_service = logs._read_service_log(client, 33, 44).note
    assert long_job and long_service
    notes.append((long_job, ["run_log_too_long"]))
    notes.append((long_service, ["deployment_log_too_long"]))
    return notes


def _notebook_notes():
    from scanner.retrieval import notebooks
    from scanner.retrieval.http import FetchError

    notes = [(notebooks.NO_LINK, ["notebook_no_link"])]
    for status, (_, label) in _describe_errors().items():
        exc = StatusError(status) if status else MadeUpError()
        links = notebooks.NotebookLinks(SimpleNamespace(notebooks=SimpleNamespace(get=_raise(exc))))
        with pytest.raises(notebooks.NotebookUnavailable) as caught:
            links._link(7)
        notes.append((caught.value.reason, ["notebook_link_failed", label]))
    links = notebooks.NotebookLinks(SimpleNamespace(notebooks=SimpleNamespace(get=lambda _id: {"notebook_url": ""})))
    with pytest.raises(notebooks.NotebookUnavailable) as caught:
        links._link(7)
    notes.append((caught.value.reason, ["notebook_no_link"]))
    for status in (403, 404, 500):
        http = SimpleNamespace(get=_raise(FetchError(f"The request was refused (HTTP {status}).", status)))
        with pytest.raises(notebooks.NotebookUnavailable) as caught:
            notebooks.fetch_notebook(http, "https://made-up.example/n.ipynb")
        notes.append((caught.value.reason, ["notebook_download_failed"]))
    return notes


def _custom_notes():
    from scanner.inventory.base import Inventory, InventoryContext
    from scanner.inventory.custom import custom_item

    def note_for(template=None, backing=None, template_id=7):
        def get_template(_id):
            if isinstance(template, Exception):
                raise template
            return template
        client = SimpleNamespace(
            templates=SimpleNamespace(get_scripts=get_template),
            scripts=SimpleNamespace(get_custom=lambda _id: {"id": 5, "from_template_id": template_id, "code_preview": ""},
                                    get_python3=_raise(backing or MadeUpError()),
                                    get_containers=_raise(backing or MadeUpError()), get_dbt=_raise(MadeUpError())))
        item = custom_item(InventoryContext(client, {}, Inventory()), {"id": 5, "name": "made-up"})
        return item.obj.scan_note

    notes = [(note_for(template_id=None), ["custom_no_template_id"]),
             (note_for(template={"script_id": 9, "script_type": "sql"}), ["template_backing_not_python"])]
    for status, (_, label) in _describe_errors().items():
        exc = StatusError(status) if status else MadeUpError()
        notes.append((note_for(template=exc), ["template_unreadable", label]))
        notes.append((note_for(template={"script_id": 9, "script_type": "containers"}, backing=exc),
                      ["template_backing_unreadable", label]))
    return notes


def _workflow_notes():
    from scanner.inventory import workflows
    from scanner.retrieval import civis_git as cg

    invalid = workflows.parse_definition("tasks: [unclosed").error
    unexpected = workflows.parse_definition("- 1\n- 2\n").error
    read_failed = cg._reason(StatusError(404), "workflow", None)
    source = inspect.getsource(workflows)
    assert 'f"{note} {SAVED_COPY_NOTE}"' in source
    return [
        (workflows.SAVED_COPY_NOTE, ["workflow_saved_definition"]),
        (f"{read_failed} {workflows.SAVED_COPY_NOTE}", ["civis_git_read_failed", "workflow_saved_definition"]),
        (invalid, ["workflow_definition_invalid_yaml"]),
        (unexpected, ["workflow_definition_unexpected_structure"]),
        (f"{invalid} {read_failed} {workflows.SAVED_COPY_NOTE}",
         ["civis_git_read_failed", "workflow_saved_definition", "workflow_definition_invalid_yaml"]),
    ]


def _unexpected_error_note():
    from scanner import runner
    from scanner.inventory.base import InventoryItem
    from common.models import CivisObject, ObjectType

    item = InventoryItem(CivisObject(object_id=3, object_type=ObjectType.PYTHON_SCRIPT, name="made-up", url=""))
    original = runner.process_item
    runner.process_item = _raise(MadeUpError())
    try:
        result = runner._safe_process(item, deps=None)
    finally:
        runner.process_item = original
    return [(result.obj.scan_note, ["unexpected_error"])]


@pytest.fixture
def every_note(monkeypatch):
    notes = (_github_notes() + _selection_notes() + _process_literals() + _civis_file_notes() + _civis_git_notes()
             + _log_notes(monkeypatch) + _notebook_notes() + _custom_notes() + _workflow_notes()
             + _unexpected_error_note())
    assert all(isinstance(note, str) and note for note, _ in notes)
    return notes


def test_every_scanner_note_maps_to_its_labels(every_note):
    for note, expected in every_note:
        assert reasons_for(note) == [label for label in LABELS if label in expected], note
        assert OTHER not in reasons_for(note)


def test_every_label_is_reached_by_a_scanner_note(every_note):
    reached = {label for note, _ in every_note for label in reasons_for(note)}
    assert reached == set(LABELS)


def test_the_log_labels_in_only_log_checked_are_the_scanners():
    """only_log_checked's fragments hold the start of the log's label (scanner/logs.py)."""
    from scanner import logs

    source = inspect.getsource(logs)
    assert 'label=f"run log (run {run_id})"' in source
    assert 'label=f"deployment log (deployment {deployment_id})"' in source


def test_a_note_joined_from_several_parts_maps_to_each_part(every_note):
    from scanner.process import PARSE_NOTE
    from scanner.retrieval import github

    note = " ".join([github.NO_TOKEN, "The run log could not be read. The Civis API returned an error (HTTP 503).",
                     PARSE_NOTE])
    assert reasons_for(note) == ["no_github_token", "run_log_unreadable", "parse_fallback", "api_error"]
    for (a, a_labels), (b, b_labels) in zip(every_note, every_note[1:]):
        assert set(reasons_for(f"{a} {b}")) == set(a_labels) | set(b_labels), (a, b)


def test_a_note_cut_at_the_scanners_limit_still_maps_its_leading_parts():
    from scanner.process import MAX_NOTE_CHARS, PARSE_NOTE
    from scanner.retrieval import github

    parts = [github.NO_TOKEN, "The run log could not be read. The scanning user cannot read this object (HTTP 403)."]
    parts += [f"{n} file(s) over 2 MB were skipped." for n in range(40)] + [PARSE_NOTE]
    note = " ".join(parts)
    assert len(note) > MAX_NOTE_CHARS
    cut = note[: MAX_NOTE_CHARS - 3] + "..."  # As scanner/process.py cuts it.
    assert reasons_for(cut) == ["no_github_token", "repo_large_files_skipped", "run_log_unreadable",
                                "object_unreadable"]


def test_a_custom_script_on_a_git_backed_container_template_without_a_token():
    """The likely main reason for partly scanned custom scripts in real use: the template's backing container
    script is git-backed, and the scanner job has no GitHub token, so only its docker command is checked."""
    from datetime import datetime, timezone

    from common.models import CivisObject, ObjectType, ScanStatus
    from scanner.engine import Engine
    from scanner.inventory.base import InventoryItem
    from scanner.process import ScanDeps, process_item
    from scanner.retrieval.github import GitHubRepos
    from scanner.retrieval.http import SafeHttp

    obj = CivisObject(object_id=5, object_type=ObjectType.CUSTOM_SCRIPT, name="made-up", url="",
                      git_repo="https://github.com/made-up/repo")
    item = InventoryItem(obj, {"backing_docker_command": "python -m made_up"})
    deps = ScanDeps(engine=Engine(), client=None, http=SafeHttp(),
                    repos=GitHubRepos(SafeHttp(), None, Path(".")), lookback_days=30,
                    now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    result = process_item(item, deps)
    assert result.obj.scan_status is ScanStatus.PARTIAL
    assert reasons_for(result.obj.scan_note) == ["no_github_token"]


# --- The dry runs ------------------------------------------------------------------------------


def _notes(path: Path) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT object_type, scan_status, scan_note FROM objects").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("token", ["synthetic-github-token", None])
def test_no_dry_run_note_maps_to_other(tmp_path, token):
    from scanner.__main__ import dry_run

    dry_run(tmp_path, github_token=token)
    rows = _notes(tmp_path / "results.sqlite")
    noted = [(t, s, n) for t, s, n in rows if n]
    assert noted
    for object_type, status, note in noted:
        assert OTHER not in reasons_for(note), (object_type, status)
    assert all(not n for _, s, n in rows if s == "scanned")


def test_no_preview_note_maps_to_other(tmp_path):
    from scanner.synthetic.preview import build_preview

    build_preview(tmp_path)
    rows = _notes(tmp_path / "results.sqlite")
    assert any(n for _, _, n in rows)
    for object_type, status, note in rows:
        if note:
            assert OTHER not in reasons_for(note), (object_type, status)
    labels = {label for _, _, n in rows for label in reasons_for(n)}
    assert {"no_github_token", "own_file_through_civis_only"} <= labels
