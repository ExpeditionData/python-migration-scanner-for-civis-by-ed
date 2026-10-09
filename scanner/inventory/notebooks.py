"""Notebooks.

Endpoints (docs/PLAN.md section 2): notebooks.list and notebooks.get. The
notebook file is downloaded from ``notebook_url`` in Phase 3. That link is
time-limited and is kept in memory only. Notebooks in languages other than
Python (for example R) are left out.
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


def _is_python(language: str | None) -> bool:
    return not language or "python" in language.lower()


def inventory_notebooks(ctx: InventoryContext) -> list[InventoryItem]:
    rows = listed_rows(ctx, ctx.client.notebooks.list, ObjectType.NOTEBOOK)
    items = []
    for row in rows:
        item = notebook_item(ctx, row)
        if item is None:
            ctx.inventory.count(ObjectType.NOTEBOOK).not_python += 1
        else:
            items.append(item)
    return items


def notebook_item(ctx: InventoryContext, row: Any) -> InventoryItem | None:
    if not _is_python(field_of(row, "language")):
        return None  # Checked on the list row first to save a detail call.
    obj = base_object(row, ObjectType.NOTEBOOK)
    item = InventoryItem(obj)
    detail, reason = ctx.fetch(ctx.client.notebooks.get, obj.object_id)
    if detail is None:
        mark_not_scanned(obj, reason, ctx.inventory)
        return item
    if not _is_python(field_of(detail, "language")):
        return None
    apply_common(ctx, obj, detail, owner_field="user")
    apply_image(
        obj,
        classify_image(ObjectType.NOTEBOOK, field_of(detail, "docker_image_name"), field_of(detail, "docker_image_tag")),
    )
    item.code["notebook_url"] = field_of(detail, "notebook_url")
    obj.git_repo = field_of(detail, "git_repo_url")
    if obj.git_repo:
        obj.git_ref = field_of(detail, "git_ref")
        obj.git_path = field_of(detail, "git_path")
    return item
