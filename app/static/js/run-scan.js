// The Overview's Run scan button. Clicking it starts the scan without leaving the page: the button changes to
// "Scan running", and it changes back when the run ends. While a run is starting or active, the page asks
// /api/scan-status every 15 seconds; it asks nothing at any other time.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP). Text is set with textContent
// only. Without JavaScript, the form posts as before and the browser opens Scan history.
// status-requests.js (loaded first) sends each request. When Civis's proxy turns 2 status polls in a row away
// (its session for the page expired), polling stops and the line under the button says to reload the whole Civis
// page. The page never reloads itself. The Run scan POST is sent once and never repeated.
(function () {
  "use strict";

  var POLL_MS = 15000;
  var START_POLLS = 8; // About 2 minutes for Civis to list a new run as queued or running.
  var box = document.querySelector("[data-run-scan]");
  if (!box || typeof window.fetch !== "function" || typeof window.FormData !== "function" ||
      !window.pmscStatusRequests) {
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

  var requests = window.pmscStatusRequests.create({ show: say });

  function showRunning(running) {
    if (!button) {
      return;
    }
    button.disabled = running;
    button.textContent = running ? "Scan running" : "Run scan";
    button.className = running ? "button" : "button button-primary";
  }

  function schedule() {
    if (timer === null && !requests.isStopped()) {
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
    say("The scan has ended. Reload the whole Civis page to see its results.");
  }

  function update(data) {
    if (!data) {
      schedule(); // Turned away: the next poll tries again, unless status-requests.js stopped polling.
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
    requests.poll(statusUrl).then(update);
  }

  function started(data) {
    if (!data) {
      showRunning(false);
      say("Civis did not start the scan. Reload the whole Civis page with the browser's reload button, then try again.");
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
      requests.post(form.getAttribute("action"), new window.FormData(form)).then(started);
    });
  }

  if (box.getAttribute("data-state") === "active") {
    schedule(); // The page opened while a run was active: watch it until it ends.
  }
})();
