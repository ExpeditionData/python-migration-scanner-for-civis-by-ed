"""Data models shared by the scanner and the web app.

These models hold metadata only. Code text is never stored on a model, so it
cannot end up in a table, a run output, or a log by accident.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class ObjectType(StrEnum):
    PYTHON_SCRIPT = "python_script"
    CONTAINER_SCRIPT = "container_script"
    CUSTOM_SCRIPT = "custom_script"
    DBT_SCRIPT = "dbt_script"
    NOTEBOOK = "notebook"
    SERVICE = "service"
    WORKFLOW = "workflow"


class Impact(StrEnum):
    AFFECTED = "affected"  # Runs on a Civis default image, so the February update applies.
    ADVISORY = "advisory"  # Runs on a custom or pinned image.
    UNKNOWN = "unknown"  # The image could not be determined.


class Level(StrEnum):
    """Used for both severity and confidence."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ScanStatus(StrEnum):
    SCANNED = "scanned"
    PARTIAL = "partial"  # Some code was scanned, some was not (see scan_note).
    NOT_SCANNED = "not_scanned"
    PENDING = "pending"  # Inventoried, code not read yet.


class FindingSource(StrEnum):
    CODE = "code"
    LOG = "log"
    NOTEBOOK_OUTPUT = "notebook_output"


class ChecklistStatus(StrEnum):
    OPEN = "open"
    FIXED = "fixed"
    IGNORED = "ignored"


@dataclass(frozen=True)
class WorkflowRef:
    """An active workflow that runs a job."""

    workflow_id: int
    name: str


# Keeps the workflows column well under its VARCHAR(4096) limit.
_WORKFLOWS_JSON_LIMIT = 3800
_WORKFLOW_NAME_LIMIT = 200


def workflows_json(workflows: list[WorkflowRef] | tuple[WorkflowRef, ...]) -> str | None:
    """JSON list of {"id", "name"} for the workflows that run a job.

    Names are user-controlled text, so the web app must escape them. If the list
    is too long for the column, it is cut and a final {"more": n} entry says how
    many were left out.
    """
    if not workflows:
        return None
    entries: list[dict] = []
    for index, ref in enumerate(sorted(workflows, key=lambda r: r.workflow_id)):
        entry = {"id": ref.workflow_id, "name": ref.name[:_WORKFLOW_NAME_LIMIT]}
        if len(json.dumps(entries + [entry])) > _WORKFLOWS_JSON_LIMIT:
            entries.append({"more": len(workflows) - index})
            break
        entries.append(entry)
    return json.dumps(entries)


@dataclass
class CivisObject:
    """One inventoried Civis object. One row in the ``objects`` table."""

    object_id: int
    object_type: ObjectType
    name: str
    url: str
    owner_id: int | None = None
    owner_name: str | None = None
    owner_active: bool | None = None
    archived: bool = False
    hidden: bool = False
    scheduled: bool = False
    schedule: str | None = None
    last_run_id: int | None = None
    last_run_at: str | None = None
    last_run_state: str | None = None
    docker_image_name: str | None = None
    docker_image_tag: str | None = None
    uses_default_image: bool | None = None
    impact: Impact = Impact.UNKNOWN
    git_repo: str | None = None
    git_ref: str | None = None
    git_path: str | None = None
    template_id: int | None = None
    backing_script_id: int | None = None
    workflows: list[WorkflowRef] = field(default_factory=list)
    scan_status: ScanStatus = ScanStatus.PENDING
    scan_note: str | None = None

    def to_row(self) -> dict:
        row = asdict(self)
        row["object_type"] = str(self.object_type)
        row["impact"] = str(self.impact)
        row["scan_status"] = str(self.scan_status)
        row["workflows"] = workflows_json(self.workflows)
        return row

    @property
    def workflow_ids(self) -> list[int]:
        return sorted(ref.workflow_id for ref in self.workflows)


@dataclass(frozen=True)
class Finding:
    """One rule match. File, line number, and rule ID only. Never a code snippet."""

    object_id: int
    object_type: ObjectType
    rule_id: str
    file: str
    line_number: int | None
    source: FindingSource
    severity: Level
    confidence: Level
    impact: Impact
    fix_guidance: str
    # Active workflows that run this object, so an admin sees what else breaks.
    workflows: tuple[WorkflowRef, ...] = ()

    def to_row(self) -> dict:
        row = asdict(self)
        for key in ("object_type", "source", "severity", "confidence", "impact"):
            row[key] = str(row[key])
        row["workflows"] = workflows_json(self.workflows)
        return row


@dataclass(frozen=True)
class CoverageItem:
    """An object that was found but not fully scanned, and why."""

    object_type: ObjectType
    object_id: int | None
    reason: str


@dataclass
class CoverageCounts:
    """Counts per object type, for the ``coverage`` summary rows."""

    object_type: ObjectType
    found: int = 0
    scanned: int = 0
    archived_included: int = 0
    # Objects left out because they cannot run Python, by language
    # (for example {"r": 2, "sql": 5}).
    not_python: dict[str, int] = field(default_factory=dict)
    # Rows in this type's list that are custom scripts made from a template (from_template_id set, and the
    # custom script list returned the same ID). They are inventoried once, as custom scripts.
    template_copies: int = 0
    # Script rows that did not run or change within LOOKBACK_DAYS of the scan's start: no detail call.
    outside_window: int = 0

    def add_not_python(self, language: str | None) -> None:
        key = (language or "unknown").strip().lower() or "unknown"
        self.not_python[key] = self.not_python.get(key, 0) + 1


# The fixed coverage note for a list call that stopped partway (coverage row_kind "list_failed", docs/PLAN.md
# 17.12). Only the object type, the number of rows read, and the HTTP status or error type name are filled in,
# never text from Civis or an exception's message.
LIST_NAMES = {
    ObjectType.PYTHON_SCRIPT: "Python scripts",
    ObjectType.CONTAINER_SCRIPT: "container scripts",
    ObjectType.CUSTOM_SCRIPT: "custom scripts",
    ObjectType.DBT_SCRIPT: "dbt scripts",
    ObjectType.NOTEBOOK: "notebooks",
    ObjectType.SERVICE: "Services",
    ObjectType.WORKFLOW: "workflows",
}
LIST_FAILED_NOTE = (
    "Civis stopped returning the list of {names} after {rows} ({cause}). Any {names} after that point were not "
    "found or scanned. Run a new scan to check them."
)


def list_failed_note(object_type: ObjectType | str, rows_read: int, status: int | None, error_name: str) -> str:
    """The note for one type's failed list: the HTTP status when the error had one, else its type name."""
    rows = "1 row" if rows_read == 1 else f"{rows_read:,} rows"
    cause = f"HTTP {status}" if status is not None else error_name
    return LIST_FAILED_NOTE.format(names=LIST_NAMES[ObjectType(object_type)], rows=rows, cause=cause)
