"""Shared inventory helpers: field access, image classification, owners, errors.

Code text read during inventory (script source, container commands, template
code previews, workflow definitions) is kept only in ``InventoryItem.code``,
which lives in memory for the length of the run and is never written out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from common.models import CivisObject, CoverageCounts, CoverageItem, Impact, ObjectType, ScanStatus
from common.urls import object_url

DATASCIENCE_IMAGE = "civisanalytics/datascience-python"
JUPYTER_IMAGE = "civisanalytics/civis-jupyter-python3"
STREAMLIT_IMAGE = "civisanalytics/civis-services-streamlit"
SHINY_IMAGE = "civisanalytics/civis-services-shiny"

# Civis default images covered by the February 2027 update.
DEFAULT_IMAGES = frozenset({DATASCIENCE_IMAGE, JUPYTER_IMAGE, STREAMLIT_IMAGE})

# What Civis uses when an object has no image name set.
IMPLIED_IMAGE = {
    ObjectType.PYTHON_SCRIPT: DATASCIENCE_IMAGE,  # Python scripts have no image name field.
    ObjectType.CONTAINER_SCRIPT: DATASCIENCE_IMAGE,
    ObjectType.NOTEBOOK: JUPYTER_IMAGE,
    ObjectType.SERVICE: SHINY_IMAGE,
}

# Civis images that never run Python. Objects on them are left out of the inventory.
NON_PYTHON_IMAGES = frozenset({SHINY_IMAGE})

_REGISTRY_PREFIXES = ("docker.io/", "index.docker.io/", "registry-1.docker.io/", "registry.hub.docker.com/")

LIST_PAGE_SIZE = 50


def field_of(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a civis Response or a plain dict."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        value = obj.get(key, default)
    else:
        value = getattr(obj, key, default)
    return default if value is None else value


def normalize_image(name: str | None, tag: str | None) -> tuple[str | None, str | None]:
    """Lower-case the image name, drop the Docker Hub registry prefix, and split off a tag."""
    if not name or not name.strip():
        return None, tag
    name = name.strip().lower()
    for prefix in _REGISTRY_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
    last = name.rsplit("/", 1)[-1]
    if ":" in last and not (tag and tag.strip()):
        name, tag = name.rsplit(":", 1)
    return name, tag


def is_default_tag(tag: str | None) -> bool:
    return tag is None or tag.strip() in ("", "latest")


@dataclass(frozen=True)
class ImageClass:
    name: str | None
    tag: str | None
    uses_default_image: bool | None
    impact: Impact


def classify_image(object_type: ObjectType, name: str | None, tag: str | None) -> ImageClass:
    """Apply the approved default-image rule.

    A blank or "latest" tag on a known Civis default image counts as the default
    image. For Services, only the Streamlit image counts (decision 12).
    """
    name, tag = normalize_image(name, tag)
    effective = name or IMPLIED_IMAGE.get(object_type)
    if object_type is ObjectType.PYTHON_SCRIPT:
        effective = DATASCIENCE_IMAGE
    if effective is None:
        return ImageClass(None, tag, None, Impact.UNKNOWN)
    if object_type is ObjectType.SERVICE:
        uses_default = effective == STREAMLIT_IMAGE and is_default_tag(tag)
    else:
        uses_default = effective in DEFAULT_IMAGES and is_default_tag(tag)
    return ImageClass(effective, tag or None, uses_default, Impact.AFFECTED if uses_default else Impact.ADVISORY)


@dataclass
class UserInfo:
    name: str | None
    active: bool | None


@dataclass
class InventoryItem:
    obj: CivisObject
    # In-memory only. Keys used: source, docker_command, code_preview,
    # backing_source, backing_docker_command, notebook_url, definition,
    # deployment_id. Never written to tables, outputs, or logs.
    code: dict[str, Any] = field(default_factory=dict)


@dataclass
class Inventory:
    items: list[InventoryItem] = field(default_factory=list)
    counts: dict[ObjectType, CoverageCounts] = field(default_factory=dict)
    coverage: list[CoverageItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def count(self, object_type: ObjectType) -> CoverageCounts:
        if object_type not in self.counts:
            self.counts[object_type] = CoverageCounts(object_type)
        return self.counts[object_type]

    def by_id(self) -> dict[int, InventoryItem]:
        return {item.obj.object_id: item for item in self.items}


class InventoryContext:
    """Holds the client, the user lookup, and the inventory being built."""

    def __init__(self, client: Any, users: dict[int, UserInfo], inventory: Inventory, civis_git: Any = None):
        self.client = client
        self.users = users
        self.inventory = inventory
        self.civis_git = civis_git  # scanner.retrieval.civis_git.CivisGitFiles, or None to read no git files.

    def list_all(self, method: Callable, **kwargs) -> list:
        """List every non-archived, non-hidden row, following pages."""
        kwargs.setdefault("archived", "false")
        return list(method(iterator=True, limit=LIST_PAGE_SIZE, **kwargs))

    def count_hidden(self, method: Callable, visible_ids: set[int], **kwargs) -> int:
        """Count hidden rows without assuming whether hidden=True returns only hidden rows."""
        kwargs.setdefault("archived", "false")
        rows = method(iterator=True, limit=LIST_PAGE_SIZE, hidden=True, **kwargs)
        return len({field_of(r, "id") for r in rows} - visible_ids)

    def fetch(self, method: Callable, *args) -> tuple[Any, str | None]:
        """Call a detail endpoint. Returns (response, None) or (None, reason)."""
        try:
            return method(*args), None
        except Exception as exc:  # The scan must continue past one unreadable object.
            return None, describe_error(exc)

    def owner(self, person: Any) -> tuple[int | None, str | None, bool | None]:
        owner_id = field_of(person, "id")
        info = self.users.get(owner_id) if owner_id is not None else None
        name = field_of(person, "name") or field_of(person, "username") or (info.name if info else None)
        return owner_id, name, info.active if info else None


def describe_error(exc: Exception) -> str:
    """A short reason for coverage. Never includes the error message text."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return f"The scanning user cannot read this object (HTTP {status})."
    if status == 404:
        return "The object was not found when its details were requested (HTTP 404)."
    if status is not None:
        return f"The Civis API returned an error (HTTP {status})."
    return f"Reading this object failed ({type(exc).__name__})."


def schedule_summary(schedule: Any) -> tuple[bool, str | None]:
    """Short text for a script or workflow schedule, or a Service runtime plan."""
    if schedule is None:
        return False, None
    plan = field_of(schedule, "runtime_plan")
    if plan:
        return plan.lower().replace(" ", "_") not in ("on_demand", "ondemand"), plan
    if not field_of(schedule, "scheduled", False):
        return False, None
    parts = []
    for key, label in (
        ("scheduled_days", "days"),
        ("scheduled_days_of_month", "days of month"),
        ("scheduled_hours", "hours"),
        ("scheduled_minutes", "minutes"),
    ):
        values = field_of(schedule, key) or []
        if values:
            parts.append(f"{label} {','.join(str(v) for v in values)}")
    per_hour = field_of(schedule, "scheduled_runs_per_hour")
    if per_hour:
        parts.append(f"{per_hour} runs per hour")
    return True, "; ".join(parts) or "scheduled"


def last_run_fields(last_run: Any) -> tuple[int | None, str | None, str | None]:
    if last_run is None:
        return None, None, None
    when = field_of(last_run, "finished_at") or field_of(last_run, "started_at") or field_of(last_run, "created_at")
    return field_of(last_run, "id"), when, field_of(last_run, "state")


def mark_not_scanned(obj: CivisObject, reason: str, inventory: Inventory) -> None:
    obj.scan_status = ScanStatus.NOT_SCANNED
    obj.scan_note = reason
    inventory.coverage.append(CoverageItem(obj.object_type, obj.object_id, reason))


def base_object(row: Any, object_type: ObjectType) -> CivisObject:
    object_id = field_of(row, "id")
    return CivisObject(
        object_id=object_id,
        object_type=object_type,
        name=field_of(row, "name", ""),
        url=object_url(object_type, object_id),
        archived=bool(field_of(row, "archived", False)),
        hidden=bool(field_of(row, "hidden", False)),
    )


def apply_common(ctx: InventoryContext, obj: CivisObject, detail: Any, owner_field: str = "author") -> None:
    """Name, archived and hidden flags, owner, schedule, and last run from a detail response."""
    obj.name = field_of(detail, "name", obj.name)
    obj.archived = bool(field_of(detail, "archived", obj.archived))
    obj.hidden = bool(field_of(detail, "hidden", obj.hidden))
    obj.owner_id, obj.owner_name, obj.owner_active = ctx.owner(field_of(detail, owner_field))
    obj.scheduled, obj.schedule = schedule_summary(field_of(detail, "schedule"))
    obj.last_run_id, obj.last_run_at, obj.last_run_state = last_run_fields(field_of(detail, "last_run"))


def apply_image(obj: CivisObject, image: ImageClass) -> None:
    obj.docker_image_name = image.name
    obj.docker_image_tag = image.tag
    obj.uses_default_image = image.uses_default_image
    obj.impact = image.impact


def listed_rows(ctx: InventoryContext, method: Callable, object_type: ObjectType, **kwargs) -> list:
    """Every non-archived, non-hidden row, plus a count of hidden rows skipped (decision 11)."""
    rows = ctx.list_all(method, **kwargs)
    visible = {field_of(r, "id") for r in rows}
    ctx.inventory.count(object_type).hidden_skipped += ctx.count_hidden(method, visible, **kwargs)
    return rows
