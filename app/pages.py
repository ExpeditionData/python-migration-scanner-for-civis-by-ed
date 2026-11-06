"""Pages and the scan status API.

The "/" route must answer quickly, or Civis treats the Service as down. It
reads only cached results, and every Civis read is cached (app/backends/cache.py).
Civis health checks use /healthz, which makes no calls at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for

from app import views
from app.backends.base import ChecklistSaveError, Progress, ResultsUnavailable, StorageSetup, safe_error_token
from app.security import safe_href
from app.services import Services
from common import release
from common.disclaimer import DISCLAIMER, NON_AFFILIATION
from common.models import ObjectType
from common.rule_loader import load_rules
from common.version import __version__

bp = Blueprint("pages", __name__)
RULES = load_rules()
RULE_IDS = set(RULES.ids())
HISTORY_LIMIT = 10
NOTICES = {
    "started": ("success", "The scan has started. This page updates every 15 seconds."),
    "busy": ("warning", "A scan is already queued or running, so a new one was not started."),
    "no-job": ("error", "Run scan is turned off because SCANNER_JOB_ID is not set on this Service."),
    "start-failed": ("error", "Civis did not start the scan. Try again in a few minutes."),
    "not-scanner": ("error", "The scan was not started, because SCANNER_JOB_ID does not point to this tool's scanner "
                             "job. See lines 1 and 2 on the Setup page."),
}
FILTER_KEYS = {
    "inventory": ("q", "type", "image", "status"),
    "findings": ("impact", "ruleset", "confidence", "status", "owner"),
}


def services() -> Services:
    return current_app.extensions["pmsc"]


def viewer_name() -> str | None:
    svc = services()
    if svc.local:
        return svc.local_user
    name = (request.headers.get("Civis-Username") or "").strip()
    return name or None


JOB_PROBLEMS = {
    "not_scanner_job": {
        "title": "SCANNER_JOB_ID does not point to this tool's scanner job",
        "body": "The job's repository, command, or image are not the ones in the README, so the app does not start "
                "it, read results with its settings, or set up storage with them. See lines 1 and 2 on the Setup "
                "page."},
    "database_missing": {
        "title": "The results database is not set",
        "body": "Choose the results database and its credential in the scanner job's RESULTS_DB parameter, and save "
                "the job. The app reads the job's settings again within 5 minutes."},
    "database_unrecognized": {
        "title": "The scanner job's database setting could not be read",
        "body": "The app did not recognize the value of the scanner job's RESULTS_DB parameter. Choose the database "
                "and its credential in that parameter again, and save the job."},
    "schema_missing": {
        "title": "The results schema is not set",
        "body": "Type the results schema name in the scanner job's RESULTS_SCHEMA parameter, and save the job. The "
                "app reads the job's settings again within 5 minutes."},
    "schema_invalid": {
        "title": "The results schema name is not valid",
        "body": "The scanner job's RESULTS_SCHEMA parameter must be a plain schema name: letters, numbers, and "
                "underscores, starting with a letter or underscore."},
}


def load_situation() -> views.Situation:
    svc = services()
    job_settings = svc.job_settings() if svc.jobs_provider else None
    if svc.results is None:
        results_state = "not_configured"
    else:
        try:
            results_state = svc.results.check()
        except ResultsUnavailable as exc:
            results_state = exc.kind
    scans = []
    if results_state == "ok":
        try:
            scans = views.summarize_scan_runs(svc.results.scan_runs())
        except ResultsUnavailable:
            results_state = "error"
    runs, jobs_error = [], False
    jobs = svc.jobs()
    # The runs of a job that is not this tool's scanner job are never read: they are not scans.
    foreign = job_settings is not None and job_settings.problem == "not_scanner_job"
    if jobs is not None and not foreign:
        try:
            runs = jobs.runs()
        except Exception:
            jobs_error = True
    lookback_read = job_settings is not None and job_settings.job is not None and not foreign
    return views.Situation(
        results_state=results_state, job_configured=jobs is not None, jobs_error=jobs_error,
        runs=runs, scans=scans, settings_problem=svc.settings.problem,
        job_problem=job_settings.problem if job_settings else None,
        job_lookback=job_settings.lookback_days if lookback_read else None, job_lookback_read=lookback_read,
    )


def banners(situation: views.Situation) -> list[dict]:
    items = []
    if situation.settings_problem:
        items.append({"kind": "error", "text": f"A setting on this Service is not valid. {situation.settings_problem}"})
    if not situation.job_configured:
        items.append({"kind": "warning", "text": "Run scan is turned off because SCANNER_JOB_ID is not set on this "
                      "Service. Ask the admin who set up the tool to add the scanner job's ID."})
    elif not situation.job_readable:
        items.append({"kind": "error", "text": "Run scan is turned off because the scanner job could not be read "
                      "from Civis. Check that SCANNER_JOB_ID is the scanner job's ID and that the Service owner can "
                      "see that job."})
    elif situation.job_problem == "not_scanner_job":
        items.append({"kind": "error", "text": "Run scan is turned off because SCANNER_JOB_ID does not point to this "
                      "tool's scanner job. See lines 1 and 2 on the Setup page.",
                      "link": url_for("pages.setup_page"), "link_text": "Open Setup"})
    elif situation.results_state == "schema_missing":
        items.append({"kind": "warning", "text": "Run scan is turned off because the results schema does not exist "
                      "yet. Click Set up results storage below to create it."})
    elif situation.jobs_error:
        items.append({"kind": "error", "text": "The scanner job's runs could not be read from Civis. Check that "
                      "SCANNER_JOB_ID is correct and that the Service owner can see that job."})
    # A banner's time goes in "when" (shown in the viewer's local time) followed by "tail".
    active = situation.active_run
    if active is not None:
        items.append({"kind": "info", "link": url_for("pages.scan"), "link_text": "See progress",
                      "text": f"A scan is {'queued' if active.state == 'queued' else 'running'}. Started",
                      "when": views.when(active.started_at or active.created_at), "tail": "."})
    outcome = situation.last_outcome
    current = situation.current
    shown = {"when": views.when(current.started_at), "tail": "."} if current else {}
    shown_text = f" The results shown are from {current.label}," if current else ""
    if outcome == "failed":
        items.append({"kind": "error", "text": f"The last scan failed before it finished.{shown_text}", **shown,
                      "link": url_for("pages.scan"), "link_text": "See scan history"})
    elif outcome == "tables_failed":
        items.append({"kind": "warning", "text": "The last scan finished but could not save its results tables, so "
                      f"its results are only in its CSV report.{shown_text}", **shown,
                      "link": url_for("pages.download_latest"), "link_text": "Download its CSV report"})
    return items


def empty_state(situation: views.Situation) -> dict | None:
    if situation.current is not None:
        return None
    empty = _empty_state(situation)
    if empty is not None and situation.storage_button:
        empty["storage_button"] = True
    return empty


def _empty_state(situation: views.Situation) -> dict | None:
    state = situation.results_state
    if not situation.job_configured:
        return {"title": "The scanner job is not set",
                "body": "Set SCANNER_JOB_ID on this Service to the scanner job's ID, then redeploy the Service. The "
                        "app reads the results database and schema from the scanner job's parameters."}
    if state == "job_unreadable" or not situation.job_readable:
        return {"title": "The scanner job could not be read",
                "body": "The app reads the results database and schema from the scanner job, and Civis did not "
                        "return the job. Check that SCANNER_JOB_ID is the scanner job's ID and that the Service "
                        "owner can see that job."}
    if state == "not_configured":
        return dict(JOB_PROBLEMS.get(situation.job_problem or "", JOB_PROBLEMS["database_missing"]))
    if state == "schema_missing":
        return {"title": "The results schema does not exist yet",
                "body": "Click Set up results storage to create the schema named in the scanner job's "
                        "RESULTS_SCHEMA parameter and its five result tables. If the database user cannot create "
                        "schemas, the app shows one statement to send to a database admin."}
    if state == "error":
        return {"title": "Results could not be read",
                "body": "The app could not read the results tables. Check the scanner job's RESULTS_DB and "
                        "RESULTS_SCHEMA parameters, and that the Service owner can read the schema."}
    if situation.active_run is not None:
        return {"title": "The first scan is running", "body": "Results appear here when it finishes.",
                "link": url_for("pages.scan"), "link_text": "See progress"}
    if situation.last_outcome == "tables_failed":
        return {"title": "The last scan saved a CSV report only",
                "body": "The scan finished but could not save its tables, so there are no results to show here. "
                        "Download the CSV report to see them, and check that the Service owner can write to the "
                        "results schema.",
                "link": url_for("pages.download_latest"), "link_text": "Download CSV report"}
    if situation.last_outcome == "failed":
        return {"title": "No completed scan yet",
                "body": "The last scan failed before it finished. Check the scanner job's run log in Civis, then run "
                        "the scan again."}
    if state == "no_tables":
        return {"title": "No scan yet",
                "body": "Run the first scan to find code affected by the February 2027 update. The scanner creates "
                        "its five result tables when it starts, or click Set up results storage to create them now."}
    return {"title": "No scan yet", "body": "Run the first scan to find code affected by the February 2027 update."}


def base_context(active: str, situation: views.Situation) -> dict:
    return {
        "active": active,
        "viewer": viewer_name(),
        "disclaimer": DISCLAIMER,
        "non_affiliation": NON_AFFILIATION,
        "version": __version__,
        "banners": banners(situation),
        "situation": situation,
        "can_start": situation.can_start,
        "local": services().local,
    }


def current_tables(situation: views.Situation) -> tuple[list, list, list, list]:
    scan = situation.current
    if scan is None:
        return [], [], [], []
    results = services().results
    return (
        results.rows("objects", scan.run_id),
        results.rows("findings", scan.run_id),
        results.rows("coverage", scan.run_id),
        results.checklist(),
    )


def _filters(page: str) -> dict:
    return {k: request.args.get(k, "").strip() for k in FILTER_KEYS[page] if request.args.get(k, "").strip()}


def _page_number() -> int:
    value = request.args.get("page", "1")
    return int(value) if value.isdigit() else 1


STORAGE_MESSAGES = {
    "created": ("success", "Results storage is set up. The results schema and its five tables are ready."),
    "created_existing": ("success", "Results storage is set up. The results schema already existed, and its five "
                                    "tables are ready."),
    "schema_failed": ("error", "The results schema could not be created (step: schema). The most common reason is "
                               "that the database user behind the scanner job's RESULTS_DB credential cannot create "
                               "schemas."),
    "schema_check_failed": ("error", "The app could not check whether the results schema exists (step: schema). "
                                     "Nothing was created."),
    "tables_failed": ("error", "The results schema exists, but its tables could not be created (step: tables). "
                               "Check that the database user behind the scanner job's RESULTS_DB credential can "
                               "create tables in the schema, then try again."),
    "not_dedicated": ("error", "The results schema already holds {objects}, so the tool will not use it. Nothing "
                               "was created. Choose a new schema name in the scanner job's RESULTS_SCHEMA parameter."),
    "tables_owner": ("error", "The results schema already existed, but its tables could not be created (step: "
                              "tables). The most likely reason is that the database user behind the scanner job's "
                              "RESULTS_DB credential cannot create tables in that schema."),
    "not_ready": ("error", "Nothing was created, because the scanner job's settings could not be read or are not "
                           "complete. See the message below."),
}
STATEMENT_INTROS = {
    "schema_failed": "Ask a database admin to run this statement, then click Set up results storage again to create "
                     "the tables:",
    "tables_owner": "Ask a database admin to run this statement. It makes the RESULTS_DB database user the owner of "
                    "the schema, which matches the README's ownership model. Then click Set up results storage "
                    "again to create the tables:",
}


def storage_key(result) -> str:
    """The allow-listed result key for a StorageSetup."""
    key = result.outcome
    if key == "created" and result.schema_existed:
        return "created_existing"
    if key == "schema_failed" and not result.admin_statement:
        return "schema_check_failed"
    if key == "tables_failed" and result.schema_existed:
        return "tables_owner"
    return key if key in STORAGE_MESSAGES else "not_ready"


STATEMENT_KINDS = {"schema_failed": "schema", "tables_owner": "owner"}


def storage_message() -> dict | None:
    """The outcome of the "Set up results storage" button, from the redirect's query string.

    Only an allow-listed result key and a strict error token are read. The statement for a database admin is
    rebuilt here from the scanner job's settings and the RESULTS_DB credential, never taken from the URL. No
    cookie or session is used, since the app runs inside Civis's frame."""
    key = request.args.get("storage", "")
    if key not in STORAGE_MESSAGES:
        return None
    kind, text = STORAGE_MESSAGES[key]
    if key == "not_dedicated":
        from app.setup import other_objects_text

        count = request.args.get("count", "")
        text = text.format(objects=other_objects_text(int(count) if count.isdigit() and len(count) <= 6 else None))
    message = {"kind": kind, "text": text, "error_type": safe_error_token(request.args.get("error")),
               "statement": None, "statement_intro": STATEMENT_INTROS.get(key), "user_known": True}
    if key in STATEMENT_KINDS:
        svc = services()
        try:
            message["statement"], message["user_known"] = svc.results.admin_statement(STATEMENT_KINDS[key])
        except Exception:
            message["statement_missing"] = True
    return message


@bp.post("/setup/storage")
def setup_storage():
    """The "Set up results storage" button. Reads the job's settings fresh, creates the schema if it is
    missing and then the five tables (IF NOT EXISTS), and clears the results cache. Then it redirects to the
    Overview, so a reload does not send the POST again. Nothing runs on a GET."""
    svc = services()
    settings = svc.job_settings(fresh=True) if svc.jobs_provider else None
    if svc.results is None or settings is None or not settings.usable:
        result = StorageSetup("not_ready")
    else:
        try:
            result = svc.results.setup_storage()
        except Exception:
            result = StorageSetup("not_ready")
    query = {"storage": storage_key(result)}
    token = safe_error_token(result.error_type)
    if token:
        query["error"] = token
    if result.outcome == "not_dedicated":
        query["count"] = str(result.other_objects)
    return redirect(url_for("pages.overview", **query))


@bp.get("/")
def overview():
    return _overview(storage=storage_message())


def _overview(storage: dict | None = None):
    situation = load_situation()
    objects, findings, coverage, checklist = current_tables(situation)
    stats = views.overview(objects, findings, coverage, checklist, RULES) if situation.current else None
    current = situation.current
    run = next((r for r in situation.runs if current and r.id == current.civis_run_id), None)
    if stats:
        scan_days = current.parameters.get("lookback_days")
        stats["outside_window_text"] = views.outside_window_text(stats["outside_window"], scan_days)
    return render_template(
        "overview.html", **base_context("overview", situation), stats=stats, empty=empty_state(situation),
        next_window=views.next_window_text(situation),
        last_scan=views.when(current.started_at) if current else None,
        duration=views.format_duration(run.started_at, run.finished_at) if run else (
            views.format_duration(current.started_at, current.finished_at) if current else None),
        scan_label=current.label if current else None, storage=storage,
    )


@bp.get("/scan")
def scan():
    situation = load_situation()
    svc = services()
    run = situation.active_run or (situation.runs[0] if situation.runs else None)
    progress = _progress(run)
    counts = {}
    completed = [s for s in situation.scans if s.status == "succeeded"][:HISTORY_LIMIT]
    for scan_run in completed:
        objects = svc.results.rows("objects", scan_run.run_id)
        findings = svc.results.rows("findings", scan_run.run_id)
        impacts = views._object_impacts(findings)
        counts[scan_run.run_id] = {
            "affected": sum(1 for v in impacts.values() if v == "affected"),
            "advisory": sum(1 for v in impacts.values() if v == "advisory"),
            "pairs": views.finding_pairs(findings),
            "objects": len(objects),
        }
    scan_run = situation.scan_for(run)
    lookback = (scan_run.parameters.get("lookback_days") if scan_run else None) or release.DEFAULT_LOOKBACK_DAYS
    notice = NOTICES.get(request.args.get("notice", ""))
    view = views.progress_view(run, progress)
    return render_template(
        "scan.html", **base_context("scan", situation),
        run=run, scan_run=scan_run, progress=view, running=view.get("state") in ("queued", "running"), lookback=lookback,
        run_started=views.when(run.started_at or run.created_at) if run else None,
        history=views.history(situation, counts), notice=notice, empty=empty_state(situation),
    )


@bp.get("/inventory")
def inventory():
    situation = load_situation()
    objects, findings, _, _ = current_tables(situation)
    filters = _filters("inventory")
    rows = views.inventory(objects, findings, filters)
    page = views.paginate(rows, _page_number())
    types = [(t.value, views.TYPE_LABELS[t.value]) for t in ObjectType if any(o["object_type"] == t.value for o in objects)]
    return render_template(
        "inventory.html", **base_context("inventory", situation), page=page, filters=filters, types=types,
        total_objects=len(objects), empty=empty_state(situation), current=situation.current,
    )


@bp.get("/findings")
def findings():
    situation = load_situation()
    objects, finding_rows, _, checklist = current_tables(situation)
    filters = _filters("findings")
    rows = views.findings_rows(objects, finding_rows, checklist, RULES, filters)
    page = views.paginate(rows, _page_number())
    saved = request.args.get("saved", "")
    error = request.args.get("error", "")
    return render_template(
        "findings.html", **base_context("findings", situation), page=page, filters=filters,
        owners=views.owners(objects, finding_rows), empty=empty_state(situation), current=situation.current,
        saved=int(saved) if saved.isdigit() else None,
        error={"viewer": "Saving needs a signed-in Civis user. Open the app through Civis Platform and try again.",
               "invalid": "The form contained a value that is not allowed. Reload the page and try again.",
               "failed": "The changes could not be saved. Try again in a few minutes.",
               "stale": "There is no completed scan to save changes against."}.get(error),
        can_save=viewer_name() is not None,
    )


@bp.post("/findings/save")
def save_findings():
    filters = {k: request.form.get(f"f_{k}", "").strip() for k in FILTER_KEYS["findings"]}
    filters = {k: v for k, v in filters.items() if v}
    page = request.form.get("f_page", "")
    if page.isdigit():
        filters["page"] = page

    def back(**extra):
        return redirect(url_for("pages.findings", **filters, **extra))

    viewer = viewer_name()
    if not viewer:
        return back(error="viewer")
    situation = load_situation()
    if situation.current is None:
        return back(error="stale")
    try:
        changes = views.checklist_changes(request.form.items(), RULE_IDS)
    except ValueError:
        return back(error="invalid")
    if not changes:
        return back(saved="0")
    try:
        services().results.save_checklist(situation.current.run_id, changes, viewer, datetime.now(timezone.utc))
    except ChecklistSaveError:
        return back(error="failed")
    return back(saved=str(len(changes)))


@bp.post("/scan/start")
def start_scan():
    svc = services()
    jobs = svc.jobs()
    if jobs is None:
        return redirect(url_for("pages.scan", notice="no-job"))
    try:
        with svc.start_lock:  # One check-then-start at a time in this worker.
            # Read the job fresh and start it only if it is this tool's scanner job (common/release.py). This
            # holds even if the button is shown by mistake.
            settings = svc.job_settings(fresh=True)
            if settings is None or settings.problem == "job_unreadable":
                return redirect(url_for("pages.scan", notice="start-failed"))
            if not release.is_scanner_job(settings.job):
                return redirect(url_for("pages.scan", notice="not-scanner"))
            if any(run.active for run in jobs.runs(limit=10)):
                return redirect(url_for("pages.scan", notice="busy"))
            jobs.start()
    except Exception:
        return redirect(url_for("pages.scan", notice="start-failed"))
    return redirect(url_for("pages.scan", notice="started"))


@bp.get("/api/scan-status")
def scan_status():
    situation = load_situation()
    run = situation.active_run or (situation.runs[0] if situation.runs else None)
    view = views.progress_view(run, _progress(run))
    view["configured"] = situation.job_configured
    view["can_start"] = situation.can_start
    view["outcome"] = situation.last_outcome
    if run is not None and not run.active:
        scan_run = situation.scan_for(run)
        if scan_run is not None and scan_run.status == "tables_failed":
            view["state"] = "tables_failed"
    return jsonify(view)


@bp.get("/download/latest")
def download_latest():
    situation = load_situation()
    for scan_run in situation.scans:
        if scan_run.status in ("succeeded", "tables_failed") and scan_run.civis_run_id:
            return download(scan_run.civis_run_id)
    abort(404)


@bp.get("/download/<int:civis_run_id>")
def download(civis_run_id: int):
    svc = services()
    jobs = svc.jobs()
    settings = svc.job_settings() if jobs is not None else None
    if jobs is None or (settings is not None and settings.problem == "not_scanner_job"):
        abort(404)  # Never another job's run outputs.
    try:
        report = jobs.report(civis_run_id)
    except Exception:
        report = None
    if report is None:
        abort(404)
    kind, target = report
    if kind == "file":
        path = Path(target)
        return send_file(path, mimetype="text/csv", as_attachment=True, download_name=path.name)
    link = safe_href(target)
    if link == "#":
        abort(404)
    return redirect(link)


def latest_state(situation: views.Situation) -> str:
    """The newest scan's state, for Setup line 10: unknown, none, queued, running, succeeded, failed, cancelled,
    or tables_failed."""
    if not situation.job_readable or situation.jobs_error or situation.job_problem == "not_scanner_job":
        return "unknown"  # No scanner job, or its runs could not be read: the newest scan is not known.
    if situation.active_run is not None:
        return situation.active_run.state
    return situation.last_outcome or "none"


def setup_facts(situation: views.Situation, settings):
    """Read everything the Setup page checks. Reads only; errors become error types."""
    from app import setup

    svc = services()
    try:
        source = svc.setup_provider() if svc.setup_provider else None
    except Exception:
        source = None
    return setup.gather(
        source, svc.settings.scanner_job_id, settings,
        report_only=bool(current_app.config.get("PMSC_FRAME_REPORT_ONLY")), latest=latest_state(situation),
    )


@bp.get("/setup")
def setup_page():
    """Ten read-only checks of the setup. Reads the scanner job's settings fresh. Writes nothing."""
    from app import setup

    svc = services()
    settings = svc.job_settings(fresh=True) if svc.jobs_provider else None
    situation = load_situation()
    facts = setup_facts(situation, settings)
    return render_template("setup.html", **base_context("setup", situation), lines=setup.evaluate(facts))


@bp.get("/setup/summary")
def setup_summary():
    """The test summary: counts, fixed labels, and error types only (app/summary.py). Reads only."""
    from app import setup, summary

    svc = services()
    settings = svc.job_settings(fresh=True) if svc.jobs_provider else None
    situation = load_situation()
    facts = setup_facts(situation, settings)
    scans = [s for s in situation.scans if summary.is_run_id(s.run_id)]
    wanted = request.args.get("run", "")
    # By default, the newest scan that has finished (a running scan has no RUN line counts yet). Any other run,
    # including a running one, can be chosen in the select list. With no finished scan, the newest one.
    newest_final = next((s for s in scans if s.status in summary.FINAL_STATUSES), scans[0] if scans else None)
    chosen = next((s for s in scans if s.run_id == wanted), newest_final)
    scan_data = checklist = run_rows = None
    if chosen is not None:
        scan_data = summary.ScanData(chosen.run_id, chosen.status, chosen.started_at, chosen.finished_at)
        finished = chosen.status == "succeeded"
        try:
            scan_data.objects = svc.results.rows("objects", chosen.run_id, finished=finished)
            scan_data.findings = svc.results.rows("findings", chosen.run_id, finished=finished)
        except Exception:
            scan_data.objects = scan_data.findings = None
        jobs = svc.jobs()
        if jobs is not None and chosen.civis_run_id:
            scan_data.log = summary.read_log(jobs, chosen.civis_run_id)
    if situation.results_state == "ok":
        try:
            checklist = svc.results.checklist()
            run_rows = svc.results.scan_runs()
        except Exception:
            checklist = run_rows = None
    text = summary.build(summary.SummaryInputs(
        tool_ref=(facts.job or {}).get("repo_ref"), lines=setup.evaluate(facts),
        database_shape=settings.database_shape if settings is not None and settings.job is not None else None,
        service_method=facts.service_method, sharing=setup.sharing_counts(facts),
        repo_token_present=facts.repo_token_present, schema_dedicated=setup.schema_dedicated(facts),
        results_state=situation.results_state, scan=scan_data, checklist=checklist, scan_run_rows=run_rows,
    ))
    return render_template("summary.html", **base_context("setup", situation), text=text, scans=scans,
                           chosen=chosen)


@bp.get("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


def _progress(run) -> Progress | None:
    """Read new scanner log lines for this run and update its parsed progress."""
    if run is None:
        return None
    svc = services()
    jobs = svc.jobs()
    with svc.progress_lock:
        progress = svc.progress.setdefault(run.id, Progress())
    if jobs is None:
        return progress
    try:
        lines = jobs.logs(run.id, progress.last_id)
    except Exception:
        return progress
    with svc.progress_lock:
        progress.feed(lines)
    return progress


@bp.app_errorhandler(404)
def not_found(_error):
    return render_template("error.html", title="Page not found",
                           body="That page or report could not be found.", viewer=viewer_name(),
                           disclaimer=DISCLAIMER, non_affiliation=NON_AFFILIATION, version=__version__,
                           banners=[], active=None), 404


@bp.app_errorhandler(400)
def bad_request(_error):
    return render_template("error.html", title="The request could not be accepted",
                           body="The form may have expired. Reload the page and try again.", viewer=viewer_name(),
                           disclaimer=DISCLAIMER, non_affiliation=NON_AFFILIATION, version=__version__,
                           banners=[], active=None), 400
