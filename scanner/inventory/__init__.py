"""Inventory of Civis objects, one module per object type.

``build_inventory`` runs every type, then applies two cross-type rules:

- Archived jobs are skipped, except archived jobs that an active workflow runs.
  Those are fetched one by one with jobs.get and added with archived=True.
- Every job that an active workflow runs gets that workflow's ID.
"""

from __future__ import annotations

from typing import Any

from common.models import CoverageItem, ObjectType
from scanner.inventory.base import Inventory, InventoryContext, InventoryItem, UserInfo, field_of
from scanner.inventory.containers import container_item, inventory_container_scripts
from scanner.inventory.custom import custom_item, inventory_custom_scripts
from scanner.inventory.notebooks import inventory_notebooks
from scanner.inventory.python3 import inventory_python_scripts, python_item
from scanner.inventory.services import inventory_services
from scanner.inventory.users import load_users
from scanner.inventory.workflows import inventory_workflows

__all__ = ["Inventory", "InventoryItem", "build_inventory"]

_BY_TYPE = (
    (ObjectType.PYTHON_SCRIPT, inventory_python_scripts),
    (ObjectType.CONTAINER_SCRIPT, inventory_container_scripts),
    (ObjectType.CUSTOM_SCRIPT, inventory_custom_scripts),
    (ObjectType.NOTEBOOK, inventory_notebooks),
    (ObjectType.SERVICE, inventory_services),
)


def build_inventory(client: Any, users: dict[int, UserInfo] | None = None) -> Inventory:
    inventory = Inventory()
    ctx = InventoryContext(client, users if users is not None else load_users(client), inventory)

    for object_type, inventory_type in _BY_TYPE:
        items = inventory_type(ctx)
        inventory.count(object_type).found += len(items)
        inventory.items.extend(items)

    workflow_items, references = inventory_workflows(ctx)
    inventory.items.extend(workflow_items)

    _add_archived_jobs_run_by_workflows(ctx, references)
    by_id = inventory.by_id()
    for job_id, workflow_ids in references.items():
        item = by_id.get(job_id)
        if item is not None and item.obj.object_type is not ObjectType.WORKFLOW:
            item.obj.workflow_ids = sorted(workflow_ids)
    return inventory


def _job_kind(job: Any) -> ObjectType | None:
    """Map a jobs.get response to an object type. Job type strings are not documented,
    so this matches on words in the type and falls back to the template ID."""
    job_type = (field_of(job, "type") or "").lower()
    if "python" in job_type:
        return ObjectType.PYTHON_SCRIPT
    if "container" in job_type:
        return ObjectType.CONTAINER_SCRIPT
    if "custom" in job_type or field_of(job, "from_template_id") is not None:
        return ObjectType.CUSTOM_SCRIPT
    return None


def _add_archived_jobs_run_by_workflows(ctx: InventoryContext, references: dict[int, set[int]]) -> None:
    known = {item.obj.object_id for item in ctx.inventory.items}
    builders = {
        ObjectType.PYTHON_SCRIPT: python_item,
        ObjectType.CONTAINER_SCRIPT: container_item,
        ObjectType.CUSTOM_SCRIPT: custom_item,
    }
    for job_id in sorted(references):
        if job_id in known:
            continue
        job, reason = ctx.fetch(ctx.client.jobs.get, job_id)
        if job is None:
            for workflow_id in sorted(references[job_id]):
                ctx.inventory.coverage.append(
                    CoverageItem(ObjectType.WORKFLOW, workflow_id, f"Runs job {job_id}, which could not be read. {reason}")
                )
            continue
        if not field_of(job, "archived", False):
            continue  # Hidden, not Python, or already counted elsewhere.
        kind = _job_kind(job)
        if kind is None:
            continue  # An archived SQL, import, or other non-Python job.
        item = builders[kind](ctx, job)
        item.obj.archived = True
        ctx.inventory.count(kind).archived_included += 1
        ctx.inventory.count(kind).found += 1
        ctx.inventory.items.append(item)
