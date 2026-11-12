"""The "Working" message (app/static/js/busy.js), the Overview's Run scan button (app/static/js/run-scan.js),
Scan history's progress card (app/static/js/scan-status.js), and what happens when Civis's proxy turns a status
request away (app/static/js/status-requests.js, shared by the two).

docs/PLAN.md 17.6, Part E, 17.8, Part B, and 17.9, Part B. The scripts run under Node with a small stand-in page (skipped if
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
# what sessionStorage held, and how many times the scripts touched sessionStorage (storageUses).
#
# Each answer in "responses" is "network-error" (the fetch rejects), or an object with "ok" (default true),
# "contentType" (default application/json), "body" (sent as JSON) or "text" (sent as is), and "hold" (the answer
# waits for a "release" step). "storage" is "ok" (default; "session" seeds it), "throws" (every use throws), or
# "none" (window.sessionStorage is missing). "now" sets Date.now; the "advance" step moves it by "ms". The
# "visibility" step sets document.hidden and fires visibilitychange. A "tick" runs the first waiting timer (an
# interval stays waiting). Elements can nest ("children"), and each snapshot gives the text the script added
# inside an element ("added") and how many " Loading" cues it holds ("cues"), and the body's class. A "submit" step
# can name its "submitter" button. The page's address is https://app.example.test plus "path".
HARNESS = r"""
const fs = require("fs");
const scenario = JSON.parse(process.argv[1]);
function element(spec) {
  const el = {
    attrs: Object.assign({}, spec.attrs || {}), hidden: !!spec.hidden, disabled: !!spec.disabled,
    textContent: spec.text || "", className: spec.className || "", listeners: {}, tag: spec.tag, children: [],
    parentNode: null, created: !!spec.created,
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    get classList() {
      const self = this;
      const names = () => self.className.split(/\s+/).filter(Boolean);
      return {
        add(c) { if (!names().includes(c)) self.className = names().concat([c]).join(" "); },
        remove(c) { self.className = names().filter((n) => n !== c).join(" "); },
        contains(c) { return names().includes(c); },
      };
    },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    insertBefore(child, ref) {
      child.parentNode = this;
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(child); else this.children.splice(i, 0, child);
      return child;
    },
    removeChild(child) {
      const i = this.children.indexOf(child);
      if (i >= 0) this.children.splice(i, 1);
      child.parentNode = null;
      return child;
    },
    get nextSibling() {
      if (!this.parentNode) return null;
      const siblings = this.parentNode.children;
      return siblings[siblings.indexOf(this) + 1] || null;
    },
    querySelector(selector) {
      for (const child of this.children) {
        if (child.matches(selector)) return child;
        const deeper = child.querySelector(selector);
        if (deeper) return deeper;
      }
      return null;
    },
    closest(selector) {
      for (let node = this; node; node = node.parentNode) if (node.matches(selector)) return node;
      return null;
    },
    // A tag, an attribute ([name] or [name="value"]), or a tag with one attribute.
    matches(selector) {
      const m = selector.match(/^([a-z]+)?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
      if (!m || (!m[1] && !m[2])) return false;
      if (m[1] && this.tag !== m[1]) return false;
      if (!m[2]) return true;
      return m[3] === undefined ? this.hasAttribute(m[2]) : this.getAttribute(m[2]) === m[3];
    },
  };
  return el;
}
const els = {};
for (const [name, spec] of Object.entries(scenario.elements)) els[name] = element(spec);
for (const [name, spec] of Object.entries(scenario.elements)) {
  for (const child of spec.children || []) els[name].appendChild(els[child]);
}
const body = els.body || element({ tag: "body" });
const docListeners = {};
const winListeners = {};
const timers = [];
let timerIds = 0;
const fetches = [];
const held = [];
let reloads = 0;
let storageUses = 0;
let clock = scenario.now || 1800000000000;
Date.now = () => clock;
const responses = scenario.responses.slice();
const stored = Object.assign({}, scenario.session || {});
const storageMode = scenario.storage || "ok";
const storage = {
  getItem(key) {
    storageUses += 1;
    if (storageMode === "throws") throw new Error("SecurityError");
    return Object.prototype.hasOwnProperty.call(stored, key) ? stored[key] : null;
  },
  setItem(key, value) {
    storageUses += 1;
    if (storageMode === "throws") throw new Error("QuotaExceededError");
    stored[key] = String(value);
  },
  removeItem(key) { storageUses += 1; delete stored[key]; },
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
  location: { pathname: scenario.path || "/", href: "https://app.example.test" + (scenario.path || "/"),
              reload() { reloads += 1; } },
};
// Every mode counts each time a script reaches for window.sessionStorage, so a test can show it is never used.
Object.defineProperty(global.window, "sessionStorage", { get() {
  storageUses += 1;
  if (storageMode === "throws") throw new Error("SecurityError");
  return storageMode === "none" ? undefined : storage;
} });
global.document = {
  hidden: !!scenario.hidden,
  body,
  createElement(tag) { return element({ tag, created: true }); },
  querySelector(selector) { return Object.values(els).find((e) => e.matches(selector)) || null; },
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
function snapshot() {
  const out = {};
  for (const [name, el] of Object.entries(els)) {
    const added = el.children.filter((c) => c.created);
    out[name] = { text: el.textContent, hidden: el.hidden, disabled: el.disabled, className: el.className,
                  busy: el.getAttribute("aria-busy"), added: added.map((c) => c.textContent).join(""),
                  cues: added.filter((c) => c.className === "busy-cue").length };
  }
  return { els: out, timers: timers.length, fetches: fetches.length, reloads,
           storage: Object.assign({}, stored), storageUses, bodyClass: body.className };
}
(async () => {
  for (const script of scenario.scripts) eval(fs.readFileSync(script, "utf8"));
  const shots = [snapshot()];
  for (const step of scenario.steps) {
    if (step.do === "submit" || step.do === "click") {
      const event = Object.assign({ target: els[step.target], defaultPrevented: false, button: 0,
                                    preventDefault() { this.defaultPrevented = true; } }, step.event || {});
      if (step.submitter) event.submitter = els[step.submitter];
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


# The page scripts that status-requests.js is loaded before, as the templates do.
NEEDS_STATUS_REQUESTS = ("run-scan.js", "scan-status.js")


def run(script: str, elements: dict, steps: list, responses: list | None = None, **page) -> dict:
    """``page`` sets the stand-in page's other parts: path, storage, session, hidden, and now (see HARNESS)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    scripts = (["status-requests.js"] if script in NEEDS_STATUS_REQUESTS else []) + [script]
    scenario = {"scripts": [str(JS / name) for name in scripts], "elements": elements, "steps": steps,
                "responses": responses or [], **page}
    result = subprocess.run([node, "-e", HARNESS, json.dumps(scenario)], capture_output=True, text=True, check=True,
                            timeout=30)
    return json.loads(result.stdout)


# --- busy.js: the busy cue and the "Working" message (docs/PLAN.md 17.6, 17.8, and 17.9, Part D) ---------------

LOADING = " Loading"


def busy_page(**changes) -> dict:
    """A stand-in Findings page (path /findings) with every kind of link and form busy.js looks at."""
    page = {
        "body": {"tag": "body"},
        "skip": {"tag": "a", "attrs": {"class": "skip-link", "href": "#main"}, "text": "Skip to main content"},
        "nav_inventory": {"tag": "a", "attrs": {"href": "/inventory"}, "text": "Inventory"},
        "nav_scan": {"tag": "a", "attrs": {"href": "/scan", "data-busy": ""}, "text": "Scan history"},
        "nav_setup": {"tag": "a", "attrs": {"href": "/setup", "data-busy": ""}, "text": "Setup"},
        "main": {"tag": "main", "attrs": {"id": "main"},
                 "children": ["region", "head", "filters", "checklist", "next_page", "civis", "download",
                              "download_attr", "same_page", "self_link", "other_site", "box", "storage",
                              "summary_link"]},
        "region": {"tag": "div", "attrs": {"data-busy-region": ""}, "hidden": True},
        "head": {"tag": "div", "children": ["download_csv", "save"]},
        "download_csv": {"tag": "a", "attrs": {"href": "/download/503", "data-no-busy": ""}, "text": "Download CSV"},
        "save": {"tag": "button", "attrs": {"type": "submit", "form": "checklist-form"}, "text": "Save changes"},
        "filters": {"tag": "form", "attrs": {"method": "get", "action": "/findings"}, "children": ["actions"]},
        "actions": {"tag": "div", "children": ["apply", "clear"]},
        "apply": {"tag": "button", "attrs": {"type": "submit"}, "text": "Apply filters"},
        "clear": {"tag": "a", "attrs": {"href": "/findings"}, "text": "Clear filters"},
        "checklist": {"tag": "form", "attrs": {"id": "checklist-form", "method": "post", "action": "/findings/save"}},
        "next_page": {"tag": "a", "attrs": {"href": "/findings?impact=affected&page=2"}, "text": "Next page"},
        "civis": {"tag": "a", "attrs": {"href": "https://platform.example.test/spa/#/scripts/python3/101",
                                        "target": "_blank", "rel": "noopener noreferrer"}, "text": "Example job"},
        "download": {"tag": "a", "attrs": {"href": "/download/503", "data-no-busy": ""}, "text": "CSV"},
        "download_attr": {"tag": "a", "attrs": {"href": "/download/503", "download": ""}, "text": "CSV file"},
        "same_page": {"tag": "a", "attrs": {"href": "/findings#rules"}, "text": "Rules on this page"},
        "self_link": {"tag": "a", "attrs": {"href": "/inventory?type=notebook", "target": "_self"}, "text": "Notebooks"},
        "other_site": {"tag": "a", "attrs": {"href": "https://www.example.test/"}, "text": "Another site"},
        "box": {"tag": "div", "attrs": {"data-run-scan": "", "data-status": "/api/scan-status"},
                "children": ["run_form"]},
        "run_form": {"tag": "form", "attrs": {"method": "post", "action": "/scan/start"}, "children": ["run_button"]},
        "run_button": {"tag": "button", "attrs": {"type": "submit"}, "text": "Run scan"},
        "storage": {"tag": "form", "attrs": {"method": "post", "action": "/setup/storage", "data-busy": ""},
                    "children": ["storage_button"]},
        "storage_button": {"tag": "button", "attrs": {"type": "submit"}, "text": "Set up results storage"},
        "summary_link": {"tag": "a", "attrs": {"href": "/setup/summary", "data-busy": ""}, "text": "Build test summary"},
    }
    for name, spec in changes.items():
        page[name] = spec
    return page


def busy(steps: list, **changes) -> dict:
    return run("busy.js", busy_page(**changes), steps, path="/findings")


def cues(shot: dict) -> dict[str, int]:
    return {name: el["cues"] for name, el in shot["els"].items() if el["cues"]}


def loading(shot: dict) -> bool:
    return shot["bodyClass"] == "is-loading" and shot["els"]["main"]["busy"] == "true"


@pytest.mark.parametrize("link", ["nav_inventory", "next_page", "clear", "self_link"])
def test_a_plain_click_on_an_app_link_shows_the_cue_at_once(link):
    out = busy([{"do": "click", "target": link}])
    shot = out["shots"][1]
    assert cues(shot) == {link: 1} and shot["els"][link]["added"] == LOADING
    assert loading(shot) and shot["prevented"] is False  # The browser still follows the link.
    assert shot["els"]["region"]["hidden"] is True  # The "Working" message is only for data-busy.
    assert out["fetches"] == []


def test_a_filter_form_shows_the_cue_next_to_its_button():
    out = busy([{"do": "submit", "target": "filters"}])  # No submitter: the form's own submit button is used.
    shot = out["shots"][1]
    assert cues(shot) == {"actions": 1} and shot["els"]["actions"]["added"] == LOADING
    assert loading(shot) and shot["prevented"] is False


@pytest.mark.parametrize("submitter", [None, "save"])
def test_save_changes_shows_the_cue_next_to_its_button_outside_the_form(submitter):
    step = {"do": "submit", "target": "checklist"}
    if submitter:
        step["submitter"] = submitter
    shot = busy([step])["shots"][1]
    assert cues(shot) == {"head": 1} and loading(shot)  # Beside "Save changes", which sits in the page head.


@pytest.mark.parametrize("target, event", [
    ("civis", {}),  # Opens Civis in a new tab.
    ("nav_inventory", {"ctrlKey": True}),
    ("nav_inventory", {"metaKey": True}),
    ("nav_inventory", {"shiftKey": True}),
    ("nav_inventory", {"altKey": True}),
    ("nav_inventory", {"button": 1}),  # Middle click: a new tab.
    ("nav_inventory", {"defaultPrevented": True}),
    ("skip", {}),  # The skip link: a place on this page.
    ("same_page", {}),
    ("download_attr", {}),
    ("download", {}),
    ("download_csv", {}),
    ("other_site", {}),
])
def test_no_cue_for_links_that_do_not_load_another_app_page_here(target, event):
    shot = busy([{"do": "click", "target": target, "event": event}])["shots"][1]
    assert cues(shot) == {} and shot["bodyClass"] == "" and shot["els"]["main"]["busy"] is None
    assert shot["els"]["region"]["hidden"] is True


def test_no_cue_for_the_run_scan_form():
    shot = busy([{"do": "submit", "target": "run_form"}])["shots"][1]
    assert cues(shot) == {} and shot["bodyClass"] == "" and shot["els"]["main"]["busy"] is None


def test_no_cue_for_a_new_tab_click_on_a_data_busy_link():
    shot = busy([{"do": "click", "target": "nav_setup", "event": {"ctrlKey": True}}])["shots"][1]
    assert cues(shot) == {} and shot["els"]["region"]["hidden"] is True


@pytest.mark.parametrize("target, kind", [("nav_scan", "click"), ("nav_setup", "click"), ("summary_link", "click"),
                                          ("storage", "submit")])
def test_data_busy_links_and_forms_get_the_cue_and_the_working_message(target, kind):
    shot = busy([{"do": kind, "target": target}])["shots"][1]
    place = "storage" if target == "storage" else target  # The storage button's cue goes beside it, in its form.
    assert cues(shot) == {place: 1} and loading(shot)
    assert (shot["els"]["region"]["text"], shot["els"]["region"]["hidden"]) == (WORKING, False)
    assert shot["els"][target]["busy"] == "true"


def test_coming_back_from_history_clears_the_cue_the_class_and_aria_busy():
    out = busy([{"do": "click", "target": "nav_setup"}, {"do": "pageshow"},
                {"do": "submit", "target": "filters"}, {"do": "pageshow"}])
    after_link, after_form = out["shots"][3], out["shots"][6]  # Two shots per click or submit, one per pageshow.
    for shot in (after_link, after_form):
        assert cues(shot) == {} and shot["bodyClass"] == "" and shot["els"]["main"]["busy"] is None
        assert (shot["els"]["region"]["hidden"], shot["els"]["region"]["text"]) == (True, "")
        assert shot["els"]["nav_setup"]["busy"] is None
    assert cues(out["shots"][4]) == {"actions": 1}  # The cue can show again after coming back.


def test_a_second_click_while_the_cue_shows_adds_nothing():
    out = busy([{"do": "click", "target": "next_page"}, {"do": "click", "target": "next_page"},
                {"do": "click", "target": "clear"}, {"do": "submit", "target": "filters"}])
    for shot in out["shots"][1:]:
        assert cues(shot) == {"next_page": 1}
    assert out["shots"][-1]["els"]["next_page"]["added"] == LOADING


def test_no_cue_while_the_checklist_has_unsaved_changes_except_for_saving_them():
    """The browser asks before leaving a page with unsaved checklist changes (findings.js), and the viewer may stay,
    so a link or the filters show no cue then. Saving the changes still does."""
    dirty = {"tag": "form", "attrs": {"id": "checklist-form", "method": "post", "action": "/findings/save",
                                      "data-dirty": "true"}}
    for step in ({"do": "click", "target": "nav_inventory"}, {"do": "submit", "target": "filters"}):
        assert cues(busy([step], checklist=dirty)["shots"][1]) == {}
    shot = busy([{"do": "submit", "target": "checklist", "submitter": "save"}], checklist=dirty)["shots"][1]
    assert cues(shot) == {"head": 1}


def test_without_the_working_region_the_cue_still_shows():
    page = busy_page()
    del page["region"]
    page["main"]["children"].remove("region")
    out = run("busy.js", page, [{"do": "click", "target": "nav_setup"}], path="/findings")
    shot = out["shots"][1]
    assert cues(shot) == {"nav_setup": 1} and loading(shot) and shot["els"]["nav_setup"]["busy"] is None


def test_busy_js_sets_text_only_and_no_inline_style():
    script = (JS / "busy.js").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|insertAdjacentHTML|document\.write|\.style\b|setAttribute\(\"style", script)
    assert '" Loading"' in script and "textContent" in script


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
    assert ended["note"]["text"] == ENDED
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


# --- status-requests.js: Civis's proxy turns a status request away (docs/PLAN.md 17.9, Part B) ---------------
#
# In the live test on rc9, the reload that 17.8 added reloaded only the app's frame, which does not renew Civis's
# proxy cookie, so the frame landed on Civis's "Cookie expired." page. The page now never reloads itself because a
# request was turned away: after 2 turned-away polls in a row it stops polling and says to reload the whole Civis
# page.

STOP_LINE = ("Progress stopped updating because the Civis session for this page expired. To see the latest "
             "progress, reload the whole Civis page with the browser's reload button (F5 on Windows). Reloading "
             "only this frame does not help.")
POST_TURNED_AWAY = ("Civis did not start the scan. Reload the whole Civis page with the browser's reload button, "
                    "then try again.")
ENDED = "The scan has ended. Reload the whole Civis page to see its results."
NOW_MS = 1_800_000_000_000
# What Civis's proxy can answer instead of the app's JSON: the fetch rejects (how Chrome showed it), a response
# that is not ok, an ok response that is not JSON (Civis's own "Cookie expired." page), and a body that does not
# parse even though the type says JSON.
TURNED_AWAY = {
    "network-error": "network-error",
    "not-ok": {"ok": False, "contentType": "text/html", "text": "<html><body>Cookie expired.</body></html>"},
    "html": {"contentType": "text/html; charset=utf-8", "text": "<html><body>Cookie expired.</body></html>"},
    "bad-json": {"contentType": "application/json", "text": "Cookie expired."},
    "json-not-object": {"contentType": "application/json", "text": "[1, 2]"},
}
RUNNING = {"body": {"state": "running", "headline": "Inventory: 20 of 36 objects read", "eta": "", "percent": 20,
                    "civis_run_id": 7, "can_start": False}}
STORAGE_MODES = ("ok", "throws", "none")


def scan_page(state: str = "running") -> dict:
    """Scan history's progress card, as scan.html renders it (no steps list: the stand-in has no createElement)."""
    return {
        "card": {"tag": "section", "attrs": {"data-scan-status": "", "data-endpoint": "/api/scan-status",
                                             "data-state": state}, "children": ["headline", "eta"]},
        "headline": {"tag": "div", "attrs": {"data-field": "headline"}, "text": "Inventory: 10 of 36 objects read"},
        "eta": {"tag": "div", "attrs": {"data-field": "eta"}, "className": "note"},
    }


def scan_history(steps: list, responses: list, **page) -> dict:
    return run("scan-status.js", scan_page(), steps, responses, path="/scan", now=NOW_MS, **page)


def active_overview(steps: list, responses: list, **page) -> dict:
    return run("run-scan.js", overview("active", "7", running=True), steps, responses, path="/", now=NOW_MS, **page)


def stop_text(out: dict, shot: int = -1) -> str:
    """The stop line's place: Scan history's note under the headline, or the Overview's note under Run scan."""
    els = out["shots"][shot]["els"]
    return els["eta"]["text"] if "eta" in els else els["note"]["text"]


PAGES = {"scan history": scan_history, "overview": active_overview}


@pytest.mark.parametrize("page", sorted(PAGES))
@pytest.mark.parametrize("kind", sorted(TURNED_AWAY))
def test_each_kind_of_turned_away_poll_counts(page, kind):
    out = PAGES[page]([{"do": "tick"}, {"do": "tick"}, {"do": "tick"}], [TURNED_AWAY[kind]] * 3)
    first, second, third = out["shots"][1], out["shots"][2], out["shots"][3]
    assert stop_text(out, 1) == "" and first["timers"] == 1  # One turned-away poll: still polling.
    assert stop_text(out, 2) == STOP_LINE and second["timers"] == 0  # Two in a row: stopped, with the line.
    assert third["fetches"] == 2  # Nothing more is sent.
    assert [shot["reloads"] for shot in out["shots"]] == [0, 0, 0, 0]


@pytest.mark.parametrize("page", sorted(PAGES))
def test_a_success_between_turned_away_polls_resets_the_count(page):
    away = TURNED_AWAY["html"]
    ok = RUNNING if page == "scan history" else status("running", 7)
    out = PAGES[page]([{"do": "tick"}] * 5, [away, ok, away, away, ok])
    texts = [stop_text(out, i) for i in range(1, 6)]
    assert texts[:3] == ["", "", ""]  # Turned away, success, turned away: still polling.
    assert texts[3] == STOP_LINE and out["shots"][4]["timers"] == 0  # The second in a row stops it.
    assert out["shots"][5]["fetches"] == 4 and out["shots"][-1]["reloads"] == 0


def test_scan_history_shows_progress_from_a_success_after_one_turned_away_poll():
    out = scan_history([{"do": "tick"}, {"do": "tick"}], [TURNED_AWAY["network-error"], RUNNING])
    assert out["shots"][-1]["els"]["headline"]["text"] == "Inventory: 20 of 36 objects read"
    assert out["shots"][-1]["els"]["eta"]["text"] == "" and out["shots"][-1]["timers"] == 1


def test_the_overview_shows_the_stop_line_under_run_scan_and_keeps_the_button():
    out = active_overview([{"do": "tick"}, {"do": "tick"}, {"do": "tick"}], [TURNED_AWAY["html"]] * 2)
    last = out["shots"][-1]
    assert (last["els"]["note"]["text"], last["els"]["note"]["hidden"]) == (STOP_LINE, False)
    assert (last["els"]["button"]["text"], last["els"]["button"]["disabled"]) == ("Scan running", True)
    assert (last["fetches"], last["timers"], last["reloads"]) == (2, 0, 0)


@pytest.mark.parametrize("storage", STORAGE_MODES)
@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("page", sorted(PAGES))
def test_a_turned_away_poll_never_reloads_or_touches_session_storage(page, hidden, storage):
    away = TURNED_AWAY["not-ok"]
    steps = [{"do": "tick"}, {"do": "advance", "ms": 120_000}, {"do": "visibility", "hidden": not hidden},
             {"do": "tick"}, {"do": "visibility", "hidden": hidden}, {"do": "tick"}]
    out = PAGES[page](steps, [away] * 3, hidden=hidden, storage=storage)
    assert all(shot["reloads"] == 0 for shot in out["shots"])
    assert all(shot["storageUses"] == 0 and shot["storage"] == {} for shot in out["shots"])
    assert stop_text(out) == STOP_LINE and out["shots"][-1]["fetches"] == 2


@pytest.mark.parametrize("storage", STORAGE_MODES)
def test_successful_polls_never_touch_session_storage(storage):
    session = {"pmsc-refresh:/scan": json.dumps({"count": 2, "last": NOW_MS})}  # Left by rc9: ignored.
    out = scan_history([{"do": "tick"}, {"do": "tick"}], [RUNNING, RUNNING], storage=storage, session=session)
    assert out["shots"][-1]["storageUses"] == 0 and out["shots"][-1]["reloads"] == 0
    out = active_overview([{"do": "tick"}], [status("running", 7)], storage=storage)
    assert out["shots"][-1]["storageUses"] == 0 and out["shots"][-1]["reloads"] == 0


def test_the_scripts_have_no_reload_on_a_turned_away_answer_and_no_session_storage():
    shared = (JS / "status-requests.js").read_text(encoding="utf-8")
    assert "location" not in shared and "reload(" not in shared
    for name in ("status-requests.js", "run-scan.js", "scan-status.js"):
        script = (JS / name).read_text(encoding="utf-8")
        assert "sessionStorage" not in script and "localStorage" not in script, name
        assert "pmscProxyRefresh" not in script, name
    # The only reload left is Scan history's, when a scan finishes after a successful poll.
    assert (JS / "scan-status.js").read_text(encoding="utf-8").count("window.location.reload()") == 1
    assert "location.reload" not in (JS / "run-scan.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", sorted(TURNED_AWAY))
def test_a_turned_away_run_scan_post_shows_its_message_and_is_not_repeated(kind):
    out = run("run-scan.js", overview(), [{"do": "submit", "target": "form"}, {"do": "tick"}, {"do": "tick"}],
              [TURNED_AWAY[kind]], path="/", now=NOW_MS)
    last = out["shots"][-1]
    assert last["els"]["note"]["text"] == POST_TURNED_AWAY
    assert (last["els"]["button"]["text"], last["els"]["button"]["disabled"]) == ("Run scan", False)
    assert last["reloads"] == 0 and last["storageUses"] == 0 and last["timers"] == 0
    assert [f["method"] for f in out["fetches"]] == ["POST"]  # Sent once, never again.


def test_a_run_scan_post_is_sent_once_while_a_poll_is_turned_away():
    # An Overview polling a run, with Run scan still on (the stand-in allows it), so a poll can be turned away
    # while the POST waits for its answer.
    page = overview("active", "7", running=False)
    started = {"hold": True, "body": {"notice": "started", "civis_run_id": 8, "message": "The scan has started."}}
    out = run("run-scan.js", page, [{"do": "submit", "target": "form"}, {"do": "tick"}, {"do": "release"}],
              [started, TURNED_AWAY["html"]], path="/", now=NOW_MS)
    assert all(shot["reloads"] == 0 for shot in out["shots"])
    assert [f["method"] for f in out["fetches"]] == ["POST", "GET"]
    assert out["shots"][-1]["els"]["note"]["text"] == "The scan has started."


def test_scan_history_still_reloads_once_when_the_scan_ends_after_a_successful_poll():
    """The finish reload follows a successful poll, so the proxy's cookie was valid a moment earlier."""
    ended = {"body": {"state": "succeeded", "civis_run_id": 7}}
    out = scan_history([{"do": "tick"}], [ended])
    assert out["shots"][-1]["reloads"] == 1 and out["shots"][-1]["storageUses"] == 0
    out = scan_history([{"do": "tick"}, {"do": "tick"}], [TURNED_AWAY["html"], ended])
    assert [shot["reloads"] for shot in out["shots"]] == [0, 0, 1]  # Only the success led to it.


def test_the_overview_says_to_reload_the_whole_civis_page_when_the_scan_ends():
    out = active_overview([{"do": "tick"}], [status("succeeded", 7, True)])
    assert out["shots"][-1]["els"]["note"]["text"] == ENDED and out["shots"][-1]["reloads"] == 0


def test_the_messages_follow_the_writing_style_and_show_in_a_checked_color():
    shared = (JS / "status-requests.js").read_text(encoding="utf-8")
    joined = "".join(re.findall(r'"([^"]*)"', shared[shared.index("var STOP_LINE"):shared.index("var JSON_TYPE")]))
    assert joined == STOP_LINE
    run_scan = (JS / "run-scan.js").read_text(encoding="utf-8")
    assert f'"{ENDED}"' in run_scan
    assert f'"{POST_TURNED_AWAY}"' in run_scan
    for text in (STOP_LINE, POST_TURNED_AWAY, ENDED):
        assert "—" not in text and "–" not in text and "!" not in text
    # The stop line shows in Scan history's note under the headline and the Overview's Run scan note, both class
    # "note", whose color (--muted on --bg and --surface) is in test_app.test_text_contrast_is_at_least_4_5_to_1.
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")
    assert ".note { font-size: 13px; color: var(--muted); margin: 0; }" in css
    from tests.test_app import test_text_contrast_is_at_least_4_5_to_1 as contrast

    contrast()
    for script in (shared, run_scan, (JS / "scan-status.js").read_text(encoding="utf-8")):
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
    for path in ("js/busy.js", "js/run-scan.js", "js/status-requests.js", "js/scan-status.js"):
        assert client.get(f"/static/{path}").status_code == 200


def test_the_shared_status_requests_load_before_the_page_scripts(preview, monkeypatch):
    """docs/PLAN.md 17.8, Part B, renamed in 17.9: one shared file, loaded (deferred, so in order) before
    scan-status.js and run-scan.js, and only on those two pages. The old file is gone."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    client = make_app(preview).test_client()
    shared = '<script src="/static/js/status-requests.js" defer></script>'
    for url, script in (("/", "run-scan.js"), ("/scan", "scan-status.js")):
        body = client.get(url).get_data(as_text=True)
        page = f'<script src="/static/js/{script}" defer></script>'
        assert body.count(shared) == 1 and body.index(shared) < body.index(page), url
    scan = client.get("/scan").get_data(as_text=True)
    assert 'data-scan-status data-endpoint="/api/scan-status"' in scan and '<div class="note" data-field="eta">' in scan
    for url in ("/inventory", "/findings", "/setup"):
        assert "status-requests.js" not in client.get(url).get_data(as_text=True), url
    for url in ("/", "/scan", "/inventory", "/findings", "/setup"):
        assert "proxy-refresh" not in client.get(url).get_data(as_text=True), url
    assert client.get("/static/js/proxy-refresh.js").status_code == 404 and not (JS / "proxy-refresh.js").exists()


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
    assert re.search(r'</p></div>\s*<a class="button button-secondary" href="/download/\d+" data-no-busy>Download CSV</a>',
                     head)
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
