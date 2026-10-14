"""Seed a local preview of the web app with made-up data.

``build_preview`` extends the synthetic instance with generated objects, then
runs the scanner several times so every app page has something to show:

1. Three weeks ago: a completed scan.
2. Two weeks ago: a scan that failed partway.
3. One week ago: a completed scan after some code was fixed, so the history
   shows "fixed since last run," plus some checklist changes by an admin.
4. Twenty minutes ago: a scan that is still running.

It writes ``results.sqlite`` (the five tables), one CSV report per completed
run, and ``local_jobs.json``, which stands in for the scanner job's Civis runs
and log lines. Everything is made up, and nothing calls the network.
"""

from __future__ import annotations

import copy
import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.synthetic.client import FIXTURES

LOCAL_JOBS_FILE = "local_jobs.json"
PREVIEW_JOB_ID = 9100
HOSTILE_NAME = "Example <script>alert('x')</script> & \"quoted\" <b>job</b>"

_TOPICS = [
    "member file load", "event sync", "volunteer export", "survey cleanup", "email list build",
    "donation rollup", "turnout model", "district match", "contact dedupe", "phone bank prep",
]
_SNIPPETS = {
    "list_me": ("import civis\nclient = civis.APIClient()\nme = client.users.list_me()\n",
                "import civis\nclient = civis.APIClient()\nme = client.users.get_me()\n"),
    "use_pandas": ("import civis\ndf = civis.io.read_civis_sql(QUERY, 'analytics', use_pandas=True)\n",
                   "import civis\ndf = civis.io.read_civis_sql(QUERY, 'analytics', return_as='pandas')\n"),
    "strptime": ("from datetime import datetime\nday = datetime.strptime(raw, '%m/%d')\n",
                 "from datetime import datetime\nday = datetime.strptime(raw + '/2026', '%m/%d/%Y')\n"),
    "feature_flags": ("import civis\nflags = civis.APIClient().feature_flags\n",
                      "import civis\nflags = civis.APIClient().users.get_me()['feature_flags']\n"),
    "container_future": ("from civis.futures import ContainerFuture\n", "from civis.futures import CivisFuture\n"),
    "sqlite": ("import sqlite3\nconn = sqlite3.connect('cache.db', 30)\n",
               "import sqlite3\nconn = sqlite3.connect('cache.db', timeout=30)\n"),
    "clean": ("print('done')\n", "print('done')\n"),
}
_COMMANDS = [
    ("pip install civis && python run.py", "pip install 'civis>=2.8,<3' && python run.py"),
    ("pip install pandas==1.5.3 && python run.py", "pip install pandas==1.5.3 && python run.py"),
    ("python run.py", "python run.py"),
]
_OWNERS = [
    (10, "Example Owner Avery", True), (11, "Example Owner Blake", True), (12, "Example Owner Casey", True),
    (13, "Example Owner Devon", True), (14, "Example Owner Emery", False), (15, "Example Robot", True),
]


def _person(owner_id: int, name: str) -> dict:
    return {"id": owner_id, "name": name, "username": name.lower().replace(" ", "_")}


def preview_instance(fixed: bool, now: datetime) -> dict:
    """The synthetic instance plus generated objects. ``fixed`` applies some code fixes."""
    data = json.loads((FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    rng = random.Random(20261014)
    data["users"] += [{"id": i, "user": n.lower().replace(" ", "_"), "name": n, "active": a} for i, n, a in _OWNERS]
    logs = data.setdefault("run_logs", {})

    for index in range(40):
        object_id = 1001 + index
        owner = _OWNERS[index % len(_OWNERS)]
        keys = rng.sample(sorted(_SNIPPETS), k=rng.choice([1, 1, 2]))
        is_fixed = fixed and index % 3 == 0
        source = "".join(_SNIPPETS[k][1 if is_fixed else 0] for k in keys)
        days_ago = rng.choice([1, 3, 6, 12, 30, 90, 150, 260])
        last_run = None
        if index % 9 != 8:
            run_id = 50000 + index
            last_run = {"id": run_id, "state": rng.choice(["succeeded"] * 5 + ["failed"]),
                        "finished_at": (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")}
            if "list_me" in keys and not is_fixed:
                logs[f"{object_id}/{run_id}"] = [
                    {"id": 1, "message": "Job started"},
                    {"id": 2, "message": "/app/job.py:3: FutureWarning: The method name ``<client>.users.list_me`` is "
                                         "deprecated and will be removed at civis-python v3.0.0 (scheduled for February 2027)."},
                ]
        name = f"Example {_TOPICS[index % len(_TOPICS)]} {index + 1:02d}"
        if index == 39:
            name = HOSTILE_NAME
        data["python3"].append({
            "id": object_id, "name": name, "type": "Python", "archived": False, "hidden": False,
            "author": _person(owner[0], owner[1]),
            "schedule": {"scheduled": index % 2 == 0, "scheduled_days": [1, 3, 5], "scheduled_hours": [6]},
            "last_run": last_run,
            "docker_image_tag": rng.choice(["", "", "latest", "9.1.1", "8.4.0"]),
            "source": source,
        })

    for index in range(12):
        object_id = 2101 + index
        owner = _OWNERS[(index + 2) % len(_OWNERS)]
        command = _COMMANDS[index % len(_COMMANDS)][1 if fixed and index % 2 == 0 else 0]
        image, tag = rng.choice([
            ("civisanalytics/datascience-python", ""), ("civisanalytics/datascience-python", "latest"),
            ("civisanalytics/datascience-python", "8.2.0"), ("example-org/custom-etl", "2.3"),
        ])
        data["containers"].append({
            "id": object_id, "name": f"Example container {_TOPICS[(index + 3) % len(_TOPICS)]} {index + 1:02d}",
            "type": "Container", "archived": False, "hidden": False, "author": _person(owner[0], owner[1]),
            "docker_image_name": image, "docker_image_tag": tag, "docker_command": command,
            "last_run": {"id": 60000 + index, "state": "succeeded",
                         "finished_at": (now - timedelta(days=2 + index)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        })

    for index in range(6):
        object_id = 3101 + index
        data["custom"].append({
            "id": object_id, "name": f"Example template run {index + 1:02d}", "type": "Custom",
            "backing_script_type": "python3" if index % 2 else "container", "archived": False, "hidden": False,
            "from_template_id": 33 if index % 2 else 31, "author": _person(*_OWNERS[index % 4][:2]),
            "code_preview": "print('preview')",
        })

    for index in range(6):
        object_id = 4101 + index
        url = f"https://example-notebooks.s3.amazonaws.com/notebook-{object_id}.ipynb?X-Amz-Signature=synthetic"
        keys = [sorted(_SNIPPETS)[index % len(_SNIPPETS)]]
        is_fixed = fixed and index % 2 == 0
        cell = "".join(_SNIPPETS[k][1 if is_fixed else 0] for k in keys)
        data["notebooks"].append({
            "id": object_id, "name": f"Example analysis notebook {index + 1:02d}", "language": "python3",
            "archived": False, "hidden": False, "user": _person(*_OWNERS[(index + 1) % len(_OWNERS)][:2]),
            "docker_image_name": "civisanalytics/civis-jupyter-python3",
            "docker_image_tag": "" if index % 3 else "5.1.1", "notebook_url": url,
        })
        data["notebook_files"][url] = {
            "nbformat": 4, "nbformat_minor": 5, "metadata": {},
            "cells": [{"cell_type": "code", "metadata": {}, "source": cell.splitlines(keepends=True), "outputs": []}],
        }

    data["workflows"].append({
        "id": 6101, "name": "Example nightly pipeline", "archived": False, "hidden": False, "valid": True,
        "user": _person(15, "Example Robot"),
        "schedule": {"scheduled": True, "scheduled_hours": [2]},
        "definition": "version: '2.0'\nnightly:\n  tasks:\n"
                      + "".join(f"    step_{i}:\n      action: civis.run_job\n      input:\n        job_id: {1001 + i}\n" for i in range(5)),
    })
    return data


class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _ticking(start: datetime, step_seconds: float):
    state = {"now": start}

    def now() -> datetime:
        state["now"] += timedelta(seconds=step_seconds)
        return state["now"]

    return now


def _log_entries(lines: list[str], start: datetime, span_seconds: float) -> list[dict]:
    step = span_seconds / max(len(lines), 1)
    return [
        {"id": i + 1, "created_at": (start + timedelta(seconds=step * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "message": line}
        for i, line in enumerate(lines)
    ]


def build_preview(out: Path, now: datetime | None = None) -> dict:
    """Write results.sqlite, CSV reports, and local_jobs.json into ``out``."""
    from scanner import scan_log
    from scanner.__main__ import DRY_RUN_SCHEMA, SimulatedStop, dry_run

    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("results.sqlite", LOCAL_JOBS_FILE):
        (out / name).unlink(missing_ok=True)
    for old in out.glob("civis-python-version-search-*.csv"):
        old.unlink()

    logger = scan_log.configure()
    plan = [
        # (Civis run ID, days ago, fixed code, stop after N objects, Civis state, minutes taken)
        (501, 21, False, None, "succeeded", 38),
        (502, 14, False, 20, "failed", 9),
        (503, 7, True, None, "succeeded", 41),
        (504, 0, True, 40, "running", None),
    ]
    runs, logs, outputs = [], {}, {}
    for civis_run_id, days_ago, fixed, stop_after, state, minutes in plan:
        start = now - timedelta(days=days_ago, minutes=20 if state == "running" else 0)
        capture = _LogCapture()
        logger.addHandler(capture)
        try:
            result = dry_run(
                out, data=preview_instance(fixed, start), civis_job_id=PREVIEW_JOB_ID, civis_run_id=civis_run_id,
                clock=_ticking(start, 2.0), stop_after=stop_after, checkpoint_objects=10,
            )
            outputs[str(civis_run_id)] = f"civis-python-version-search-{result.run_id}.csv"
        except SimulatedStop:
            pass
        finally:
            logger.removeHandler(capture)
        finished = None if minutes is None else start + timedelta(minutes=minutes)
        span = (minutes or 20) * 60
        logs[str(civis_run_id)] = _log_entries(capture.lines, start, span)
        runs.append({
            "id": civis_run_id, "state": state,
            "created_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "started_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%SZ") if finished else None,
        })
        if civis_run_id == 503:
            _admin_checklist_changes(out / "results.sqlite", DRY_RUN_SCHEMA, start + timedelta(days=1))

    jobs = {"job_id": PREVIEW_JOB_ID, "runs": sorted(runs, key=lambda r: r["id"], reverse=True), "logs": logs, "outputs": outputs}
    (out / LOCAL_JOBS_FILE).write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    return jobs


def _admin_checklist_changes(path: Path, schema: str, when: datetime) -> None:
    """A made-up admin marks a few findings fixed or ignored."""
    conn = sqlite3.connect(path)
    try:
        pairs = conn.execute("SELECT DISTINCT object_id, rule_id FROM checklist_status ORDER BY object_id, rule_id").fetchall()
        run_id = conn.execute("SELECT run_id FROM scan_runs ORDER BY recorded_at DESC LIMIT 1").fetchone()[0]
        rows = []
        for index, (object_id, rule_id) in enumerate(pairs):
            if index % 5 == 0:
                rows.append((run_id, object_id, rule_id, "fixed", "example_admin", when.strftime("%Y-%m-%d %H:%M:%S")))
            elif index % 11 == 0:
                rows.append((run_id, object_id, rule_id, "ignored", "example_admin", when.strftime("%Y-%m-%d %H:%M:%S")))
        conn.executemany(
            "INSERT INTO checklist_status (run_id, object_id, rule_id, status, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
