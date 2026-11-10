"""Whole numbers and booleans written the way the database load reads them (docs/PLAN.md 17.7, Part A).

civis 2.10's dataframe_to_civis writes the frame with ``df.to_csv(index=False)`` and loads that CSV into the
table. In the first live test, an INTEGER or BIGINT column that mixed numbers and None became float64, which
to_csv wrote as 12345.0, and the load of the objects table failed (TABLE_WRITE_FAILED error=CivisJobFailure,
status tables_failed). The fakes now render the CSV the same way and check every typed cell
(``tests.civis_fakes.load_frame_like_civis``), so these tests fail on the old frame. Made-up values only.
"""

from __future__ import annotations

import io as io_module
import json
import logging
import re
from datetime import datetime, timezone

import pytest

from common.models import CivisObject, ObjectType
from common.schema import COVERAGE, OBJECTS, TABLES
from scanner import scan_log
from scanner.config import ScanConfig
from scanner.output import (
    WHOLE_NUMBER_TYPES, CivisSink, ColumnValueError, SqliteSink, TableWriteError, boolean_text, table_frame,
    table_rows, whole_number,
)
from scanner.runner import run_scan
from tests.civis_fakes import CivisJobFailure, RecordingIO, load_frame_like_civis

pandas = pytest.importorskip("pandas")  # The scanner image has pandas; the Service image does not.

SCHEMA = "migration_results"
RUN = "run-20261110T120000Z-abc123"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
NAMESPACE = {"pg_catalog.pg_namespace": [["nspname"], [SCHEMA]]}


def _cells(text: str) -> list[dict]:
    import csv

    return list(csv.DictReader(io_module.StringIO(text)))


def _old_frame(table, run_id, rows):
    """The frame before this fix: pandas infers each column's type, so a whole-number column with None is float."""
    return pandas.DataFrame(table_rows(table, run_id, rows), columns=table.column_names)


def _mixed_rows(table) -> list[dict]:
    """Three rows: every typed column set, then None in every INTEGER, BIGINT, and BOOLEAN column with False in the
    booleans, then None everywhere but the run ID."""
    first, second, third = {}, {}, {}
    for column in table.columns:
        if column.sql_type in WHOLE_NUMBER_TYPES:
            first[column.name], second[column.name], third[column.name] = 12345, None, None
        elif column.sql_type == "BOOLEAN":
            first[column.name], second[column.name], third[column.name] = True, False, None
        elif column.sql_type == "TIMESTAMP":
            first[column.name] = second[column.name] = datetime(2026, 11, 10, 12, 0)
            third[column.name] = None
        else:
            first[column.name] = second[column.name] = "made-up text"
            third[column.name] = None
    return [first, second, third]


# --- The frame builder ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES, ids=[t.name for t in TABLES])
def test_rows_mixing_none_and_values_load_in_every_table(table):
    text = load_frame_like_civis(table_frame(table, RUN, _mixed_rows(table)), f"{SCHEMA}.{table.name}")
    rows = _cells(text)
    assert len(rows) == 3
    for column in table.columns:
        values = [row[column.name] for row in rows]
        if column.sql_type in WHOLE_NUMBER_TYPES:
            assert values == ["12345", "", ""], column.name
        elif column.sql_type == "BOOLEAN":
            assert values == ["true", "false", ""], column.name


@pytest.mark.parametrize("table", TABLES, ids=[t.name for t in TABLES])
def test_the_old_frame_fails_the_check_wherever_a_number_column_has_none(table):
    """Proves the check itself: the frame built before this fix is rejected for every table with a number column."""
    with pytest.raises(CivisJobFailure, match="not a whole number: '12345.0'"):
        load_frame_like_civis(_old_frame(table, RUN, _mixed_rows(table)), f"{SCHEMA}.{table.name}")


def test_the_old_frame_writes_python_booleans_the_check_refuses():
    rows = [{"object_id": 1, "owner_active": True, "archived": False, "hidden": False, "scheduled": False}]
    with pytest.raises(CivisJobFailure, match="is not a boolean: 'True'"):
        load_frame_like_civis(_old_frame(OBJECTS, RUN, rows), f"{SCHEMA}.objects")


def _custom_and_python_batch() -> list[dict]:
    """The live case: in one objects batch, only custom scripts have template_id and backing_script_id."""
    custom = CivisObject(object_id=9001, object_type=ObjectType.CUSTOM_SCRIPT, name="Example custom script",
                         url="https://platform.example.test/spa/#/scripts/custom/9001", owner_id=77,
                         owner_active=True, template_id=301, backing_script_id=4401, last_run_id=55501)
    python = CivisObject(object_id=9002, object_type=ObjectType.PYTHON_SCRIPT, name="Example Python script",
                         url="https://platform.example.test/spa/#/scripts/python3/9002", owner_id=78,
                         owner_active=False, last_run_id=55502)
    return [custom.to_row(), python.to_row()]


def test_an_objects_batch_where_only_custom_scripts_have_template_ids_loads():
    text = load_frame_like_civis(table_frame(OBJECTS, RUN, _custom_and_python_batch()), f"{SCHEMA}.objects")
    custom, python = _cells(text)
    assert (custom["template_id"], custom["backing_script_id"], custom["owner_id"]) == ("301", "4401", "77")
    assert (python["template_id"], python["backing_script_id"], python["owner_id"]) == ("", "", "78")
    assert (custom["owner_active"], python["owner_active"], python["archived"]) == ("true", "false", "false")
    # The same batch the old way: template_id and backing_script_id mix numbers and None, so to_csv writes 301.0.
    old = _old_frame(OBJECTS, RUN, _custom_and_python_batch())
    old_custom, old_python = _cells(old.to_csv(index=False))
    assert (old_custom["template_id"], old_custom["backing_script_id"], old_python["template_id"]) == ("301.0", "4401.0", "")
    with pytest.raises(CivisJobFailure):
        load_frame_like_civis(old, f"{SCHEMA}.objects")


def test_the_final_coverage_write_with_a_users_row_loads():
    """Summary rows have counts; the users row has none (docs/PLAN.md 17.5.3). Before this fix the counts were
    written as floats."""
    rows = [
        {"row_kind": "summary", "object_type": "python_script", "found_count": 12, "scanned_count": 10,
         "hidden_skipped_count": None, "archived_included_count": 0},
        {"row_kind": "users", "reason": "Owner active status is only partly known."},
    ]
    text = load_frame_like_civis(table_frame(COVERAGE, RUN, rows), f"{SCHEMA}.coverage")
    summary, users = _cells(text)
    assert (summary["found_count"], summary["scanned_count"], summary["archived_included_count"]) == ("12", "10", "0")
    assert summary["hidden_skipped_count"] == users["found_count"] == users["object_id"] == ""
    with pytest.raises(CivisJobFailure):
        load_frame_like_civis(_old_frame(COVERAGE, RUN, rows), f"{SCHEMA}.coverage")


@pytest.mark.parametrize("value, expected", [
    (None, None), (0, 0), (-12, -12), (12345, 12345), (12.0, 12), ("678", 678), (" 9 ", 9),
])
def test_whole_numbers(value, expected):
    assert whole_number(value) == expected


@pytest.mark.parametrize("value", [True, False, 1.5, float("nan"), float("inf"), "12.0", "abc", "", [1]])
def test_values_that_are_not_whole_numbers_are_refused(value):
    with pytest.raises((ValueError, TypeError, OverflowError)):
        whole_number(value)


def test_numpy_values():
    numpy = pytest.importorskip("numpy")
    assert whole_number(numpy.int64(7)) == 7 and boolean_text(numpy.bool_(True)) == "true"
    with pytest.raises(ValueError):
        whole_number(numpy.bool_(False))


@pytest.mark.parametrize("value, expected", [(True, "true"), (False, "false"), (None, None)])
def test_booleans(value, expected):
    assert boolean_text(value) == expected


@pytest.mark.parametrize("value", [1, 0, "true", "yes"])
def test_values_that_are_not_booleans_are_refused(value):
    with pytest.raises(ValueError):
        boolean_text(value)


@pytest.mark.parametrize("column, value", [("owner_id", True), ("last_run_id", 12.5), ("hidden", "yes")])
def test_a_bad_value_stops_the_write_before_anything_is_sent(column, value):
    io = RecordingIO(NAMESPACE)
    sink = CivisSink(object(), io, 31, SCHEMA, 41)
    with pytest.raises(TableWriteError) as excinfo:
        sink.append(OBJECTS, RUN, [{"object_id": 1, column: value}])
    assert excinfo.value.table == "objects" and isinstance(excinfo.value.__cause__, ColumnValueError)
    assert str(value) not in str(excinfo.value.__cause__)  # The error names the column, never the value.
    assert io.frames == []


def test_the_sink_writes_the_typed_frame():
    io = RecordingIO(NAMESPACE)
    CivisSink(object(), io, 31, SCHEMA, 41).append(OBJECTS, RUN, _custom_and_python_batch())
    assert io.frames == [f"{SCHEMA}.objects"] and io.failed_loads == []
    assert ",301,4401," in io.loaded[0][1]


# --- Whole scans -----------------------------------------------------------------------------------


def _fake_civis_scan(io):
    from scanner.synthetic import FIXTURES, FakeClient, SyntheticSession

    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    stream = io_module.StringIO()
    logger = scan_log.configure()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        result = run_scan(
            ScanConfig(database=31, schema=SCHEMA, credential_id=41, request_budget=1000),
            client=FakeClient(data), http_session=SyntheticSession(data),
            sink=CivisSink(object(), io, 31, SCHEMA, 41), outputs=_Outputs(), now=lambda: NOW,
        )
    finally:
        logger.removeHandler(handler)
    return result, stream.getvalue().splitlines()


class _Outputs:
    def __init__(self):
        self.saved = []

    def save_csv(self, name, data):
        self.saved.append(name)


def test_a_full_fake_civis_scan_succeeds_and_loads_every_table():
    io = RecordingIO(NAMESPACE)
    result, log = _fake_civis_scan(io)
    assert result.status == "succeeded" and result.tables_ok
    assert not [line for line in log if line.startswith(("TABLE_WRITE_FAILED", "FATAL"))]
    assert io.failed_loads == []
    assert {table for table, _ in io.loaded} == {f"{SCHEMA}.{t.name}" for t in TABLES}
    objects = [row for table, text in io.loaded if table.endswith(".objects") for row in _cells(text)]
    assert any(row["template_id"] for row in objects) and any(not row["template_id"] for row in objects)


def test_the_same_scan_with_the_old_frame_ends_tables_failed(monkeypatch):
    """The live failure, reproduced: the load fails, the scan logs TABLE_WRITE_FAILED error=CivisJobFailure and
    ends tables_failed, and the CSV report is still saved."""
    monkeypatch.setattr("scanner.output.table_frame", _old_frame)
    io = RecordingIO(NAMESPACE)
    result, log = _fake_civis_scan(io)
    assert result.status == "tables_failed" and not result.tables_ok and result.report_saved
    failed = [line for line in log if line.startswith("TABLE_WRITE_FAILED")]
    assert len(failed) == 1 and re.fullmatch(r"TABLE_WRITE_FAILED table=\w+ error=CivisJobFailure", failed[0])


def test_the_dry_run_succeeds_and_every_row_it_writes_would_load(tmp_path, monkeypatch):
    from scanner.__main__ import dry_run

    original = SqliteSink.append
    checked: set[str] = set()

    def checked_append(self, table, run_id, rows):
        if rows:
            load_frame_like_civis(table_frame(table, run_id, rows), f"{SCHEMA}.{table.name}")
            checked.add(table.name)
        return original(self, table, run_id, rows)

    monkeypatch.setattr(SqliteSink, "append", checked_append)
    result = dry_run(tmp_path, now=NOW, github_token=None)
    assert result.status == "succeeded" and result.tables_ok
    assert checked == {t.name for t in TABLES}
