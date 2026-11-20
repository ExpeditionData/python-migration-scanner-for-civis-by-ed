// Returns to the same app page after the whole Civis page is reloaded (docs/PLAN.md 17.19, Part B).
//
// Civis's proxy cookie for the app expires a few minutes after the whole Civis page loads. After that, a click on
// any app link shows Civis's "Cookie expired." page in the frame, and only a reload of the whole Civis page (F5)
// fixes it. F5 always opens the app on the Overview. So the app remembers the last app page in the browser, and
// when Civis loads the Overview fresh, it goes back to that page.
//
// One localStorage entry on the app's own origin, "pmsc-return-page": JSON {"path": <path and query>, "saved":
// <milliseconds>}, at most 2,000 characters. Every use of storage is inside try/catch: when storage is missing,
// throws, or holds a value that does not parse, the script does nothing and the app opens on the Overview.
//
// The pages it remembers come from the script tag (data-return-overview and data-return-pages, from url_for), so
// nothing assumes the app is served at "/". They are GET pages only: never a POST, a download, /api/..., or
// /healthz.
//   - Saving: each remembered page saves its own path and query when it loads. A plain left click on a same-origin
//     link to a remembered page (same tab, no modifier key, not a download, not a hash link) saves the link's path
//     and query, and so does a GET form sent to a remembered page, so a click that lands on "Cookie expired." still
//     has its target saved. Nothing is saved while the Findings checklist has unsaved changes and the browser will
//     ask before leaving.
//   - Going back: only on a load of the Overview's own path whose referrer is empty or not on the app's origin
//     (Civis loaded the frame; the app sends Referrer-Policy: same-origin, so a click or a redirect inside the app
//     always has an app referrer). The entry must be at most 30 minutes old, for a remembered page other than the
//     Overview, and a relative path on the app's origin. Then location.replace goes there, with the query as saved.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP).
(function () {
  "use strict";

  var KEY = "pmsc-return-page";
  var MAX_LENGTH = 2000;
  var MAX_AGE_MS = 30 * 60 * 1000;
  var tag = document.querySelector("script[data-return-pages]");
  if (!tag) {
    return;
  }
  var overview = tag.getAttribute("data-return-overview") || "";
  var pages = (tag.getAttribute("data-return-pages") || "").split(/\s+/).filter(Boolean);
  if (overview.charAt(0) !== "/") {
    return;
  }
  var root = overview.charAt(overview.length - 1) === "/" ? overview : overview + "/";

  function here() {
    try {
      return new URL(window.location.href);
    } catch (error) {
      return null;
    }
  }

  function storage() {
    try {
      var store = window.localStorage;
      return store && typeof store.getItem === "function" ? store : null;
    } catch (error) {
      return null;
    }
  }

  function remembered(pathname) {
    return pathname === overview || pages.indexOf(pathname) >= 0;
  }

  // The path and query of a same-origin http(s) URL for a remembered page, or null.
  function rememberedTarget(url) {
    var page = here();
    if (!page || url.origin !== page.origin || (url.protocol !== "https:" && url.protocol !== "http:")) {
      return null;
    }
    return remembered(url.pathname) ? url.pathname + url.search : null;
  }

  function save(path) {
    var store = storage();
    if (!store) {
      return;
    }
    try {
      var value = JSON.stringify({ path: path, saved: Date.now() });
      if (value.length <= MAX_LENGTH) {
        store.setItem(KEY, value);
      } else {
        store.removeItem(KEY); // Too long to keep: the last page is unknown, so F5 opens the Overview.
      }
    } catch (error) {
      // Storage refused the write: the app behaves as if nothing were saved.
    }
  }

  // The saved path to go back to, or null.
  function savedReturn() {
    var store = storage();
    if (!store) {
      return null;
    }
    var raw;
    var entry;
    try {
      raw = store.getItem(KEY);
    } catch (error) {
      return null;
    }
    if (typeof raw !== "string" || raw.length > MAX_LENGTH) {
      return null;
    }
    try {
      entry = JSON.parse(raw);
    } catch (error) {
      return null;
    }
    if (!entry || typeof entry !== "object" || typeof entry.path !== "string" || typeof entry.saved !== "number") {
      return null;
    }
    var age = Date.now() - entry.saved;
    if (!(age >= 0 && age <= MAX_AGE_MS)) {
      return null;
    }
    var path = entry.path;
    // A relative path under the app's root: no scheme, no "//" at the start, no backslash, and no control
    // characters or spaces (a browser drops tabs and newlines from a URL, which could turn "/\t/x" into "//x").
    if (path.indexOf(root) !== 0 || path.charAt(1) === "/" || path.indexOf("\\") >= 0 || /[\s\x00-\x1f\x7f]/.test(path)) {
      return null;
    }
    var page = here();
    var url;
    try {
      url = new URL(path, page ? page.href : undefined);
    } catch (error) {
      return null;
    }
    if (!page || url.origin !== page.origin || url.pathname + url.search !== path) {
      return null;
    }
    return url.pathname !== overview && pages.indexOf(url.pathname) >= 0 ? path : null;
  }

  function fromOutsideTheApp() {
    var referrer = document.referrer || "";
    if (!referrer) {
      return true;
    }
    var page = here();
    try {
      return !page || new URL(referrer).origin !== page.origin;
    } catch (error) {
      return true;
    }
  }

  // True when the Findings checklist (or any form marked dirty) has unsaved changes other than ``form``.
  function unsavedChangesElsewhere(form) {
    var dirty = document.querySelector('form[data-dirty="true"]');
    return dirty !== null && dirty !== form;
  }

  var page = here();
  if (!page) {
    return;
  }
  if (page.pathname === overview && fromOutsideTheApp()) {
    var back = savedReturn();
    if (back !== null) {
      window.location.replace(back);
      return;
    }
  }
  if (remembered(page.pathname)) {
    save(page.pathname + page.search);
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey ||
        event.altKey) {
      return;
    }
    var link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || link.hasAttribute("download") || unsavedChangesElsewhere(null)) {
      return;
    }
    var target = link.getAttribute("target");
    var href = link.getAttribute("href");
    if ((target && target.toLowerCase() !== "_self") || !href || href.charAt(0) === "#") {
      return;
    }
    var url;
    try {
      url = new URL(href, window.location.href);
    } catch (error) {
      return;
    }
    var current = here();
    if (url.hash && current && url.pathname === current.pathname && url.search === current.search) {
      return; // A place on this page.
    }
    var path = rememberedTarget(url);
    if (path !== null) {
      save(path);
    }
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented || !form || !form.getAttribute || unsavedChangesElsewhere(form)) {
      return;
    }
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") {
      return;
    }
    var url;
    var params;
    try {
      url = new URL(form.getAttribute("action") || window.location.href, window.location.href);
      params = new URLSearchParams();
      new window.FormData(form).forEach(function (value, name) {
        if (typeof value === "string") {
          params.append(name, value);
        }
      });
      var submitter = event.submitter;
      if (submitter && submitter.getAttribute && submitter.getAttribute("name")) {
        params.append(submitter.getAttribute("name"), submitter.getAttribute("value") || "");
      }
    } catch (error) {
      return;
    }
    var path = rememberedTarget(url);
    if (path !== null) {
      var query = params.toString();
      save(url.pathname + (query ? "?" + query : ""));
    }
  });
})();
