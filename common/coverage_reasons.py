"""Fixed reasons for partly scanned and not scanned objects, worked out from a stored ``scan_note``.

The scanner builds every ``objects.scan_note`` from fixed texts (scanner/process.py,
scanner/logs.py, scanner/retrieval/github.py, scanner/retrieval/civis_git.py,
scanner/retrieval/notebooks.py, scanner/inventory/custom.py,
scanner/inventory/workflows.py, scanner/inventory/base.describe_error, and
scanner/runner.py) joined with spaces, with a few variable parts: counts, a run
or deployment ID, an HTTP status, and an exception class name. Each reason here
has a fixed label and one or more fragments of that fixed text. A fragment
never includes a variable part, so a note matches the same way on every scan,
and scans already stored need no new scan (docs/PLAN.md 17.19, Part D).

``reasons_for(note)`` gives the labels whose fragment the note contains, in the
order of ``REASONS``; "other" when the note is not empty and matches none; and
nothing when the note is empty. A note joined from several parts gets each
part's label. A note cut at the scanner's 1,000 characters still matches its
leading parts.

The last five reasons (object_unreadable to unexpected_error) name the kind of
Civis error. A note that says what could not be read (a run log, a template, a
notebook link) also gets one of them; a note from an object's own detail call
gets only that one.

This module runs on Python 3.12 and 3.14 and imports nothing from scanner/ or
app/. tests/test_coverage_reasons.py checks every fragment against the
scanner's own texts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OTHER = "other"


@dataclass(frozen=True)
class Reason:
    label: str
    fragments: tuple[str, ...]


REASONS: tuple[Reason, ...] = (
    # Repository code and GitHub (scanner/retrieval/github.py, scanner/process.py).
    Reason("no_github_token", (
        "because the scanner job has no GitHub token",  # github.NO_TOKEN
        "were not checked without a GitHub token",  # process.civis_file_note, no token
    )),
    Reason("own_file_through_civis_only", ("own file was checked, read through Civis",)),
    Reason("civis_git_read_failed", ("own file could not be read through Civis",)),
    Reason("git_tag_read_at_branch", (
        "Civis lists commits on the branch, so the file was read at the branch's newest commit and may not match "
        "the tag",
    )),
    Reason("github_not_found", (
        "GitHub returned 404: the repository or ref does not exist, or the GitHub token cannot read it",
    )),
    Reason("github_token_rejected", ("GitHub rejected the token (HTTP 401)",)),
    Reason("github_forbidden", ("GitHub denied access (HTTP 403)",)),
    Reason("github_rate_limited", ("GitHub's rate limit was reached",)),
    Reason("github_request_failed", ("The repository was not scanned.",)),
    Reason("github_archive_unreadable", ("The repository archive could not be read.",)),
    Reason("github_archive_not_saved", ("The repository archive could not be saved to the scanner's temp folder.",)),
    Reason("repo_not_on_github", ("The repository is not on github.com",)),
    Reason("repo_address_unreadable", ("The repository address could not be read",)),
    Reason("repo_path_file_not_found", ("The file set in Civis was not found in the repository.",)),
    Reason("repo_file_cap", ("Python files were scanned.",)),
    Reason("repo_large_files_skipped", ("file(s) over 2 MB were skipped.",)),
    Reason("repo_too_large", ("The repository is too large to read in full",)),
    Reason("repo_no_python_files", ("No Python or dependency files were found at the repository path set in Civis.",)),
    Reason("service_no_git_repo", ("The Service has no git repository set",)),
    Reason("dbt_no_git_repo", ("The dbt script has no git repository set",)),
    # Custom scripts and their templates (scanner/inventory/custom.py).
    Reason("custom_no_template_id", ("The custom script has no template ID.",)),
    Reason("template_unreadable", ("The template could not be read.",)),
    Reason("template_backing_unreadable", ("The template's backing script could not be read.",)),
    Reason("template_backing_not_python", ("The template's backing script is not a Python, container, or dbt script.",)),
    # Workflows (scanner/inventory/workflows.py).
    Reason("workflow_saved_definition", ("The definition saved in Civis was scanned instead.",)),
    Reason("workflow_definition_invalid_yaml", ("The workflow definition is not valid YAML.",)),
    Reason("workflow_definition_unexpected_structure", ("The workflow definition has an unexpected structure.",)),
    # Notebooks (scanner/retrieval/notebooks.py).
    Reason("notebook_no_link", ("Civis did not return a link to the notebook file.",)),
    Reason("notebook_link_failed", ("The link to the notebook file could not be requested from Civis.",)),
    Reason("notebook_download_failed", ("The notebook file could not be downloaded.",)),
    # Logs (scanner/logs.py, scanner/process.py).
    Reason("run_log_unreadable", ("The run log could not be read.",)),
    Reason("run_log_too_long", ("The run log is longer than",)),
    Reason("deployment_log_unreadable", ("The deployment log could not be read.",)),
    Reason("deployment_log_too_long", ("The deployment log has more than",)),
    Reason("only_log_checked", ("Only its run log (run ", "Only its deployment log (deployment ")),
    # Code checked with the text rules (scanner/process.PARSE_NOTE).
    Reason("parse_fallback", ("could not be parsed as Python 3, so it was checked with simpler text rules",)),
    # The kind of Civis error (scanner/inventory/base.describe_error), and an unexpected error (scanner/runner.py).
    Reason("object_unreadable", ("The scanning user cannot read this object",)),
    Reason("object_not_found", ("The object was not found when its details were requested",)),
    Reason("api_error", ("The Civis API returned an error",)),
    Reason("read_failed", ("Reading this object failed",)),
    Reason("unexpected_error", ("The scanner hit an unexpected error",)),
)

LABELS: tuple[str, ...] = tuple(reason.label for reason in REASONS)
LABEL_PATTERN = re.compile(r"^[a-z0-9_]+$")  # Every label matches it (tests/test_coverage_reasons.py).


def reasons_for(note: str | None) -> list[str]:
    """The labels whose fragment ``note`` contains, in REASONS order; ["other"] for a note that matches none;
    [] for an empty note."""
    if not isinstance(note, str) or not note.strip():
        return []
    found = [reason.label for reason in REASONS if any(fragment in note for fragment in reason.fragments)]
    return found or [OTHER]
