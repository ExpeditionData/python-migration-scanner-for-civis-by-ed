"""The FATAL line and the final "failed" scan_runs row (docs/PLAN.md 17.5, Part D).

Dry runs on the synthetic instance and a local SQLite file. No network calls.
"""

from __future__ import annotations

import copy
import io
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from scanner import scan_log
from scanner import __main__ as entry
from scanner.__main__ import dry_run
from scanner.output import SchemaNotDedicated, SqliteSink
from scanner.runner import ScanFailed
from scanner.synthetic import FIXTURES, FakeClient

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
MARK = "zqmark"  # Made-up text in error messages; it must never reach the log.


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


def lines(stream) -> list[str]:
    return stream.getvalue().splitlines()


def synthetic(**changes) -> dict:
    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    data.update(copy.deepcopy(changes))
    return data


def scan_runs(folder) -> list[tuple]:
    conn = sqlite3.connect(folder / "results.sqlite")
    try:
        return conn.execute("SELECT status, finished_at IS NOT NULL FROM scan_runs ORDER BY rowid").fetchall()
    finally:
        conn.close()


# --- The FATAL line ----------------------------------------------------------------------------


def test_fatal_line_with_a_status(log_stream):
    scan_log.fatal(CivisAPIError(404), "inventory_users")
    assert lines(log_stream) == ["FATAL error=CivisAPIError status=404 step=inventory_users"]


def test_fatal_line_without_a_status(log_stream):
    scan_log.fatal(KeyError(MARK), "code_and_logs")
    assert lines(log_stream) == ["FATAL error=KeyError step=code_and_logs"]


@pytest.mark.parametrize("status", [True, "404", 404.0, None])
def test_fatal_line_logs_only_an_integer_status(log_stream, status):
    scan_log.fatal(CivisAPIError(status), "tables")
    assert lines(log_stream) == ["FATAL error=CivisAPIError step=tables"]


@pytest.mark.parametrize("step", ["inventory", "inventory_list_workflow", MARK, "", None, "Tables"])
def test_an_unknown_step_is_rejected(log_stream, step):
    with pytest.raises(ValueError):
        scan_log.fatal(KeyError("x"), step)
    assert log_stream.getvalue() == ""


def test_the_steps():
    assert set(scan_log.FATAL_STEPS) >= {
        "schema_check", "tables", "scanning_user", "inventory_users", "inventory_workflows",
        "inventory_archived_jobs", "code_and_logs", "save_results", "output",
        "inventory_list_python_script", "inventory_list_container_script", "inventory_list_custom_script",
        "inventory_list_dbt_script", "inventory_list_notebook", "inventory_list_service",
    }


# --- A FATAL during the scan ------------------------------------------------------------------


def test_a_fatal_in_inventory_writes_a_failed_row(tmp_path, log_stream):
    """The users "all" list fails with a status that is not 403 or 404, so the scan stops in inventory."""
    with pytest.raises(ScanFailed):
        dry_run(tmp_path, now=NOW, data=synthetic(users_list_errors={"all": 500}))
    assert scan_runs(tmp_path) == [("running", 0), ("failed", 1)]
    logged = lines(log_stream)
    assert logged[-1] == "FATAL error=FakeAPIError status=500 step=inventory_users"
    assert "PHASE name=inventory state=start" in logged and MARK not in log_stream.getvalue()


def test_a_fatal_in_a_list_call_names_the_object_type(tmp_path, log_stream, monkeypatch):
    original = FakeClient._scripts__list

    def refuse_containers(self, type=None, **kwargs):
        if type == "containers":
            raise CivisAPIError(503)
        return original(self, type=type, **kwargs)

    monkeypatch.setattr(FakeClient, "_scripts__list", refuse_containers)
    with pytest.raises(ScanFailed):
        dry_run(tmp_path, now=NOW)
    assert lines(log_stream)[-1] == "FATAL error=CivisAPIError status=503 step=inventory_list_container_script"
    assert scan_runs(tmp_path) == [("running", 0), ("failed", 1)]


def test_a_fatal_in_the_code_scan(tmp_path, log_stream, monkeypatch):
    import scanner.runner as runner

    def broken_engine():
        raise KeyError(MARK)

    monkeypatch.setattr(runner, "Engine", broken_engine)
    with pytest.raises(ScanFailed):
        dry_run(tmp_path, now=NOW)
    assert lines(log_stream)[-1] == "FATAL error=KeyError step=code_and_logs"
    assert scan_runs(tmp_path) == [("running", 0), ("failed", 1)]


def test_main_exits_1_with_one_fatal_line(tmp_path, log_stream, monkeypatch):
    original = entry.dry_run
    monkeypatch.setattr(entry, "dry_run", lambda out, **kwargs: original(
        out, data=synthetic(users_list_errors={"all": 500}), **kwargs))
    assert entry.main(["--dry-run", "--out", str(tmp_path)]) == 1
    assert [line for line in lines(log_stream) if line.startswith("FATAL")] == [
        "FATAL error=FakeAPIError status=500 step=inventory_users"]


def test_a_failure_before_the_scan_starts_is_step_setup(tmp_path, log_stream, monkeypatch):
    def broken(out, **kwargs):
        raise RuntimeError(MARK)

    monkeypatch.setattr(entry, "dry_run", broken)
    assert entry.main(["--dry-run", "--out", str(tmp_path)]) == 1
    assert lines(log_stream) == ["FATAL error=RuntimeError step=setup"]


def test_a_failed_row_that_cannot_be_written_logs_table_write_failed_and_still_exits_1(tmp_path, log_stream,
                                                                                         monkeypatch):
    original = SqliteSink.append

    def refuse_failed_row(self, table, run_id, rows):
        if table.name == "scan_runs" and rows and rows[0]["status"] == "failed":
            raise sqlite3.OperationalError(MARK)
        return original(self, table, run_id, rows)

    monkeypatch.setattr(SqliteSink, "append", refuse_failed_row)
    original_run = entry.dry_run
    monkeypatch.setattr(entry, "dry_run", lambda out, **kwargs: original_run(
        out, data=synthetic(users_list_errors={"all": 500}), **kwargs))
    assert entry.main(["--dry-run", "--out", str(tmp_path)]) == 1
    assert lines(log_stream)[-2:] == ["FATAL error=FakeAPIError status=500 step=inventory_users",
                                      "TABLE_WRITE_FAILED table=scan_runs error=OperationalError"]
    assert scan_runs(tmp_path) == [("running", 0)]


def test_no_failed_row_when_the_running_row_was_not_written(tmp_path, log_stream, monkeypatch):
    original = SqliteSink.append

    def refuse_scan_runs(self, table, run_id, rows):
        if table.name == "scan_runs":
            raise sqlite3.OperationalError(MARK)
        return original(self, table, run_id, rows)

    monkeypatch.setattr(SqliteSink, "append", refuse_scan_runs)
    with pytest.raises(ScanFailed):
        dry_run(tmp_path, now=NOW, data=synthetic(users_list_errors={"all": 500}))
    logged = lines(log_stream)
    assert logged.count("TABLE_WRITE_FAILED table=scan_runs error=OperationalError") == 1  # Only the running row.
    assert logged[-1] == "FATAL error=FakeAPIError status=500 step=inventory_users"
    assert scan_runs(tmp_path) == []


def test_no_row_when_the_schema_check_fails(tmp_path, log_stream):
    """SchemaNotDedicated still writes nothing, and its FATAL line is unchanged (no step)."""
    tmp_path.mkdir(exist_ok=True)
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    conn.execute(f"CREATE TABLE {MARK}_their_table (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaNotDedicated):
        dry_run(tmp_path, now=NOW)
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()
    assert "scan_runs" not in names
    assert not [line for line in lines(log_stream) if line.startswith("FATAL")]  # main logs that line.
    assert entry.main(["--dry-run", "--out", str(tmp_path)]) == 2
    assert lines(log_stream) == [
        "FATAL error=SchemaNotDedicated reason=results_schema_holds_other_tables_or_views other_objects=1"]


def test_a_fatal_in_the_detail_calls_names_inventory_details(tmp_path, log_stream, monkeypatch):
    """The detail calls of the kept scripts, notebooks, and Services have their own step (docs/PLAN.md 17.6); the
    inventory_list_<type> steps still name the list calls."""
    import scanner.inventory as inventory

    def broken_detail(*args, **kwargs):
        raise KeyError(MARK)

    monkeypatch.setattr(inventory, "_detail", broken_detail)
    with pytest.raises(ScanFailed):
        dry_run(tmp_path, now=NOW)
    assert lines(log_stream)[-1] == "FATAL error=KeyError step=inventory_details"
    assert scan_runs(tmp_path) == [("running", 0), ("failed", 1)]
    assert "inventory_details" in scan_log.FATAL_STEPS
