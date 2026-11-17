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
        (505, "running"), (504, "cancelled"), (503, "succeeded"), (502, "failed"), (501, "succeeded"),
    ]
    assert set(jobs["outputs"]) == {"501", "503"}
    assert all((tmp_path / name).exists() for name in jobs["outputs"].values())
    assert any(line["message"].startswith("PROGRESS phase=code") for line in jobs["logs"]["505"])
    assert json.loads((tmp_path / LOCAL_JOBS_FILE).read_text(encoding="utf-8"))["job_id"] == jobs["job_id"]

    conn = sqlite3.connect(tmp_path / "results.sqlite")
    statuses = dict(conn.execute(
        "SELECT civis_run_id, GROUP_CONCAT(status) FROM scan_runs GROUP BY civis_run_id"
    ).fetchall())
    assert statuses == {501: "running,succeeded", 502: "running", 503: "running,succeeded", 504: "running",
                        505: "running"}
    # Civis run 505 resumed the scan Civis run 504 started and stopped (docs/PLAN.md 17.13).
    scans = dict(conn.execute("SELECT civis_run_id, run_id FROM scan_runs").fetchall())
    assert scans[505] == scans[504] and len(set(scans.values())) == 4
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM checklist_status GROUP BY status").fetchall())
    assert counts["fixed"] > 0 and counts["ignored"] > 0 and counts["open"] > 0
    assert conn.execute("SELECT COUNT(*) FROM objects WHERE name = ?", (HOSTILE_NAME,)).fetchone()[0] > 0


def test_each_preview_run_makes_the_scanners_resume_decision(tmp_path):
    """Each run logs one RESUME line from the scanner's own decision (docs/PLAN.md 17.13), with the earlier
    runs' states from the synthetic scripts.get_containers_runs."""
    jobs = build_preview(tmp_path, now=NOW)
    resume = {run: [e["message"] for e in entries if e["message"].startswith("RESUME")]
              for run, entries in jobs["logs"].items()}
    scan_504 = next(e["message"].split("run_id=")[1].split()[0] for e in jobs["logs"]["504"]
                    if e["message"].startswith("RUN run_id="))
    assert resume == {
        "501": ["RESUME decision=new reason=no_scan"],
        "502": ["RESUME decision=new reason=last_scan_finished"],
        "503": ["RESUME decision=new reason=too_old"],  # 502 stopped 7 days earlier.
        "504": ["RESUME decision=new reason=last_scan_finished"],
        "505": [f"RESUME decision=resume run_id={scan_504} resumes=1"],
    }
    progress = [e["message"] for e in jobs["logs"]["505"] if e["message"].startswith("PROGRESS phase=code")]
    assert progress[0].startswith("PROGRESS phase=code objects_done=20 ")  # 504's 20 objects are skipped.
    assert progress[-1].startswith("PROGRESS phase=code objects_done=40 ")


def test_fixed_code_removes_some_findings():
    before = preview_instance(False, NOW)
    after = preview_instance(True, NOW)
    sources = lambda data: [s.get("source") for s in data["python3"]]  # noqa: E731
    assert sources(before) != sources(after)
    assert len(before["python3"]) == len(after["python3"])


def test_preview_cli(tmp_path, capsys):
    assert main(["--dry-run", "--preview", "--out", str(tmp_path)]) == 0
    assert "python -m app.local --results" in capsys.readouterr().out


def test_preview_cli_explains_when_the_data_is_in_use(tmp_path, monkeypatch, capsys):
    def locked(out, now=None):
        raise PermissionError("in use")

    monkeypatch.setattr("scanner.synthetic.preview.build_preview", locked)
    assert main(["--dry-run", "--preview", "--out", str(tmp_path)]) == 1
    assert "Stop it with Ctrl+C" in capsys.readouterr().err
