"""The local preview seed: enough made-up data to fill every app page."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from scanner.__main__ import main
from scanner.synthetic.preview import HOSTILE_NAME, LOCAL_JOBS_FILE, build_preview, preview_instance

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def test_preview_seed_has_history_running_and_failed_runs(tmp_path):
    jobs = build_preview(tmp_path, now=NOW)
    assert [(r["id"], r["state"]) for r in jobs["runs"]] == [
        (504, "running"), (503, "succeeded"), (502, "failed"), (501, "succeeded"),
    ]
    assert set(jobs["outputs"]) == {"501", "503"}
    assert all((tmp_path / name).exists() for name in jobs["outputs"].values())
    assert any(line["message"].startswith("PROGRESS phase=code") for line in jobs["logs"]["504"])
    assert json.loads((tmp_path / LOCAL_JOBS_FILE).read_text(encoding="utf-8"))["job_id"] == jobs["job_id"]

    conn = sqlite3.connect(tmp_path / "results.sqlite")
    statuses = dict(conn.execute(
        "SELECT civis_run_id, GROUP_CONCAT(status) FROM scan_runs GROUP BY civis_run_id"
    ).fetchall())
    assert statuses == {501: "running,succeeded", 502: "running", 503: "running,succeeded", 504: "running"}
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM checklist_status GROUP BY status").fetchall())
    assert counts["fixed"] > 0 and counts["ignored"] > 0 and counts["open"] > 0
    assert conn.execute("SELECT COUNT(*) FROM objects WHERE name = ?", (HOSTILE_NAME,)).fetchone()[0] > 0


def test_fixed_code_removes_some_findings():
    before = preview_instance(False, NOW)
    after = preview_instance(True, NOW)
    sources = lambda data: [s.get("source") for s in data["python3"]]  # noqa: E731
    assert sources(before) != sources(after)
    assert len(before["python3"]) == len(after["python3"])


def test_preview_cli(tmp_path, capsys):
    assert main(["--dry-run", "--preview", "--out", str(tmp_path)]) == 0
    assert "python -m app.local --results" in capsys.readouterr().out
