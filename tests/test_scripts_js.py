"""The "Working" message (app/static/js/busy.js) and the Overview's Run scan button (app/static/js/run-scan.js).

docs/PLAN.md 17.6, Part E. The scripts run under Node with a small stand-in page (skipped if Node is not
installed), and the pages are checked for the markup the scripts use and for working without JavaScript.
Made-up data only. No Civis calls.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

# The scanner image has no Flask. These tests run in the Service image instead.
pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")

from app import create_app
from app.security import generate_csrf_token
from app.services import local_services
from scanner.synthetic.preview import build_preview

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "static" / "js"
SECRET = "scripts-test-secret-key-that-is-long-enough-01"  # pragma: allowlist secret  gitleaks:allow
WORKING = "Working. This can take up to a minute."

# A stand-in page. The scenario (JSON) lists the page's elements and the steps to run; the output lists what the
# page showed after each step (for a submit or click, right after the event and again once its fetches were
# answered), every fetch the script sent, and how many timers were waiting.
HARNESS = r"""
const fs = require("fs");
const scenario = JSON.parse(process.argv[1]);
function element(spec) {
  const el = {
    attrs: Object.assign({}, spec.attrs || {}), hidden: !!spec.hidden, disabled: !!spec.disabled,
    textContent: spec.text || "", className: spec.className || "", listeners: {}, tag: spec.tag, children: [],
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    querySelector(selector) { return this.children.find((c) => c.matches(selector)) || null; },
    closest(selector) { return this.matches(selector) ? this : null; },
    matches(selector) {
      if (selector === "form") return this.tag === "form";
      if (selector === "button") return this.tag === "button";
      if (selector === "a[data-busy]") return this.tag === "a" && this.hasAttribute("data-busy");
      const m = selector.match(/^\[([\w-]+)\]$/);
      return m ? this.hasAttribute(m[1]) : false;
    },
  };
  return el;
}
const els = {};
for (const [name, spec] of Object.entries(scenario.elements)) els[name] = element(spec);
for (const [name, spec] of Object.entries(scenario.elements)) {
  for (const child of spec.children || []) els[name].children.push(els[child]);
}
const docListeners = {};
const winListeners = {};
const timers = [];
const fetches = [];
const responses = scenario.responses.slice();
global.window = {
  setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
  fetch(url, options) {
    options = options || {};
    fetches.push({ url, method: options.method || "GET", accept: (options.headers || {}).Accept || null,
                   body: options.body ? options.body.from : null });
    const next = responses.shift();
    if (next === undefined || next === "network-error") return Promise.reject(new Error("offline"));
    return Promise.resolve({ ok: next.ok !== false, json: () => Promise.resolve(next.body) });
  },
  FormData: function (form) { this.from = form.getAttribute("action"); },
};
global.document = {
  querySelector(selector) { return Object.values(els).find((e) => e.matches(selector)) || null; },
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
function snapshot() {
  const out = {};
  for (const [name, el] of Object.entries(els)) {
    out[name] = { text: el.textContent, hidden: el.hidden, disabled: el.disabled, className: el.className,
                  busy: el.getAttribute("aria-busy") };
  }
  return { els: out, timers: timers.length, fetches: fetches.length };
}
(async () => {
  eval(fs.readFileSync(scenario.script, "utf8"));
  const shots = [snapshot()];
  for (const step of scenario.steps) {
    if (step.do === "submit" || step.do === "click") {
      const event = Object.assign({ target: els[step.target], defaultPrevented: false, button: 0,
                                    preventDefault() { this.defaultPrevented = true; } }, step.event || {});
      for (const fn of els[step.target].listeners[step.do] || []) fn(event);
      for (const fn of docListeners[step.do] || []) fn(event);
      shots.push(Object.assign(snapshot(), { prevented: event.defaultPrevented }));
    } else if (step.do === "pageshow") {
      for (const fn of winListeners.pageshow || []) fn({ persisted: true });
    } else if (step.do === "tick") {
      const timer = timers.shift();
      if (timer) timer.fn();
    }
    for (let i = 0; i < 5; i++) await flush();
    shots.push(snapshot());  // After any answers the step's fetches got.
  }
  console.log(JSON.stringify({ shots, fetches }));
})();
"""


def run(script: str, elements: dict, steps: list, responses: list | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    scenario = {"script": str(JS / script), "elements": elements, "steps": steps, "responses": responses or []}
    result = subprocess.run([node, "-e", HARNESS, json.dumps(scenario)], capture_output=True, text=True, check=True,
                            timeout=30)
    return json.loads(result.stdout)


# --- busy.js -------------------------------------------------------------------------------------------


BUSY_PAGE = {
    "region": {"tag": "div", "attrs": {"data-busy-region": ""}, "hidden": True},
    "storage": {"tag": "form", "attrs": {"data-busy": "", "action": "/setup/storage"}},
    "setup": {"tag": "a", "attrs": {"data-busy": "", "href": "/setup"}},
    "plain": {"tag": "a", "attrs": {"href": "/inventory"}},
}


def test_the_working_message_shows_when_storage_setup_is_sent():
    out = run("busy.js", BUSY_PAGE, [{"do": "submit", "target": "storage"}])
    region = out["shots"][1]["els"]["region"]
    assert (region["text"], region["hidden"]) == (WORKING, False)
    assert out["shots"][1]["els"]["storage"]["busy"] == "true" and out["shots"][1]["prevented"] is False


def test_the_working_message_shows_for_a_plain_click_on_setup_only():
    out = run("busy.js", BUSY_PAGE, [
        {"do": "click", "target": "plain"},
        {"do": "click", "target": "setup", "event": {"ctrlKey": True}},  # Opens a new tab: nothing to wait for.
        {"do": "click", "target": "setup", "event": {"button": 1}},
        {"do": "click", "target": "setup"},
        {"do": "pageshow"},  # Back from the browser's history.
    ])
    regions = [shot["els"]["region"] for shot in out["shots"]]  # Two shots per click, one for the pageshow.
    assert [r["hidden"] for r in regions] == [True, True, True, True, True, True, True, False, False, True]
    assert regions[7]["text"] == WORKING and regions[9]["text"] == ""
    assert out["fetches"] == []  # It only shows text; it sends nothing.


def test_busy_does_nothing_without_its_region():
    out = run("busy.js", {"storage": BUSY_PAGE["storage"]}, [{"do": "submit", "target": "storage"}])
    assert out["shots"][1]["els"]["storage"]["busy"] is None


# --- run-scan.js ---------------------------------------------------------------------------------------


def overview(state: str = "idle", run: str | None = None, running: bool = False) -> dict:
    attrs = {"data-run-scan": "", "data-status": "/api/scan-status", "data-state": state}
    if run:
        attrs["data-run"] = run
    return {
        "box": {"tag": "div", "attrs": attrs, "children": ["form", "button", "note"]},
        "form": {"tag": "form", "attrs": {"action": "/scan/start", "method": "post"}},
        "button": {"tag": "button", "text": "Scan running" if running else "Run scan", "disabled": running,
                   "className": "button" if running else "button button-primary"},
        "note": {"tag": "p", "attrs": {"data-run-scan-note": ""}, "hidden": True},
    }


def status(state: str, run_id: int, can_start: bool = False) -> dict:
    return {"body": {"state": state, "civis_run_id": run_id, "can_start": can_start}}


def test_an_idle_overview_asks_for_nothing():
    out = run("run-scan.js", overview(), [])
    assert out["fetches"] == [] and out["shots"][0]["timers"] == 0


def test_run_scan_starts_without_a_reload_and_comes_back_when_the_run_ends():
    started = {"body": {"notice": "started", "civis_run_id": 9, "message": "The scan has started."}}
    out = run("run-scan.js", overview(), [
        {"do": "submit", "target": "form"},
        {"do": "tick"},  # Civis does not list the new run yet: the newest run is the last one, finished.
        {"do": "tick"},  # Now it is running.
        {"do": "tick"},  # It ended.
        {"do": "tick"},  # Nothing is waiting: no more polls.
    ], [started, status("succeeded", 8, True), status("running", 9), status("succeeded", 9, True)])
    shots = out["shots"]
    assert shots[1]["prevented"] is True  # The page does not reload.
    assert [(f["url"], f["method"], f["accept"]) for f in out["fetches"]] == [
        ("/scan/start", "POST", "application/json"), ("/api/scan-status", "GET", "application/json"),
        ("/api/scan-status", "GET", "application/json"), ("/api/scan-status", "GET", "application/json")]
    assert out["fetches"][0]["body"] == "/scan/start"  # The form's fields, CSRF token included.
    after_start = shots[2]["els"]  # After the start's answer.
    assert (after_start["button"]["text"], after_start["button"]["disabled"]) == ("Scan running", True)
    assert after_start["note"] == {**after_start["note"], "text": "The scan has started.", "hidden": False}
    assert shots[3]["els"]["button"]["text"] == "Scan running"  # Still starting.
    assert shots[4]["els"]["button"]["text"] == "Scan running"
    ended = shots[5]["els"]
    assert (ended["button"]["text"], ended["button"]["disabled"], ended["button"]["className"]) == (
        "Run scan", False, "button button-primary")
    assert ended["note"]["text"] == "The scan has ended. Reload this page to see its results."
    assert shots[5]["timers"] == 0 and len(out["fetches"]) == 4  # Polling stopped when the run ended.


def test_an_overview_opened_during_a_run_polls_until_it_ends():
    out = run("run-scan.js", overview("active", "7", running=True), [{"do": "tick"}, {"do": "tick"}],
              [status("running", 7), status("failed", 7, True)])
    assert out["shots"][0]["timers"] == 1
    assert out["shots"][1]["els"]["button"]["text"] == "Scan running"
    last = out["shots"][2]
    assert (last["els"]["button"]["text"], last["els"]["button"]["disabled"], last["timers"]) == ("Run scan", False, 0)


def test_run_scan_stays_off_after_the_run_when_the_app_says_so():
    out = run("run-scan.js", overview("active", "7", running=True), [{"do": "tick"}], [status("succeeded", 7, False)])
    button = out["shots"][1]["els"]["button"]
    assert (button["text"], button["disabled"], button["className"]) == ("Run scan", True, "button")


def test_a_busy_or_failed_start_says_why():
    busy = {"body": {"notice": "busy", "civis_run_id": 5, "message": "A scan is already queued or running."}}
    out = run("run-scan.js", overview(), [{"do": "submit", "target": "form"}], [busy])
    els = out["shots"][2]["els"]
    assert els["button"]["text"] == "Scan running" and els["note"]["text"] == "A scan is already queued or running."
    assert out["shots"][2]["timers"] == 1

    refused = {"body": {"notice": "not-scanner", "civis_run_id": None, "message": "The scan was not started."}}
    out = run("run-scan.js", overview(), [{"do": "submit", "target": "form"}], [refused])
    els = out["shots"][2]["els"]
    assert (els["button"]["text"], els["button"]["disabled"]) == ("Run scan", False)
    assert els["note"]["text"] == "The scan was not started." and out["shots"][2]["timers"] == 0

    out = run("run-scan.js", overview(), [{"do": "submit", "target": "form"}], [{"ok": False, "body": None}])
    els = out["shots"][2]["els"]
    assert els["button"]["text"] == "Run scan" and "did not start" in els["note"]["text"]


# --- The pages ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def preview(tmp_path_factory) -> Path:
    folder = tmp_path_factory.mktemp("preview")
    build_preview(folder, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    return folder


def make_app(folder: Path, state: str = "normal"):
    return create_app({"SECRET_KEY": SECRET, "SERVICES": local_services(folder, state), "TESTING": True})


def test_every_page_has_the_working_region_and_loads_busy_js(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    client = make_app(preview).test_client()
    for url in ("/", "/scan", "/inventory", "/findings", "/setup", "/setup/summary"):
        body = client.get(url).get_data(as_text=True)
        assert 'src="/static/js/busy.js"' in body, url
        assert '<div class="banner banner-info" data-busy-region role="status" aria-live="polite" hidden></div>' in body
        assert '<a href="/setup"' in body and 'data-busy>Setup</a>' in body
        assert not re.search(r"<script(?![^>]*\bsrc=)", body) and ' style="' not in body
    setup = client.get("/setup").get_data(as_text=True)
    assert 'href="/setup/summary" data-busy>Build test summary</a>' in setup
    summary = client.get("/setup/summary").get_data(as_text=True)
    assert 'class="filters" data-busy>' in summary and 'data-busy>Back to Setup</a>' in summary
    for path in ("js/busy.js", "js/run-scan.js"):
        assert client.get(f"/static/{path}").status_code == 200


def test_the_storage_button_is_a_plain_post_form_that_shows_the_message(tmp_path, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    build_preview(tmp_path, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    body = make_app(tmp_path, "schema-missing").test_client().get("/").get_data(as_text=True)
    assert re.search(r'<form method="post" action="/setup/storage" data-busy>\s*<input type="hidden" '
                     r'name="csrf_token" value="[^"]+">\s*<button type="submit"', body)


def test_only_the_overview_loads_run_scan_js_and_wraps_the_button(preview, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    client = make_app(preview).test_client()
    body = client.get("/").get_data(as_text=True)
    assert 'src="/static/js/run-scan.js"' in body
    # The preview has a running scan: the form is there, with a disabled "Scan running" button, so the button can
    # come back when the run ends.
    assert re.search(r'<div class="run-scan" data-run-scan data-status="/api/scan-status" data-state="active" '
                     r'data-run="\d+">', body)
    assert re.search(r'<form method="post" action="/scan/start">\s*<input type="hidden" name="csrf_token" '
                     r'value="[^"]+">\s*<button type="submit" class="button" disabled>Scan running</button>', body)
    assert '<p class="note run-scan-note" data-run-scan-note role="status" aria-live="polite" hidden></p>' in body
    scan = client.get("/scan").get_data(as_text=True)
    assert "run-scan.js" not in scan and "data-run-scan" not in scan


def test_an_idle_overview_has_a_plain_run_scan_form(tmp_path, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    build_preview(tmp_path, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    app = make_app(tmp_path)
    jobs = app.extensions["pmsc"].jobs()
    jobs._runs[:] = [r for r in jobs._runs if not r.active]  # The made-up running scan ends.
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'data-run-scan data-status="/api/scan-status" data-state="idle">' in body
    assert re.search(r'<form method="post" action="/scan/start">\s*<input type="hidden" name="csrf_token" '
                     r'value="[^"]+">\s*<button type="submit" class="button button-primary">Run scan</button>', body)


def _start(app, accept: str, token: bool = True):
    with app.test_request_context("/"):
        csrf = generate_csrf_token(app)
    data = {"csrf_token": csrf} if token else {}
    return app.test_client().post("/scan/start", data=data, headers={"Accept": accept})


def test_the_start_answers_json_for_the_script_and_redirects_a_form_post(tmp_path, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    build_preview(tmp_path, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    app = make_app(tmp_path)
    jobs = app.extensions["pmsc"].jobs()
    active = next(r for r in jobs._runs if r.active)

    busy = _start(app, "application/json")
    assert busy.mimetype == "application/json"
    assert busy.get_json() == {"notice": "busy", "kind": "warning", "civis_run_id": active.id,
                               "message": "A scan is already queued or running, so a new one was not started."}
    jobs._runs[:] = [r for r in jobs._runs if not r.active]
    started = _start(app, "application/json").get_json()
    assert started["notice"] == "started" and started["civis_run_id"] == max(r.id for r in jobs._runs)
    assert started["message"].startswith("The scan has started.")

    form_post = _start(app, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    assert form_post.status_code == 302 and form_post.headers["Location"].endswith("/scan?notice=busy")
    assert _start(app, "application/json", token=False).status_code == 400  # The CSRF check still applies.
