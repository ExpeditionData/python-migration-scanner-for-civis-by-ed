// The Overview's Run scan button. Clicking it starts the scan without leaving the page: the button changes to
// "Scan running", and it changes back when the run ends. While a run is starting or active, the page asks
// /api/scan-status every 15 seconds; it asks nothing at any other time.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP). Text is set with textContent
// only. Without JavaScript, the form posts as before and the browser opens Scan history.
// proxy-refresh.js (loaded first) sends each request and reloads the page when Civis's proxy turns a status
// request away. The Run scan POST is sent once and never makes the page reload.
(function () {
  "use strict";

  var POLL_MS = 15000;
  var START_POLLS = 8; // About 2 minutes for Civis to list a new run as queued or running.
  var box = document.querySelector("[data-run-scan]");
  if (!box || typeof window.fetch !== "function" || typeof window.FormData !== "function" ||
      !window.pmscProxyRefresh) {
    return;
  }
  var form = box.querySelector("form");
  var button = box.querySelector("button");
  var note = box.querySelector("[data-run-scan-note]");
  var statusUrl = box.getAttribute("data-status");
  var runId = box.getAttribute("data-run"); // The run being watched, once known.
  var startingPolls = 0; // Polls left for a run that was just started but is not listed yet.
  var timer = null;

  function say(text) {
    if (note) {
      note.textContent = text || "";
      note.hidden = !text;
    }
  }

  var refresh = window.pmscProxyRefresh.create({ show: say });

  function showRunning(running) {
    if (!button) {
      return;
    }
    button.disabled = running;
    button.textContent = running ? "Scan running" : "Run scan";
    button.className = running ? "button" : "button button-primary";
  }

  function schedule() {
    if (timer === null && !refresh.isStopped()) {
      timer = window.setTimeout(poll, POLL_MS);
    }
  }

  function ended(data) {
    runId = null;
    startingPolls = 0;
    showRunning(false);
    if (button && !data.can_start) {
      button.disabled = true; // Run scan is off for another reason; the page says why after a reload.
      button.className = "button";
    }
    say("The scan has ended. Reload this page to see its results.");
  }

  function update(data) {
    if (!data) {
      schedule(); // Turned away: proxy-refresh.js reloads the page or stops, and otherwise the next poll tries again.
      return;
    }
    var active = data.state === "queued" || data.state === "running";
    var sameRun = runId !== null && String(data.civis_run_id) === String(runId);
    if (active && (runId === null || sameRun)) {
      runId = String(data.civis_run_id);
      startingPolls = 0;
      showRunning(true);
      schedule();
    } else if (startingPolls > 0 && !sameRun) {
      startingPolls -= 1; // The new run is not listed yet.
      schedule();
    } else if (active) {
      runId = String(data.civis_run_id); // Another run is active now: watch it instead.
      showRunning(true);
      schedule();
    } else {
      ended(data);
    }
  }

  function poll() {
    timer = null;
    refresh.poll(statusUrl).then(update);
  }

  function started(data) {
    if (!data) {
      showRunning(false);
      say("Civis did not start the scan. Reload the page and try again.");
      return;
    }
    say(data.message);
    if (data.notice === "started" || data.notice === "busy") {
      runId = data.civis_run_id === null || data.civis_run_id === undefined ? null : String(data.civis_run_id);
      startingPolls = data.notice === "started" ? START_POLLS : 0;
      showRunning(true);
      schedule();
    } else {
      showRunning(false);
    }
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (button && button.disabled) {
        return;
      }
      showRunning(true);
      refresh.post(form.getAttribute("action"), new window.FormData(form)).then(started);
    });
  }

  if (box.getAttribute("data-state") === "active") {
    schedule(); // The page opened while a run was active: watch it until it ends.
  }
})();
