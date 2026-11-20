"""Objects of different types with the same ID, in the scanner (docs/PLAN.md 17.19). Made-up data only.

Civis numbers jobs (Python, container, custom, and dbt scripts) in one ID series, apart from notebooks,
Services, and workflows, so a job and a notebook can have the same ID. These tests add made-up notebooks to a
copy of the synthetic instance: one with Python script 101's ID (active workflow 604 runs job 101), and one
with the ID of archived job 105, which active workflow 601 runs. The synthetic instance file is not changed.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from common.models import ObjectType
from scanner import runner
from scanner.__main__ import SimulatedStop, dry_run
from scanner.inventory import build_inventory
from scanner.output import REPORT_NOTICE_LINES
from scanner.runner import _dedupe_findings
from scanner.synthetic import FIXTURES, FakeClient

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
SHARED_ID = 101  # Python script 101 and a made-up notebook 101.
ARCHIVED_JOB_ID = 105  # Archived Python job 105 (run by workflow 601) and a made-up notebook 105.
DRY_RUN_OBJECTS = 39  # Objects in a dry run of the unchanged synthetic instance.


def _notebook_like_401(data: dict, object_id: int) -> None:
    template = next(n for n in data["notebooks"] if n["id"] == 401)
    url = f"https://example-notebooks.s3.amazonaws.com/notebook-shared-{object_id}.ipynb"
    notebook = copy.deepcopy(template)
    notebook.update({"id": object_id, "name": f"Example notebook with job ID {object_id}", "notebook_url": url})
    data["notebooks"].append(notebook)
    data["notebook_files"][url] = copy.deepcopy(data["notebook_files"][template["notebook_url"]])


def plain_instance() -> dict:
    return json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))


def shared_instance(*ids: int) -> dict:
    """A fresh copy of the synthetic instance with a made-up notebook for each ID (101 by default)."""
    data = plain_instance()
    for object_id in ids or (SHARED_ID,):
        _notebook_like_401(data, object_id)
    return data


def report_rows(folder) -> list[dict]:
    text = next(folder.glob("*.csv")).read_text(encoding="utf-8-sig")
    stream = io.StringIO(text)
    for _ in range(REPORT_NOTICE_LINES):
        stream.readline()
    return list(csv.DictReader(stream))


def findings_of(conn: sqlite3.Connection, object_type: str, object_id: int) -> list[tuple]:
    return conn.execute(
        "SELECT rule_id, file, line_number, source FROM findings WHERE object_type = ? AND object_id = ? "
        "ORDER BY 1, 2, 3, 4", (object_type, object_id),
    ).fetchall()


def test_the_synthetic_instance_has_no_shared_ids():
    data = plain_instance()
    jobs = {r["id"] for key in ("python3", "containers", "custom", "dbt") for r in data[key]}
    others = [r["id"] for key in ("notebooks", "services", "workflows") for r in data[key]]
    assert not jobs & set(others) and len(others) == len(set(others))


def test_inventory_keeps_both_objects_and_records_the_workflow_on_the_job_only():
    inventory = build_inventory(FakeClient(shared_instance()))
    by_key = inventory.by_key()
    script = by_key[(ObjectType.PYTHON_SCRIPT, SHARED_ID)].obj
    notebook = by_key[(ObjectType.NOTEBOOK, SHARED_ID)].obj
    assert script.workflow_ids == [604]
    assert notebook.workflows == []
    assert inventory.jobs_by_id()[SHARED_ID].obj is script  # A job ID never maps to the notebook.
    assert len(inventory.by_key()) == len(inventory.items)


def test_an_archived_job_run_by_a_workflow_is_read_when_a_notebook_has_its_id():
    client = FakeClient(shared_instance(ARCHIVED_JOB_ID))
    inventory = build_inventory(client)
    by_key = inventory.by_key()
    archived = by_key[(ObjectType.PYTHON_SCRIPT, ARCHIVED_JOB_ID)].obj
    assert archived.archived is True and archived.workflow_ids == [601]
    assert by_key[(ObjectType.NOTEBOOK, ARCHIVED_JOB_ID)].obj.workflows == []


def test_a_dry_run_writes_each_object_and_its_own_findings(tmp_path):
    full = tmp_path / "plain"
    dry_run(full, now=NOW, data=plain_instance())
    shared = tmp_path / "shared"
    result = dry_run(shared, now=NOW, data=shared_instance())
    assert result.status == "succeeded"

    conn = sqlite3.connect(shared / "results.sqlite")
    plain = sqlite3.connect(full / "results.sqlite")
    rows = conn.execute("SELECT object_type, workflows FROM objects WHERE object_id = ? ORDER BY 1", (SHARED_ID,))
    rows = dict(rows.fetchall())
    assert set(rows) == {"notebook", "python_script"}
    assert json.loads(rows["python_script"]) == [{"id": 604, "name": "Example orchestration only"}]
    assert rows["notebook"] is None
    assert conn.execute("SELECT COUNT(1) FROM objects").fetchone()[0] == DRY_RUN_OBJECTS + 1

    script_findings = findings_of(conn, "python_script", SHARED_ID)
    notebook_findings = findings_of(conn, "notebook", SHARED_ID)
    assert script_findings and notebook_findings
    assert script_findings == findings_of(plain, "python_script", SHARED_ID)  # The notebook adds nothing to it.
    assert notebook_findings == findings_of(conn, "notebook", 401)  # The same file as notebook 401.

    # The CSV lists each object's findings under that object.
    report = [r for r in report_rows(shared) if r["object_id"] == str(SHARED_ID)]
    for object_type, expected in (("python_script", script_findings), ("notebook", notebook_findings)):
        mine = [r for r in report if r["object_type"] == object_type]
        assert all(r["row_type"] == "finding" for r in mine)
        assert sorted((r["rule_id"], r["file"], int(r["line_number"]), r["source"]) for r in mine) == expected
        assert {bool(r["workflows"]) for r in mine} == {object_type == "python_script"}


def test_a_resumed_scan_scans_the_notebook_and_not_the_finished_script_again(tmp_path, monkeypatch):
    # Results in the order the objects were sent, so the first object saved is Python script 101 (the first
    # item in the inventory).
    monkeypatch.setattr(runner, "as_completed", lambda futures: list(futures))
    with pytest.raises(SimulatedStop):
        dry_run(tmp_path, now=NOW, data=shared_instance(), stop_after=1, checkpoint_objects=1)
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    assert conn.execute("SELECT object_type, object_id FROM objects").fetchall() == [("python_script", SHARED_ID)]
    run_id = conn.execute("SELECT run_id FROM scan_runs").fetchone()[0]
    first_findings = findings_of(conn, "python_script", SHARED_ID)
    conn.close()

    resumed = dry_run(tmp_path, now=NOW, data=shared_instance(), run_id=run_id, checkpoint_objects=5)
    assert resumed.status == "succeeded" and resumed.skipped_as_done == 1
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    rows = conn.execute("SELECT object_type FROM objects WHERE object_id = ? ORDER BY 1", (SHARED_ID,)).fetchall()
    assert rows == [("notebook",), ("python_script",)]  # The notebook is scanned; the script is not scanned again.
    assert conn.execute("SELECT COUNT(1) FROM objects").fetchone()[0] == DRY_RUN_OBJECTS + 1
    assert conn.execute("SELECT COUNT(1) FROM (SELECT DISTINCT object_type, object_id FROM objects)").fetchone()[0] \
        == DRY_RUN_OBJECTS + 1
    assert findings_of(conn, "python_script", SHARED_ID) == first_findings
    assert findings_of(conn, "notebook", SHARED_ID)


def test_dedupe_keeps_findings_that_differ_only_in_object_type():
    base = {"object_id": SHARED_ID, "rule_id": "CV3-ML", "file": "code", "line_number": 3, "source": "code"}
    rows = [{**base, "object_type": "python_script"}, {**base, "object_type": "notebook"},
            {**base, "object_type": "python_script"}]
    assert [r["object_type"] for r in _dedupe_findings(rows)] == ["python_script", "notebook"]
