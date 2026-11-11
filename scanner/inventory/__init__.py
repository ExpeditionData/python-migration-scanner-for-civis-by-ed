"""Inventory of Civis objects, one module per object type.

``build_inventory`` works in three steps (docs/PLAN.md 17.6):

1. List every type, all pages (the users list first, then Python, container,
   custom, and dbt scripts, notebooks, Services, and workflows).
2. Choose which listed rows to read, in this order:
   - A row in the Python, container, or dbt script list with
     ``from_template_id`` set is a custom script made from a template. When
     the custom script list returned the same ID, it is skipped there and
     counted as a template copy, so each custom script is inventoried once, as
     a custom script. A copy the custom list did not return is read under its
     own type.
   - Rows that cannot run Python are left out on the list row where it shows
     (a custom script on a SQL, R, or JavaScript template, a notebook in
     another language).
   - A Python, container, custom, or dbt script is kept only when its last run
     time (finished, else started, else created) or its updated_at is within
     LOOKBACK_DAYS of the scan's start. Others are counted as outside the
     window, with no detail call. Notebooks, Services, and workflows are always
     kept: their list rows have no reliable last-use time, and they are few.
     There is no early stop on the lists (they come back newest updated_at
     first), because updated_at does not change when a script runs.
3. Log one INVENTORY line per listed type (counts only), then make the detail
   calls for the rows kept, then for the workflows, with a PROGRESS
   phase=inventory line every 50 objects or 30 seconds.

Then two cross-type rules apply:

- Archived jobs are skipped, except archived jobs that an active workflow runs.
  Those are fetched one by one with jobs.get and added with archived=True. An
  ID seen in any script list is known, so a reference to it never calls
  jobs.get.
- Every job that an active workflow runs gets that workflow's ID and name, which
  are also copied onto the job's findings.

Every object ID appears at most once in ``Inventory.items``: the checkpoints,
resume, the objects and findings rows, checklist pairs, and the web app all
key objects by ID.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from common.models import CoverageItem, ObjectType, WorkflowRef
from scanner import scan_log
from scanner.inventory import custom, notebooks
from scanner.inventory.base import Inventory, InventoryContext, InventoryItem, field_of, in_window
from scanner.inventory.containers import container_item, list_container_scripts
from scanner.inventory.custom import TemplateCache, custom_item, list_custom_scripts
from scanner.inventory.dbt import dbt_item, list_dbt_scripts
from scanner.inventory.notebooks import list_notebooks, notebook_item
from scanner.inventory.python3 import list_python_scripts, python_item
from scanner.inventory.services import list_services, service_item
from scanner.inventory.users import load_users
from scanner.inventory.workflows import list_workflows, workflow_item

__all__ = ["Inventory", "InventoryItem", "build_inventory"]

_LISTS = (
    (ObjectType.PYTHON_SCRIPT, list_python_scripts),
    (ObjectType.CONTAINER_SCRIPT, list_container_scripts),
    (ObjectType.CUSTOM_SCRIPT, list_custom_scripts),
    (ObjectType.DBT_SCRIPT, list_dbt_scripts),
    (ObjectType.NOTEBOOK, list_notebooks),
    (ObjectType.SERVICE, list_services),
)
# Script lists whose rows can be custom scripts made from a template (from_template_id set).
_COPY_TYPES = frozenset({ObjectType.PYTHON_SCRIPT, ObjectType.CONTAINER_SCRIPT, ObjectType.DBT_SCRIPT})
SCRIPT_TYPES = (ObjectType.PYTHON_SCRIPT, ObjectType.CONTAINER_SCRIPT, ObjectType.CUSTOM_SCRIPT, ObjectType.DBT_SCRIPT)
# The list-row check for rows that cannot run Python, by type.
_ROW_NOT_PYTHON = {ObjectType.CUSTOM_SCRIPT: custom.row_not_python, ObjectType.NOTEBOOK: notebooks.row_not_python}


@dataclass
class Listed:
    """One type's list: every row, and the rows kept for detail calls."""

    object_type: ObjectType
    rows: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    template_copies: int = 0
    not_python: int = 0  # Left out on the list row; detail calls can find more (counted in coverage).
    outside_window: int = 0


PROGRESS_EVERY_OBJECTS = 50
PROGRESS_EVERY_SECONDS = 30.0


class _DetailProgress:
    """PROGRESS phase=inventory lines during the detail calls: at the start, every 50 objects or 30 seconds, and
    once more at the end."""

    def __init__(self, total: int, clock: Callable[[], float]):
        self.total, self.clock, self.done = total, clock, 0
        self.last = clock()
        scan_log.progress("inventory", 0, total)

    def tick(self) -> None:
        self.done += 1
        if self.done % PROGRESS_EVERY_OBJECTS == 0 or self.clock() - self.last >= PROGRESS_EVERY_SECONDS:
            scan_log.progress("inventory", self.done, self.total)
            self.last = self.clock()

    def finish(self) -> None:
        if self.done % PROGRESS_EVERY_OBJECTS or self.done == 0:
            scan_log.progress("inventory", self.done, self.total)


def build_inventory(client: Any, civis_git: Any = None, step: Callable[[str], None] | None = None,
                    cutoff: datetime | None = None, clock: Callable[[], float] = time.monotonic) -> Inventory:
    """``civis_git`` (a CivisGitFiles) reads git-backed workflows' definitions through Civis.
    Without it, every workflow is read from the definition saved in Civis.

    ``step`` is told each part as it starts (names from scan_log.FATAL_STEPS), so a FATAL line can
    say where the scan stopped.

    ``cutoff`` is the scan's start minus LOOKBACK_DAYS (a resumed run uses its first start). Scripts that did
    not run or change since then are counted as outside the window and not read. None keeps every script."""
    step = step or (lambda name: None)
    inventory = Inventory()
    step("inventory_users")
    lookup = load_users(client)
    scan_log.users(lookup.source, lookup.status)
    inventory.users_source, inventory.users_note = lookup.source, lookup.note()
    ctx = InventoryContext(client, lookup.users, inventory, civis_git)

    # 1. List every type, all pages.
    listed: dict[ObjectType, Listed] = {}
    for object_type, list_rows in _LISTS:
        step(f"inventory_list_{object_type.value}")
        listed[object_type] = Listed(object_type, list_rows(ctx))
    step("inventory_workflows")
    workflow_rows = list_workflows(ctx)

    # 2. Choose the rows to read.
    custom_ids = {field_of(row, "id") for row in listed[ObjectType.CUSTOM_SCRIPT].rows}
    for entry in listed.values():
        _choose_rows(entry, custom_ids, inventory, cutoff)

    # 3. Counts per listed type, then the detail calls for the rows kept, then the workflows.
    for entry in listed.values():
        scan_log.inventory(entry.object_type, listed=len(entry.rows), kept=len(entry.kept),
                           outside_window=entry.outside_window, template_copies=entry.template_copies,
                           not_python=entry.not_python)
    scan_log.inventory(ObjectType.WORKFLOW, listed=len(workflow_rows), kept=len(workflow_rows), outside_window=0,
                       template_copies=0, not_python=0)
    progress = _DetailProgress(sum(len(entry.kept) for entry in listed.values()) + len(workflow_rows), clock)

    step("inventory_details")
    templates = TemplateCache(ctx)
    for entry in listed.values():
        counts = inventory.count(entry.object_type)
        for row in entry.kept:
            item = _detail(ctx, entry.object_type, row, templates)
            if item is not None:
                counts.found += 1
                inventory.items.append(item)
            progress.tick()

    step("inventory_workflows")
    inventory.count(ObjectType.WORKFLOW).found += len(workflow_rows)
    references: dict[int, set[WorkflowRef]] = defaultdict(set)
    for row in workflow_rows:
        item = workflow_item(ctx, row, references)
        if item is not None:
            inventory.items.append(item)
        progress.tick()
    progress.finish()

    step("inventory_archived_jobs")
    known = {field_of(row, "id") for t in SCRIPT_TYPES for row in listed[t].rows}
    _add_archived_jobs_run_by_workflows(ctx, references, known, templates)
    by_id = inventory.by_id()
    for job_id, refs in references.items():
        item = by_id.get(job_id)
        if item is not None and item.obj.object_type is not ObjectType.WORKFLOW:
            item.obj.workflows = sorted(refs, key=lambda r: r.workflow_id)
    return inventory


def _choose_rows(entry: Listed, custom_ids: set, inventory: Inventory, cutoff: datetime | None) -> None:
    """Sort one type's rows into template copies, rows that cannot run Python, scripts outside the lookback
    window, and rows to read."""
    counts = inventory.count(entry.object_type)
    row_not_python = _ROW_NOT_PYTHON.get(entry.object_type)
    windowed = cutoff is not None and entry.object_type in SCRIPT_TYPES
    for row in entry.rows:
        if (entry.object_type in _COPY_TYPES and field_of(row, "from_template_id") is not None
                and field_of(row, "id") in custom_ids):
            entry.template_copies += 1
            continue
        language = row_not_python(row) if row_not_python else None
        if language is not None:
            entry.not_python += 1
            counts.add_not_python(language)
            continue
        if windowed and not in_window(row, cutoff):
            entry.outside_window += 1
            continue
        entry.kept.append(row)
    counts.template_copies += entry.template_copies
    counts.outside_window += entry.outside_window


def _detail(ctx: InventoryContext, object_type: ObjectType, row: Any, templates: TemplateCache) -> InventoryItem | None:
    """Read one kept row. None when the detail shows it cannot run Python (counted as not Python)."""
    counts = ctx.inventory.count(object_type)
    if object_type is ObjectType.PYTHON_SCRIPT:
        return python_item(ctx, row)
    if object_type is ObjectType.CONTAINER_SCRIPT:
        return container_item(ctx, row)
    if object_type is ObjectType.CUSTOM_SCRIPT:
        item = custom_item(ctx, row, templates)
        if custom.is_not_python(item):
            counts.add_not_python((field_of(row, "backing_script_type") or "").lower() or "dbt")
            return None
        return item
    if object_type is ObjectType.DBT_SCRIPT:
        item = dbt_item(ctx, row)
        if item is None:
            counts.add_not_python("dbt")  # dbt off the default image.
        return item
    if object_type is ObjectType.NOTEBOOK:
        item = notebook_item(ctx, row)
        if item is None:
            counts.add_not_python(field_of(row, "language"))
        return item
    item = service_item(ctx, row)
    if item is None:
        counts.add_not_python("r")  # Civis's Shiny image runs R.
    return item


def _job_kind(job: Any) -> ObjectType | None:
    """Map a jobs.get response to an object type.

    A job with ``from_template_id`` set is a custom script, whatever its type says: the type of a custom script
    can name its template's backing script (in the live test, an archived custom script run by a workflow was
    read as a Python script, so its template's code was never read; docs/PLAN.md 17.8). The civis 2.10 API spec
    lists ``fromTemplateId`` on the GET /jobs/{id} response. Job type strings are not documented, so the other
    kinds are matched on words in the type."""
    if field_of(job, "from_template_id") is not None:
        return ObjectType.CUSTOM_SCRIPT
    job_type = (field_of(job, "type") or "").lower()
    if "dbt" in job_type:
        return ObjectType.DBT_SCRIPT
    if "python" in job_type:
        return ObjectType.PYTHON_SCRIPT
    if "container" in job_type:
        return ObjectType.CONTAINER_SCRIPT
    if "custom" in job_type:
        return ObjectType.CUSTOM_SCRIPT
    return None


def _add_archived_jobs_run_by_workflows(
    ctx: InventoryContext, references: dict[int, set[WorkflowRef]], listed_ids: set, templates: TemplateCache
) -> None:
    """``listed_ids`` holds every ID seen in a script list (read, template copy, or not Python): those are
    not archived, so a workflow reference to one needs no jobs.get."""
    known = listed_ids | {item.obj.object_id for item in ctx.inventory.items}
    builders = {
        ObjectType.PYTHON_SCRIPT: python_item,
        ObjectType.CONTAINER_SCRIPT: container_item,
        ObjectType.CUSTOM_SCRIPT: lambda c, job: custom_item(c, job, templates),
        ObjectType.DBT_SCRIPT: dbt_item,
    }
    for job_id in sorted(references):
        if job_id in known:
            continue
        job, reason = ctx.fetch(ctx.client.jobs.get, job_id)
        if job is None:
            for ref in sorted(references[job_id], key=lambda r: r.workflow_id):
                ctx.inventory.coverage.append(
                    CoverageItem(ObjectType.WORKFLOW, ref.workflow_id, f"Runs job {job_id}, which could not be read. {reason}")
                )
            continue
        if not field_of(job, "archived", False):
            continue  # Hidden, not Python, or already counted elsewhere.
        kind = _job_kind(job)
        if kind is None:
            continue  # An archived SQL, import, or other non-Python job.
        item = builders[kind](ctx, job)
        if not isinstance(item, InventoryItem):
            ctx.inventory.count(kind).add_not_python("dbt")  # dbt off the default image.
            continue
        item.obj.archived = True
        ctx.inventory.count(kind).archived_included += 1
        ctx.inventory.count(kind).found += 1
        ctx.inventory.items.append(item)
