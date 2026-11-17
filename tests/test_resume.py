"""Resume a stopped scan without editing the job (docs/PLAN.md 17.13).

The decision in common/resume.py with made-up rows and times, and the scanner
making it by itself in dry runs on the synthetic instance: a canceled scan, a
failed scan, the next start after a resumed scan, failed reads, a RUN_ID left
on the job, and no names or text from Civis in the RESUME line. Made-up data
only; no network calls.
"""

from __future__ import annotations

import copy
import io
import json
import re
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from common import resume
from common.resume import MAX_RESUMES, RESUME_WINDOW_HOURS, CivisRun, decide, newest_scan
from common.version import __version__
from scanner import scan_log
from scanner.__main__ import SimulatedStop, dry_run
from scanner.synthetic import FIXTURES, FakeClient

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
JOB = 9100
MARK = "zqmark"  # Made-up text in Civis answers; it must never reach the log.
DRY_RUN_OBJECTS = 39  # tests/test_output_and_runs.py


# --- The decision, with made-up rows ------------------------------------------------------------


def row(run_id="run-20261113T100000Z-aaaaaa", status="running", civis_run_id=11, recorded="2026-11-13 10:00:00",
        started="2026-11-13 10:00:00", version=__version__, lookback=400, token=False, job=JOB, **extra) -> dict:
    parameters = {"lookback_days": lookback, "github_token_attached": token, "max_concurrency": 4,
                  "request_budget_per_5_minutes": 500, "retention_days": None, "resumed_run_id": None, **extra}
    return {"run_id": run_id, "status": status, "civis_run_id": civis_run_id, "civis_job_id": job,
            "recorded_at": recorded, "started_at": started, "tool_version": version,
            "parameters": json.dumps(parameters)}


def ended(hours_ago: float, state: str = "cancelled") -> CivisRun:
    return CivisRun(state, NOW - timedelta(hours=hours_ago))


def check(rows, runs=None, **known) -> resume.Decision:
    known = {"version": __version__, "lookback_days": 400, "github_attached": False, **known}
    return decide(rows, NOW, civis_runs={11: ended(1)} if runs is None else runs, **known)


def test_a_canceled_scan_with_only_a_running_row_is_resumed():
    decision = check([row()])
    assert (decision.resume, decision.run_id, decision.reason, decision.resumes) == (
        True, "run-20261113T100000Z-aaaaaa", None, 0)
    assert decision.window_ends == NOW - timedelta(hours=1) + timedelta(hours=RESUME_WINDOW_HOURS)


def test_a_failed_scan_is_resumed():
    decision = check([row(), row(status="failed", recorded="2026-11-13 10:30:00")], {11: ended(1, "failed")})
    assert decision.resume and decision.reason is None


def test_no_scan():
    assert check([]).reason == "no_scan"


@pytest.mark.parametrize("final", ["succeeded", "tables_failed"])
def test_a_finished_scan_is_not_resumed(final):
    assert check([row(), row(status=final, recorded="2026-11-13 11:00:00")]).reason == "last_scan_finished"
    # Any finished row counts, even when a later "running" row was written (a resume of a finished scan).
    assert check([row(), row(status=final, recorded="2026-11-13 11:00:00"),
                  row(recorded="2026-11-13 11:30:00", civis_run_id=12)], {12: ended(0.2)}).reason == "last_scan_finished"


def test_only_the_newest_scan_by_first_start_is_considered():
    older = row(run_id="run-20261112T100000Z-bbbbbb", started="2026-11-12 10:00:00", recorded="2026-11-12 10:00:00",
                civis_run_id=9)
    newest_done = [row(), row(status="succeeded", recorded="2026-11-13 11:00:00")]
    assert check([older, *newest_done], {9: ended(25), 11: ended(1, "succeeded")}).reason == "last_scan_finished"
    # A scan whose newest row is recorded later but that started earlier is still the older one.
    late_row = dict(older, recorded_at="2026-11-13 11:50:00", civis_run_id=10)
    assert check([older, late_row, *newest_done], {10: ended(0.1)}).reason == "last_scan_finished"


def test_a_scan_with_no_civis_run_id_is_not_resumed_live():
    assert check([row(civis_run_id=None, job=None)]).reason == "run_state_unknown"


@pytest.mark.parametrize("runs", [{}, {11: CivisRun("cancelled", None)}, {11: CivisRun(None, None)}])
def test_an_unknown_end_time_starts_a_new_scan(runs):
    assert check([row()], runs).reason == "run_state_unknown"


@pytest.mark.parametrize("state", ["queued", "running"])
def test_an_active_run_starts_a_new_scan(state):
    assert check([row()], {11: CivisRun(state, None)}).reason == "run_active"
    # An earlier run of the scan that is still active counts too.
    rows = [row(), row(recorded="2026-11-13 11:00:00", civis_run_id=12)]
    assert check(rows, {11: CivisRun(state, None), 12: ended(0.5)}).reason == "run_active"


def test_the_window_edge():
    exactly = check([row()], {11: CivisRun("cancelled", NOW - timedelta(hours=RESUME_WINDOW_HOURS))})
    assert exactly.resume
    late = check([row()], {11: CivisRun("cancelled", NOW - timedelta(hours=RESUME_WINDOW_HOURS, seconds=1))})
    assert (late.resume, late.reason) == (False, "too_old")
    assert late.window_ends == NOW - timedelta(seconds=1)


def test_a_scan_resumed_once_and_twice():
    once = [row(), row(recorded="2026-11-13 10:40:00", civis_run_id=12)]
    decision = check(once, {11: ended(2), 12: ended(1)})
    assert decision.resume and decision.resumes == 1
    twice = once + [row(recorded="2026-11-13 11:10:00", civis_run_id=13)]
    decision = check(twice, {11: ended(2), 12: ended(1.5), 13: ended(0.5)})
    assert (decision.resume, decision.reason, decision.resumes) == (False, "resumed_twice", MAX_RESUMES)
    # Several rows from one Civis run count once.
    repeated = once + [row(status="failed", recorded="2026-11-13 10:50:00", civis_run_id=12)]
    assert check(repeated, {11: ended(2), 12: ended(1)}).resume


def test_the_newest_civis_run_decides_the_window():
    rows = [row(), row(recorded="2026-11-13 10:40:00", civis_run_id=12)]
    assert check(rows, {11: ended(30), 12: ended(1)}).resume
    assert check(rows, {11: ended(1), 12: ended(30)}).reason == "too_old"


def test_a_changed_version():
    assert check([row(version="0.0.9")]).reason == "version_changed"
    assert check([row(), row(version="0.0.9", recorded="2026-11-13 10:10:00")]).reason == "version_changed"


@pytest.mark.parametrize("changes, known", [
    ({"lookback": 30}, {}),
    ({}, {"lookback_days": 30}),
    ({}, {"github_attached": True}),  # A token added since the scan started.
    ({"token": True}, {}),  # A token removed.
])
def test_changed_settings(changes, known):
    assert check([row(**changes)], **known).reason == "settings_changed"


def test_unreadable_or_missing_parameters_count_as_changed():
    assert check([dict(row(), parameters="not json")]).reason == "settings_changed"
    assert check([dict(row(), parameters=json.dumps({"lookback_days": 400}))]).reason == "settings_changed"


def test_concurrency_budget_and_retention_do_not_block_a_resume():
    rows = [row(max_concurrency=16, request_budget_per_5_minutes=100, retention_days=30)]
    assert check(rows).resume


def test_a_scan_that_saved_its_summary_rows():
    assert check([row()], results_saved=True).reason == "results_saved"
    assert check([row()], results_saved=False).resume


def test_checks_the_caller_cannot_make_are_skipped():
    rows = [row(version="0.0.9", lookback=30, token=True)]
    assert decide(rows, NOW, civis_runs={11: ended(1)}).resume


def test_text_values_as_civis_returns_them():
    """read_civis_sql returns every value as text."""
    text_row = {k: (str(v) if v is not None else "") for k, v in row().items()}
    decision = decide([text_row], NOW, civis_runs={11: CivisRun("cancelled", "2026-11-13T11:00:00Z")},
                      version=__version__, lookback_days=400, github_attached=False)
    assert decision.resume and newest_scan([text_row]).civis_run_ids == (11,)


def test_reasons_are_fixed():
    assert set(resume.REASONS) == {
        "no_scan", "last_scan_finished", "run_state_unknown", "run_active", "too_old", "resumed_twice",
        "version_changed", "settings_changed", "results_saved", "tables_unavailable", "read_failed"}
    with pytest.raises(ValueError):
        resume.Decision.new("something else")


# --- The scanner makes the decision by itself ---------------------------------------------------


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


def synthetic(**runs) -> dict:
    """The synthetic instance, with this job's earlier Civis runs ("<run ID>": {state, finished_at})."""
    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    data["container_runs"] = {f"{JOB}/{run}": copy.deepcopy(value) for run, value in runs.items()}
    return data


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def scan(tmp_path, civis_run_id: int, data: dict | None = None, at: datetime = NOW, **kwargs):
    return dry_run(tmp_path, now=at, data=data or synthetic(), civis_job_id=JOB, civis_run_id=civis_run_id,
                   checkpoint_objects=5, **kwargs)


def lines(stream) -> list[str]:
    return stream.getvalue().splitlines()


def resume_lines(stream) -> list[str]:
    return [line for line in lines(stream) if line.startswith("RESUME")]


def table(tmp_path, statement: str) -> list[tuple]:
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


def stopped_scan(tmp_path, civis_run_id: int = 11, objects: int = 10) -> str:
    with pytest.raises(SimulatedStop):
        scan(tmp_path, civis_run_id, stop_after=objects)
    return table(tmp_path, "SELECT run_id FROM scan_runs")[0][0]


def test_the_runner_resumes_a_canceled_scan(tmp_path, log_stream):
    run_id = stopped_scan(tmp_path)
    assert table(tmp_path, "SELECT status FROM scan_runs") == [("running",)]
    later = NOW + timedelta(hours=2)
    data = synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW + timedelta(minutes=10))}})
    result = scan(tmp_path, 12, data, at=later)
    assert (result.run_id, result.status, result.skipped_as_done) == (run_id, "succeeded", 10)
    logged = lines(log_stream)
    assert f"RESUME decision=resume run_id={run_id} resumes=1" in logged
    index = logged.index(f"RESUME decision=resume run_id={run_id} resumes=1")
    assert logged[index + 1] == f"RUN run_id={run_id} status=running"
    assert logged[-1].startswith(f"RUN run_id={run_id} status=succeeded objects_total={DRY_RUN_OBJECTS} "
                                 f"objects_scanned={DRY_RUN_OBJECTS - 10} ")
    # Earlier rows kept, every object once, the first start used, and the resumed run ID in the parameters.
    assert table(tmp_path, "SELECT COUNT(DISTINCT object_id), COUNT(*) FROM objects") == [
        (DRY_RUN_OBJECTS, DRY_RUN_OBJECTS)]
    rows = table(tmp_path, "SELECT civis_run_id, status, started_at, parameters FROM scan_runs ORDER BY rowid")
    assert [(r[0], r[1]) for r in rows] == [(11, "running"), (12, "running"), (12, "succeeded")]
    assert {r[2] for r in rows} == {"2026-11-13 12:00:00"}
    assert json.loads(rows[-1][3])["resumed_run_id"] == run_id


def test_the_resumed_scan_keeps_its_first_civis_run_label(tmp_path):
    pytest.importorskip("flask")  # app/views.py imports app/backends, which needs Flask in the app package.
    from app.views import summarize_scan_runs

    stopped_scan(tmp_path)
    data = synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW + timedelta(minutes=10))}})
    scan(tmp_path, 12, data, at=NOW + timedelta(hours=1))
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM scan_runs")]
    conn.close()
    [summary] = summarize_scan_runs(rows)
    assert (summary.label, summary.civis_run_id, summary.resumes) == ("Civis run 11", 12, 1)


def test_the_runner_resumes_a_failed_scan(tmp_path, log_stream, monkeypatch):
    """A FATAL after every object was written (in save_results, before the summary rows): the "failed" row from
    docs/PLAN.md 17.5.10. The resume skips every finished object."""
    import scanner.runner as runner

    def broken(*args):
        raise KeyError(MARK)

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_summary_rows", broken)
        with pytest.raises(runner.ScanFailed):
            scan(tmp_path, 11)
    run_id = table(tmp_path, "SELECT run_id FROM scan_runs")[0][0]
    assert table(tmp_path, "SELECT status FROM scan_runs ORDER BY rowid") == [("running",), ("failed",)]
    data = synthetic(**{"11": {"state": "failed", "finished_at": stamp(NOW + timedelta(minutes=5))}})
    result = scan(tmp_path, 12, data, at=NOW + timedelta(hours=3))
    assert (result.run_id, result.status, result.skipped_as_done) == (run_id, "succeeded", DRY_RUN_OBJECTS)
    assert f"RESUME decision=resume run_id={run_id} resumes=1" in lines(log_stream)
    assert lines(log_stream)[-1].startswith(f"RUN run_id={run_id} status=succeeded objects_total={DRY_RUN_OBJECTS} "
                                            "objects_scanned=0 ")


def test_the_next_start_after_a_resumed_scan_finishes_is_a_new_scan(tmp_path, log_stream):
    first = stopped_scan(tmp_path)
    data = synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW + timedelta(minutes=10))}})
    scan(tmp_path, 12, data, at=NOW + timedelta(hours=1))
    client_data = synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW)},
                               "12": {"state": "succeeded", "finished_at": stamp(NOW + timedelta(hours=1))}})
    third = scan(tmp_path, 13, client_data, at=NOW + timedelta(hours=2))
    assert third.run_id != first and third.skipped_as_done == 0
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=last_scan_finished"


def test_twice_resumed_scan_is_not_resumed_again(tmp_path, log_stream):
    stopped_scan(tmp_path, 11, objects=5)
    runs = {"11": {"state": "cancelled", "finished_at": stamp(NOW)}}
    with pytest.raises(SimulatedStop):
        scan(tmp_path, 12, synthetic(**runs), at=NOW + timedelta(hours=1), stop_after=5)
    runs["12"] = {"state": "cancelled", "finished_at": stamp(NOW + timedelta(hours=1))}
    with pytest.raises(SimulatedStop):
        scan(tmp_path, 13, synthetic(**runs), at=NOW + timedelta(hours=2), stop_after=5)
    runs["13"] = {"state": "cancelled", "finished_at": stamp(NOW + timedelta(hours=2))}
    result = scan(tmp_path, 14, synthetic(**runs), at=NOW + timedelta(hours=3))
    assert result.skipped_as_done == 0
    assert resume_lines(log_stream)[-3:] == [
        "RESUME decision=resume run_id={} resumes=1".format(*table(tmp_path, "SELECT run_id FROM scan_runs")[0]),
        "RESUME decision=resume run_id={} resumes=2".format(*table(tmp_path, "SELECT run_id FROM scan_runs")[0]),
        "RESUME decision=new reason=resumed_twice"]


@pytest.mark.parametrize("run, reason", [
    ({"state": "cancelled", "finished_at": "2026-11-12T11:59:00Z"}, "too_old"),
    ({"state": "running"}, "run_active"),
    ({"state": "cancelled"}, "run_state_unknown"),
    ({"_error": 500}, "read_failed"),
    ({"_error": "read_timeout"}, "read_failed"),
    (None, "read_failed"),  # Civis answers 404 for a run it does not have.
])
def test_the_runner_starts_a_new_scan_when_it_should(tmp_path, log_stream, run, reason):
    first = stopped_scan(tmp_path)
    data = synthetic(**({"11": run} if run is not None else {}))
    result = scan(tmp_path, 12, data, at=NOW + timedelta(hours=1))
    assert result.run_id != first and result.status == "succeeded" and result.skipped_as_done == 0
    assert resume_lines(log_stream)[-1] == f"RESUME decision=new reason={reason}"
    assert "TABLE_WRITE_FAILED" not in log_stream.getvalue()  # A failed read does not turn the table writes off.


def test_a_changed_lookback_or_a_token_starts_a_new_scan(tmp_path, log_stream, monkeypatch):
    stopped_scan(tmp_path)
    data = synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW)}})
    scan(tmp_path, 12, data, at=NOW + timedelta(hours=1), github_token=None)  # The first scan had a token.
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=settings_changed"


def test_a_changed_version_starts_a_new_scan(tmp_path, log_stream, monkeypatch):
    import scanner.runner as runner

    stopped_scan(tmp_path)
    monkeypatch.setattr(runner, "__version__", "9.9.9")
    scan(tmp_path, 12, synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW)}}),
         at=NOW + timedelta(hours=1))
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=version_changed"


def test_a_scan_that_saved_its_summary_rows_is_not_resumed(tmp_path, log_stream, monkeypatch):
    """A FATAL after the coverage rows were written: a resume would write the summary rows twice."""
    import scanner.runner as runner

    def broken(rule_id, count):
        raise KeyError(MARK)

    with monkeypatch.context() as patch:
        patch.setattr(runner.scan_log, "findings_count", broken)
        with pytest.raises(runner.ScanFailed):
            scan(tmp_path, 11)
    assert table(tmp_path, "SELECT COUNT(*) FROM coverage WHERE row_kind = 'summary'")[0][0] > 0
    scan(tmp_path, 12, synthetic(**{"11": {"state": "failed", "finished_at": stamp(NOW)}}), at=NOW + timedelta(hours=1))
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=results_saved"


def test_a_run_of_another_job_is_not_read(tmp_path, log_stream, monkeypatch):
    stopped_scan(tmp_path)
    calls = []
    original = FakeClient._scripts__get_containers_runs
    monkeypatch.setattr(FakeClient, "_scripts__get_containers_runs",
                        lambda self, *args: calls.append(args) or original(self, *args))
    dry_run(tmp_path, now=NOW + timedelta(hours=1), data=synthetic(), civis_job_id=JOB + 1, civis_run_id=12,
            checkpoint_objects=5)
    assert calls == [] and resume_lines(log_stream)[-1] == "RESUME decision=new reason=run_state_unknown"


def test_the_civis_read_is_one_budgeted_call(tmp_path, log_stream, monkeypatch):
    stopped_scan(tmp_path)
    calls, clients = [], []
    original = FakeClient._scripts__get_containers_runs

    def answer(self, *args):
        calls.append(args)
        clients.append(self)
        return original(self, *args)

    monkeypatch.setattr(FakeClient, "_scripts__get_containers_runs", answer)
    result = scan(tmp_path, 12, synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW)}}),
                  at=NOW + timedelta(hours=1))
    assert calls == [(JOB, 11)]
    # Charged to the request budget like every other Civis call: api_calls counts every call the client got.
    assert result.api_calls == len(clients[0].calls)
    assert clients[0].called("scripts.get_containers_runs") == [((JOB, 11), {})]


def test_no_read_when_the_last_scan_finished(tmp_path, log_stream, monkeypatch):
    scan(tmp_path, 11)
    calls = []
    monkeypatch.setattr(FakeClient, "_scripts__get_containers_runs", lambda self, *args: calls.append(args))
    scan(tmp_path, 12, at=NOW + timedelta(hours=1))
    assert calls == [] and resume_lines(log_stream) == ["RESUME decision=new reason=no_scan",
                                                        "RESUME decision=new reason=last_scan_finished"]


def test_unusable_tables_start_a_new_scan(tmp_path, log_stream, monkeypatch):
    from scanner.output import SqliteSink

    stopped_scan(tmp_path)

    def refuse(self):
        raise RuntimeError(MARK)

    monkeypatch.setattr(SqliteSink, "ensure_tables", refuse)
    result = scan(tmp_path, 12, synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW)}}),
                  at=NOW + timedelta(hours=1))
    assert result.status == "tables_failed"
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=tables_unavailable"


@pytest.mark.parametrize("method", ["scan_run_rows", "has_summary_rows"])
def test_a_failed_table_read_starts_a_new_scan_and_keeps_writing(tmp_path, log_stream, monkeypatch, method):
    from scanner.output import SqliteSink

    stopped_scan(tmp_path)

    def refuse(self, *args):
        raise sqlite3.OperationalError(MARK)

    monkeypatch.setattr(SqliteSink, method, refuse)
    result = scan(tmp_path, 12, synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW)}}),
                  at=NOW + timedelta(hours=1))
    assert result.status == "succeeded" and result.tables_ok
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=read_failed"


def test_the_resume_line_holds_no_names_or_text_from_civis(tmp_path, log_stream):
    stopped_scan(tmp_path)
    data = synthetic(**{"11": {"state": "cancelled", "finished_at": stamp(NOW), "error": f"{MARK} error text",
                               "name": f"{MARK} job name"}})
    scan(tmp_path, 12, data, at=NOW + timedelta(hours=1))
    assert MARK not in log_stream.getvalue()
    pattern = re.compile(r"^RESUME decision=(resume run_id=run-\d{8}T\d{6}Z-[0-9a-f]{6} resumes=\d+"
                         r"|new reason=[a-z_]+)$")
    assert all(pattern.match(line) for line in resume_lines(log_stream))
    with pytest.raises(ValueError):
        scan_log.resume(reason=f"{MARK}")
    with pytest.raises(ValueError):
        scan_log.resume(f"run <{MARK}>", 1)


def test_a_dry_run_with_no_civis_run_starts_a_new_scan(tmp_path, log_stream):
    """Without a Civis run ID on the rows, there is no end time to check, so the scan is not resumed."""
    with pytest.raises(SimulatedStop):
        dry_run(tmp_path, now=NOW, stop_after=10, checkpoint_objects=5)
    dry_run(tmp_path, now=NOW)
    assert resume_lines(log_stream)[-1] == "RESUME decision=new reason=run_state_unknown"


def test_the_dry_runs_run_id_still_resumes_that_run(tmp_path, log_stream):
    run_id = stopped_scan(tmp_path)
    result = dry_run(tmp_path, now=NOW, run_id=run_id, checkpoint_objects=5)
    assert result.run_id == run_id and result.skipped_as_done == 10
    assert resume_lines(log_stream)[-1] == f"RESUME decision=resume run_id={run_id} resumes=1"


# --- RUN_ID left on a job from an earlier release -------------------------------------------------


def test_run_id_in_the_environment_is_ignored_with_one_log_line(monkeypatch, log_stream):
    from scanner import __main__ as entry
    from scanner import ratelimit

    seen = {}
    fake_civis = types.ModuleType("civis")
    fake_civis.APIClient = lambda **kwargs: object()
    fake_civis.io = types.ModuleType("civis.io")
    monkeypatch.setitem(sys.modules, "civis", fake_civis)
    monkeypatch.setattr(ratelimit, "install_request_timeout", lambda: None)
    monkeypatch.setattr(ratelimit, "civis_retrying", lambda: None)
    for name, value in {"CIVIS_JOB_ID": "11", "CIVIS_RUN_ID": "22", "RESULTS_DB_NAME": "example_db",
                        "RESULTS_SCHEMA": "example_schema", "RUN_ID": "run-20261013T120000Z-abc123"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "run_scan", lambda config, **kwargs: seen.setdefault("config", config))
    entry.live_run()
    assert seen["config"].run_id is None
    assert lines(log_stream) == ["RUN_ID_IGNORED"]
    monkeypatch.setenv("RUN_ID", "  ")
    log_stream.truncate(0)
    log_stream.seek(0)
    entry.live_run()
    assert lines(log_stream) == []


def test_the_summary_reports_the_resume_line():
    pytest.importorskip("flask")
    from app.summary import count_log

    assert count_log(["RESUME decision=resume run_id=run-20261113T100000Z-aaaaaa resumes=1"]).resume == "resume"
    assert count_log(["RESUME decision=new reason=too_old"]).resume == "new reason=too_old"
    assert count_log([f"RESUME decision=new reason={MARK}"]).resume == "other"
    assert count_log([f"RESUME decision={MARK}"]).resume == "other"
    assert count_log(["RUN_ID_IGNORED", "RESUME decision=new reason=no_scan"]).other_lines == 0
