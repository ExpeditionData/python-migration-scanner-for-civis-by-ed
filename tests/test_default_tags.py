"""The current default tag and its major tag count as the default image (docs/PLAN.md 17.8, Part C).

Civis saves the then-current default tag on an object made with no tag (datascience-python 9.1 and
civis-jupyter-python3 5.1 in the live test). That tag and its major part (9, 5) can move with Civis's default, so
they count as the default image, like a blank or "latest" tag. A patch tag or an older minor tag names a fixed
image and counts as pinned. Each object type is built through its own inventory code from a small made-up
instance. Made-up data only. No Civis calls.
"""

from __future__ import annotations

import pytest

from common import release
from common.models import Impact, ObjectType
from common.rule_loader import load_rules
from scanner.engine import Engine
from scanner.inventory import build_inventory
from scanner.inventory.base import DEFAULT_IMAGES, STREAMLIT_IMAGE, classify_image, is_default_tag
from tests.fake_civis import FakeClient

DATASCIENCE = "civisanalytics/datascience-python"
JUPYTER = "civisanalytics/civis-jupyter-python3"

# datascience-python tags. Blank and "latest" were the default before this change and still are.
DS_DEFAULT = ["9.1", "9", " 9.1 ", " 9 ", "", "latest"]
DS_PINNED = ["9.1.1", "9.0", "9.10", "91", "8", "8.1", "5.1", "5"]
NB_DEFAULT = ["5.1", "5", " 5.1 ", " 5 ", "", "latest"]
NB_PINNED = ["5.1.0", "5.0", "4", "9.1", "9"]

PERSON = {"id": 2, "name": "Example Analyst", "username": "example_analyst"}


def instance(tag: str, image: str = DATASCIENCE, notebook_tag: str = "5.1", notebook_image: str = JUPYTER) -> dict:
    """One object of each type on ``tag``: a Python script, a container script, three custom scripts (Python,
    container, and dbt templates whose backing scripts are on the tag), a dbt script, a workflow with an inline
    Python task and an inline container task, a Streamlit Service, and a notebook on ``notebook_tag``."""
    def row(object_id, name, **fields):
        return {"id": object_id, "name": name, "archived": False, "hidden": False, "author": PERSON,
                "updated_at": "2026-10-01T00:00:00Z", **fields}

    definition = (
        "version: '2.0'\nexample:\n  tasks:\n"
        "    inline_python:\n      action: civis.scripts.python3\n      input:\n"
        f"        docker_image_tag: '{tag}'\n        source: |\n          print('example')\n"
        "    inline_container:\n      action: civis.scripts.containers\n      input:\n"
        f"        docker_image_name: '{image}'\n        docker_image_tag: '{tag}'\n"
        "        docker_command: python run.py\n"
    )
    return {
        "users": [{"id": 2, "name": "Example Analyst", "username": "example_analyst", "active": True}],
        "python3": [
            row(1, "Example script", type="Python", docker_image_tag=tag, source="print('example')\n"),
            row(11, "Example Python backing script", type="Python", docker_image_tag=tag,
                source="print('example')\n", _listed=False),
        ],
        "containers": [
            row(2, "Example container", type="Container", docker_image_name=image, docker_image_tag=tag,
                docker_command="python run.py"),
            row(12, "Example container backing script", type="Container", docker_image_name=image,
                docker_image_tag=tag, docker_command="python run.py", _listed=False),
        ],
        "dbt": [
            row(4, "Example dbt project", type="Dbt", docker_image_tag=tag),
            row(13, "Example dbt backing script", type="Dbt", docker_image_tag=tag, _listed=False),
        ],
        "templates": {
            "21": {"id": 21, "name": "Example Python template", "script_id": 11, "script_type": "python3"},
            "22": {"id": 22, "name": "Example container template", "script_id": 12, "script_type": "container"},
            "23": {"id": 23, "name": "Example dbt template", "script_id": 13, "script_type": "dbt"},
        },
        "custom": [
            row(31, "Example custom on a Python template", type="Custom", backing_script_type="python3",
                from_template_id=21),
            row(32, "Example custom on a container template", type="Custom", backing_script_type="container",
                from_template_id=22),
            row(33, "Example custom on a dbt template", type="Custom", backing_script_type="dbt",
                from_template_id=23),
        ],
        "notebooks": [
            {"id": 5, "name": "Example notebook", "language": "python3", "archived": False, "hidden": False,
             "user": PERSON, "docker_image_name": notebook_image, "docker_image_tag": notebook_tag,
             "notebook_url": "https://example-notebooks.s3.amazonaws.com/notebook-5.ipynb", "git_repo_url": None},
        ],
        "services": [
            {"id": 7, "name": "Example Streamlit app", "type": "App", "archived": False, "hidden": False,
             "user": PERSON, "docker_image_name": STREAMLIT_IMAGE, "docker_image_tag": tag},
        ],
        "workflows": [
            {"id": 6, "name": "Example workflow", "archived": False, "hidden": False, "user": PERSON,
             "definition": definition},
        ],
    }


def inventoried(data: dict) -> dict:
    inventory = build_inventory(FakeClient(data))
    return {item.obj.object_id: item.obj for item in inventory.items}, inventory


def impacts(objects: dict, ids) -> dict:
    return {i: (objects[i].impact, objects[i].uses_default_image) for i in ids if i in objects}


# --- One place for the tags ---------------------------------------------------------------------------------


def test_the_current_default_tags_are_in_common_release():
    assert release.CURRENT_DEFAULT_TAGS == {DATASCIENCE: "9.1", JUPYTER: "5.1"}
    assert set(release.CURRENT_DEFAULT_TAGS) <= DEFAULT_IMAGES and STREAMLIT_IMAGE not in release.CURRENT_DEFAULT_TAGS
    assert release.major_tag("9.1") == "9" and release.major_tag("5.1") == "5" and release.major_tag("10") == "10"
    assert release.default_tag_forms(DATASCIENCE) == ("9.1", "9")
    assert release.default_tag_forms(JUPYTER) == ("5.1", "5")
    assert release.default_tag_forms(STREAMLIT_IMAGE) == () and release.default_tag_forms(None) == ()


def test_the_rule_reads_the_tags_from_common_release(monkeypatch):
    """The scanner derives the major tag from the stored tag, so a new current tag changes both forms."""
    monkeypatch.setitem(release.CURRENT_DEFAULT_TAGS, DATASCIENCE, "10.2")
    assert is_default_tag(DATASCIENCE, "10.2") and is_default_tag(DATASCIENCE, "10")
    assert not is_default_tag(DATASCIENCE, "9.1") and not is_default_tag(DATASCIENCE, "9")


@pytest.mark.parametrize("object_type", [ObjectType.PYTHON_SCRIPT, ObjectType.CONTAINER_SCRIPT])
@pytest.mark.parametrize("tag", DS_DEFAULT)
def test_classify_datascience_default_tags(object_type, tag):
    image = classify_image(object_type, DATASCIENCE, tag)
    assert (image.impact, image.uses_default_image) == (Impact.AFFECTED, True)


@pytest.mark.parametrize("object_type", [ObjectType.PYTHON_SCRIPT, ObjectType.CONTAINER_SCRIPT])
@pytest.mark.parametrize("tag", DS_PINNED)
def test_classify_datascience_pinned_tags(object_type, tag):
    image = classify_image(object_type, DATASCIENCE, tag)
    assert (image.impact, image.uses_default_image) == (Impact.ADVISORY, False)


@pytest.mark.parametrize("tag", NB_DEFAULT)
def test_classify_notebook_default_tags(tag):
    for name in (JUPYTER, None, ""):  # A notebook with no image name runs on civis-jupyter-python3.
        image = classify_image(ObjectType.NOTEBOOK, name, tag)
        assert (image.impact, image.uses_default_image) == (Impact.AFFECTED, True), name


@pytest.mark.parametrize("tag", NB_PINNED)
def test_classify_notebook_pinned_tags(tag):
    image = classify_image(ObjectType.NOTEBOOK, JUPYTER, tag)
    assert (image.impact, image.uses_default_image) == (Impact.ADVISORY, False)


def test_an_image_name_with_the_tag_inside_it():
    for object_type in (ObjectType.CONTAINER_SCRIPT, ObjectType.PYTHON_SCRIPT):
        for name in (f"{DATASCIENCE}:9.1", f"{DATASCIENCE}:9", f"docker.io/CivisAnalytics/datascience-python:9.1"):
            assert classify_image(object_type, name, None).impact is Impact.AFFECTED, (object_type, name)
            assert classify_image(object_type, name, "").uses_default_image is True
        assert classify_image(object_type, f"{DATASCIENCE}:9.1.1", None).impact is Impact.ADVISORY
    assert classify_image(ObjectType.NOTEBOOK, f"{JUPYTER}:5.1", None).impact is Impact.AFFECTED
    assert classify_image(ObjectType.NOTEBOOK, f"{JUPYTER}:5", "  ").impact is Impact.AFFECTED
    assert classify_image(ObjectType.NOTEBOOK, f"{JUPYTER}:9.1", None).impact is Impact.ADVISORY


def test_each_tag_counts_for_its_own_image_only():
    assert classify_image(ObjectType.CONTAINER_SCRIPT, DATASCIENCE, "5.1").impact is Impact.ADVISORY
    assert classify_image(ObjectType.CONTAINER_SCRIPT, DATASCIENCE, "5").impact is Impact.ADVISORY
    assert classify_image(ObjectType.NOTEBOOK, JUPYTER, "9.1").impact is Impact.ADVISORY
    for tag in ("9.1", "9", "5.1", "5"):
        service = classify_image(ObjectType.SERVICE, STREAMLIT_IMAGE, tag)
        assert (service.impact, service.uses_default_image) == (Impact.ADVISORY, False), tag
        other = classify_image(ObjectType.CONTAINER_SCRIPT, "example-org/custom-etl", tag)
        assert other.impact is Impact.ADVISORY, tag
    assert classify_image(ObjectType.SERVICE, STREAMLIT_IMAGE, "").impact is Impact.AFFECTED  # Unchanged.
    assert classify_image(ObjectType.SERVICE, STREAMLIT_IMAGE, "latest").impact is Impact.AFFECTED


# --- Every object type, through its own inventory code ---------------------------------------------------------

# Python script, container script, custom scripts on Python and container templates, and the workflow.
RUNS_ON_TAG = (1, 2, 31, 32, 6)


@pytest.mark.parametrize("tag", DS_DEFAULT)
def test_every_object_type_on_a_default_tag_is_affected(tag):
    objects, inventory = inventoried(instance(tag))
    expected = {i: (Impact.AFFECTED, True) for i in RUNS_ON_TAG + (4, 33)}
    assert impacts(objects, RUNS_ON_TAG + (4, 33)) == expected
    # dbt and dbt-backed custom scripts on the tag run on the default image, so they are inventoried.
    assert objects[4].object_type is ObjectType.DBT_SCRIPT and objects[33].object_type is ObjectType.CUSTOM_SCRIPT
    assert inventory.count(ObjectType.DBT_SCRIPT).not_python == {}
    # The notebook is on 5.1 in every case here.
    assert impacts(objects, [5]) == {5: (Impact.AFFECTED, True)}
    # The Streamlit Service has no known current tag: only blank or "latest" count.
    streamlit = (Impact.AFFECTED, True) if tag.strip() in ("", "latest") else (Impact.ADVISORY, False)
    assert impacts(objects, [7]) == {7: streamlit}


@pytest.mark.parametrize("tag", DS_PINNED)
def test_every_object_type_on_a_pinned_tag_is_advisory(tag):
    objects, inventory = inventoried(instance(tag, notebook_tag="5.1.0"))
    assert impacts(objects, RUNS_ON_TAG + (5, 7)) == {i: (Impact.ADVISORY, False) for i in RUNS_ON_TAG + (5, 7)}
    # dbt off the default image is counted as not Python, as before.
    assert 4 not in objects and 33 not in objects
    assert inventory.count(ObjectType.DBT_SCRIPT).not_python == {"dbt": 1}
    assert inventory.count(ObjectType.CUSTOM_SCRIPT).not_python == {"dbt": 1}


@pytest.mark.parametrize("tag", NB_DEFAULT)
def test_a_notebook_on_a_default_tag_is_affected(tag):
    objects, _ = inventoried(instance("9.1.1", notebook_tag=tag))
    assert impacts(objects, [5]) == {5: (Impact.AFFECTED, True)}


@pytest.mark.parametrize("tag", NB_PINNED)
def test_a_notebook_on_a_pinned_tag_is_advisory(tag):
    objects, _ = inventoried(instance("9.1", notebook_tag=tag))
    assert impacts(objects, [5]) == {5: (Impact.ADVISORY, False)}


def test_image_names_with_the_tag_inside_them_through_inventory():
    objects, _ = inventoried(instance("", image=f"{DATASCIENCE}:9.1", notebook_tag="",
                                      notebook_image=f"{JUPYTER}:5.1"))
    # The container script, the custom script on the container template, the workflow's container task, and the
    # notebook carry an image name; the Python script, its template, and dbt have only a tag (blank here).
    assert impacts(objects, (2, 32, 6, 5)) == {i: (Impact.AFFECTED, True) for i in (2, 32, 6, 5)}
    assert objects[2].docker_image_tag == "9.1" and objects[5].docker_image_tag == "5.1"


def test_a_workflow_is_affected_when_one_inline_task_is_on_a_default_tag():
    data = instance("9.1.1")
    data["workflows"][0]["definition"] = data["workflows"][0]["definition"].replace(
        "docker_image_tag: '9.1.1'\n        source", "docker_image_tag: '9'\n        source", 1)
    objects, _ = inventoried(data)
    assert impacts(objects, [6]) == {6: (Impact.AFFECTED, True)}


# --- Rules that apply only on the default image ----------------------------------------------------------------


def test_default_image_only_rules_apply_on_the_current_default_tag():
    """PY315-PINNED-PACKAGE-INSTALL asks to confirm pinned versions support Python 3.15, which an object that moves
    with Civis's default gets. Its text needs no change."""
    pinned = [rule for rule in load_rules() if rule.default_image_only]
    assert [rule.id for rule in pinned] == ["PY315-PINNED-PACKAGE-INSTALL"]
    engine = Engine()
    command = "pip install pandas==2.2.3 && python run.py"
    for tag, expected in (("9.1", True), ("9", True), ("", True), ("9.1.1", False), ("9.0", False)):
        objects, _ = inventoried(instance(tag))
        matches = engine.scan_shell(command, "docker_command").matches
        found = {f.rule_id for f in engine.to_findings(objects[2], matches)}
        assert ("PY315-PINNED-PACKAGE-INSTALL" in found) is expected, tag


# --- The synthetic instance, the dry run, and the preview ------------------------------------------------------

# Synthetic objects saved on the current default tags (scanner/synthetic/synthetic_instance.json).
ON_DEFAULT_TAGS = {114: "9.1", 209: "9", 233: "9.1", 309: "9.1", 404: "5.1", 405: "5"}
# Synthetic objects on patch tags, still pinned.
ON_PATCH_TAGS = {102: "8.4.0", 108: "9.1.1", 203: "9.1.1", 403: "5.1.1"}


def test_the_synthetic_objects_on_the_current_default_tags_are_affected():
    inventory = build_inventory(FakeClient.from_fixture("synthetic_instance.json"))
    objects = inventory.by_id()
    for object_id, tag in ON_DEFAULT_TAGS.items():
        obj = objects[object_id].obj
        assert (obj.impact, obj.uses_default_image, obj.docker_image_tag) == (Impact.AFFECTED, True, tag), object_id
    for object_id, tag in ON_PATCH_TAGS.items():
        obj = objects[object_id].obj
        assert (obj.impact, obj.uses_default_image, obj.docker_image_tag) == (Impact.ADVISORY, False, tag), object_id


def test_the_preview_shows_the_objects_on_the_current_default_tags(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    from scanner.synthetic.preview import build_preview

    build_preview(tmp_path, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    marks = ",".join("?" * len(ON_DEFAULT_TAGS))
    rows = set(conn.execute(f"SELECT DISTINCT object_id, impact FROM objects WHERE object_id IN ({marks})",
                            tuple(ON_DEFAULT_TAGS)).fetchall())
    assert rows == {(object_id, "affected") for object_id in ON_DEFAULT_TAGS}  # In every scan of the preview.
    pinned_install = {r[0] for r in conn.execute(
        "SELECT object_id FROM findings WHERE rule_id = 'PY315-PINNED-PACKAGE-INSTALL'")}
    assert {209, 405} <= pinned_install  # A default-image-only rule, now applied on the current default tags.


def test_the_readme_states_the_current_default_tags():
    """README "Affected and Advisory" names the tags in common/release.py, so they change together."""
    from pathlib import Path

    readme = " ".join((Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8").split())
    for image, tag in release.CURRENT_DEFAULT_TAGS.items():
        assert f"`{image.rsplit('/', 1)[-1]}` `{tag}` or `{release.major_tag(tag)}`" in readme, image
    first = next(iter(release.CURRENT_DEFAULT_TAGS.values()))
    assert f"A patch tag such as `{first}.1` names a fixed image" in readme
