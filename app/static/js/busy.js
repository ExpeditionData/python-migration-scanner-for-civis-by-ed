// A short busy cue as soon as the browser starts loading another app page in the same tab, because most pages
// take a few seconds and the browser shows nothing until the next page arrives (docs/PLAN.md 17.9, Part D):
//   - a plain click (left button, no modifier keys, default not prevented) on a same-origin link that loads
//     another app page in this tab, and the sending of a form (the Inventory and Findings filters, the Findings
//     "Save changes" form, the test summary's run choice, and "Set up results storage");
//   - the cue (docs/PLAN.md 17.11, Part A): the "is-busy-trigger" class on the clicked link or on the sent form's
//     submit button (gray, no underline or hover change, and the progress cursor, from app.css), aria-busy="true"
//     on main, and the "is-loading" class on body (the progress cursor and dimmed main content, from app.css). The
//     submit button is not disabled, so the form still sends its name and value. No text is added;
//   - no cue for a link that opens elsewhere (a target other than _self), a download link (download or
//     data-no-busy, as on every Download CSV link, since the page stays), a link to a place on this page (the skip
//     link), the Run scan form (run-scan.js handles it), or while the Findings checklist has unsaved changes and
//     the browser will ask before leaving;
//   - a second click while the cue shows adds nothing.
// Links and forms with data-busy also show "Working. This can take up to a minute." in the page's busy region,
// as before: the Scan history and Setup links, Build test summary, Back to Setup, the summary's run choice, and the
// "Set up results storage" button. Coming back to the page from the browser's history removes everything.
// Plain JavaScript, loaded from the app itself (no inline scripts or styles; see the CSP). The message is set with
// textContent only. Without JavaScript, every link and form still works; only the cue is missing.
(function () {
  "use strict";

  var DEFAULT_MESSAGE = "Working. This can take up to a minute.";
  var TRIGGER_CLASS = "is-busy-trigger";
  var BODY_CLASS = "is-loading";
  var region = document.querySelector("[data-busy-region]");
  var loading = false;
  var trigger = null; // The link or submit button given TRIGGER_CLASS, to clear on pageshow.
  var marked = []; // Links and forms given aria-busy, to clear on pageshow.

  function mainElement() {
    return document.querySelector("main");
  }

  function bodyClasses() {
    return document.body && document.body.classList ? document.body.classList : null;
  }

  // Shows the cue once. ``source`` is the clicked link or the sent form (it may have data-busy), and ``shown`` is
  // the link or submit button that turns gray (none when a form has no submit button).
  function start(source, shown) {
    if (loading) {
      return; // Already loading: a second click adds nothing.
    }
    loading = true;
    if (shown && shown.classList) {
      shown.classList.add(TRIGGER_CLASS);
      trigger = shown;
    }
    var classes = bodyClasses();
    if (classes) {
      classes.add(BODY_CLASS);
    }
    var main = mainElement();
    if (main) {
      main.setAttribute("aria-busy", "true");
    }
    if (region && source.hasAttribute("data-busy")) {
      region.textContent = source.getAttribute("data-busy") || DEFAULT_MESSAGE;
      region.hidden = false;
      source.setAttribute("aria-busy", "true");
      marked.push(source);
    }
  }

  function clear() {
    if (trigger !== null) {
      trigger.classList.remove(TRIGGER_CLASS);
    }
    trigger = null;
    loading = false;
    var classes = bodyClasses();
    if (classes) {
      classes.remove(BODY_CLASS);
    }
    var main = mainElement();
    if (main) {
      main.removeAttribute("aria-busy");
    }
    marked.forEach(function (el) {
      el.removeAttribute("aria-busy");
    });
    marked = [];
    if (region) {
      region.hidden = true;
      region.textContent = "";
    }
  }

  // True when the Findings checklist (or any form marked dirty) has unsaved changes other than ``form``: the
  // browser asks before leaving, and the viewer may stay, so no cue.
  function unsavedChangesElsewhere(form) {
    var dirty = document.querySelector('form[data-dirty="true"]');
    return dirty !== null && dirty !== form;
  }

  // True for a link that loads another app page in this tab.
  function loadsAnotherAppPage(link) {
    if (link.hasAttribute("download") || link.hasAttribute("data-no-busy")) {
      return false;
    }
    var target = link.getAttribute("target");
    if (target && target.toLowerCase() !== "_self") {
      return false;
    }
    var href = link.getAttribute("href");
    if (!href || href.charAt(0) === "#") {
      return false;
    }
    var url;
    var here;
    try {
      url = new URL(href, window.location.href);
      here = new URL(window.location.href);
    } catch (error) {
      return false;
    }
    if (url.origin !== here.origin || (url.protocol !== "https:" && url.protocol !== "http:")) {
      return false;
    }
    return !(url.hash && url.pathname === here.pathname && url.search === here.search); // A place on this page.
  }

  function submitButton(event, form) {
    if (event.submitter) {
      return event.submitter;
    }
    var inside = form.querySelector('button[type="submit"]');
    if (inside) {
      return inside;
    }
    var id = form.getAttribute("id");
    return id ? document.querySelector('button[form="' + id + '"]') : null;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || !form || !form.hasAttribute || form.hasAttribute("data-no-busy")) {
      return;
    }
    if ((form.closest && form.closest("[data-run-scan]")) || unsavedChangesElsewhere(form)) {
      return; // Run scan keeps run-scan.js's behavior.
    }
    start(form, submitButton(event, form));
  });

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey ||
        event.altKey) {
      return;
    }
    var link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || !loadsAnotherAppPage(link) || unsavedChangesElsewhere(null)) {
      return;
    }
    start(link, link);
  });

  // Back to this page from the browser's history: the page load it started is over.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      clear();
    }
  });
})();
