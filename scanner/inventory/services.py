"""Services, including Streamlit apps.

Endpoints (docs/PLAN.md section 2): services.list and services.get. Services
have no inline code; their code comes from the git repo (Phase 3). The log scan
reads only the current deployment (decision 6 of the build brief), so its ID is
kept for later.

Only Streamlit Services on a blank or "latest" tag are "affected" (decision 12).
Services on Civis's R Shiny image, which is also the default when no image is
set, cannot run Python and are left out.
"""

from __future__ import annotations

from typing import Any

from common.models import ObjectType
from scanner.inventory.base import (
    NON_PYTHON_IMAGES,
    InventoryContext,
    InventoryItem,
    apply_common,
    apply_image,
    base_object,
    classify_image,
    field_of,
    mark_not_scanned,
)


def list_services(ctx: InventoryContext) -> list:
    return ctx.list_all(ctx.client.services.list)


def service_item(ctx: InventoryContext, row: Any) -> InventoryItem | None:
    obj = base_object(row, ObjectType.SERVICE)
    item = InventoryItem(obj)
    detail, reason = ctx.fetch(ctx.client.services.get, obj.object_id)
    if detail is None:
        mark_not_scanned(obj, reason, ctx.inventory)
        return item
    image = classify_image(ObjectType.SERVICE, field_of(detail, "docker_image_name"), field_of(detail, "docker_image_tag"))
    if image.name in NON_PYTHON_IMAGES:
        return None
    apply_common(ctx, obj, detail, owner_field="user")
    apply_image(obj, image)

    deployment = field_of(detail, "current_deployment")
    if deployment is not None:
        item.code["deployment_id"] = field_of(deployment, "deployment_id")
        obj.last_run_id = field_of(deployment, "deployment_id")
        obj.last_run_at = field_of(deployment, "created_at")
        obj.last_run_state = field_of(deployment, "state")

    obj.git_repo = field_of(detail, "git_repo_url")
    if obj.git_repo:
        obj.git_ref = field_of(detail, "git_repo_ref")
        obj.git_path = field_of(detail, "git_path_dir")
    return item
