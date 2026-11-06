"""dbt scripts (not created from a template).

Endpoints: scripts.list(type="dbt") and scripts.get_dbt. Civis runs dbt inside
a Python container, so a dbt script is inventoried when it runs on a Civis
default Python image, the same rule as dbt templates (Phase 4 question 5).
The dbt script API returns only an image tag, so a blank or "latest" tag is
treated as the default image. Others are counted as not Python ("dbt").

The code to scan is the git repository: Python models and dependency files in
the dbt project folder.
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


def list_dbt_scripts(ctx: InventoryContext) -> list:
    return ctx.list_all(ctx.client.scripts.list, type="dbt")


def dbt_item(ctx: InventoryContext, row: Any) -> InventoryItem | None:
    """Build one item from a list row, or from a job referenced by a workflow.

    Returns None for a dbt script that does not run on a Civis default image.
    """
    obj = base_object(row, ObjectType.DBT_SCRIPT)
    item = InventoryItem(obj)
    detail, reason = ctx.fetch(ctx.client.scripts.get_dbt, obj.object_id)
    if detail is None:
        mark_not_scanned(obj, reason, ctx.inventory)
        return item
    image = classify_image(ObjectType.PYTHON_SCRIPT, None, field_of(detail, "docker_image_tag"))
    if not image.uses_default_image:
        return None
    apply_common(ctx, obj, detail)
    apply_image(obj, image)
    obj.git_repo = field_of(detail, "repo_http_uri")
    if obj.git_repo:
        obj.git_ref = field_of(detail, "repo_ref")
        obj.git_path = field_of(field_of(detail, "dbt_project"), "project_dir")
    return item
