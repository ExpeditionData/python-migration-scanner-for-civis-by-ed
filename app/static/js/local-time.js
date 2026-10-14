// Shows times in the viewer's local time zone, month first, with the time zone
// abbreviation, for example "October 12, 2026 at 10:14 AM EDT".
// The server writes each time as <time datetime="...Z" data-local="datetime|date">
// with UTC text, so pages without JavaScript still show the time in UTC.
// Plain JavaScript, loaded from the app itself (no inline scripts; see the CSP).
(function () {
  "use strict";

  var LOCALE = "en-US"; // American English, month first.

  function part(parts, type) {
    for (var i = 0; i < parts.length; i += 1) {
      if (parts[i].type === type) {
        return parts[i].value;
      }
    }
    return "";
  }

  function formatDateTime(date) {
    var parts = new Intl.DateTimeFormat(LOCALE, {
      month: "long",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "short"
    }).formatToParts(date);
    return part(parts, "month") + " " + part(parts, "day") + ", " + part(parts, "year") + " at " +
      part(parts, "hour") + ":" + part(parts, "minute") + " " + part(parts, "dayPeriod").toUpperCase() + " " +
      part(parts, "timeZoneName");
  }

  function formatDate(date) {
    var parts = new Intl.DateTimeFormat(LOCALE, { month: "short", day: "numeric", year: "numeric" }).formatToParts(date);
    return part(parts, "month") + " " + part(parts, "day") + ", " + part(parts, "year");
  }

  function convert(root) {
    if (typeof Intl === "undefined" || !Intl.DateTimeFormat || !Intl.DateTimeFormat.prototype.formatToParts) {
      return; // Keep the UTC text.
    }
    var items = (root || document).querySelectorAll("time[data-local]");
    for (var i = 0; i < items.length; i += 1) {
      var el = items[i];
      var date = new Date(el.getAttribute("datetime"));
      if (isNaN(date.getTime())) {
        continue;
      }
      var utcText = el.textContent;
      el.textContent = el.getAttribute("data-local") === "date" ? formatDate(date) : formatDateTime(date);
      el.setAttribute("title", utcText);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      convert(document);
    });
  } else {
    convert(document);
  }
})();
