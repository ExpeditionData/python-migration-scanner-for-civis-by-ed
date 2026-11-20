"""One type's list call fails (docs/PLAN.md 17.12, Part B).

A list that fails after civis's retries keeps the rows read, logs one LIST_FAILED line, adds a "list_failed"
coverage row with a fixed note, and the scan goes on with the next type. Only when every list fails does the
scan stop with FATAL (tests/test_fatal.py). Dry runs on the synthetic instance; no network calls.
"""

from __future__ import annotations

import copy
import io
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from common.models import ObjectType, list_failed_note
from scanner import scan_log
from scanner.__main__ import dry_run
from scanner.inventory import build_inventory
from scanner.inventory.base import InventoryContext, Inventory, ListFailed
from scanner.synthetic import FIXTURES, FakeClient

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
MARK = "zq-list-marker-5521"  # Made-up text in an error message; it must never reach any output.


class CivisAPIError(Exception):
    """Stands in for civis.base.CivisAPIError: a message and an integer status_code."""

    def __init__(self, status_code, message=f"{MARK} made-up message"):
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def synthetic(**changes) -> dict:
    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    data.update(copy.deepcopy(changes))
    return data


def table(folder, statement: str) -> list[tuple]:
    conn = sqlite3.connect(folder / "results.sqlite")
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


def objects_by_type(folder) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for object_type, object_id in table(folder, "SELECT object_type, object_id FROM objects"):
        found.setdefault(object_type, set()).add(object_id)
    return found


def list_failed_rows(folder) -> list[tuple]:
    return table(folder, "SELECT object_type, found_count, reason FROM coverage WHERE row_kind = 'list_failed'")


# --- list_all keeps the rows read ----------------------------------------------------------------


def test_list_all_keeps_the_rows_read_and_the_original_error():
    error = CivisAPIError(503)

    def method(**kwargs):
        yield {"id": 1}
        yield {"id": 2}
        raise error

    ctx = InventoryContext(None, {}, Inventory())
    with pytest.raises(ListFailed) as raised:
        ctx.list_all(method)
    assert raised.value.rows == [{"id": 1}, {"id": 2}] and raised.value.error is error
    assert str(raised.value) == "CivisAPIError" and MARK not in repr(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_the_synthetic_list_errors_setting():
    client = FakeClient(synthetic(list_errors={"container_script": {"status": 503, "after": 3}}))
    rows = []
    with pytest.raises(Exception) as raised:
        for row in client.scripts.list(type="containers", iterator=True, limit=50, archived="false"):
            rows.append(row)
    assert len(rows) == 3 and raised.value.status_code == 503
    assert len(list(client.scripts.list(type="python3", iterator=True, limit=50, archived="false"))) == 12


# --- A failure on the first page and after some rows -------------------------------------------------


def test_a_failure_on_the_first_page_keeps_scanning_the_other_types(tmp_path, log_stream):
    normal = tmp_path / "normal"
    dry_run(normal, now=NOW)
    failed = tmp_path / "failed"
    result = dry_run(failed, now=NOW, data=synthetic(list_errors={"notebook": {"status": 503}}))

    assert result.status == "succeeded"
    logged = log_stream.getvalue().splitlines()
    assert "LIST_FAILED type=notebook rows_read=0 error=FakeAPIError status=503" in logged
    assert "INVENTORY type=notebook listed=0 kept=0 outside_window=0 template_copies=0 not_python=0" in logged
    note = ("Civis stopped returning the list of notebooks after 0 rows (HTTP 503). Any notebooks after that "
            "point were not found or scanned. Run a new scan to check them.")
    assert list_failed_rows(failed) == [("notebook", 0, note)]
    before, after = objects_by_type(normal), objects_by_type(failed)
    assert "notebook" in before and "notebook" not in after
    assert {k: v for k, v in before.items() if k != "notebook"} == after  # Every other type is scanned as before.
    assert table(failed, "SELECT status FROM scan_runs ORDER BY rowid") == [("running",), ("succeeded",)]


def test_a_failure_after_some_rows_keeps_those_rows(tmp_path, log_stream):
    result = dry_run(tmp_path, now=NOW, data=synthetic(list_errors={"container_script": {"status": 502, "after": 3}}))
    assert result.status == "succeeded"
    logged = log_stream.getvalue().splitlines()
    assert "LIST_FAILED type=container_script rows_read=3 error=FakeAPIError status=502" in logged
    assert "INVENTORY type=container_script listed=3 kept=3 outside_window=0 template_copies=0 not_python=0" in logged
    assert objects_by_type(tmp_path)["container_script"] == {201, 202, 203}  # The first three listed rows.
    (row,) = list_failed_rows(tmp_path)
    assert row[:2] == ("container_script", 3)
    assert row[2].startswith("Civis stopped returning the list of container scripts after 3 rows (HTTP 502).")


def test_the_note_without_a_status_gives_the_error_type():
    assert list_failed_note(ObjectType.SERVICE, 1, None, "ReadTimeout") == (
        "Civis stopped returning the list of Services after 1 row (ReadTimeout). Any Services after that point "
        "were not found or scanned. Run a new scan to check them.")
    assert "after 1,250 rows (HTTP 503)" in list_failed_note("custom_script", 1250, 503, "CivisAPIError")


def test_two_failed_lists_get_two_lines_and_two_rows(tmp_path, log_stream):
    errors = {"dbt_script": {"status": 500, "after": 1}, "service": {"status": 504, "after": 2}}
    assert dry_run(tmp_path, now=NOW, data=synthetic(list_errors=errors)).status == "succeeded"
    assert len([line for line in log_stream.getvalue().splitlines() if line.startswith("LIST_FAILED ")]) == 2
    assert sorted(r[:2] for r in list_failed_rows(tmp_path)) == [("dbt_script", 1), ("service", 2)]


# --- The custom script list and the workflow list ---------------------------------------------------


def test_custom_list_failing_reads_template_copies_under_their_own_type(tmp_path, log_stream):
    assert dry_run(tmp_path, now=NOW, data=synthetic(list_errors={"custom_script": {"status": 503}})).status == "succeeded"
    found = objects_by_type(tmp_path)
    assert "custom_script" not in {k for k, v in found.items() if v - {311}}  # Only 311, run by a workflow.
    # Template copies whose custom script the list did not return are read under their own type.
    assert {301, 302, 208} <= found["container_script"]
    assert 304 in found["python_script"] and 306 in found["dbt_script"]
    assert "template_copies=0" in next(line for line in log_stream.getvalue().splitlines()
                                       if line.startswith("INVENTORY type=container_script"))


def test_workflow_list_failing_on_the_first_page_adds_no_workflows_or_archived_jobs(tmp_path, log_stream):
    assert dry_run(tmp_path, now=NOW, data=synthetic(list_errors={"workflow": {"status": 503}})).status == "succeeded"
    found = objects_by_type(tmp_path)
    assert "workflow" not in found
    every_id = set().union(*found.values())
    assert not {105, 311} & every_id  # Archived jobs found only through workflow references.
    assert "LIST_FAILED type=workflow rows_read=0 error=FakeAPIError status=503" in log_stream.getvalue()
    (row,) = list_failed_rows(tmp_path)
    assert row[0] == "workflow" and "list of workflows after 0 rows" in row[2]
    assert table(tmp_path, "SELECT COUNT(*) FROM objects WHERE workflows IS NOT NULL")[0][0] == 0


def test_workflow_list_failing_partway_keeps_the_workflows_read(tmp_path):
    """Implementation choice: the workflows read before the failure are read as usual, with their references
    and the archived jobs they run. Workflows the list did not return add nothing."""
    assert dry_run(tmp_path, now=NOW, data=synthetic(list_errors={"workflow": {"status": 503, "after": 1}})).status \
        == "succeeded"
    found = objects_by_type(tmp_path)
    assert found["workflow"] == {601}
    assert {105, 311} <= set().union(*found.values())  # Run by 601.


def test_build_inventory_records_the_failure_and_the_known_ids():
    client = FakeClient(synthetic(list_errors={"python_script": {"status": 503, "after": 4}}))
    steps = []
    inventory = build_inventory(client, step=steps.append)
    (failure,) = inventory.list_failures
    assert (failure.object_type, failure.rows_read, failure.error_name, failure.status) == (
        ObjectType.PYTHON_SCRIPT, 4, "FakeAPIError", 503)
    assert steps[:2] == ["inventory_users", "inventory_list_python_script"] and "inventory_details" in steps


# --- Nothing from the error reaches any output --------------------------------------------------------


def test_no_exception_text_reaches_the_log_tables_csv_or_summary(tmp_path, log_stream, monkeypatch):
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary

    original = FakeClient._scripts__list_custom

    def failing_custom(self, **kwargs):
        rows = list(original(self, **kwargs))

        def pages():
            yield from rows[:2]
            raise CivisAPIError(503)

        return pages()

    monkeypatch.setattr(FakeClient, "_scripts__list_custom", failing_custom)
    assert dry_run(tmp_path, now=NOW).status == "succeeded"
    logged = log_stream.getvalue()
    assert "LIST_FAILED type=custom_script rows_read=2 error=CivisAPIError status=503" in logged
    text = "\n".join(summary._scan_lines(summary.ScanData(
        "run-20261105T120000Z-a1b2c3", "succeeded", None, None, log=summary.LogRead(logged.splitlines()))))
    assert "  list_failed: custom_script=2" in text
    assert "    LIST_FAILED CivisAPIError status=503: 1" in text
    outputs = {
        "log": logged,
        "tables": (tmp_path / "results.sqlite").read_bytes().decode("utf-8", errors="replace"),
        "csv": "".join(path.read_text(encoding="utf-8-sig") for path in tmp_path.glob("*.csv")),
        "summary": text,
    }
    for where, output in outputs.items():
        assert MARK not in output and "made-up message" not in output, where


# --- The support summary ------------------------------------------------------------------------------------


def test_summary_counts_list_failed_lines_and_lists_each_type():
    pytest.importorskip("flask")  # The scanner image has no Flask, and importing app loads it.
    from app import summary

    lines = [
        "LIST_FAILED type=custom_script rows_read=150 error=CivisAPIError status=503",
        "LIST_FAILED type=workflow rows_read=0 error=ReadTimeout",
        f"LIST_FAILED type={MARK} rows_read=3 error=CivisAPIError status=500",
    ]
    counts = summary.count_log(lines)
    assert counts.other_lines == 0
    assert counts.list_failed == {"custom_script": 150, "workflow": 0, "other": None}
    text = "\n".join(summary._scan_lines(summary.ScanData(
        "run-20261105T120000Z-a1b2c3", "succeeded", None, None, log=summary.LogRead(lines))))
    assert "  list_failed: custom_script=150" in text and "  list_failed: workflow=0" in text
    assert "  list_failed: other" in text and "list_failed: none" not in text
    assert "    LIST_FAILED CivisAPIError status=503: 1" in text and "    LIST_FAILED ReadTimeout: 1" in text
    assert MARK not in text
    none = "\n".join(summary._scan_lines(summary.ScanData(
        "run-20261105T120000Z-a1b2c3", "succeeded", None, None, log=summary.LogRead(["PHASE name=inventory state=start"]))))
    assert "  list_failed: none" in none


def test_scan_log_list_failed_validates_its_values(log_stream):
    scan_log.list_failed(ObjectType.CUSTOM_SCRIPT, 150, CivisAPIError(503))
    scan_log.list_failed("workflow", 0, TimeoutError(MARK))
    scan_log.list_failed("service", 2, CivisAPIError(True))
    assert log_stream.getvalue().splitlines() == [
        "LIST_FAILED type=custom_script rows_read=150 error=CivisAPIError status=503",
        "LIST_FAILED type=workflow rows_read=0 error=TimeoutError",
        "LIST_FAILED type=service rows_read=2 error=CivisAPIError",
    ]
    for bad in ((MARK, 1), ("notebook", -1), ("notebook", True)):
        with pytest.raises(ValueError):
            scan_log.list_failed(bad[0], bad[1], KeyError("x"))
