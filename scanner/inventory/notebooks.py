"""Notebooks.

Endpoints (docs/PLAN.md section 2): notebooks.list and notebooks.get. The
detail call gives the language, image, owner, and git fields. Its
``notebook_url`` is not kept: each link expires 5 minutes after Civis issues
it, so the code and log scan asks for a fresh one right before the download
(scanner/retrieval/notebooks.py, docs/PLAN.md 17.9). Notebooks in languages
other than Python (for example R) are left out.
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
    mark_not_scanned,
)


def _is_python(language: str | None) -> bool:
    return not language or "python" in language.lower()


def list_notebooks(ctx: InventoryContext) -> list:
    return ctx.list_all(ctx.client.notebooks.list)


def row_not_python(row: Any) -> str | None:
    """The language of a list row that is not a Python notebook (for example "r"), checked on the list row so
    it costs no detail call. None for a Python notebook."""
    language = field_of(row, "language")
    return None if _is_python(language) else language


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
    obj.git_repo = field_of(detail, "git_repo_url")
    if obj.git_repo:
        obj.git_ref = field_of(detail, "git_ref")
        obj.git_path = field_of(detail, "git_path")
    return item
