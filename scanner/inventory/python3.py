"""Python scripts.

Endpoints (docs/PLAN.md section 2): scripts.list(type="python3"),
scripts.get_python3, and scripts.get_python3_git. The detail response has no
field that says whether a script is git-backed, so get_python3_git is called
for every script. A git-backed script's own file can also be read through
Civis without a GitHub token (scanner/retrieval/civis_git.py); that happens
during the code scan, not here.
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


def inventory_python_scripts(ctx: InventoryContext) -> list[InventoryItem]:
    rows = listed_rows(ctx, ctx.client.scripts.list, ObjectType.PYTHON_SCRIPT, type="python3")
    return [python_item(ctx, row) for row in rows]


def python_item(ctx: InventoryContext, row: Any) -> InventoryItem:
    """Build one item from a list row, or from a job referenced by a workflow."""
    obj = base_object(row, ObjectType.PYTHON_SCRIPT)
    item = InventoryItem(obj)
    detail, reason = ctx.fetch(ctx.client.scripts.get_python3, obj.object_id)
    if detail is None:
        mark_not_scanned(obj, reason, ctx.inventory)
        return item
    apply_common(ctx, obj, detail)
    # Python scripts always run on datascience-python; only the tag can change.
    apply_image(obj, classify_image(ObjectType.PYTHON_SCRIPT, None, field_of(detail, "docker_image_tag")))
    item.code["source"] = field_of(detail, "source", "")

    git, _ = ctx.fetch(ctx.client.scripts.get_python3_git, obj.object_id)
    repo = field_of(field_of(git, "git_repo"), "repo_url")
    if repo:
        obj.git_repo = repo
        obj.git_ref = field_of(git, "git_ref") or field_of(git, "git_branch")
        obj.git_path = field_of(git, "git_path")
        item.code["git_ref_type"] = field_of(git, "git_ref_type")
        item.code["git_branch"] = field_of(git, "git_branch")  # Tells a hex branch name from a SHA.
    return item
