"""Workflows.

Endpoints (docs/PLAN.md section 2): workflows.list and workflows.get. Every
active (not archived) workflow is parsed to map which jobs it runs. Workflows
that define Python or container tasks inline are also added to the inventory,
because that inline code exists nowhere else (decision 10). Workflows that only
run other jobs are counted but not inventoried; their jobs are scanned as jobs.

Workflow definitions use Mistral v2 YAML:

    version: '2.0'
    my_workflow:
      tasks:
        load:
          action: civis.run_job
          input:
            job_id: 123
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import yaml

from common.models import CoverageItem, Impact, ObjectType, WorkflowRef
from scanner.inventory.base import (
    InventoryContext,
    InventoryItem,
    apply_common,
    base_object,
    classify_image,
    field_of,
    listed_rows,
    mark_not_scanned,
)

RUN_JOB = "civis.run_job"
RUN_WORKFLOW = "civis.workflows.execute"
INLINE_PYTHON = {"civis.scripts.python3", "civis.python3_script"}
INLINE_CONTAINER = {"civis.scripts.container", "civis.scripts.containers"}
INLINE_CUSTOM = {"civis.scripts.custom"}


@dataclass
class InlineTask:
    task: str
    action: str
    uses_default_image: bool | None
    # In memory only. Never written out.
    source: str = ""
    docker_command: str = ""


@dataclass
class WorkflowDefinition:
    job_ids: set[int] = field(default_factory=set)
    workflow_ids: set[int] = field(default_factory=set)
    inline_tasks: list[InlineTask] = field(default_factory=list)
    dynamic_references: int = 0  # job_id values set by an expression, which cannot be resolved.
    error: str | None = None


def _as_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _task_maps(document: dict) -> list[dict]:
    """Every 'tasks' mapping in a definition, including workbook-style 'workflows:' blocks."""
    found = []
    for key, value in document.items():
        if key == "version" or not isinstance(value, dict):
            continue
        if isinstance(value.get("tasks"), dict):
            found.append(value["tasks"])
        if key == "workflows":
            found.extend(_task_maps(value))
    return found


def parse_definition(text: str | None) -> WorkflowDefinition:
    result = WorkflowDefinition()
    if not text or not text.strip():
        return result
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        result.error = "The workflow definition is not valid YAML."
        return result
    if not isinstance(document, dict):
        result.error = "The workflow definition has an unexpected structure."
        return result

    for tasks in _task_maps(document):
        for name, task in tasks.items():
            if not isinstance(task, dict):
                continue
            action = task.get("action")
            data = task.get("input") if isinstance(task.get("input"), dict) else {}
            if action == RUN_JOB:
                job_id = _as_id(data.get("job_id"))
                if job_id is None:
                    result.dynamic_references += 1
                else:
                    result.job_ids.add(job_id)
            elif action == RUN_WORKFLOW:
                workflow_id = _as_id(data.get("workflow_id"))
                if workflow_id is not None:
                    result.workflow_ids.add(workflow_id)
            elif action in INLINE_PYTHON:
                image = classify_image(ObjectType.PYTHON_SCRIPT, None, data.get("docker_image_tag"))
                result.inline_tasks.append(
                    InlineTask(str(name), action, image.uses_default_image, source=str(data.get("source") or ""))
                )
            elif action in INLINE_CONTAINER:
                image = classify_image(
                    ObjectType.CONTAINER_SCRIPT, data.get("docker_image_name"), data.get("docker_image_tag")
                )
                result.inline_tasks.append(
                    InlineTask(
                        str(name), action, image.uses_default_image, docker_command=str(data.get("docker_command") or "")
                    )
                )
            elif action in INLINE_CUSTOM:
                template_id = _as_id(data.get("from_template_id"))
                result.inline_tasks.append(InlineTask(str(name), action, None))
                if template_id is None:
                    result.dynamic_references += 1
    return result


def inventory_workflows(ctx: InventoryContext) -> tuple[list[InventoryItem], dict[int, set[WorkflowRef]]]:
    """Returns workflow items with inline code, and job ID -> active workflows that run it."""
    rows = listed_rows(ctx, ctx.client.workflows.list, ObjectType.WORKFLOW)
    ctx.inventory.count(ObjectType.WORKFLOW).found += len(rows)
    references: dict[int, set[WorkflowRef]] = defaultdict(set)
    items = []
    for row in rows:
        obj = base_object(row, ObjectType.WORKFLOW)
        item = InventoryItem(obj)
        detail, reason = ctx.fetch(ctx.client.workflows.get, obj.object_id)
        if detail is None:
            mark_not_scanned(obj, reason, ctx.inventory)
            items.append(item)
            continue
        apply_common(ctx, obj, detail, owner_field="user")
        obj.last_run_id = field_of(detail, "last_execution_id")
        if obj.archived:
            continue  # Only active workflows count (decision 15).

        parsed = parse_definition(field_of(detail, "definition"))
        if parsed.error:
            mark_not_scanned(obj, parsed.error, ctx.inventory)
            items.append(item)
            continue
        ref = WorkflowRef(obj.object_id, obj.name)
        for job_id in parsed.job_ids:
            references[job_id].add(ref)
        if parsed.dynamic_references:
            ctx.inventory.coverage.append(
                CoverageItem(
                    ObjectType.WORKFLOW,
                    obj.object_id,
                    f"{parsed.dynamic_references} task(s) choose their job or template with an expression, "
                    "so the job they run could not be determined.",
                )
            )
        if not parsed.inline_tasks:
            continue  # Runs other jobs only. Counted as found; nothing of its own to scan.

        defaults = [t.uses_default_image for t in parsed.inline_tasks]
        if any(d is True for d in defaults):
            obj.uses_default_image = True
        elif all(d is False for d in defaults):
            obj.uses_default_image = False
        obj.impact = {True: Impact.AFFECTED, False: Impact.ADVISORY}.get(obj.uses_default_image, Impact.UNKNOWN)
        item.code["inline_tasks"] = parsed.inline_tasks
        items.append(item)
    return items, references
