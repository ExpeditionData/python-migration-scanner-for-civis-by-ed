// Findings checklist: counts unsaved status changes and marks changed dropdowns.
// The Save button submits one form, which the server saves as one batch.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP).
(function () {
  "use strict";

  var form = document.getElementById("checklist-form");
  var label = document.querySelector('[data-field="changed-text"]');
  if (!form || !label) {
    return;
  }
  var selects = form.querySelectorAll("select.status-select");

  function refresh() {
    var changed = 0;
    selects.forEach(function (select) {
      var isChanged = select.value !== select.getAttribute("data-original");
      select.classList.toggle("changed", isChanged);
      if (isChanged) {
        changed += 1;
      }
    });
    if (changed === 0) {
      label.textContent = "No unsaved changes";
    } else {
      label.textContent = changed + (changed === 1 ? " unsaved change" : " unsaved changes");
    }
    form.setAttribute("data-dirty", changed > 0 ? "true" : "false");
  }

  selects.forEach(function (select) {
    select.addEventListener("change", refresh);
  });

  var submitting = false;
  form.addEventListener("submit", function () {
    submitting = true;
  });
  window.addEventListener("beforeunload", function (event) {
    if (!submitting && form.getAttribute("data-dirty") === "true") {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  refresh();
})();
