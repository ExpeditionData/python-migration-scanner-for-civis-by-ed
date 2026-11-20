"""Objects of different types with the same ID, in the web app (docs/PLAN.md 17.19). Made-up data only.

Civis numbers jobs apart from notebooks, Services, and workflows, so a Python script and a notebook can have
the same ID. Each is its own object on every page. The checklist stays keyed by object ID and rule ID
(checklist_status has no object_type column), so the two share one checklist status, and the Findings page says
so on the second row.
"""

from __future__ import annotations

import html
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import create_app, pages, views  # noqa: E402
from app.security import generate_csrf_token  # noqa: E402
from app.services import local_services  # noqa: E402
from app.summary import scan_counts  # noqa: E402
from common.rule_loader import load_rules  # noqa: E402
from scanner.synthetic.preview import build_preview  # noqa: E402
from tests.test_history_counts import _reference_counts, _sql_counts  # noqa: E402

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
SECRET = "shared-id-test-secret-key-long-enough-01"  # pragma: allowlist secret  gitleaks:allow
RULES = load_rules()
SHARED_ID = 7
RULE = "CV3-ML"
NOTE = "This object has the same ID as another object of a different type, so they share one checklist status."


def _objects() -> list[dict]:
    """A Python script and a notebook with the same ID, the notebook listed last, and a custom script whose
    backing script has that ID."""
    return [
        {"object_id": SHARED_ID, "object_type": "python_script", "name": "Example shared script",
         "url": "https://civis.test/scripts/7", "owner_name": "Example Script Owner", "scan_status": "scanned",
         "uses_default_image": True},
        {"object_id": 30, "object_type": "custom_script", "name": "Example custom", "template_id": 3,
         "backing_script_id": SHARED_ID, "url": "https://civis.test/custom/30", "owner_name": "Example Custom Owner",
         "scan_status": "scanned"},
        {"object_id": SHARED_ID, "object_type": "notebook", "name": "Example shared notebook",
         "url": "https://civis.test/notebooks/7", "owner_name": "Example Notebook Owner", "scan_status": "scanned",
         "uses_default_image": False},
    ]


def _finding(object_type: str, rule_id: str, impact: str, line: int = 1) -> dict:
    return {"run_id": "run-a", "object_id": SHARED_ID, "object_type": object_type, "rule_id": rule_id,
            "file": "inline source", "line_number": line, "impact": impact, "source": "code",
            "confidence": "high", "fix_guidance": "Made-up fix."}


def _findings() -> list[dict]:
    """The script is affected (two rules), the notebook advisory (one rule, the same as the script's first)."""
    return [_finding("python_script", RULE, "affected"), _finding("python_script", "CV3-IO", "affected", 2),
            _finding("notebook", RULE, "advisory")]


# --- The views, on made-up rows ---------------------------------------------------------------


def test_the_overview_counts_two_objects():
    view = views.overview(_objects(), _findings(), [], [], RULES)
    assert (view["affected"], view["advisory"]) == (1, 1)
    by_type = {t["key"]: (t["affected"], t["advisory"]) for t in view["types"]}
    assert by_type == {"python_script": (1, 0), "custom_script": (0, 0), "notebook": (0, 1)}
    assert {i["rule_id"]: i["objects"] for i in view["issues"]}[RULE] == 2


def test_the_overview_checklist_counts_the_findings_rows_with_their_shared_status():
    checklist = [{"object_id": SHARED_ID, "rule_id": RULE, "status": "fixed", "changed_at": "2026-10-14 15:00:00"}]
    progress = views.overview(_objects(), _findings(), [], checklist, RULES)["checklist"]
    rows = views.findings_rows(_objects(), _findings(), checklist, RULES, {"status": "all"})
    assert progress["total"] == len(rows) == 3
    assert progress["objects"] == 2
    assert (progress["fixed"], progress["open"]) == (2, 1)  # Both RULE rows share the fixed status.


def test_scan_history_counts_two_objects():
    findings = _findings() + [_finding("python_script", RULE, "affected") | {"run_id": "run-b"}]
    pairs = [("run-a", "run-b")]
    expected = _reference_counts(findings, ["run-a", "run-b"], pairs)
    assert _sql_counts(findings, ["run-a", "run-b"], pairs) == expected
    assert expected["run-a"] == {"affected": 1, "advisory": 1}
    assert expected["run-b"] == {"affected": 1, "advisory": 0, "fixed": 2}  # The script's CV3-IO, the notebook's RULE.


def test_the_support_summary_counts_two_objects():
    counts = scan_counts(_objects(), _findings())
    assert (counts["affected"], counts["advisory"], counts["findings"]) == (1, 1, 3)


def test_inventory_rows_show_each_objects_own_findings_count():
    rows = views.inventory(_objects(), _findings(), {})
    counts = {(r["type"], r["object_id"]): r["findings"] for r in rows}
    assert counts == {("Python script", SHARED_ID): 2, ("Notebook", SHARED_ID): 1, ("Custom script", 30): 0}


def test_template_info_links_the_job_and_never_the_notebook():
    objects = _objects()
    custom = objects[1]
    assert views.template_info(custom, views.backing_scripts(objects))["backing_url"] == "https://civis.test/scripts/7"
    row = next(r for r in views.inventory(objects, _findings(), {}) if r["custom"])
    assert row["template"]["backing_url"] == "https://civis.test/scripts/7"
    without_job = [o for o in objects if o["object_type"] != "python_script"]
    assert views.template_info(custom, views.backing_scripts(without_job))["backing_url"] is None


def test_template_findings_text_counts_custom_script_findings_only():
    objects = _objects() + [{"object_id": 30, "object_type": "notebook", "name": "Example notebook 30"}]
    notebook_finding = {**_finding("notebook", RULE, "advisory"), "object_id": 30}
    assert views.template_findings_text(objects, [notebook_finding]) == (
        "0 templates, used by 0 custom scripts, have findings.")
    custom_finding = {**_finding("custom_script", RULE, "affected"), "object_id": 30}
    assert views.template_findings_text(objects, [custom_finding]) == (
        "1 template, used by 1 custom script, has findings.")


def test_findings_has_one_row_per_object_and_one_status_control():
    rows = views.findings_rows(_objects(), _findings(), [], RULES, {"status": "all"})
    shared = [r for r in rows if r["rule_id"] == RULE]
    assert [(r["object_type"], r["shared_status"]) for r in shared] == [("notebook", False), ("python_script", True)]
    assert [r["object_name"] for r in shared] == ["Example shared notebook", "Example shared script"]
    assert {r["key"] for r in shared} == {f"{SHARED_ID}:{RULE}"}
    assert not [r for r in rows if r["rule_id"] != RULE and r["shared_status"]]


def test_owners_lists_both_owners():
    assert views.owners(_objects(), _findings()) == ["Example Notebook Owner", "Example Script Owner"]
    script_only = [f for f in _findings() if f["object_type"] == "python_script"]
    assert views.owners(_objects(), script_only) == ["Example Script Owner"]  # The notebook has no findings.


# --- The Findings page and a save, on the preview with an added notebook ----------------------


@pytest.fixture
def shared_preview(tmp_path) -> tuple[Path, int, str]:
    """The preview, plus a made-up notebook in the current scan with the ID of a Python script there, and a
    finding with one of that script's rules. Returns the folder, the ID, and the rule."""
    build_preview(tmp_path, now=NOW)
    app = _app(tmp_path)
    with app.test_request_context("/findings"):
        run_id = pages.load_situation().current.run_id
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        script = conn.execute(
            "SELECT * FROM objects WHERE run_id = ? AND object_type = 'python_script' AND object_id IN "
            "(SELECT object_id FROM findings WHERE run_id = ? AND object_type = 'python_script') "
            "ORDER BY object_id LIMIT 1", (run_id, run_id)).fetchone()
        finding = conn.execute(
            "SELECT * FROM findings WHERE run_id = ? AND object_type = 'python_script' AND object_id = ? "
            "ORDER BY rule_id LIMIT 1", (run_id, script["object_id"])).fetchone()
        assert not conn.execute("SELECT 1 FROM objects WHERE object_type = 'notebook' AND object_id = ?",
                                (script["object_id"],)).fetchall()
        notebook = dict(script) | {"object_type": "notebook", "name": script["name"],
                                   "url": f"https://civis.test/notebooks/{script['object_id']}",
                                   "owner_name": "Example Notebook Owner"}
        conn.execute(f"INSERT INTO objects ({', '.join(notebook)}) VALUES ({', '.join('?' * len(notebook))})",
                     list(notebook.values()))
        added = dict(finding) | {"object_type": "notebook", "impact": "advisory"}
        conn.execute(f"INSERT INTO findings ({', '.join(added)}) VALUES ({', '.join('?' * len(added))})",
                     list(added.values()))
        conn.commit()
        return tmp_path, int(script["object_id"]), str(finding["rule_id"])
    finally:
        conn.close()


def _app(folder: Path):
    return create_app({"SECRET_KEY": SECRET, "SERVICES": local_services(folder, "normal"), "TESTING": True})


def _pages(client, query: str = "status=all") -> list[str]:
    bodies, number = [], 1
    while True:
        body = client.get(f"/findings?{query}&page={number}").get_data(as_text=True)
        bodies.append(body)
        if f"page={number + 1}" not in body:
            return bodies
        number += 1


def _form_names(body: str) -> list[str]:
    form = body[body.index('id="checklist-form"'): body.index("</form>", body.index('id="checklist-form"'))]
    return [html.unescape(n) for n in re.findall(r'\bname="([^"]+)"', form)]


def test_the_findings_page_shows_two_rows_one_control_and_the_note(shared_preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    folder, object_id, rule_id = shared_preview
    client = _app(folder).test_client()
    bodies = _pages(client)
    field = f"s:{object_id}:{rule_id}"
    assert sum(_form_names(b).count(field) for b in bodies) == 1
    assert sum(b.count(NOTE) for b in bodies) == 1
    for body in bodies:
        names = _form_names(body)
        assert len(names) == len(set(names)), "a field name is sent twice"
    # Both rows are on the page: the same name, rule, and ID, sorted next to each other.
    rows = re.findall(r"<tr>(.*?)</tr>", "".join(bodies), flags=re.S)
    shared = [r for r in rows if f">{rule_id}<" in r and (f'name="{field}"' in r or NOTE in r)]
    assert len(shared) == 2
    assert "Notebook, owner Example Notebook Owner" in shared[0] and f'name="{field}"' in shared[0]
    assert "Python script, owner" in shared[1] and NOTE in shared[1] and "<select" not in shared[1]


def test_a_save_writes_one_checklist_row(shared_preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    folder, object_id, rule_id = shared_preview
    app = _app(folder)

    def rows() -> list:
        conn = sqlite3.connect(folder / "results.sqlite")
        try:
            return conn.execute("SELECT status FROM checklist_status WHERE object_id = ? AND rule_id = ?",
                                (object_id, rule_id)).fetchall()
        finally:
            conn.close()

    before = rows()
    with app.test_request_context():
        token = generate_csrf_token(app)
    data = {"csrf_token": token, "f_status": "all", f"o:{object_id}:{rule_id}": "open",
            f"s:{object_id}:{rule_id}": "fixed"}
    response = app.test_client().post("/findings/save", data=data)
    assert response.status_code == 302 and "saved=1" in response.headers["Location"]
    assert rows() == before + [("fixed",)]
    # Both rows show the saved status: one in its control, the other as text.
    body = "".join(_pages(app.test_client()))
    shared = [r for r in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S) if f">{rule_id}<" in r
              and (f'name="s:{object_id}:{rule_id}"' in r or NOTE in r)]
    assert len(shared) == 2
    assert re.search(r'<option value="fixed" selected>', shared[0])
    assert re.search(r"<div>Fixed</div>", shared[1])
