"""Shape result rows into what each page shows. No Flask and no I/O here.

Terms used on the pages:

- A *scan* is one scanner run (one run ID in the tables).
- A scan's results are shown only when it finished with status "succeeded".
  The newest such scan is the *current* scan.
- An object is *affected* if at least one of its findings is affected (it runs
  on a Civis default image), and *advisory* if it has findings but none are
  affected.
- Checklist status is per object and rule; the newest checklist row wins.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from common.models import ObjectType
from common.rule_loader import RuleSet
from app.backends.base import JobRun, Progress

PAGE_SIZE = 50
STATUSES = ("open", "fixed", "ignored")
TYPE_LABELS = {
    "python_script": "Python script",
    "container_script": "Container script",
    "custom_script": "Custom script",
    "dbt_script": "dbt script",
    "notebook": "Notebook",
    "service": "Service",
    "workflow": "Workflow",
}
TYPE_LABELS_PLURAL = {
    "python_script": "Python scripts",
    "container_script": "Container scripts",
    "custom_script": "Custom scripts",
    "dbt_script": "dbt scripts",
    "notebook": "Notebooks",
    "service": "Services",
    "workflow": "Workflows with inline code",
}
SCAN_STATUS_LABELS = {"scanned": "Scanned", "partial": "Partial", "not_scanned": "Not scanned", "pending": "Not scanned"}
LANGUAGE_LABELS = {"r": "R", "sql": "SQL", "javascript": "JavaScript", "dbt": "dbt on a custom or pinned image"}


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def format_datetime(value: Any) -> str:
    """Month-first, for example "October 12, 2026 at 2:14 PM UTC"."""
    moment = parse_time(value)
    if moment is None:
        return "Not recorded"
    moment = moment.astimezone(timezone.utc)
    hour = moment.strftime("%I").lstrip("0") or "12"
    return f"{moment.strftime('%B')} {moment.day}, {moment.year} at {hour}:{moment.strftime('%M %p')} UTC"


def format_date(value: Any) -> str:
    moment = parse_time(value)
    if moment is None:
        return "Never"
    return f"{moment.strftime('%b')} {moment.day}, {moment.year}"


def format_duration(start: Any, end: Any) -> str:
    begin, finish = parse_time(start), parse_time(end)
    if begin is None or finish is None:
        return "Not recorded"
    minutes = max(int((finish - begin).total_seconds() // 60), 0)
    if minutes < 1:
        return "Under 1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} hr {minutes % 60} min"


@dataclass(frozen=True)
class When:
    """A time for a template: UTC text for pages without JavaScript, plus the ISO
    time that app/static/js/local-time.js turns into the viewer's local time."""

    iso: str | None
    text: str
    kind: str = "datetime"  # "datetime" or "date"

    def __str__(self) -> str:
        return self.text


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def when(value: Any) -> When:
    moment = parse_time(value)
    return When(_iso(moment) if moment else None, format_datetime(value), "datetime")


def when_date(value: Any) -> When:
    moment = parse_time(value)
    return When(_iso(moment) if moment else None, format_date(value), "date")


def number(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


# ---------------------------------------------------------------------------
# Scans and the overall situation
# ---------------------------------------------------------------------------


@dataclass
class ScanRun:
    run_id: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    civis_run_id: int | None
    scanning_user: str | None
    parameters: dict
    ordinal: int = 0

    @property
    def label(self) -> str:
        return f"Run {self.ordinal}"


def summarize_scan_runs(rows: list[dict]) -> list[ScanRun]:
    """One entry per run ID, newest first, numbered Run 1, Run 2, ... by start time."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("run_id"):
            grouped[row["run_id"]].append(row)
    scans = []
    for run_id, items in grouped.items():
        newest = max(items, key=lambda r: (str(r.get("recorded_at") or ""), r.get("status") != "running"))
        final = [r for r in items if r.get("status") != "running"]
        try:
            parameters = json.loads(newest.get("parameters") or "{}")
        except ValueError:
            parameters = {}
        scans.append(
            ScanRun(
                run_id=run_id,
                status=newest.get("status") or "running",
                started_at=parse_time(min((r.get("started_at") or "" for r in items), default=None)),
                finished_at=parse_time(final[-1].get("finished_at")) if final else None,
                civis_run_id=newest.get("civis_run_id"),
                scanning_user=newest.get("scanning_user_name"),
                parameters=parameters if isinstance(parameters, dict) else {},
            )
        )
    scans.sort(key=lambda s: s.started_at or datetime.min.replace(tzinfo=timezone.utc))
    for index, scan in enumerate(scans, start=1):
        scan.ordinal = index
    return list(reversed(scans))


@dataclass
class Situation:
    results_state: str  # ok, no_tables, schema_missing, not_configured, job_unreadable, error
    job_configured: bool
    jobs_error: bool = False
    runs: list[JobRun] = field(default_factory=list)
    scans: list[ScanRun] = field(default_factory=list)
    settings_problem: str | None = None
    job_problem: str | None = None  # Why the scanner job's settings cannot be used (app/job_settings.py).
    # The window the next scan will use (the job's LOOKBACK_DAYS, or 400 when not set); None when the saved value
    # is not valid. Only set when the job was read and is this tool's scanner job.
    job_lookback: int | None = None
    job_lookback_read: bool = False

    @property
    def active_run(self) -> JobRun | None:
        return next((r for r in self.runs if r.active), None)

    @property
    def current(self) -> ScanRun | None:
        """The newest scan whose results can be shown."""
        return next((s for s in self.scans if s.status == "succeeded"), None)

    def scan_for(self, run: JobRun | None) -> ScanRun | None:
        if run is None:
            return None
        return next((s for s in self.scans if s.civis_run_id == run.id), None)

    @property
    def latest_finished_run(self) -> JobRun | None:
        return next((r for r in self.runs if not r.active), None)

    @property
    def last_outcome(self) -> str | None:
        """How the newest finished scan ended: succeeded, failed, tables_failed, or cancelled."""
        run = self.latest_finished_run
        if run is not None:
            scan = self.scan_for(run)
            if scan is not None and scan.status == "tables_failed":
                return "tables_failed"
            if run.state in ("failed", "cancelled"):
                return run.state
            return "succeeded"
        if self.scans and self.scans[0].status == "tables_failed":
            return "tables_failed"
        return None

    @property
    def job_readable(self) -> bool:
        return self.job_configured and self.job_problem != "job_unreadable"

    @property
    def storage_button(self) -> bool:
        """Show "Set up results storage" while the schema or its tables are missing."""
        return self.results_state in ("schema_missing", "no_tables")

    @property
    def can_start(self) -> bool:
        """Run scan stays off while the results schema is missing (the button creates it)."""
        return self.active_run is None and self.can_start_when_idle

    @property
    def can_start_when_idle(self) -> bool:
        """Run scan would be on if no run were active (the Overview's button comes back when the run ends)."""
        return (self.job_readable and not self.jobs_error and self.results_state != "schema_missing"
                and self.job_problem != "not_scanner_job")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def _object_impacts(findings: list[dict]) -> dict[int, str]:
    impact: dict[int, str] = {}
    for f in findings:
        oid = f["object_id"]
        if f.get("impact") == "affected":
            impact[oid] = "affected"
        else:
            impact.setdefault(oid, "advisory")
    return impact


def current_checklist(rows: list[dict]) -> dict[tuple[int, str], dict]:
    """Newest checklist row per object and rule."""
    current: dict[tuple[int, str], dict] = {}
    for row in rows:
        key = (row.get("object_id"), row.get("rule_id"))
        if key[0] is None or not key[1]:
            continue
        existing = current.get(key)
        if existing is None or str(row.get("changed_at") or "") >= str(existing.get("changed_at") or ""):
            current[key] = row
    return current


def overview(objects: list[dict], findings: list[dict], coverage: list[dict], checklist: list[dict], rules: RuleSet) -> dict:
    impacts = _object_impacts(findings)
    by_type: dict[str, dict[str, int]] = {}
    for obj in objects:
        stats = by_type.setdefault(obj["object_type"], Counter())
        stats["found"] += 1
        status = obj.get("scan_status")
        stats["scanned"] += status == "scanned"
        stats["partial"] += status == "partial"
        stats["not_scanned"] += status in ("not_scanned", "pending")
        stats[impacts.get(obj["object_id"], "none")] += 1
    order = [t.value for t in ObjectType]
    types = [
        {"key": t, "label": TYPE_LABELS_PLURAL.get(t, t), **{k: by_type[t].get(k, 0) for k in
         ("found", "scanned", "partial", "not_scanned", "affected", "advisory")}}
        for t in order if t in by_type
    ]
    left_out = [
        {"type": TYPE_LABELS_PLURAL.get(r.get("object_type"), r.get("object_type")),
         "language": LANGUAGE_LABELS.get(r.get("language") or "", r.get("language") or "Unknown"),
         "count": r.get("found_count") or 0}
        for r in coverage if r.get("row_kind") == "not_python"
    ]
    hidden = sum(r.get("hidden_skipped_count") or 0 for r in coverage if r.get("row_kind") == "summary")
    # Scripts that did not run or change within LOOKBACK_DAYS (docs/PLAN.md 17.6). Template copies need no text:
    # they are counted once, as custom scripts.
    outside_window = sum(r.get("found_count") or 0 for r in coverage if r.get("row_kind") == "outside_window")
    # The scanner's fixed note when owner active status is only partly known or not known (row_kind "users").
    users_note = next((r.get("reason") for r in coverage if r.get("row_kind") == "users" and r.get("reason")), None)

    pairs = {(f["object_id"], f["rule_id"]) for f in findings}
    status_of = current_checklist(checklist)
    progress = Counter(_status(status_of.get(p)) for p in pairs)
    total_pairs = len(pairs)

    objects_by_rule: dict[str, set[int]] = defaultdict(set)
    for f in findings:
        objects_by_rule[f["rule_id"]].add(f["object_id"])
    issues = []
    for rule_id, ids in sorted(objects_by_rule.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]:
        rule = _rule(rules, rule_id)
        issues.append({
            "rule_id": rule_id, "title": rule.title if rule else rule_id,
            "confidence": rule.confidence.value.capitalize() if rule else "", "objects": len(ids),
        })

    return {
        "found": len(objects),
        "scanned": sum(1 for o in objects if o.get("scan_status") == "scanned"),
        "affected": sum(1 for v in impacts.values() if v == "affected"),
        "advisory": sum(1 for v in impacts.values() if v == "advisory"),
        "not_scanned": sum(1 for o in objects if o.get("scan_status") in ("not_scanned", "pending")),
        "partial": sum(1 for o in objects if o.get("scan_status") == "partial"),
        "types": types,
        "left_out": left_out,
        "hidden_skipped": hidden,
        "outside_window": outside_window,
        "users_note": users_note,
        "checklist": {
            "total": total_pairs, "objects": len({p[0] for p in pairs}),
            "open": progress.get("open", 0), "fixed": progress.get("fixed", 0), "ignored": progress.get("ignored", 0),
            "fixed_pct": _pct(progress.get("fixed", 0), total_pairs),
            "ignored_pct": _pct(progress.get("ignored", 0), total_pairs),
        },
        "issues": issues,
    }


def _days(days: int) -> str:
    return "1 day" if days == 1 else f"{days:,} days"


def outside_window_text(count: int, days: int | None) -> str | None:
    """The fixed sentence under "By object type" for scripts left out by the scan's LOOKBACK_DAYS."""
    if not count:
        return None
    window = f"the last {_days(days)}" if isinstance(days, int) and days > 0 else "the scan's LOOKBACK_DAYS window"
    if count == 1:
        return (f"1 script that has not run or changed in {window} was left out. To include it, raise LOOKBACK_DAYS "
                "on the scanner job.")
    return (f"{count:,} scripts that have not run or changed in {window} were left out. To include them, raise "
            "LOOKBACK_DAYS on the scanner job.")


def next_window_text(situation: "Situation") -> str | None:
    """Next to Run scan: which window a scan will use, from the scanner job's saved LOOKBACK_DAYS."""
    if not situation.job_lookback_read:
        return None
    if situation.job_lookback is None:
        return ("The scanner job's LOOKBACK_DAYS is not a whole number from 1 to 3,650, so a scan would stop before "
                "it starts. See line 4 on the Setup page.")
    return (f"A scan checks scripts that ran or changed in the last {_days(situation.job_lookback)} (LOOKBACK_DAYS "
            "on the scanner job).")


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def _status(row: dict | None) -> str:
    status = (row or {}).get("status")
    return status if status in STATUSES else "open"


def _rule(rules: RuleSet, rule_id: str):
    try:
        return rules.get(rule_id)
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def finding_pairs(findings: list[dict]) -> set[tuple[int, str]]:
    return {(f["object_id"], f["rule_id"]) for f in findings}


def history(situation: Situation, counts: dict[str, dict]) -> list[dict]:
    """Rows for the scan history table. ``counts`` maps run ID to affected, advisory, and pairs."""
    rows = []
    seen = set()
    completed = [s for s in reversed(situation.scans) if s.status == "succeeded" and s.run_id in counts]
    fixed_since: dict[str, int] = {}
    for previous, scan in zip(completed, completed[1:]):
        fixed_since[scan.run_id] = len(counts[previous.run_id]["pairs"] - counts[scan.run_id]["pairs"])

    def row_for(scan: ScanRun | None, run: JobRun | None) -> dict:
        started = (run.started_at or run.created_at) if run else None
        started = started or (scan.started_at if scan else None)
        finished = run.finished_at if run else (scan.finished_at if scan else None)
        label = _status_label(run, scan)
        data = counts.get(scan.run_id) if scan else None
        shows = scan is not None and scan.status == "succeeded" and data is not None
        return {
            "run": scan.label if scan else f"Civis run {run.id}",
            "started": when(started),
            "duration": "Running" if run and run.active else format_duration(started, finished),
            "status": label,
            "affected": number(data["affected"]) if shows else "n/a",
            "advisory": number(data["advisory"]) if shows else "n/a",
            "fixed": number(fixed_since.get(scan.run_id)) if shows and scan.run_id in fixed_since else "n/a",
            "civis_run_id": run.id if run else scan.civis_run_id if scan else None,
            "has_report": shows or (scan is not None and scan.status == "tables_failed"),
        }

    for run in situation.runs:
        scan = situation.scan_for(run)
        if scan:
            seen.add(scan.run_id)
        rows.append(row_for(scan, run))
    for scan in situation.scans:
        if scan.run_id not in seen:
            rows.append(row_for(scan, None))
    return rows


def _status_label(run: JobRun | None, scan: ScanRun | None) -> str:
    if run is not None:
        if run.state == "queued":
            return "Queued"
        if run.state == "running":
            return "In progress"
        if run.state == "cancelled":
            return "Cancelled"
        if scan is not None and scan.status == "tables_failed":
            return "Tables not saved (CSV report only)"
        if run.state == "failed":
            return "Failed"
        return "Completed"
    if scan is None:
        return "Unknown"
    return {
        "succeeded": "Completed", "tables_failed": "Tables not saved (CSV report only)",
        "failed": "Failed", "running": "Did not finish",
    }.get(scan.status, scan.status)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def workflow_names(value: Any) -> list[str]:
    if not value:
        return []
    try:
        entries = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    names = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and "name" in entry:
            names.append(str(entry["name"]))
        elif isinstance(entry, dict) and "more" in entry:
            names.append(f"and {entry['more']} more")
    return names


def image_text(obj: dict) -> str:
    if not obj.get("docker_image_name") and not obj.get("docker_image_tag"):
        return "Not known"
    name = obj.get("docker_image_name") or "Image not set"
    tag = obj.get("docker_image_tag")
    return f"{name} : {tag if tag else '(blank)'}"


def owner_text(name: str | None, active: bool | None) -> str:
    text = name or "Unknown owner"
    return f"{text} (deactivated)" if active is False else text


@dataclass
class Page:
    rows: list
    total: int
    page: int
    pages: int

    @property
    def first(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * PAGE_SIZE + 1

    @property
    def last(self) -> int:
        return min(self.page * PAGE_SIZE, self.total)


def paginate(rows: list, page: int) -> Page:
    pages = max((len(rows) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 1), pages)
    return Page(rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], len(rows), page, pages)


def inventory(objects: list[dict], findings: list[dict], filters: dict) -> list[dict]:
    counts = Counter(f["object_id"] for f in findings)
    query = (filters.get("q") or "").strip().lower()
    rows = []
    for obj in objects:
        default = obj.get("uses_default_image")
        default_key = "default" if default is True else "custom" if default is False else "unknown"
        status = obj.get("scan_status") or "pending"
        owner = owner_text(obj.get("owner_name"), obj.get("owner_active"))
        if query and query not in (obj.get("name") or "").lower() and query not in owner.lower():
            continue
        if filters.get("type") and obj.get("object_type") != filters["type"]:
            continue
        if filters.get("image") and default_key != filters["image"]:
            continue
        if filters.get("status") and status != filters["status"]:
            continue
        rows.append({
            "object_id": obj["object_id"],
            "name": obj.get("name") or f"Object {obj['object_id']}",
            "url": obj.get("url"),
            "type": TYPE_LABELS.get(obj.get("object_type"), obj.get("object_type")),
            "owner": owner,
            "image": image_text(obj),
            "default": {"default": "Yes", "custom": "No", "unknown": "Unknown"}[default_key],
            "default_key": default_key,
            "last_run": when_date(obj.get("last_run_at")) if obj.get("last_run_at") else "Never",
            "findings": counts.get(obj["object_id"], 0),
            "status": SCAN_STATUS_LABELS.get(status, status),
            "status_key": status,
            "reason": obj.get("scan_note") if status != "scanned" else None,
            "workflows": workflow_names(obj.get("workflows")),
            "archived": bool(obj.get("archived")),
        })
    rows.sort(key=lambda r: (r["name"].lower(), r["object_id"]))
    return rows


# ---------------------------------------------------------------------------
# Findings and checklist
# ---------------------------------------------------------------------------

_LEVELS = {"high": 3, "medium": 2, "low": 1}


def findings_rows(objects: list[dict], findings: list[dict], checklist: list[dict], rules: RuleSet, filters: dict) -> list[dict]:
    """One row per object and rule, the unit the checklist tracks, with every location listed."""
    by_object = {o["object_id"]: o for o in objects}
    status_of = current_checklist(checklist)
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for f in findings:
        grouped[(f["object_id"], f["rule_id"])].append(f)

    rows = []
    for (object_id, rule_id), items in grouped.items():
        obj = by_object.get(object_id, {})
        rule = _rule(rules, rule_id)
        impact = "affected" if any(i.get("impact") == "affected" for i in items) else (
            "advisory" if any(i.get("impact") == "advisory" for i in items) else "unknown")
        confidence = max((i.get("confidence") or "low" for i in items), key=lambda c: _LEVELS.get(c, 0))
        current = status_of.get((object_id, rule_id))
        status = _status(current)
        owner = owner_text(obj.get("owner_name"), obj.get("owner_active"))
        family = rule.family if rule else ("civis_v3" if rule_id.startswith("CV3-") else "python315")
        if filters.get("impact") and impact != filters["impact"]:
            continue
        if filters.get("ruleset") and family != filters["ruleset"]:
            continue
        if filters.get("confidence") and confidence != filters["confidence"]:
            continue
        wanted_status = filters.get("status", "open")
        if wanted_status != "all" and status != wanted_status:
            continue
        if filters.get("owner") and (obj.get("owner_name") or "") != filters["owner"]:
            continue
        locations = sorted(
            {(_location(i), i.get("line_number") or 0) for i in items}, key=lambda loc: (loc[0], loc[1])
        )
        rows.append({
            "key": f"{object_id}:{rule_id}",
            "object_id": object_id,
            "object_name": obj.get("name") or f"Object {object_id}",
            "object_url": obj.get("url"),
            "type": TYPE_LABELS.get(obj.get("object_type"), obj.get("object_type") or ""),
            "owner": owner,
            "rule_id": rule_id,
            "title": rule.title if rule else rule_id,
            "fix": (items[0].get("fix_guidance") or (rule.fix if rule else "")),
            "locations": [loc[0] for loc in locations[:5]],
            "more_locations": max(len(locations) - 5, 0),
            "impact": impact,
            "confidence": confidence.capitalize(),
            "status": status,
            "changed_by": (current or {}).get("changed_by") if status != "open" else None,
            "changed_at": when_date((current or {}).get("changed_at")) if status != "open" and current else None,
            "workflows": workflow_names(items[0].get("workflows")),
        })
    rows.sort(key=lambda r: (r["object_name"].lower(), r["object_id"], r["rule_id"]))
    return rows


def _location(finding: dict) -> str:
    file = finding.get("file") or "unknown location"
    line = finding.get("line_number")
    return f"{file}, line {line}" if line else file


def owners(objects: list[dict], findings: list[dict]) -> list[str]:
    with_findings = {f["object_id"] for f in findings}
    return sorted({o.get("owner_name") for o in objects if o["object_id"] in with_findings and o.get("owner_name")})


def checklist_changes(form: Iterable[tuple[str, str]], valid_rules: set[str]) -> list[tuple[int, str, str]]:
    """Changed statuses from the findings form. Fields: s:<id>:<rule> (new) and o:<id>:<rule> (original)."""
    values = dict(form)
    changes = []
    for name, new in values.items():
        if not name.startswith("s:"):
            continue
        object_part, _, rule_id = name[2:].partition(":")
        if not object_part.isdigit() or rule_id not in valid_rules or new not in STATUSES:
            raise ValueError("The form contained a value that is not allowed.")
        original = values.get(f"o:{object_part}:{rule_id}")
        if original != new:
            changes.append((int(object_part), rule_id, new))
    return changes


# ---------------------------------------------------------------------------
# Scan progress
# ---------------------------------------------------------------------------


def progress_view(run: JobRun | None, progress: Progress | None, now: datetime | None = None) -> dict:
    if run is None:
        return {"state": "idle"}
    p = progress or Progress()
    inventory_done = p.phases.get("inventory") == "done"
    code_done = p.phases.get("code") == "done"
    output_done = p.phases.get("output") == "done"
    total = p.total or p.inventory_total
    done = p.done or 0

    def step(name: str, state: str, detail: str) -> dict:
        return {"name": name, "state": state, "detail": detail}

    # While inventory runs, its detail calls report N of M (scanner/inventory, PROGRESS phase=inventory). Once
    # it is done, the code scan's total is the number of objects listed.
    reading = p.inventory_total is not None and not inventory_done
    listed = p.total if p.total is not None else p.inventory_total
    if run.state == "queued":
        headline = "Waiting for Civis to start the scan"
    elif not inventory_done:
        headline = (f"Inventory: {number(p.inventory_done or 0)} of {number(p.inventory_total)} objects read"
                    if reading else "Listing Python objects")
    elif not code_done:
        headline = f"Scanning code and logs: {number(done)} of {number(total)} objects"
    else:
        headline = "Saving results"

    running = run.state in ("queued", "running")
    steps = [
        step("1. Inventory", "Done" if inventory_done else ("Running" if running else "Waiting"),
             f"{number(listed)} Python objects listed" if inventory_done else (
                 f"{number(p.inventory_done or 0)} of {number(p.inventory_total)} objects" if reading
                 else "Listing every Python object")),
        step("2. Code and log scan", "Done" if code_done else ("Running" if inventory_done and running else "Waiting"),
             f"{number(done)} of {number(total)} objects" if inventory_done else "Code, notebooks, repositories, and the most recent run log"),
        step("3. Save results", "Done" if output_done else ("Running" if code_done and running else "Waiting"),
             "Tables and CSV report"),
    ]
    percent = 0
    if total:
        percent = min(int(100 * done / total), 100)
    if inventory_done and code_done:
        percent = 100
    return {
        "state": run.state,
        "civis_run_id": run.id,
        "started": format_datetime(run.started_at or run.created_at),
        "headline": headline,
        "percent": percent,
        "eta": _eta(p, done, total),
        "steps": steps,
    }


def _eta(p: Progress, done: int, total: int | None) -> str:
    start, last = parse_time(p.code_started_at), parse_time(p.last_at)
    if not start or not last or not total or done <= 0 or done >= total:
        return ""
    elapsed = (last - start).total_seconds()
    if elapsed <= 0:
        return ""
    minutes = int((total - done) * elapsed / done // 60) + 1
    return f"About {minutes} minute{'s' if minutes != 1 else ''} left"
