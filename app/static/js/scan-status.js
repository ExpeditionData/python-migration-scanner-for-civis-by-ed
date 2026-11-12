// Scan progress: asks /api/scan-status every 15 seconds and updates the page.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP).
// Values are written with textContent only, never as HTML.
// status-requests.js (loaded first) sends each request. When Civis's proxy turns 2 polls in a row away (its session
// for the page expired), polling stops and the note under the headline says to reload the whole Civis page. The
// page never reloads itself because a request was turned away. It reloads once when the scan finishes, right
// after a successful poll, so the proxy's cookie was valid a moment earlier.
(function () {
  "use strict";

  var POLL_MS = 15000;
  var card = document.querySelector("[data-scan-status]");
  if (!card || !window.pmscStatusRequests || typeof window.fetch !== "function") {
    return;
  }
  var endpoint = card.getAttribute("data-endpoint");
  var startState = card.getAttribute("data-state");
  var timer = null;
  var requests = window.pmscStatusRequests.create({
    show: function (text) {
      setText("eta", text); // The note under the headline.
    },
    stopped: function () {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    }
  });

  function field(name) {
    return card.querySelector('[data-field="' + name + '"]');
  }

  function setText(name, value) {
    var el = field(name);
    if (el) {
      el.textContent = value || "";
    }
  }

  function renderSteps(steps) {
    var list = field("steps");
    if (!list || !Array.isArray(steps)) {
      return;
    }
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    steps.forEach(function (step) {
      var item = document.createElement("li");
      item.className = "step";
      var state = document.createElement("span");
      state.className = "step-state step-state-" + String(step.state || "").toLowerCase();
      state.textContent = step.state || "";
      var name = document.createElement("span");
      name.className = "step-name";
      name.textContent = step.name || "";
      var detail = document.createElement("span");
      detail.className = "step-detail";
      detail.textContent = step.detail || "";
      item.appendChild(state);
      item.appendChild(name);
      item.appendChild(detail);
      list.appendChild(item);
    });
  }

  function update(data) {
    if (!data) {
      return;
    }
    var active = data.state === "queued" || data.state === "running";
    if (!active && (startState === "queued" || startState === "running")) {
      // The scan finished: reload to show the results and the updated history.
      window.location.reload();
      return;
    }
    setText("headline", data.headline);
    setText("eta", data.eta);
    var bar = field("percent");
    if (bar && typeof data.percent === "number") {
      bar.value = data.percent;
      bar.textContent = data.percent + " percent";
    }
    renderSteps(data.steps);
  }

  function poll() {
    // A turned-away request resolves to null: the next interval tries again, unless status-requests.js stopped
    // polling after 2 in a row.
    requests.poll(endpoint).then(update);
  }

  timer = window.setInterval(poll, POLL_MS);
})();
