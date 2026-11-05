"""Custom scripts created from templates.

Endpoints (docs/PLAN.md section 2): scripts.list_custom, scripts.get_custom,
templates.get_scripts, then the backing script's get_python3, get_containers,
or get_dbt. Custom scripts have no image fields; they run on their backing
script's image. If the backing script is unreadable, the custom script's
code_preview is scanned instead (decision 5) and the impact is "unknown".

dbt templates: Civis runs dbt inside a Python container, so a dbt-backed custom
script is inventoried like a container script when its backing script runs on
a Civis default Python image. Otherwise it is counted as not Python. The dbt
script API returns only an image tag, so a blank or "latest" tag is treated as
the Civis default image.
"""

from __future__ import annotations

from typing import Any

from common.models import Impact, ObjectType, ScanStatus
from scanner.inventory.base import (
    InventoryContext,
    InventoryItem,
    apply_common,
    apply_image,
    base_object,
    classify_image,
    field_of,
    mark_not_scanned,
)

PYTHON_BACKING_TYPES = {"python3", "python", "container", "containers"}
DBT_BACKING_TYPES = {"dbt"}

_NOT_PYTHON = object()  # Returned by custom_item for scripts that cannot run Python.


class TemplateCache:
    """Many custom scripts share a template, so each template is read once."""

    def __init__(self, ctx: InventoryContext):
        self.ctx = ctx
        self._cache: dict[int, tuple[str | None, Any, str | None]] = {}

    def backing(self, template_id: int) -> tuple[str | None, Any, str | None]:
        """Returns (backing script type, backing script detail, reason it could not be read)."""
        if template_id not in self._cache:
            self._cache[template_id] = self._load(template_id)
        return self._cache[template_id]

    def _load(self, template_id: int) -> tuple[str | None, Any, str | None]:
        client = self.ctx.client
        template, reason = self.ctx.fetch(client.templates.get_scripts, template_id)
        if template is None:
            return None, None, f"The template could not be read. {reason}"
        script_id = field_of(template, "script_id")
        script_type = (field_of(template, "script_type") or "").lower()
        if "python" in script_type:
            method = client.scripts.get_python3
        elif "container" in script_type:
            method = client.scripts.get_containers
        elif "dbt" in script_type:
            method = client.scripts.get_dbt
        else:
            return script_type, None, "The template's backing script is not a Python, container, or dbt script."
        detail, reason = self.ctx.fetch(method, script_id)
        if detail is None:
            return script_type, None, f"The template's backing script could not be read. {reason}"
        return script_type, detail, None


def inventory_custom_scripts(ctx: InventoryContext) -> list[InventoryItem]:
    rows = ctx.list_all(ctx.client.scripts.list_custom)
    counts = ctx.inventory.count(ObjectType.CUSTOM_SCRIPT)
    templates = TemplateCache(ctx)
    items = []
    for row in rows:
        backing_type = (field_of(row, "backing_script_type") or "").lower()
        if backing_type and backing_type not in PYTHON_BACKING_TYPES | DBT_BACKING_TYPES:
            counts.add_not_python(backing_type)  # SQL, R, or JavaScript template.
            continue
        item = custom_item(ctx, row, templates)
        if item is _NOT_PYTHON:
            counts.add_not_python(backing_type or "dbt")
            continue
        items.append(item)
    return items


def custom_item(ctx: InventoryContext, row: Any, templates: TemplateCache | None = None):
    """Build one item from a list row, or from a job referenced by a workflow.

    Returns _NOT_PYTHON for a dbt-backed script that does not run on a Civis
    default Python image.
    """
    templates = templates or TemplateCache(ctx)
    obj = base_object(row, ObjectType.CUSTOM_SCRIPT)
    item = InventoryItem(obj)
    detail, reason = ctx.fetch(ctx.client.scripts.get_custom, obj.object_id)
    if detail is None:
        mark_not_scanned(obj, reason, ctx.inventory)
        return item
    apply_common(ctx, obj, detail)
    obj.template_id = field_of(detail, "from_template_id")
    item.code["code_preview"] = field_of(detail, "code_preview", "")

    problem = "The custom script has no template ID."
    backing_type, backing = None, None
    if obj.template_id is not None:
        backing_type, backing, problem = templates.backing(obj.template_id)
    if backing is None:
        obj.uses_default_image = None
        obj.impact = Impact.UNKNOWN
        obj.scan_status = ScanStatus.PARTIAL
        obj.scan_note = f"{problem} Scanned the code preview instead."
        return item

    obj.backing_script_id = field_of(backing, "id")
    backing_type = backing_type or ""
    if "dbt" in backing_type:
        # Only the tag is available; the image name is implied.
        image = classify_image(ObjectType.PYTHON_SCRIPT, None, field_of(backing, "docker_image_tag"))
        if not image.uses_default_image:
            return _NOT_PYTHON
        apply_image(obj, image)
        obj.git_repo = field_of(backing, "repo_http_uri")
        obj.git_ref = field_of(backing, "repo_ref") if obj.git_repo else None
        obj.git_path = field_of(field_of(backing, "dbt_project"), "project_dir") if obj.git_repo else None
    elif "python" in backing_type:
        apply_image(obj, classify_image(ObjectType.PYTHON_SCRIPT, None, field_of(backing, "docker_image_tag")))
        item.code["backing_source"] = field_of(backing, "source", "")
    else:
        apply_image(
            obj,
            classify_image(
                ObjectType.CONTAINER_SCRIPT,
                field_of(backing, "docker_image_name"),
                field_of(backing, "docker_image_tag"),
            ),
        )
        item.code["backing_docker_command"] = field_of(backing, "docker_command", "")
        obj.git_repo = field_of(backing, "repo_http_uri")
        obj.git_ref = field_of(backing, "repo_ref") if obj.git_repo else None
    return item
