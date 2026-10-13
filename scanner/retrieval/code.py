"""Code that Civis returns inline, turned into units the engine can scan.

Sources (all read during inventory and held in ``InventoryItem.code``):

- Python scripts: ``source``. Not used for git-backed scripts, because saved
  source can be out of date; their code comes from the repository.
- Container scripts: ``docker_command`` (shell).
- Custom scripts: the backing script's source or docker command. If the
  backing script could not be read, ``code_preview`` instead (Python if the
  template is Python, shell otherwise).
- Workflows: inline task ``source`` (Python) and ``docker_command`` (shell).

Labels name where the code lives, never the code itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.models import ObjectType


@dataclass(frozen=True)
class CodeUnit:
    label: str
    text: str  # In memory only.
    kind: str  # "python" or "shell"


def inline_units(object_type: ObjectType, code: dict, git_backed: bool) -> list[CodeUnit]:
    units: list[CodeUnit] = []

    def add(label: str, text, kind: str) -> None:
        if isinstance(text, str) and text.strip():
            units.append(CodeUnit(label, text, kind))

    if object_type is ObjectType.PYTHON_SCRIPT:
        if not git_backed:
            add("inline source", code.get("source"), "python")
    elif object_type is ObjectType.CONTAINER_SCRIPT:
        add("docker command", code.get("docker_command"), "shell")
    elif object_type is ObjectType.CUSTOM_SCRIPT:
        if code.get("backing_source"):
            add("template script source", code.get("backing_source"), "python")
        elif code.get("backing_docker_command"):
            add("template docker command", code.get("backing_docker_command"), "shell")
        elif "backing_source" not in code and "backing_docker_command" not in code:
            preview = code.get("code_preview")
            add("code preview", preview, _guess_kind(preview))
    elif object_type is ObjectType.WORKFLOW:
        for task in code.get("inline_tasks") or []:
            add(f"workflow task {task.task}", task.source, "python")
            add(f"workflow task {task.task} docker command", task.docker_command, "shell")
    return units


def _guess_kind(text) -> str:
    """A code preview is Python for Python templates and a command for container templates."""
    if not isinstance(text, str):
        return "python"
    first = text.lstrip().split("\n", 1)[0]
    shell_starts = ("python ", "python3 ", "pip ", "pip3 ", "sh ", "bash ", "cd ", "dbt ", "./")
    return "shell" if first.startswith(shell_starts) else "python"
