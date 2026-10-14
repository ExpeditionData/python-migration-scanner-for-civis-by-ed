"""Times in the viewer's local time zone (app/static/js/local-time.js), with UTC as the fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import create_app, views
from app.services import local_services
from scanner.synthetic.preview import build_preview

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "app" / "static" / "js" / "local-time.js"
SECRET = "local-time-test-secret-key-long-enough-01"  # pragma: allowlist secret  gitleaks:allow

# A stub page with three <time> elements; runs the real script under Node.
HARNESS = """
const fs = require("fs");
const items = JSON.parse(process.argv[2]).map((t) => ({
  attrs: { datetime: t.datetime, "data-local": t.kind },
  textContent: t.text,
  getAttribute(name) { return this.attrs[name] ?? null; },
  setAttribute(name, value) { this.attrs[name] = value; },
}));
global.document = {
  readyState: "complete",
  querySelectorAll() { return items; },
  addEventListener() {},
};
eval(fs.readFileSync(process.argv[1], "utf8"));
console.log(JSON.stringify(items.map((i) => ({ text: i.textContent, title: i.attrs.title || null }))));
"""


def run_script(times: list[dict], tz: str) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    result = subprocess.run(
        [node, "-e", HARNESS, str(SCRIPT), json.dumps(times)],
        capture_output=True, text=True, check=True, env={**os.environ, "TZ": tz}, timeout=30,
    )
    return json.loads(result.stdout)


def test_script_shows_local_time_with_zone_abbreviation():
    times = [
        {"datetime": "2026-10-12T14:14:00Z", "kind": "datetime", "text": "October 12, 2026 at 2:14 PM UTC"},
        {"datetime": "2026-04-02T03:00:00Z", "kind": "date", "text": "Apr 2, 2026"},
        {"datetime": "not a time", "kind": "datetime", "text": "Not recorded"},
    ]
    new_york = run_script(times, "America/New_York")
    assert new_york[0] == {"text": "October 12, 2026 at 10:14 AM EDT", "title": "October 12, 2026 at 2:14 PM UTC"}
    assert new_york[1] == {"text": "Apr 1, 2026", "title": "Apr 2, 2026"}  # The local date can differ.
    assert new_york[2] == {"text": "Not recorded", "title": None}  # Unreadable times keep the server text.
    chicago_winter = run_script([{"datetime": "2026-01-15T18:05:00Z", "kind": "datetime", "text": "x"}], "America/Chicago")
    assert chicago_winter[0]["text"] == "January 15, 2026 at 12:05 PM CST"


def test_when_values_carry_iso_and_utc_text():
    value = views.when("2026-10-12 14:14:00")
    assert (value.iso, value.text, value.kind, str(value)) == (
        "2026-10-12T14:14:00Z", "October 12, 2026 at 2:14 PM UTC", "datetime", "October 12, 2026 at 2:14 PM UTC"
    )
    assert views.when_date("2026-04-02T03:00:00Z").kind == "date"
    assert views.when(None).iso is None and views.when(None).text == "Not recorded"


def test_pages_mark_times_for_local_display_and_keep_utc_text(tmp_path, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    build_preview(tmp_path, now=datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc))
    app = create_app({"SECRET_KEY": SECRET, "SERVICES": local_services(tmp_path), "TESTING": True})
    client = app.test_client()
    pattern = re.compile(r'<time datetime="\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ" data-local="(datetime|date)">([^<]+)</time>')
    for url, kinds in (("/", {"datetime"}), ("/scan", {"datetime"}), ("/inventory", {"date"}),
                       ("/findings?status=fixed", {"date"})):
        body = client.get(url).get_data(as_text=True)
        assert 'src="/static/js/local-time.js"' in body
        found = pattern.findall(body)
        assert found and kinds <= {kind for kind, _ in found}, url
        assert all(text.endswith(" UTC") for kind, text in found if kind == "datetime")
    banner = client.get("/").get_data(as_text=True)
    assert re.search(r"A scan is running\. Started <time [^>]+>[^<]+ UTC</time>\.", banner)
    assert client.get("/static/js/local-time.js").status_code == 200
