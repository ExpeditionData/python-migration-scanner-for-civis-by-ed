// Copy button for the test summary. Vendored, no inline script (the CSP allows 'self' only).
// Without JavaScript the button stays hidden and the text can be selected and copied by hand.
(function () {
  "use strict";

  function ready() {
    var button = document.getElementById("copy-summary");
    var text = document.getElementById("summary-text");
    var status = document.getElementById("copy-status");
    if (!button || !text || !status) {
      return;
    }
    button.hidden = false;
    status.textContent = "";

    function done(copied) {
      status.textContent = copied
        ? "Copied. Paste it into your message."
        : "Copy did not work here. Select the text and press Ctrl+C (Cmd+C on a Mac).";
    }

    function fallback() {
      text.focus();
      text.select();
      var copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (error) {
        copied = false;
      }
      done(copied);
    }

    button.addEventListener("click", function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text.value).then(function () { done(true); }, fallback);
      } else {
        fallback();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready);
  } else {
    ready();
  }
})();
