"""Run labels that stay the same after retention (docs/PLAN.md 17.9, Part C).

A scan is labeled "Civis run <ID>" by the Civis run of the scanner run that started it, or "Scan <run ID>" when
no row has a Civis run ID. In the live test, retention removed Runs 1 to 5 and the Overview then called scan C
"Run 1", because runs were numbered by their position among the runs still stored. Made-up data only.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The scanner image has no Flask, and importing the app package needs it. These tests run in the Service image.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app.views import ScanRun, summarize_scan_runs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SECRET = "run-labels-test-secret-key-that-is-long-enough-01"  # pragma: allowlist secret  gitleaks:allow


def row(run_id: str, status: str, recorded: str, civis_run_id: int | None, started: str | None = None) -> dict:
    return {"run_id": run_id, "status": status, "started_at": started or recorded, "finished_at": None,
            "recorded_at": recorded, "civis_run_id": civis_run_id, "scanning_user_name": None, "parameters": "{}"}


SCANS = [
    row("run-20261101T120000Z-aaaaaa", "running", "2026-11-01 12:00:00", 101),
    row("run-20261101T120000Z-aaaaaa", "succeeded", "2026-11-01 12:30:00", 101),
    row("run-20261105T120000Z-bbbbbb", "running", "2026-11-05 12:00:00", 105),
    row("run-20261105T120000Z-bbbbbb", "failed", "2026-11-05 12:10:00", 105),
    row("run-20261112T120000Z-cccccc", "running", "2026-11-12 12:00:00", 112),
    row("run-20261112T120000Z-cccccc", "succeeded", "2026-11-12 12:22:00", 112),
]


def labels(rows: list[dict]) -> dict[str, str]:
    return {scan.run_id: scan.label for scan in summarize_scan_runs(rows)}


def test_labels_are_the_civis_run_that_started_each_scan():
    assert labels(SCANS) == {
        "run-20261101T120000Z-aaaaaa": "Civis run 101",
        "run-20261105T120000Z-bbbbbb": "Civis run 105",
        "run-20261112T120000Z-cccccc": "Civis run 112",
    }
    assert [s.run_id for s in summarize_scan_runs(SCANS)][0] == "run-20261112T120000Z-cccccc"  # Newest first.


def test_labels_do_not_change_when_retention_removes_the_oldest_runs():
    before = labels(SCANS)
    kept = [r for r in SCANS if r["run_id"] == "run-20261112T120000Z-cccccc"]
    assert labels(kept) == {"run-20261112T120000Z-cccccc": before["run-20261112T120000Z-cccccc"]}
    assert labels(kept)["run-20261112T120000Z-cccccc"] == "Civis run 112"


def test_a_resumed_scan_keeps_the_civis_run_that_started_it():
    """The first scanner run stopped partway; a second run (RUN_ID) resumed it and finished."""
    rows = [
        row("run-20261110T120000Z-dddddd", "running", "2026-11-10 12:00:00", 201),
        row("run-20261110T120000Z-dddddd", "running", "2026-11-10 13:00:00", 202, started="2026-11-10 12:00:00"),
        row("run-20261110T120000Z-dddddd", "succeeded", "2026-11-10 13:20:00", 202, started="2026-11-10 12:00:00"),
    ]
    (scan,) = summarize_scan_runs(rows)
    assert scan.label == "Civis run 201" and scan.first_civis_run_id == 201
    assert scan.civis_run_id == 202  # The newest row's, which Download CSV uses, is unchanged.
    assert summarize_scan_runs(list(reversed(rows)))[0].label == "Civis run 201"  # Row order does not matter.


def test_the_earliest_row_with_a_civis_run_id_names_the_scan():
    rows = [
        row("run-20261110T120000Z-eeeeee", "running", "2026-11-10 12:00:00", None),
        row("run-20261110T120000Z-eeeeee", "succeeded", "2026-11-10 12:20:00", 301),
    ]
    assert summarize_scan_runs(rows)[0].label == "Civis run 301"


def test_a_scan_with_no_civis_run_id_is_labeled_by_its_run_id():
    rows = [row("run-20261013T120000Z-a1b2c3", "running", "2026-10-13 12:00:00", None),
            row("run-20261013T120000Z-a1b2c3", "succeeded", "2026-10-13 12:20:00", None)]
    assert summarize_scan_runs(rows)[0].label == "Scan run-20261013T120000Z-a1b2c3"
    plain = ScanRun(run_id="run-20261013T120000Z-a1b2c3", status="succeeded", started_at=None, finished_at=None,
                    civis_run_id=None, scanning_user=None, parameters={})
    assert plain.label == "Scan run-20261013T120000Z-a1b2c3"


def test_scan_run_has_no_ordinal_any_more():
    assert "ordinal" not in ScanRun.__dataclass_fields__
    for folder in ("app", "common", "scanner"):
        for path in (ROOT / folder).rglob("*.py"):
            assert "ordinal" not in path.read_text(encoding="utf-8"), path


def test_no_numbered_run_label_remains_in_templates_pages_or_tests():
    numbered = re.compile(r"\bRun (\d+|\{)")  # "Run 1", "Run 3.", or f"Run {...}".
    files = [*(ROOT / "app" / "templates").glob("*.html"), *(ROOT / "app").glob("*.py"), *(ROOT / "tests").glob("*.py")]
    for path in files:
        if path.name == Path(__file__).name:
            continue
        assert not numbered.search(path.read_text(encoding="utf-8")), path


# --- Through the app, with the local preview's four scans ----------------------------------------------------


@pytest.fixture(scope="module")
def preview(tmp_path_factory) -> Path:
    from scanner.synthetic.preview import build_preview

    folder = tmp_path_factory.mktemp("preview")
    build_preview(folder, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    return folder


def web(folder: Path):
    from app import create_app
    from app.services import local_services

    return create_app({"SECRET_KEY": SECRET, "SERVICES": local_services(folder, "normal"), "TESTING": True}).test_client()


def text(response) -> str:
    body = response.get_data(as_text=True)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def test_the_preview_scans_are_labeled_by_their_civis_runs(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    client = web(preview)
    history = text(client.get("/scan"))
    for civis_run in (501, 502, 503, 504):
        assert f"Civis run {civis_run}" in history
    assert "Civis run 503." in text(client.get("/"))  # The current scan (the newest completed one).
    assert "Civis run 503." in text(client.get("/inventory"))
    summary = client.get("/setup/summary").get_data(as_text=True)
    options = re.findall(r"<option value=\"(run-[^\"]+)\"[^>]*>([^<]+)</option>", summary)
    assert sorted(label.split(" (")[0] for _, label in options) == [f"Civis run {n}" for n in (501, 502, 503, 504)]
    assert all(label.endswith(f"({run_id})") for run_id, label in options)


def test_labels_stay_the_same_after_the_oldest_runs_are_removed(preview, tmp_path, monkeypatch):
    """Remove the two oldest scans the way retention does (every table), then read the pages again."""
    import shutil

    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    folder = tmp_path / "after-retention"
    shutil.copytree(preview, folder)
    conn = sqlite3.connect(folder / "results.sqlite")
    try:
        oldest = [r[0] for r in conn.execute("SELECT run_id FROM scan_runs GROUP BY run_id ORDER BY MIN(started_at) LIMIT 2")]
        assert len(oldest) == 2
        for table in ("findings", "coverage", "objects", "checklist_status", "scan_runs"):
            conn.executemany(f"DELETE FROM {table} WHERE run_id = ?", [(r,) for r in oldest])
        conn.commit()
    finally:
        conn.close()
    client = web(folder)
    history = text(client.get("/scan"))
    assert "Civis run 503" in history and "Civis run 504" in history
    assert "Civis run 503." in text(client.get("/"))
    assert not re.search(r"\bRun 1\b", history)
