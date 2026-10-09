"""Container scripts.

Endpoints (docs/PLAN.md section 2): scripts.list(type="containers") and
scripts.get_containers. The code to scan is the docker command plus, when a
repo is attached, the repo files (fetched in Phase 3).
"""

from __future__ import annotations

from typing import Any

from common.models import ObjectType
from scanner.inventory.base import (
    InventoryContext,
    InventoryItem,
    apply_common,
    apply_image,
    base_object,
    classify_image,
    field_of,
    listed_rows,
    mark_not_scanned,
)


def inventory_container_scripts(ctx: InventoryContext) -> list[InventoryItem]:
    rows = listed_rows(ctx, ctx.client.scripts.list, ObjectType.CONTAINER_SCRIPT, type="containers")
    return [container_item(ctx, row) for row in rows]


def container_item(ctx: InventoryContext, row: Any) -> InventoryItem:
    """Build one item from a list row, or from a job referenced by a workflow."""
    obj = base_object(row, ObjectType.CONTAINER_SCRIPT)
    item = InventoryItem(obj)
    detail, reason = ctx.fetch(ctx.client.scripts.get_containers, obj.object_id)
    if detail is None:
        mark_not_scanned(obj, reason, ctx.inventory)
        return item
    apply_common(ctx, obj, detail)
    apply_image(
        obj,
        classify_image(
            ObjectType.CONTAINER_SCRIPT,
            field_of(detail, "docker_image_name"),
            field_of(detail, "docker_image_tag"),
        ),
    )
    item.code["docker_command"] = field_of(detail, "docker_command", "")
    obj.git_repo = field_of(detail, "repo_http_uri")
    obj.git_ref = field_of(detail, "repo_ref") if obj.git_repo else None
    return item
