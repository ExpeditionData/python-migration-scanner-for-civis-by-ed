"""Phase 4: configuration, sinks, the CSV report, checkpoints, resume, and end-to-end dry runs.

All runs use the synthetic instance and a local SQLite file. No network calls.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from common.models import ObjectType, ScanStatus
from common.rule_loader import load_rules
from common.schema import CHECKLIST_STATUS, FINDINGS, OBJECTS, SCAN_RUNS
from scanner import scan_log
from scanner.__main__ import SimulatedStop, dry_run, main
from scanner.checkpoint import Checkpointer
from scanner.config import ConfigError, ScanConfig
from scanner.output import CivisRunOutputs, CivisSink, SqliteSink, TableWriteError, fit, report_csv
from scanner.process import ObjectResult
from scanner.runner import AlreadyFinished
from common.models import CivisObject

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
FAKE_LOG_VALUES = ("Synthetic-Log-Pw-7731", "synthetic.member@example.org")
FAKE_CODE = ("make_backend_factory", "select 1", "import civis")


def db(out: Path) -> sqlite3.Connection:
    return sqlite3.connect(out / "results.sqlite")


def count(conn, table: str, where: str = "1=1") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


@pytest.fixture
def log_stream():
    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    yield stream
    logger.handlers[0].setStream(previous)


# --- Configuration -------------------------------------------------------------------

BASE_ENV = {"RESULTS_DB_NAME": "analytics", "RESULTS_SCHEMA": "migration_scan"}


def test_config_from_env_defaults():
    config = ScanConfig.from_env(BASE_ENV)
    assert (config.database, config.schema, config.lookback_days, config.max_concurrency, config.request_budget) == (
        "analytics", "migration_scan", 200, 4, 500
    )
    assert config.retention_days is None and config.run_id is None and config.github_token is None


def test_config_reads_every_parameter_and_hides_the_token():
    env = {
        **BASE_ENV, "RESULTS_DB_CREDENTIAL_ID": "77", "GITHUB_PASSWORD": "ghp_" + "x" * 36,  # pragma: allowlist secret  gitleaks:allow
        "LOOKBACK_DAYS": "120", "MAX_CONCURRENCY": "8", "REQUEST_BUDGET": "300", "RETENTION_DAYS": "365",
        "RUN_ID": "run-20261013T120000Z-abc123", "CIVIS_JOB_ID": "5", "CIVIS_RUN_ID": "9",
    }
    config = ScanConfig.from_env(env)
    assert (config.credential_id, config.lookback_days, config.max_concurrency, config.request_budget) == (77, 120, 8, 300)
    assert (config.retention_days, config.run_id, config.civis_job_id, config.civis_run_id) == (365, "run-20261013T120000Z-abc123", 5, 9)
    assert "ghp_" not in repr(config) and "ghp_" not in config.parameters_json()
    assert '"github_token_attached": true' in config.parameters_json()
    assert ScanConfig.from_env({**BASE_ENV, "RETENTION_DAYS": "0"}).retention_days is None
    assert ScanConfig.from_env({"RESULTS_DB_ID": "12", "RESULTS_SCHEMA": "s"}).database == 12


@pytest.mark.parametrize(
    "env, message",
    [
        ({"RESULTS_SCHEMA": "s"}, "RESULTS_DB"),
        ({**BASE_ENV, "RESULTS_SCHEMA": "s; DROP TABLE x"}, "RESULTS_SCHEMA"),
        ({**BASE_ENV, "MAX_CONCURRENCY": "64"}, "MAX_CONCURRENCY must be between 1 and 16"),
        ({**BASE_ENV, "LOOKBACK_DAYS": "abc"}, "LOOKBACK_DAYS must be a whole number"),
        ({**BASE_ENV, "REQUEST_BUDGET": "5000"}, "REQUEST_BUDGET"),
        ({**BASE_ENV, "RUN_ID": "x'; DROP"}, "RUN_ID"),
    ],
)
def test_config_errors_name_the_parameter_not_the_value(env, message):
    with pytest.raises(ConfigError, match=re.escape(message)) as excinfo:
        ScanConfig.from_env(env)
    assert "DROP" not in str(excinfo.value)


# --- Sinks and the CSV report -------------------------------------------------------------


def test_fit_cuts_text_by_utf8_bytes():
    assert fit("abc", "VARCHAR(2)") == "ab"
    assert fit("ééé", "VARCHAR(5)") == "éé"  # Each é is 2 bytes.
    assert fit(42, "VARCHAR(1)") == 42 and fit("text", "TIMESTAMP") == "text"


class FakeFuture:
    def result(self):
        return None


class FakeCivisIO:
    def __init__(self, read_rows=None, fail_on=None):
        self.calls = []
        self.read_rows = read_rows or [["object_id"], ["101"], ["102"]]
        self.fail_on = fail_on

    def query_civis(self, sql, database, **kwargs):
        self.calls.append(("query", sql, kwargs))
        return FakeFuture()

    def read_civis_sql(self, sql, database, **kwargs):
        self.calls.append(("read", sql, kwargs))
        return self.read_rows

    def dataframe_to_civis(self, df, database, table, **kwargs):
        self.calls.append(("load", table, kwargs, list(df.columns), len(df)))
        if self.fail_on and table.endswith(self.fail_on):
            raise RuntimeError("load failed")
        return FakeFuture()

    def file_to_civis(self, buf, name=None, expires_at="DEFAULT", client=None):
        self.calls.append(("file", name, expires_at, buf.read()))
        return 555


def test_civis_sink_sql_is_safe_and_append_only():
    pytest.importorskip("pandas")
    fake = FakeCivisIO()
    sink = CivisSink(object(), fake, "analytics", "migration_scan", 77)
    sink.ensure_tables()
    sink.append(FINDINGS, "run-1", [{"object_id": 1, "rule_id": "CV3-ML", "file": "x.py"}])
    assert sink.finished_object_ids("run-1") == {101, 102}
    statements = [c[1] for c in fake.calls if c[0] in ("query", "read")]
    assert all(s.startswith("CREATE TABLE IF NOT EXISTS migration_scan.") for s in statements[:5])
    assert "WHERE run_id = 'run-1'" in statements[-1]
    load = next(c for c in fake.calls if c[0] == "load")
    assert load[1] == "migration_scan.findings" and load[2]["existing_table_rows"] == "append" and load[2]["credential_id"] == 77
    assert load[3] == FINDINGS.column_names
    joined = " ".join(statements).upper()
    assert "GRANT" not in joined and "DROP" not in joined and "DELETE" not in joined
    sink.delete_runs(["run-0"])
    deletes = [c[1] for c in fake.calls if c[0] == "query" and c[1].startswith("DELETE")]
    assert len(deletes) == 5 and all("'run-0'" in d for d in deletes)
    with pytest.raises(Exception):
        sink.finished_object_ids("x'; DROP TABLE y; --")


def test_civis_sink_reports_which_table_failed():
    pytest.importorskip("pandas")
    sink = CivisSink(object(), FakeCivisIO(fail_on="objects"), "analytics", "migration_scan", None)
    with pytest.raises(TableWriteError) as excinfo:
        sink.append(OBJECTS, "run-1", [{"object_id": 1}])
    assert excinfo.value.table == "objects"


def test_civis_run_output_never_expires():
    class Scripts:
        def __init__(self):
            self.calls = []

        def post_containers_runs_outputs(self, *args):
            self.calls.append(args)

    class Client:
        scripts = Scripts()

    fake = FakeCivisIO()
    client = Client()
    CivisRunOutputs(client, fake, 5, 9).save_csv("report.csv", b"a,b\n")
    assert fake.calls == [("file", "report.csv", None, b"a,b\n")]
    assert client.scripts.calls == [(5, 9, "File", 555)]


def test_report_csv_lists_every_object_and_blocks_formulas():
    objects = [
        {"object_id": 1, "object_type": "python_script", "name": "=HYPERLINK(\"http://x\")", "scan_status": "scanned"},
        {"object_id": 2, "object_type": "python_script", "name": "+cmd", "scan_status": "not_scanned", "scan_note": "@risk"},
    ]
    findings = [
        {"object_id": 1, "rule_id": "CV3-ML", "file": "-x.py", "line_number": 3, "severity": "high",
         "confidence": "high", "source": "code", "fix_guidance": "Pin civis.", "impact": "affected"},
    ]
    rows = list(csv.DictReader(io.StringIO(report_csv(objects, findings, load_rules()).decode("utf-8-sig"))))
    assert [(r["row_type"], r["object_id"]) for r in rows] == [("finding", "1"), ("object", "2")]
    assert rows[0]["object_name"].startswith("'=") and rows[0]["file"] == "'-x.py"
    assert rows[1]["object_name"] == "'+cmd" and rows[1]["scan_note"] == "'@risk"
    assert rows[0]["rule_title"] == "The civis.ml module is removed"


def test_sqlite_retention_keeps_the_current_checklist_status(tmp_path):
    sink = SqliteSink(tmp_path / "r.sqlite", "results")
    sink.ensure_tables()
    old, new = datetime(2025, 1, 1), datetime(2026, 10, 1)
    sink.append(CHECKLIST_STATUS, "run-old", [
        {"object_id": 1, "rule_id": "CV3-ML", "status": "open", "changed_by": "scanner", "changed_at": old},
        {"object_id": 2, "rule_id": "CV3-ML", "status": "fixed", "changed_by": "admin", "changed_at": old},
    ])
    sink.append(CHECKLIST_STATUS, "run-new", [
        {"object_id": 1, "rule_id": "CV3-ML", "status": "ignored", "changed_by": "admin", "changed_at": new},
    ])
    sink.append(FINDINGS, "run-old", [{"object_id": 1, "rule_id": "CV3-ML", "file": "x.py"}])
    sink.delete_runs(["run-old"])
    rows = sink.conn.execute("SELECT object_id, status FROM results.checklist_status ORDER BY object_id").fetchall()
    assert rows == [(1, "ignored"), (2, "fixed")]  # Object 2's only status survives.
    assert sink.conn.execute("SELECT COUNT(*) FROM results.findings").fetchone()[0] == 0


def test_checkpoints_every_n_objects_or_n_seconds():
    class Sink:
        def __init__(self):
            self.writes = []

        def append(self, table, run_id, rows):
            self.writes.append((table.name, len(rows)))

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    sink, clock = Sink(), Clock()
    checkpointer = Checkpointer(sink, "run-1", every_objects=3, every_seconds=600, clock=clock)
    obj = CivisObject(1, ObjectType.PYTHON_SCRIPT, "x", "u", scan_status=ScanStatus.SCANNED)
    for _ in range(3):
        checkpointer.add(ObjectResult(obj))
    assert checkpointer.flushes == 1 and sink.writes[-1] == ("objects", 3)
    checkpointer.add(ObjectResult(obj))
    clock.now = 601
    checkpointer.add(ObjectResult(obj))
    assert checkpointer.flushes == 2 and sink.writes[-1] == ("objects", 2)
    names = [w[0] for w in sink.writes]
    assert names[:3] == ["findings", "coverage", "objects"]  # Object rows last: they mark "done".


# --- End-to-end dry runs ----------------------------------------------------------------------


def test_dry_run_end_to_end(tmp_path, log_stream):
    result = dry_run(tmp_path, now=NOW)
    assert result.status == "succeeded" and result.report_saved and result.objects == 23
    conn = db(tmp_path)
    assert count(conn, "scan_runs") == 2
    assert [r[0] for r in conn.execute("SELECT status FROM scan_runs ORDER BY rowid")] == ["running", "succeeded"]
    assert count(conn, "objects") == 23
    assert count(conn, "findings") == result.findings > 0
    assert count(conn, "coverage", "row_kind = 'summary'") == len(ObjectType)
    assert count(conn, "coverage", "row_kind = 'not_python'") == 5
    assert count(conn, "checklist_status") == count(conn, "(SELECT DISTINCT object_id, rule_id FROM findings)")
    assert set(dict(conn.execute("SELECT scan_status, COUNT(*) FROM objects GROUP BY 1")).keys()) == {"scanned", "partial", "not_scanned"}
    params = conn.execute("SELECT parameters FROM scan_runs LIMIT 1").fetchone()[0]
    assert "synthetic-github-token" not in params

    logged = log_stream.getvalue()
    assert "PROGRESS phase=code objects_done=23 objects_total=23" in logged
    assert re.search(r"RUN run_id=run-\d{8}T\d{6}Z-[0-9a-f]{6} status=succeeded", logged)
    assert logged.splitlines()[-1].endswith(f" objects_total=23 objects_scanned=23 api_calls={result.api_calls}")

    report = next(tmp_path.glob("*.csv")).read_bytes()
    database = (tmp_path / "results.sqlite").read_bytes()
    for secret in FAKE_LOG_VALUES + FAKE_CODE:
        assert secret.encode() not in report
        assert secret.encode() not in database
        assert secret not in logged


def test_final_log_line_counts_every_civis_api_call(tmp_path, log_stream, monkeypatch):
    """Calls per object are measured in Phase 7 from the final RUN line, so the count must be exact."""
    import scanner.synthetic as synthetic

    clients = []

    class CountedClient(synthetic.FakeClient):
        def __init__(self, data):
            super().__init__(data)
            clients.append(self)

    monkeypatch.setattr(synthetic, "FakeClient", CountedClient)
    result = dry_run(tmp_path, now=NOW)
    sent = len(clients[0].calls)
    assert sent > result.objects  # Inventory, detail, and log calls: more than one per object.
    assert result.api_calls == sent
    final = log_stream.getvalue().splitlines()[-1]
    assert final == f"RUN run_id={result.run_id} status=succeeded objects_total=23 objects_scanned=23 api_calls={sent}"


def test_resumed_run_counts_only_its_own_calls_and_objects(tmp_path, log_stream):
    with pytest.raises(SimulatedStop):
        dry_run(tmp_path, now=NOW, stop_after=10, checkpoint_objects=5)
    run_id = re.search(r"RUN run_id=(\S+) status=running", log_stream.getvalue()).group(1)
    result = dry_run(tmp_path, now=NOW, run_id=run_id)
    final = log_stream.getvalue().splitlines()[-1]
    scanned = int(re.search(r"objects_scanned=(\d+)", final).group(1))
    assert final.startswith(f"RUN run_id={run_id} status=succeeded objects_total=23 ")
    assert 0 < scanned < 23 and final.endswith(f" api_calls={result.api_calls}")


def test_rescan_adds_only_new_open_checklist_rows(tmp_path):
    dry_run(tmp_path, now=NOW)
    conn = db(tmp_path)
    before = count(conn, "checklist_status")
    conn.execute("INSERT INTO checklist_status (run_id, object_id, rule_id, status, changed_by, changed_at) "
                 "VALUES ('run-x', 101, 'CV3-RENAME-USERS-LIST-ME', 'fixed', 'admin', '2026-10-13 13:00:00')")
    conn.commit()
    dry_run(tmp_path, now=NOW + timedelta(hours=2))
    conn = db(tmp_path)
    assert count(conn, "scan_runs") == 4
    assert count(conn, "checklist_status") == before + 1  # Only the admin's row was added.
    assert count(conn, "checklist_status", "status = 'fixed'") == 1


def test_stop_partway_then_resume(tmp_path):
    with pytest.raises(SimulatedStop):
        dry_run(tmp_path, now=NOW, checkpoint_objects=5, stop_after=10)
    conn = db(tmp_path)
    run_id = conn.execute("SELECT run_id FROM scan_runs").fetchone()[0]
    assert count(conn, "objects") == 10
    assert conn.execute("SELECT status FROM scan_runs").fetchall() == [("running",)]
    conn.close()

    resumed = dry_run(tmp_path, now=NOW, run_id=run_id, checkpoint_objects=5)
    assert resumed.status == "succeeded" and resumed.skipped_as_done == 10
    conn = db(tmp_path)
    assert count(conn, "objects") == 23
    assert count(conn, "(SELECT DISTINCT object_id FROM objects)") == 23  # No object written twice.

    full = tmp_path / "full"
    complete = dry_run(full, now=NOW)
    reference = db(full)
    finding_set = "SELECT DISTINCT object_id, rule_id, file, line_number, source FROM findings ORDER BY 1, 2, 3, 4, 5"
    assert conn.execute(finding_set).fetchall() == reference.execute(finding_set).fetchall()
    assert resumed.findings == complete.findings
    report = next(tmp_path.glob("*.csv")).read_text(encoding="utf-8-sig")
    assert len({row["object_id"] for row in csv.DictReader(io.StringIO(report))}) == 23

    with pytest.raises(AlreadyFinished):
        dry_run(tmp_path, now=NOW, run_id=run_id)


def test_table_failure_still_saves_the_csv(tmp_path, monkeypatch, log_stream):
    def broken_append(self, table, run_id, rows):
        if table.name == "findings" and rows:
            raise TableWriteError("findings")
        return original(self, table, run_id, rows)

    original = SqliteSink.append
    monkeypatch.setattr(SqliteSink, "append", broken_append)
    result = dry_run(tmp_path, now=NOW)
    assert result.status == "tables_failed" and result.report_saved and not result.tables_ok
    assert "TABLE_WRITE_FAILED table=findings" in log_stream.getvalue()
    report = next(tmp_path.glob("*.csv")).read_text(encoding="utf-8-sig")
    assert len({row["object_id"] for row in csv.DictReader(io.StringIO(report))}) == 23


def test_retention_deletes_old_runs_but_not_the_current_one(tmp_path):
    first = dry_run(tmp_path, now=NOW - timedelta(days=400))
    from scanner import __main__ as entry

    original = entry.ScanConfig

    class WithRetention(original):
        def __init__(self, **kwargs):
            super().__init__(**{**kwargs, "retention_days": 365})

    entry.ScanConfig = WithRetention
    try:
        second = dry_run(tmp_path, now=NOW)
    finally:
        entry.ScanConfig = original
    conn = db(tmp_path)
    runs = {r[0] for r in conn.execute("SELECT DISTINCT run_id FROM scan_runs")}
    assert runs == {second.run_id} and first.run_id not in {r[0] for r in conn.execute("SELECT DISTINCT run_id FROM objects")}


def test_cli_dry_run_and_exit_codes(tmp_path, monkeypatch):
    assert main(["--dry-run", "--out", str(tmp_path / "a")]) == 0
    for name in ("CIVIS_JOB_ID", "CIVIS_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESULTS_DB_NAME", "analytics")
    monkeypatch.setenv("RESULTS_SCHEMA", "migration_scan")
    assert main([]) == 2  # A live scan refuses to run outside Civis.
    monkeypatch.setenv("RESULTS_SCHEMA", "bad schema")
    assert main([]) == 2


def test_cli_stop_after_then_resume(tmp_path, capsys):
    out = tmp_path / "cli"
    assert main(["--dry-run", "--out", str(out), "--stop-after", "10"]) == 1
    run_id = sqlite3.connect(out / "results.sqlite").execute("SELECT run_id FROM scan_runs").fetchone()[0]
    assert main(["--dry-run", "--out", str(out), "--run-id", run_id]) == 0
    assert main(["--dry-run", "--out", str(out), "--run-id", run_id]) == 2
