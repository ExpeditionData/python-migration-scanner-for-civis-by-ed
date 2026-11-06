// Shows "Working. This can take up to a minute." as soon as a slow action starts: the "Set up results storage"
// button, opening the Setup page, and building the test summary. Each of those reads Civis or the results
// database, which can take a while, and the browser shows nothing until the next page arrives.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP). The text is set with
// textContent only. Without JavaScript, every action still works; only the message is missing.
(function () {
  "use strict";

  var DEFAULT_MESSAGE = "Working. This can take up to a minute.";
  var region = document.querySelector("[data-busy-region]");
  if (!region) {
    return;
  }

  function show(trigger) {
    region.textContent = trigger.getAttribute("data-busy") || DEFAULT_MESSAGE;
    region.hidden = false;
    trigger.setAttribute("aria-busy", "true");
  }

  function hide() {
    region.hidden = true;
    region.textContent = "";
  }

  // A form with data-busy: show the message when it is sent.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!event.defaultPrevented && form && form.hasAttribute && form.hasAttribute("data-busy")) {
      show(form);
    }
  });

  // A link with data-busy: show the message for a plain click (not one that opens a new tab or window).
  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey ||
        event.altKey) {
      return;
    }
    var link = event.target && event.target.closest ? event.target.closest("a[data-busy]") : null;
    if (link && link.target !== "_blank") {
      show(link);
    }
  });

  // Back to this page from the browser's history: the action is over.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      hide();
    }
  });
})();
