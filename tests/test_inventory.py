"""Inventory tests against a synthetic Civis instance (tests/fixtures/civis_api/synthetic_instance.json)."""

from __future__ import annotations

import copy
import json

import pytest

from common.models import Impact, ObjectType, ScanStatus, WorkflowRef
from scanner.inventory import build_inventory
from scanner.inventory.base import classify_image, normalize_image, schedule_summary
from scanner.inventory.users import USERS_ACTIVE_NOTE, USERS_NONE_NOTE, load_users, scanning_user
from scanner.inventory.workflows import SAVED_COPY_NOTE, parse_definition
from scanner.retrieval.civis_git import TAG_READ_AT_BRANCH, CivisGitFiles
from tests.fake_civis import FakeAPIError, FakeClient


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
        ObjectType.PYTHON_SCRIPT: {101, 102, 105, 106, 107, 109, 110, 111, 112},
        ObjectType.CONTAINER_SCRIPT: {201, 202, 203, 204, 205, 206, 208},
        ObjectType.CUSTOM_SCRIPT: {301, 302, 304, 306},
        ObjectType.DBT_SCRIPT: {231},
        ObjectType.NOTEBOOK: {401, 403},
        ObjectType.SERVICE: {501, 502, 503},
        ObjectType.WORKFLOW: {601, 603, 605, 606},
    }


def test_counts_cover_archived_and_non_python(built):
    _, inventory = built
    c = inventory.counts
    assert c[ObjectType.PYTHON_SCRIPT].found == 9
    assert not hasattr(c[ObjectType.PYTHON_SCRIPT], "hidden_skipped")  # Hidden objects are not counted (PLAN 17.5).
    assert c[ObjectType.PYTHON_SCRIPT].archived_included == 1
    assert c[ObjectType.CUSTOM_SCRIPT].not_python == {"sql": 1, "dbt": 1}
    assert c[ObjectType.NOTEBOOK].not_python == {"r": 1}
    assert c[ObjectType.SERVICE].not_python == {"r": 2}
    assert c[ObjectType.PYTHON_SCRIPT].not_python == {}
    assert c[ObjectType.DBT_SCRIPT].not_python == {"dbt": 1}  # The pinned dbt script.
    assert c[ObjectType.WORKFLOW].found == 5


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


def _users_allowed(errors: dict | None = None) -> FakeClient:
    """The synthetic instance with users.list allowed for every account_status, or with ``errors``."""
    data = copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)
    data["users_list_errors"] = errors or {}
    return FakeClient(data)


def test_owners_from_the_active_list_by_default(built):
    """The default synthetic instance refuses account_status "all", as Civis did in the live test."""
    _, inventory = built
    active = obj(built, 101)
    former = obj(built, 102)
    assert (active.owner_id, active.owner_name, active.owner_active) == (2, "Example Analyst", True)
    # Not in the active list: unknown, never False. The name still comes from the object's own owner field.
    assert (former.owner_id, former.owner_name, former.owner_active) == (3, "Example Former Analyst", None)
    assert obj(built, 403).owner_active is None
    assert inventory.users_source == "active"
    assert inventory.users_note == USERS_ACTIVE_NOTE.format(status=" (HTTP 404)")


def test_owners_include_deactivated_users_when_all_can_be_listed():
    inventory = build_inventory(_users_allowed())
    by_id = inventory.by_id()
    assert (by_id[101].obj.owner_active, by_id[102].obj.owner_active, by_id[403].obj.owner_active) == (True, False, False)
    assert by_id[102].obj.owner_name == "Example Former Analyst"
    assert (inventory.users_source, inventory.users_note) == ("all", None)


def test_owners_unknown_when_no_user_list_can_be_read():
    inventory = build_inventory(_users_allowed({"all": 403, "active": 503}))
    by_id = inventory.by_id()
    assert {by_id[i].obj.owner_active for i in (101, 102, 403)} == {None}
    assert (by_id[101].obj.owner_name, by_id[102].obj.owner_name) == ("Example Analyst", "Example Former Analyst")
    assert inventory.users_source == "none"
    assert inventory.users_note == USERS_NONE_NOTE.format(status=" (HTTP 503)")


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
    # Hidden objects are skipped and not counted: no call asks for hidden rows (docs/PLAN.md 17.5).
    assert not [n for n, _, k in client.calls if "hidden" in k]


def test_hidden_objects_are_skipped(built):
    _, inventory = built
    assert 103 not in inventory.by_id()  # Hidden in the synthetic instance.


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
    client = _users_allowed()
    lookup = load_users(client)
    assert (lookup.source, lookup.status, lookup.note()) == ("all", None, None)
    assert lookup.users[3].active is False and lookup.users[3].name == "Example Former Analyst"
    assert [k["account_status"] for _, k in client.called("users.list")] == ["all"]
    assert scanning_user(client) == (1, "example_robot")
    assert client.called("users.get_me")


@pytest.mark.parametrize("status", [403, 404])
def test_users_fall_back_to_the_active_list(status):
    client = _users_allowed({"all": status, "inactive": status})
    lookup = load_users(client)
    assert (lookup.source, lookup.status) == ("active", status)
    assert sorted(lookup.users) == [1, 2] and all(u.active is True for u in lookup.users.values())
    assert [(k["account_status"], k["limit"], k["iterator"]) for _, k in client.called("users.list")] == [
        ("all", 1000, True), ("active", 1000, True)]
    assert lookup.note() == USERS_ACTIVE_NOTE.format(status=f" (HTTP {status})")


def test_the_default_synthetic_users_list_refuses_all_and_inactive():
    client = FakeClient.from_fixture("synthetic_instance.json")
    for account_status in ("all", "inactive"):
        with pytest.raises(FakeAPIError) as raised:
            list(client.users.list(account_status=account_status, iterator=True, limit=1000))
        assert raised.value.status_code == 404
    assert [u["id"] for u in client.users.list(account_status="active", iterator=True, limit=1000)] == [1, 2]
    assert [u["id"] for u in client.users.list(iterator=True, limit=50)] == [1, 2]


def test_no_user_lookup_when_the_active_list_also_fails():
    lookup = load_users(_users_allowed({"all": 404, "active": 503}))
    assert (lookup.source, lookup.status, lookup.users) == ("none", 503, {})
    assert lookup.note() == USERS_NONE_NOTE.format(status=" (HTTP 503)")


def test_no_user_lookup_note_without_a_status():
    class Broken:
        def list(self, **kwargs):
            if kwargs["account_status"] == "all":
                raise FakeAPIError(404)
            raise ConnectionError("made-up connection failure")

    client = type("C", (), {"users": Broken()})()
    lookup = load_users(client)
    assert (lookup.source, lookup.status) == ("none", None)
    assert lookup.note() == USERS_NONE_NOTE.format(status="")
    assert "made-up" not in lookup.note()


def test_other_failures_of_the_all_list_are_raised():
    with pytest.raises(FakeAPIError) as raised:
        load_users(_users_allowed({"all": 500}))
    assert raised.value.status_code == 500


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

# --- Git-backed workflows -------------------------------------------------------------------

WORKFLOW_SHA = "c3d4e5f60718293a4b5c6d7e8f9012345678a1b2"  # pragma: allowlist secret  gitleaks:allow (a made-up commit SHA)
GIT_DEFINITION = (
    "version: '2.0'\ndaily:\n  tasks:\n    from_git:\n      action: civis.scripts.python3\n"
    "      input:\n        source: |\n          import civis.ml\n    load:\n      action: civis.run_job\n"
    "      input:\n        job_id: 102\n"
)


def git_workflow_data(workflow_id: int = 601, **entry) -> dict:
    data = copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)
    data.setdefault("workflows_git", {})[str(workflow_id)] = {
        "git_ref": "main", "git_branch": "main", "git_path": "workflows/daily.yml", "git_ref_type": "branch",
        "pull_from_git": False, "git_repo": {"id": 78, "repo_url": "https://github.com/example-org/example-workflows.git"},
    }
    data.setdefault("civis_git", {})[f"workflows/{workflow_id}"] = {
        "commits": [{"commit_hash": WORKFLOW_SHA, "author_name": "Example Analyst", "date": "2026-10-02T09:00:00Z",
                     "message": "Example change"}],
        "files": {WORKFLOW_SHA: GIT_DEFINITION},
        **entry,
    }
    return data


def git_inventory(data):
    client = FakeClient(data)
    reader = CivisGitFiles(client)
    return client, build_inventory(client, civis_git=reader), reader


def test_git_backed_workflow_is_parsed_from_its_git_file():
    client, inventory, reader = git_inventory(git_workflow_data())
    workflow = inventory.by_id()[601]
    assert [t.task for t in workflow.code["inline_tasks"]] == ["from_git"]
    assert "civis.ml" in workflow.code["inline_tasks"][0].source
    assert (workflow.obj.git_repo, workflow.obj.git_ref, workflow.obj.git_path) == (
        "https://github.com/example-org/example-workflows.git", "main", "workflows/daily.yml")
    assert workflow.obj.scan_status is not ScanStatus.PARTIAL and workflow.obj.scan_note is None
    by_id = inventory.by_id()
    assert by_id[102].obj.workflow_ids == [601]  # Job references come from the git file too,
    assert 601 not in by_id[201].obj.workflow_ids  # not from the saved definition.
    assert (reader.reads, reader.failures) == (2, 1)  # 601 and 605 are read; 606 fails (synthetic instance).


def test_git_backed_workflow_falls_back_to_its_saved_definition():
    _, inventory, reader = git_inventory(git_workflow_data(file_error=403))
    workflow = inventory.by_id()[601]
    assert [t.task for t in workflow.code["inline_tasks"]] == ["inline_step"]
    assert workflow.obj.scan_status is ScanStatus.PARTIAL
    assert workflow.obj.scan_note == (
        "The workflow's own file could not be read through Civis (HTTP 403). " + SAVED_COPY_NOTE
    )
    assert inventory.by_id()[201].obj.workflow_ids == [601]
    assert (reader.reads, reader.failures) == (1, 2)  # 605 is read; 606 fails (synthetic instance).


def test_git_backed_workflow_that_only_runs_jobs_reports_a_failed_read_in_coverage():
    _, inventory, _ = git_inventory(git_workflow_data(604, commits_error=404))
    assert 604 not in inventory.by_id()
    notes = [c.reason for c in inventory.coverage if (c.object_type, c.object_id) == (ObjectType.WORKFLOW, 604)]
    assert notes == ["The workflow's own file could not be read through Civis (HTTP 404). " + SAVED_COPY_NOTE]


def test_get_git_is_called_once_per_active_workflow():
    client, _, _ = git_inventory(git_workflow_data())
    active = [w["id"] for w in client.data["workflows"] if not w.get("archived")]
    assert sorted(a[0] for a, _ in client.called("workflows.get_git")) == sorted(active)
    assert sorted(a[0] for a, _ in client.called("workflows.list_git_commits")) == [601, 605, 606]
    assert ((601, WORKFLOW_SHA), {}) in client.called("workflows.get_git_commits")
    plain = FakeClient.from_fixture("synthetic_instance.json")
    build_inventory(plain)
    assert not plain.called("workflows.get_git")  # Without a reader, no git calls.


def test_synthetic_git_backed_objects():
    client, inventory, reader = git_inventory(copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data))
    by_id = inventory.by_id()
    assert [t.task for t in by_id[605].code["inline_tasks"]] == ["from_git"]  # Not the saved "old_step".
    assert by_id[109].obj.workflow_ids == [605]
    assert by_id[606].obj.scan_status is ScanStatus.PARTIAL and SAVED_COPY_NOTE in by_id[606].obj.scan_note
    assert [t.task for t in by_id[606].code["inline_tasks"]] == ["wait_step"]
    refs = {
        i: (by_id[i].obj.git_ref, by_id[i].code["git_ref_type"], by_id[i].code["git_branch"])
        for i in (109, 110, 111, 112)
    }
    assert refs == {109: ("5ca1ab1", "branch", "main"), 110: ("v2.0.0", "tag", "main"),
                    111: ("main", "branch", "main"), 112: ("main", "branch", "main")}
    assert (reader.reads, reader.failures) == (1, 1)  # Inventory reads only workflows.


def test_git_backed_workflow_on_a_tag_is_partial_with_a_note():
    data = git_workflow_data()
    data["workflows_git"]["601"].update({"git_ref": "v1.0.0", "git_ref_type": "tag"})
    _, inventory, _ = git_inventory(data)
    workflow = inventory.by_id()[601]
    assert [t.task for t in workflow.code["inline_tasks"]] == ["from_git"]
    assert workflow.obj.scan_status is ScanStatus.PARTIAL
    assert workflow.obj.scan_note == TAG_READ_AT_BRANCH.format(item="workflow")


# --- Template copies (docs/PLAN.md 17.6, Part A) ----------------------------------------------------


def test_template_copies_are_skipped_in_their_own_list_and_counted(built):
    """A Python, container, or dbt list row with from_template_id set, whose ID the custom list also returned,
    is that custom script. It is skipped there and inventoried once, as a custom script."""
    _, inventory = built
    c = inventory.counts
    assert c[ObjectType.CONTAINER_SCRIPT].template_copies == 2  # 301 and 302.
    assert c[ObjectType.PYTHON_SCRIPT].template_copies == 1  # 304.
    assert c[ObjectType.DBT_SCRIPT].template_copies == 1  # 306.
    assert c[ObjectType.CUSTOM_SCRIPT].template_copies == 0
    by_id = inventory.by_id()
    for object_id in (301, 302, 304, 306):
        assert by_id[object_id].obj.object_type is ObjectType.CUSTOM_SCRIPT


def test_a_copy_the_custom_list_did_not_return_is_kept_under_its_own_type(built):
    copy_208 = obj(built, 208)
    assert copy_208.object_type is ObjectType.CONTAINER_SCRIPT and copy_208.impact is Impact.AFFECTED


def test_template_copies_cost_no_detail_call(built):
    client, _ = built
    assert not {301, 302} & {a[0] for a, _ in client.called("scripts.get_containers")}
    assert 304 not in {a[0] for a, _ in client.called("scripts.get_python3")}
    assert 306 not in {a[0] for a, _ in client.called("scripts.get_dbt")}
    assert [a[0] for a, _ in client.called("scripts.get_custom")].count(301) == 1


def test_every_object_id_is_inventoried_once(built):
    """Inventory.by_id, the checkpoints, resume, the objects and findings rows, and checklist pairs all key
    objects by ID, so an instance with template copies must give one item per ID."""
    _, inventory = built
    ids = [it.obj.object_id for it in inventory.items]
    assert len(ids) == len(set(ids)) == len(inventory.by_id())


def test_every_listed_script_id_is_known_to_workflow_references():
    """A workflow that runs a template copy or a custom script that is not Python calls no jobs.get for it, and
    the custom script gets the workflow's name."""
    data = copy.deepcopy(FakeClient.from_fixture("synthetic_instance.json").data)
    data["workflows"].append({
        "id": 607, "name": "Example template runner", "archived": False, "hidden": False, "valid": True,
        "user": {"id": 2, "name": "Example Analyst", "username": "example_analyst"},
        "definition": "version: '2.0'\nrunner:\n  tasks:\n    copy:\n      action: civis.run_job\n      input:\n"
                      "        job_id: 301\n    sql:\n      action: civis.run_job\n      input:\n        job_id: 303\n",
    })
    client = FakeClient(data)
    inventory = build_inventory(client)
    assert not {301, 303} & {a[0] for a, _ in client.called("jobs.get")}
    assert inventory.by_id()[301].obj.workflow_ids == [607]
    assert inventory.by_id()[301].obj.object_type is ObjectType.CUSTOM_SCRIPT
