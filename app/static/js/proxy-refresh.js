// Shared by scan-status.js (Scan history) and run-scan.js (the Overview's Run scan button). Loaded before both.
//
// Civis's proxy in front of the Service uses a cookie that expires after some minutes. A page load renews it; a
// background request does not, and the proxy then answers a status request with its own "Cookie expired." page
// instead of the app's JSON (docs/PLAN.md 17.8). A status request counts as turned away when the fetch rejects (a
// network or CORS failure), the response is not ok, its Content-Type is not JSON, or its body does not parse as a
// JSON object. When a poll is turned away, the page reloads itself so the proxy issues a fresh cookie, with limits:
//   - at most one automatic reload per minute per page;
//   - after 3 automatic reloads with no successful poll in between, polling stops and one line says so;
//   - the count and the time of the last reload are kept in sessionStorage, keyed by the page's path; without
//     sessionStorage there is no automatic reload, and the line shows at the first turned-away poll;
//   - no reload while the page is hidden (it reloads when it is visible again, if a turned-away poll is waiting);
//   - no reload while a POST from the page is waiting for its answer, and a POST is never sent again.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP). The caller shows the line with
// textContent only.
(function () {
  "use strict";

  var RELOAD_GAP_MS = 60000;
  var MAX_RELOADS = 3;
  var STOP_LINE = "Progress stopped updating because Civis asked for the page to be reloaded. " +
    "Reload this page to see the latest progress.";
  var JSON_TYPE = /^\s*application\/([a-z0-9.+-]+\+)?json\s*(;|$)/i;

  function isJson(response) {
    var headers = response.headers;
    var type = headers && typeof headers.get === "function" ? headers.get("Content-Type") : null;
    return typeof type === "string" && JSON_TYPE.test(type);
  }

  // The answer's JSON object, or null when the answer is not ok, not JSON, or does not parse as a JSON object.
  function readJson(response) {
    if (!response || !response.ok || !isJson(response) || typeof response.text !== "function") {
      return Promise.resolve(null);
    }
    return response.text().then(function (text) {
      var data;
      try {
        data = JSON.parse(text);
      } catch (error) {
        return null;
      }
      return data !== null && typeof data === "object" && !Array.isArray(data) ? data : null;
    }, function () {
      return null;
    });
  }

  function storageKey() {
    return "pmsc-refresh:" + window.location.pathname;
  }

  // The saved count and last reload time, or null when sessionStorage is not available or throws.
  function load() {
    var raw;
    try {
      raw = window.sessionStorage.getItem(storageKey());
    } catch (error) {
      return null;
    }
    var saved = {};
    try {
      saved = raw ? JSON.parse(raw) || {} : {};
    } catch (error) {
      saved = {}; // A value this script did not write: start again.
    }
    return { count: Math.max(0, Math.floor(Number(saved.count) || 0)), last: Number(saved.last) || 0 };
  }

  function save(state) {
    try {
      window.sessionStorage.setItem(storageKey(), JSON.stringify(state));
    } catch (error) {
      return false;
    }
    var check = load(); // Reload only when the new count was really kept, so a loop is not possible.
    return check !== null && check.count === state.count && check.last === state.last;
  }

  // options.show(text): shows the stop line in the page's status area. options.stopped(): stops the page's polling.
  function create(options) {
    var stopped = false;
    var waiting = false; // A turned-away poll is waiting for a reload.
    var posts = 0; // POSTs waiting for their answer.

    function stop() {
      if (stopped) {
        return;
      }
      stopped = true;
      waiting = false;
      if (options.stopped) {
        options.stopped();
      }
      if (options.show) {
        options.show(STOP_LINE);
      }
    }

    function tryReload() {
      if (!waiting || stopped || posts > 0 || document.hidden) {
        return;
      }
      var state = load();
      if (state === null || state.count >= MAX_RELOADS) {
        stop();
        return;
      }
      var now = Date.now();
      if (state.last && now >= state.last && now - state.last < RELOAD_GAP_MS) {
        return; // Reloaded less than a minute ago: the next turned-away poll tries again.
      }
      if (!save({ count: state.count + 1, last: now })) {
        stop();
        return;
      }
      waiting = false;
      window.location.reload();
    }

    function turnedAway() {
      if (stopped) {
        return;
      }
      var state = load();
      if (state === null || state.count >= MAX_RELOADS) {
        stop(); // No sessionStorage, or 3 reloads with no successful poll: say so now, even on a hidden page.
        return;
      }
      waiting = true;
      tryReload();
    }

    function succeeded() {
      waiting = false;
      var state = load();
      if (state !== null && state.count > 0) {
        save({ count: 0, last: state.last }); // The last reload time still counts for the one-a-minute limit.
      }
    }

    if (typeof document.addEventListener === "function") {
      document.addEventListener("visibilitychange", tryReload);
    }

    return {
      // GET a status URL. Resolves to the app's JSON object, or null when the request was turned away.
      poll: function (url) {
        return window.fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
          .then(readJson, function () {
            return null;
          })
          .then(function (data) {
            if (data === null) {
              turnedAway();
            } else {
              succeeded();
            }
            return data;
          });
      },
      // POST once. Resolves to the app's JSON object, or null. A turned-away POST is never sent again and never
      // reloads the page; no reload happens while it waits for its answer.
      post: function (url, body) {
        posts += 1;
        return window.fetch(url, {
          method: "POST",
          body: body,
          headers: { Accept: "application/json" },
          credentials: "same-origin"
        })
          .then(readJson, function () {
            return null;
          })
          .then(function (data) {
            posts -= 1;
            tryReload();
            return data;
          });
      },
      isStopped: function () {
        return stopped;
      }
    };
  }

  window.pmscProxyRefresh = { create: create, STOP_LINE: STOP_LINE };
})();
