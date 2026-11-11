"""The "Working" message (app/static/js/busy.js), the Overview's Run scan button (app/static/js/run-scan.js),
Scan history's progress card (app/static/js/scan-status.js), and the reload when Civis's proxy turns a status
request away (app/static/js/proxy-refresh.js, shared by the two).

docs/PLAN.md 17.6, Part E, and 17.8, Part B. The scripts run under Node with a small stand-in page (skipped if
Node is not installed), and the pages are checked for the markup the scripts use and for working without
JavaScript. Made-up data only. No Civis calls.
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
# answered), every fetch the script sent, how many timers were waiting, how many times the page reloaded itself,
# and what sessionStorage held.
#
# Each answer in "responses" is "network-error" (the fetch rejects), or an object with "ok" (default true),
# "contentType" (default application/json), "body" (sent as JSON) or "text" (sent as is), and "hold" (the answer
# waits for a "release" step). "storage" is "ok" (default; "session" seeds it), "throws" (every use throws), or
# "none" (window.sessionStorage is missing). "now" sets Date.now; the "advance" step moves it by "ms". The
# "visibility" step sets document.hidden and fires visibilitychange. A "tick" runs the first waiting timer (an
# interval stays waiting).
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
      const m = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
      if (!m) return false;
      return m[2] === undefined ? this.hasAttribute(m[1]) : this.getAttribute(m[1]) === m[2];
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
let timerIds = 0;
const fetches = [];
const held = [];
let reloads = 0;
let clock = scenario.now || 1800000000000;
Date.now = () => clock;
const responses = scenario.responses.slice();
const stored = Object.assign({}, scenario.session || {});
const storageMode = scenario.storage || "ok";
const storage = {
  getItem(key) {
    if (storageMode === "throws") throw new Error("SecurityError");
    return Object.prototype.hasOwnProperty.call(stored, key) ? stored[key] : null;
  },
  setItem(key, value) {
    if (storageMode === "throws") throw new Error("QuotaExceededError");
    stored[key] = String(value);
  },
};
function answer(next) {
  const type = next.contentType === undefined ? "application/json" : next.contentType;
  const text = next.text !== undefined ? next.text : JSON.stringify(next.body === undefined ? null : next.body);
  return { ok: next.ok !== false, headers: { get: (name) => (name.toLowerCase() === "content-type" ? type : null) },
           text: () => Promise.resolve(text), json: () => Promise.resolve(JSON.parse(text)) };
}
global.window = {
  setTimeout(fn, ms) { timers.push({ fn, ms, id: ++timerIds }); return timerIds; },
  setInterval(fn, ms) { timers.push({ fn, ms, id: ++timerIds, repeat: true }); return timerIds; },
  clearInterval(id) { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
  fetch(url, options) {
    options = options || {};
    fetches.push({ url, method: options.method || "GET", accept: (options.headers || {}).Accept || null,
                   body: options.body ? options.body.from : null });
    const next = responses.shift();
    if (next === undefined || next === "network-error") return Promise.reject(new Error("offline"));
    if (next.hold) return new Promise((resolve) => held.push(() => resolve(answer(next))));
    return Promise.resolve(answer(next));
  },
  FormData: function (form) { this.from = form.getAttribute("action"); },
  location: { pathname: scenario.path || "/", reload() { reloads += 1; } },
};
if (storageMode === "throws") {
  Object.defineProperty(global.window, "sessionStorage", { get() { throw new Error("SecurityError"); } });
} else if (storageMode !== "none") {
  global.window.sessionStorage = storage;
}
global.document = {
  hidden: !!scenario.hidden,
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
  return { els: out, timers: timers.length, fetches: fetches.length, reloads,
           storage: Object.assign({}, stored) };
}
(async () => {
  for (const script of scenario.scripts) eval(fs.readFileSync(script, "utf8"));
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
      const timer = timers[0];
      if (timer && !timer.repeat) timers.shift();
      if (timer) timer.fn();
    } else if (step.do === "advance") {
      clock += step.ms;
    } else if (step.do === "visibility") {
      document.hidden = !!step.hidden;
      for (const fn of docListeners.visibilitychange || []) fn({});
    } else if (step.do === "release") {
      const next = held.shift();
      if (next) next();
    }
    for (let i = 0; i < 5; i++) await flush();
    shots.push(snapshot());  // After any answers the step's fetches got.
  }
  console.log(JSON.stringify({ shots, fetches }));
})();
"""


# The page scripts that proxy-refresh.js is loaded before, as the templates do.
NEEDS_REFRESH = ("run-scan.js", "scan-status.js")


def run(script: str, elements: dict, steps: list, responses: list | None = None, **page) -> dict:
    """``page`` sets the stand-in page's other parts: path, storage, session, hidden, and now (see HARNESS)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    scripts = (["proxy-refresh.js"] if script in NEEDS_REFRESH else []) + [script]
    scenario = {"scripts": [str(JS / name) for name in scripts], "elements": elements, "steps": steps,
                "responses": responses or [], **page}
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


# --- proxy-refresh.js: Civis's proxy turns a status request away (docs/PLAN.md 17.8, Part B) -------------------

STOP_LINE = ("Progress stopped updating because Civis asked for the page to be reloaded. "
             "Reload this page to see the latest progress.")
NOW_MS = 1_800_000_000_000
SCAN_KEY = "pmsc-refresh:/scan"
OVERVIEW_KEY = "pmsc-refresh:/"
# What Civis's proxy can answer instead of the app's JSON: the fetch rejects (how Chrome showed it), a response
# that is not ok, an ok response that is not JSON (Civis's own "Cookie expired." page), and a body that does not
# parse even though the type says JSON.
TURNED_AWAY = {
    "network-error": "network-error",
    "not-ok": {"ok": False, "contentType": "text/html", "text": "<html><body>Cookie expired.</body></html>"},
    "html": {"contentType": "text/html; charset=utf-8", "text": "<html><body>Cookie expired.</body></html>"},
    "bad-json": {"contentType": "application/json", "text": "Cookie expired."},
}
RUNNING = {"body": {"state": "running", "headline": "Inventory: 20 of 36 objects read", "eta": "", "percent": 20,
                    "civis_run_id": 7, "can_start": False}}


def scan_page(state: str = "running") -> dict:
    """Scan history's progress card, as scan.html renders it (no steps list: the stand-in has no createElement)."""
    return {
        "card": {"tag": "section", "attrs": {"data-scan-status": "", "data-endpoint": "/api/scan-status",
                                             "data-state": state}, "children": ["headline", "eta"]},
        "headline": {"tag": "div", "attrs": {"data-field": "headline"}, "text": "Inventory: 10 of 36 objects read"},
        "eta": {"tag": "div", "attrs": {"data-field": "eta"}, "className": "note"},
    }


def saved(out: dict, key: str, shot: int = -1) -> dict | None:
    value = out["shots"][shot]["storage"].get(key)
    return json.loads(value) if value is not None else None


def scan_history(steps: list, responses: list, **page) -> dict:
    return run("scan-status.js", scan_page(), steps, responses, path="/scan", now=NOW_MS, **page)


def active_overview(steps: list, responses: list, **page) -> dict:
    return run("run-scan.js", overview("active", "7", running=True), steps, responses, path="/", now=NOW_MS, **page)


@pytest.mark.parametrize("kind", sorted(TURNED_AWAY))
def test_each_kind_of_turned_away_poll_reloads_scan_history_once(kind):
    out = scan_history([{"do": "tick"}], [TURNED_AWAY[kind]])
    assert [shot["reloads"] for shot in out["shots"]] == [0, 1]
    assert saved(out, SCAN_KEY) == {"count": 1, "last": NOW_MS}  # Kept for the next page load.
    assert out["shots"][-1]["els"]["eta"]["text"] == ""  # No stop line yet.


@pytest.mark.parametrize("kind", sorted(TURNED_AWAY))
def test_each_kind_of_turned_away_poll_reloads_the_overview_once(kind):
    out = active_overview([{"do": "tick"}], [TURNED_AWAY[kind]])
    assert [shot["reloads"] for shot in out["shots"]] == [0, 1]
    assert saved(out, OVERVIEW_KEY) == {"count": 1, "last": NOW_MS}
    assert [(f["url"], f["method"]) for f in out["fetches"]] == [("/api/scan-status", "GET")]


def test_no_second_reload_within_a_minute():
    away = TURNED_AWAY["html"]
    out = scan_history([{"do": "tick"}, {"do": "advance", "ms": 59_000}, {"do": "tick"},
                        {"do": "advance", "ms": 1_000}, {"do": "tick"}], [away, away, away])
    assert [shot["reloads"] for shot in out["shots"]] == [0, 1, 1, 1, 1, 2]
    assert saved(out, SCAN_KEY) == {"count": 2, "last": NOW_MS + 60_000}


def test_the_minute_counts_across_page_loads():
    """The page that reloaded 30 seconds ago is a new page load: the time comes from sessionStorage."""
    session = {SCAN_KEY: json.dumps({"count": 1, "last": NOW_MS - 30_000})}
    away = TURNED_AWAY["network-error"]
    out = scan_history([{"do": "tick"}, {"do": "advance", "ms": 30_000}, {"do": "tick"}], [away, away],
                       session=session)
    assert [shot["reloads"] for shot in out["shots"]] == [0, 0, 0, 1]
    assert saved(out, SCAN_KEY) == {"count": 2, "last": NOW_MS + 30_000}


def test_the_stop_line_after_three_reloads_with_no_success():
    away = TURNED_AWAY["not-ok"]
    steps = []
    for _ in range(3):
        steps += [{"do": "tick"}, {"do": "advance", "ms": 61_000}]
    steps += [{"do": "tick"}, {"do": "tick"}]  # The fourth is turned away and stops polling; the fifth sends nothing.
    out = scan_history(steps, [away] * 5)
    last = out["shots"][-1]
    assert last["reloads"] == 3 and last["fetches"] == 4 and last["timers"] == 0
    assert last["els"]["eta"]["text"] == STOP_LINE
    assert saved(out, SCAN_KEY)["count"] == 3


def test_the_stop_line_on_the_overview_after_three_reloads():
    session = {OVERVIEW_KEY: json.dumps({"count": 3, "last": NOW_MS - 120_000})}
    out = active_overview([{"do": "tick"}, {"do": "tick"}], [TURNED_AWAY["html"]], session=session)
    last = out["shots"][-1]
    assert (last["reloads"], last["fetches"], last["timers"]) == (0, 1, 0)
    assert (last["els"]["note"]["text"], last["els"]["note"]["hidden"]) == (STOP_LINE, False)


def test_a_successful_poll_resets_the_count():
    session = {SCAN_KEY: json.dumps({"count": 2, "last": NOW_MS - 120_000})}
    out = scan_history([{"do": "tick"}, {"do": "tick"}], [RUNNING, TURNED_AWAY["html"]], session=session)
    assert saved(out, SCAN_KEY, shot=1) == {"count": 0, "last": NOW_MS - 120_000}
    assert out["shots"][1]["els"]["headline"]["text"] == "Inventory: 20 of 36 objects read"
    assert out["shots"][2]["reloads"] == 1 and saved(out, SCAN_KEY) == {"count": 1, "last": NOW_MS}


@pytest.mark.parametrize("storage", ["throws", "none"])
def test_without_session_storage_the_line_shows_and_the_page_never_reloads(storage):
    away = TURNED_AWAY["network-error"]
    out = scan_history([{"do": "tick"}, {"do": "advance", "ms": 120_000}, {"do": "tick"}], [away, away],
                       storage=storage)
    last = out["shots"][-1]
    assert [shot["reloads"] for shot in out["shots"]] == [0, 0, 0, 0]
    assert last["els"]["eta"]["text"] == STOP_LINE and last["fetches"] == 1 and last["timers"] == 0

    out = active_overview([{"do": "tick"}], [away], storage=storage)
    last = out["shots"][-1]
    assert last["reloads"] == 0 and last["els"]["note"]["text"] == STOP_LINE and last["timers"] == 0


def test_a_hidden_page_reloads_only_when_it_is_visible_again():
    out = scan_history([{"do": "visibility", "hidden": True}, {"do": "tick"}, {"do": "advance", "ms": 300_000},
                        {"do": "visibility", "hidden": False}], [TURNED_AWAY["html"]], hidden=True)
    assert [shot["reloads"] for shot in out["shots"]] == [0, 0, 0, 0, 1]
    assert saved(out, SCAN_KEY, shot=3) is None  # Nothing counted while hidden.
    assert saved(out, SCAN_KEY) == {"count": 1, "last": NOW_MS + 300_000}


def test_becoming_visible_with_no_turned_away_poll_does_not_reload():
    out = scan_history([{"do": "tick"}, {"do": "visibility", "hidden": True}, {"do": "visibility", "hidden": False}],
                       [RUNNING])
    assert out["shots"][-1]["reloads"] == 0


def test_no_reload_while_the_run_scan_post_waits_and_the_post_is_sent_once():
    # An Overview polling a run, with Run scan still on (the stand-in allows it), so a poll can be turned away
    # while the POST waits for its answer.
    page = overview("active", "7", running=False)
    started = {"hold": True, "body": {"notice": "started", "civis_run_id": 8, "message": "The scan has started."}}
    out = run("run-scan.js", page, [{"do": "submit", "target": "form"}, {"do": "tick"}, {"do": "release"}],
              [started, TURNED_AWAY["html"]], path="/", now=NOW_MS)
    reloads = [shot["reloads"] for shot in out["shots"]]
    assert reloads == [0, 0, 0, 0, 1]  # Start, submit (two shots), the turned-away poll, then the POST's answer.
    assert [f["method"] for f in out["fetches"]] == ["POST", "GET"]


@pytest.mark.parametrize("kind", sorted(TURNED_AWAY))
def test_a_turned_away_run_scan_post_keeps_its_message_and_never_reloads(kind):
    out = run("run-scan.js", overview(), [{"do": "submit", "target": "form"}, {"do": "tick"}], [TURNED_AWAY[kind]],
              path="/", now=NOW_MS)
    last = out["shots"][-1]
    assert last["els"]["note"]["text"] == "Civis did not start the scan. Reload the page and try again."
    assert last["els"]["button"]["text"] == "Run scan" and last["reloads"] == 0 and last["storage"] == {}
    assert [f["method"] for f in out["fetches"]] == ["POST"]  # Sent once, never again.


def test_a_json_answer_never_reloads():
    out = scan_history([{"do": "tick"}, {"do": "advance", "ms": 120_000}, {"do": "tick"}, {"do": "tick"}],
                       [RUNNING, RUNNING, RUNNING])
    assert out["shots"][-1]["reloads"] == 0 and out["shots"][-1]["storage"] == {}
    assert out["shots"][-1]["fetches"] == 3 and out["shots"][-1]["timers"] == 1

    out = active_overview([{"do": "tick"}, {"do": "tick"}], [status("running", 7), status("running", 7)])
    assert out["shots"][-1]["reloads"] == 0 and out["shots"][-1]["storage"] == {}


def test_scan_history_still_reloads_once_when_the_scan_ends():
    out = scan_history([{"do": "tick"}], [{"body": {"state": "succeeded", "civis_run_id": 7}}])
    assert out["shots"][-1]["reloads"] == 1 and out["shots"][-1]["storage"] == {}  # Not an automatic reload.


def test_the_stop_line_follows_the_writing_style_and_shows_in_a_checked_color():
    script = (JS / "proxy-refresh.js").read_text(encoding="utf-8")
    assert '"Progress stopped updating because Civis asked for the page to be reloaded. " +\n' in script
    assert '"Reload this page to see the latest progress."' in script
    assert "—" not in STOP_LINE and "–" not in STOP_LINE and "!" not in STOP_LINE
    # The line shows in Scan history's ETA note and the Overview's Run scan note, both class "note", whose color
    # (--muted on --bg and --surface) is in test_app.test_text_contrast_is_at_least_4_5_to_1.
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    assert ".note { font-size: 13px; color: var(--muted); margin: 0; }" in css
    from tests.test_app import test_text_contrast_is_at_least_4_5_to_1 as contrast

    contrast()
    assert not re.search(r"innerHTML|insertAdjacentHTML|document\.write", script)


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
    for path in ("js/busy.js", "js/run-scan.js", "js/proxy-refresh.js", "js/scan-status.js"):
        assert client.get(f"/static/{path}").status_code == 200


def test_the_shared_refresh_loads_before_the_page_scripts(preview, monkeypatch):
    """docs/PLAN.md 17.8, Part B: one shared file, loaded (deferred, so in order) before scan-status.js and
    run-scan.js, and only on those two pages."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    client = make_app(preview).test_client()
    shared = '<script src="/static/js/proxy-refresh.js" defer></script>'
    for url, script in (("/", "run-scan.js"), ("/scan", "scan-status.js")):
        body = client.get(url).get_data(as_text=True)
        page = f'<script src="/static/js/{script}" defer></script>'
        assert body.count(shared) == 1 and body.index(shared) < body.index(page), url
    scan = client.get("/scan").get_data(as_text=True)
    assert 'data-scan-status data-endpoint="/api/scan-status"' in scan and '<div class="note" data-field="eta">' in scan
    for url in ("/inventory", "/findings", "/setup"):
        assert "proxy-refresh.js" not in client.get(url).get_data(as_text=True), url


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


def test_the_run_scan_message_has_its_own_row_under_the_buttons(preview, monkeypatch):
    """docs/PLAN.md 17.7, Part C. The message used to sit beside Run scan and push Download CSV sideways. The
    Overview's page head is now a grid: the title and both buttons in row 1, the message in row 2 (last at phone
    width), styled from app.css only. The markup and its status region are unchanged."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    body = make_app(preview).test_client().get("/").get_data(as_text=True)
    head = body[body.index('<div class="page-head">'): body.index('<p class="note run-window">')]
    # The selector's path: the page head > .actions > .run-scan > the message, with Download CSV in .actions.
    assert re.search(r'<div class="actions">\s*<div class="run-scan" data-run-scan', head)
    assert re.search(r'</p></div>\s*<a class="button button-secondary" href="/download/\d+">Download CSV</a>', head)
    assert 'role="status" aria-live="polite" hidden></p>' in head and "style=" not in head
    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text(encoding="utf-8")
    grid = ".page-head:has(> .actions > .run-scan)"
    assert f"{grid} {{\n  display: grid;" in css
    assert f"{grid} > .actions,\n{grid} .run-scan {{ display: contents; }}" in css
    note = css[css.index(f"{grid} .run-scan-note {{"):]
    assert "grid-row: 2;" in note[:200]
    phone = css[css.index("@media (max-width: 640px)"):]
    assert f"{grid} .run-scan-note {{" in phone and f"{grid} > .actions > .button {{ grid-column: 1; grid-row: 3; }}" in phone


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
