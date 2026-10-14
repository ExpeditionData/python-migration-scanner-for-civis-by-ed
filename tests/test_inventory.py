"""Inventory tests against a synthetic Civis instance (tests/fixtures/civis_api/synthetic_instance.json)."""

from __future__ import annotations

import copy
import json

import pytest

from common.models import Impact, ObjectType, ScanStatus, WorkflowRef
from scanner.inventory import build_inventory
from scanner.inventory.base import classify_image, normalize_image, schedule_summary
from scanner.inventory.users import load_users, scanning_user
from scanner.inventory.workflows import parse_definition
from tests.fake_civis import FakeClient


@pytest.fixture(scope="module")
def built():
    client = FakeClient.from_fixture("synthetic_instance.json")
    return client, build_inventory(client)


def obj(built, object_id):
    _, inventory = built
    return inventory.by_id()[object_id].obj


def item(built, object_id):
    _, inventory = built
    return inventory.by_id()[object_id]


def test_inventoried_ids_by_type(built):
    _, inventory = built
    ids = {}
    for it in inventory.items:
        ids.setdefault(it.obj.object_type, set()).add(it.obj.object_id)
    assert ids == {
        ObjectType.PYTHON_SCRIPT: {101, 102, 105, 106, 107},
        ObjectType.CONTAINER_SCRIPT: {201, 202, 203, 204, 205, 206},
        ObjectType.CUSTOM_SCRIPT: {301, 302, 304, 306},
        ObjectType.DBT_SCRIPT: {231},
        ObjectType.NOTEBOOK: {401, 403},
        ObjectType.SERVICE: {501, 502, 503},
        ObjectType.WORKFLOW: {601, 603},
    }


def test_counts_cover_hidden_archived_and_non_python(built):
    _, inventory = built
    c = inventory.counts
    assert (c[ObjectType.PYTHON_SCRIPT].found, c[ObjectType.PYTHON_SCRIPT].hidden_skipped) == (5, 1)
    assert c[ObjectType.PYTHON_SCRIPT].archived_included == 1
    assert c[ObjectType.CUSTOM_SCRIPT].not_python == {"sql": 1, "dbt": 1}
    assert c[ObjectType.NOTEBOOK].not_python == {"r": 1}
    assert c[ObjectType.SERVICE].not_python == {"r": 2}
    assert c[ObjectType.PYTHON_SCRIPT].not_python == {}
    assert c[ObjectType.DBT_SCRIPT].not_python == {"dbt": 1}  # The pinned dbt script.
    assert c[ObjectType.WORKFLOW].found == 3


def test_archived_jobs_are_skipped_unless_an_active_workflow_runs_them(built):
    _, inventory = built
    assert 104 not in inventory.by_id()  # Archived and only used by an archived workflow.
    archived = obj(built, 105)
    assert archived.archived is True
    assert archived.workflow_ids == [601]
    assert archived.impact is Impact.AFFECTED


def test_workflow_ids_are_attached_to_jobs(built):
    assert obj(built, 201).workflow_ids == [601]
    assert obj(built, 101).workflow_ids == [604]
    assert obj(built, 102).workflow_ids == []


def test_workflow_names_are_recorded_on_jobs_and_rows(built):
    # Workflow 604 only orchestrates other jobs, so it is not inventoried itself,
    # but its name still appears on the job it runs.
    assert obj(built, 101).workflows == [WorkflowRef(604, "Example orchestration only")]
    row = obj(built, 105).to_row()
    assert json.loads(row["workflows"]) == [{"id": 601, "name": "Example daily pipeline"}]
    assert obj(built, 102).to_row()["workflows"] is None


def test_workflows_are_copied_onto_findings(built):
    from scanner.engine import Engine

    engine = Engine()
    job = item(built, 101)
    findings = engine.to_findings(job.obj, engine.scan_python(job.code["source"], "inline source").matches)
    assert findings and all(f.workflows == (WorkflowRef(604, "Example orchestration only"),) for f in findings)
    assert json.loads(findings[0].to_row()["workflows"]) == [{"id": 604, "name": "Example orchestration only"}]


def test_standalone_dbt_scripts_follow_the_image_rule(built):
    dbt = obj(built, 231)
    assert (dbt.uses_default_image, dbt.impact, dbt.git_path) == (True, Impact.AFFECTED, "warehouse")
    assert dbt.url.endswith("/spa/#/scripts/dbt/231")
    _, inventory = built
    assert 232 not in inventory.by_id()


def test_dbt_templates_follow_the_backing_image(built):
    dbt = obj(built, 306)
    assert (dbt.uses_default_image, dbt.impact) == (True, Impact.AFFECTED)
    assert (dbt.backing_script_id, dbt.git_repo, dbt.git_ref, dbt.git_path) == (
        221, "github.com/example-org/example-dbt.git", "main", "warehouse"
    )
    _, inventory = built
    assert 307 not in inventory.by_id()  # dbt on a pinned image: counted as not Python.


@pytest.mark.parametrize(
    "object_id, uses_default, impact",
    [
        (101, True, Impact.AFFECTED),  # Python script, blank tag
        (102, False, Impact.ADVISORY),  # Python script, pinned tag
        (106, True, Impact.AFFECTED),  # Python script, "latest"
        (201, True, Impact.AFFECTED),  # datascience-python, blank tag
        (202, False, Impact.ADVISORY),  # custom image
        (203, False, Impact.ADVISORY),  # datascience-python pinned
        (204, True, Impact.AFFECTED),  # docker.io prefix and mixed case, "latest"
        (301, True, Impact.AFFECTED),  # custom script on a default-image container template
        (302, None, Impact.UNKNOWN),  # backing script unreadable
        (304, False, Impact.ADVISORY),  # custom script on a pinned Python template
        (401, True, Impact.AFFECTED),  # notebook with no image set
        (403, False, Impact.ADVISORY),  # notebook pinned
        (501, True, Impact.AFFECTED),  # Streamlit on blank tag
        (502, False, Impact.ADVISORY),  # Streamlit pinned
        (503, False, Impact.ADVISORY),  # Flask Service is never "affected"
        (601, True, Impact.AFFECTED),  # workflow with an inline Python task
    ],
)
def test_default_image_rule(built, object_id, uses_default, impact):
    o = obj(built, object_id)
    assert (o.uses_default_image, o.impact) == (uses_default, impact)


def test_image_names_are_normalized(built):
    assert obj(built, 204).docker_image_name == "civisanalytics/datascience-python"
    assert obj(built, 101).docker_image_name == "civisanalytics/datascience-python"
    assert obj(built, 401).docker_image_name == "civisanalytics/civis-jupyter-python3"


def test_owners_include_deactivated_users(built):
    active = obj(built, 101)
    former = obj(built, 102)
    assert (active.owner_id, active.owner_name, active.owner_active) == (2, "Example Analyst", True)
    assert (former.owner_id, former.owner_name, former.owner_active) == (3, "Example Former Analyst", False)
    assert obj(built, 403).owner_active is False


def test_schedule_and_last_run(built):
    o = obj(built, 101)
    assert o.scheduled is True
    assert o.schedule == "days 1,2,3,4,5; hours 6; minutes 30"
    assert (o.last_run_id, o.last_run_at, o.last_run_state) == (9001, "2026-09-30T06:41:00Z", "succeeded")
    assert obj(built, 502).scheduled is True and obj(built, 502).schedule == "always_on"
    assert obj(built, 501).scheduled is False
    assert obj(built, 501).last_run_id == 7001


def test_git_settings(built):
    git = obj(built, 106)
    assert (git.git_repo, git.git_ref, git.git_path) == ("https://github.com/example-org/example-etl.git", "main", "jobs/load.py")
    assert (obj(built, 202).git_repo, obj(built, 202).git_ref) == ("github.com/example-org/example-etl.git", "v1.2.0")
    assert obj(built, 201).git_repo is None
    assert (obj(built, 403).git_ref, obj(built, 403).git_path) == ("main", "analysis.ipynb")
    assert obj(built, 501).git_path == "app"


def test_unreadable_objects_are_listed_with_a_reason(built):
    _, inventory = built
    private = obj(built, 107)
    assert private.scan_status is ScanStatus.NOT_SCANNED
    assert "HTTP 403" in private.scan_note
    reasons = {(c.object_type, c.object_id): c.reason for c in inventory.coverage}
    assert "HTTP 403" in reasons[(ObjectType.PYTHON_SCRIPT, 107)]
    assert "not valid YAML" in reasons[(ObjectType.WORKFLOW, 603)]
    assert any("job 888" in r for (t, i), r in reasons.items() if (t, i) == (ObjectType.WORKFLOW, 601))


def test_dynamic_workflow_references_are_reported(built):
    _, inventory = built
    notes = [c.reason for c in inventory.coverage if c.object_id == 601]
    assert any("expression" in n for n in notes)


def test_custom_script_fallback_to_code_preview(built):
    locked = item(built, 302)
    assert locked.obj.scan_status is ScanStatus.PARTIAL
    assert "backing script could not be read" in locked.obj.scan_note
    assert "code preview" in locked.obj.scan_note
    assert "feature_flags" in locked.code["code_preview"]
    container_backed = item(built, 301)
    assert container_backed.obj.backing_script_id == 211
    assert container_backed.code["backing_docker_command"] == "python template.py"
    assert "backing_source" in item(built, 304).code


def test_code_is_kept_in_memory_only(built):
    assert "list_me" in item(built, 101).code["source"]
    assert item(built, 201).code["docker_command"].startswith("pip install civis")
    assert item(built, 401).code["notebook_url"].startswith("https://example-notebooks.s3.amazonaws.com/")
    assert item(built, 501).code["deployment_id"] == 7001
    inline = item(built, 601).code["inline_tasks"]
    assert [t.action for t in inline] == ["civis.scripts.python3"]
    for it in built[1].items:
        row = it.obj.to_row()
        assert not set(row) & {"code", "source", "docker_command", "code_preview", "notebook_url", "definition"}
        assert "list_me" not in repr(row)
        assert "Signature=" not in repr(row)


def test_api_usage(built):
    client, _ = built
    names = {name for name, _, _ in client.calls}
    assert "scripts.get_python3_git" in names and "scripts.list_python3_git" not in names
    assert client.called("users.list")[0][1]["account_status"] == "all"
    for name, _, kwargs in client.calls:
        if name != "users.list" and (name.endswith(".list") or name.endswith(".list_custom")):
            assert kwargs["archived"] == "false" and kwargs["iterator"] is True and kwargs["limit"] == 50
    # One hidden-object pass per listed type.
    hidden_passes = [k for n, _, k in client.calls if k.get("hidden") is True]
    assert len(hidden_passes) == 7


def test_templates_are_read_once_per_template():
    data = FakeClient.from_fixture("synthetic_instance.json").data
    data = copy.deepcopy(data)
    twin = copy.deepcopy(next(c for c in data["custom"] if c["id"] == 301))
    twin["id"] = 305
    data["custom"].append(twin)
    client = FakeClient(data)
    build_inventory(client)
    assert [a for a, _ in client.called("templates.get_scripts")].count((31,)) == 1


def test_users_and_scanning_user():
    client = FakeClient.from_fixture("synthetic_instance.json")
    users = load_users(client)
    assert users[3].active is False and users[3].name == "Example Former Analyst"
    assert scanning_user(client) == (1, "example_robot")
    assert client.called("users.get_me")


@pytest.mark.parametrize(
    "name, tag, expected",
    [
        ("civisanalytics/datascience-python:9", None, ("civisanalytics/datascience-python", "9")),
        ("Index.Docker.IO/civisanalytics/datascience-python", "", ("civisanalytics/datascience-python", "")),
        ("registry.example.test:5000/team/image", "1.0", ("registry.example.test:5000/team/image", "1.0")),
        ("", "1.0", (None, "1.0")),
    ],
)
def test_normalize_image(name, tag, expected):
    assert normalize_image(name, tag) == expected


def test_classify_image_rules():
    assert classify_image(ObjectType.CONTAINER_SCRIPT, None, None).uses_default_image is True
    assert classify_image(ObjectType.CONTAINER_SCRIPT, "civisanalytics/datascience-python", "9").uses_default_image is False
    assert classify_image(ObjectType.SERVICE, "civisanalytics/civis-services-flask", "latest").impact is Impact.ADVISORY
    assert classify_image(ObjectType.SERVICE, None, None).name == "civisanalytics/civis-services-shiny"


def test_schedule_summary_unscheduled():
    assert schedule_summary({"scheduled": False}) == (False, None)
    assert schedule_summary(None) == (False, None)


def test_parse_definition_handles_workbooks_and_bad_input():
    text = (
        "version: '2.0'\n"
        "workflows:\n"
        "  inner:\n"
        "    tasks:\n"
        "      a:\n"
        "        action: civis.run_job\n"
        "        input: {job_id: 12}\n"
        "      b:\n"
        "        action: civis.scripts.container\n"
        "        input: {docker_command: 'python x.py', docker_image_name: 'example/image', docker_image_tag: '2'}\n"
    )
    parsed = parse_definition(text)
    assert parsed.job_ids == {12}
    assert parsed.inline_tasks[0].uses_default_image is False
    assert parse_definition("").job_ids == set()
    assert parse_definition("- just\n- a list\n").error
    assert parse_definition("a: [unclosed").error
