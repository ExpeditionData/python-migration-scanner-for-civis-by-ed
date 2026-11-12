// Shared by scan-status.js (Scan history) and run-scan.js (the Overview's Run scan button). Loaded before both.
//
// Civis's proxy in front of the Service uses a cookie that lasts about 5 to 6 minutes. Only a reload of the whole
// Civis page renews it: a background request does not, and neither does a reload of the app's frame, which then
// lands on Civis's "Cookie expired." page and the app's code never runs again (docs/PLAN.md 17.8 and 17.9). Once
// the cookie expires, the proxy answers a status request with its own page instead of the app's JSON. A request
// counts as turned away when the fetch rejects (a network or CORS failure), the response is not ok, its
// Content-Type is not JSON, or its body does not parse as a JSON object.
//
// The page never reloads itself because a request was turned away. After 2 turned-away polls in a row, polling
// stops and one line in the page's status area says to reload the whole Civis page. A successful poll resets the
// count. A hidden page behaves the same as a visible one. Nothing is kept between page loads (no browser storage).
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP). The caller shows the line with
// textContent only.
(function () {
  "use strict";

  var MAX_TURNED_AWAY = 2;
  var STOP_LINE = "Progress stopped updating because the Civis session for this page expired. " +
    "To see the latest progress, reload the whole Civis page with the browser's reload button (F5 on Windows). " +
    "Reloading only this frame does not help.";
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

  function send(url, options) {
    return window.fetch(url, options).then(readJson, function () {
      return null;
    });
  }

  // options.show(text): shows the stop line in the page's status area. options.stopped(): stops the page's polling.
  function create(options) {
    var stopped = false;
    var turnedAway = 0; // Turned-away polls in a row.

    function stop() {
      if (stopped) {
        return;
      }
      stopped = true;
      if (options.stopped) {
        options.stopped();
      }
      if (options.show) {
        options.show(STOP_LINE);
      }
    }

    return {
      // GET a status URL. Resolves to the app's JSON object, or null when the request was turned away.
      poll: function (url) {
        return send(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
          .then(function (data) {
            if (data === null) {
              turnedAway += 1;
              if (turnedAway >= MAX_TURNED_AWAY) {
                stop();
              }
            } else {
              turnedAway = 0;
            }
            return data;
          });
      },
      // POST once. Resolves to the app's JSON object, or null when it was turned away. Never sent again, and not
      // counted with the polls.
      post: function (url, body) {
        return send(url, {
          method: "POST",
          body: body,
          headers: { Accept: "application/json" },
          credentials: "same-origin"
        });
      },
      isStopped: function () {
        return stopped;
      }
    };
  }

  window.pmscStatusRequests = { create: create, STOP_LINE: STOP_LINE, MAX_TURNED_AWAY: MAX_TURNED_AWAY };
})();
