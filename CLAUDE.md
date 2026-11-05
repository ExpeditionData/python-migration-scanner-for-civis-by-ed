# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

Python Migration Scanner for Civis Platform by Expedition Data is a free, open-source tool that
Civis Platform customers run inside their own Civis environment. It finds scripts, notebooks,
workflows, Services, and container scripts affected by the February 2027 Civis
default image update (Python 3.14 to 3.15, civis-python v2 to v3). A scanner
job (a Civis container script) inventories objects, reads their code and recent
logs, and writes findings to a schema the user chooses. A Flask web app (a Civis
Service) launches scans, shows progress and results, and lets the team track a
fix checklist. The tool is read-only on every Civis object. The code is licensed
under Apache 2.0 by Expedition Data LLC and is written so the repository could
be made public at any time.

## Hard rules

- **`Context/` is never committed.** It holds private reference material and is
  listed in `.gitignore`. Do not remove that entry, force-add files from it, or
  copy its contents into tracked files. Only read files in `Context/` when the
  user asks.
- **Every committed file must be safe for a public repo.** That means no client
  names, client data, Civis instance or organization names, secrets, API keys,
  pricing, or business strategy. Use generic examples and placeholders instead.
- **Secrets live only in `.env`.** `.env` and `.env.*` are ignored by git. The
  only tracked env file is `.env.example`, and it holds placeholder values only.
  When a new setting is added, add a placeholder for it to `.env.example`.
- **Client data never leaves the client's Civis environment unless Rob explicitly approves it in this session for a specific purpose. Client data includes object names, user names, code, run logs, notebook outputs, scan results, and table contents.**
- **No live client instances from this machine.** Never run the scanner or the
  app locally against a real Civis instance, and never call a client's Civis API
  from this machine. Local runs use synthetic fixtures only. Live testing happens
  only by running the scanner job and the Service inside the client's Civis.
- **Never read client data in a session.** Never read, print, or summarize real
  scan results, run logs, or code from a client instance. Rob shares only
  aggregate counts, or error messages with client details removed. If something
  pasted into the session looks like client data, stop and say so instead of
  using it.
- **The tool treats everything in Civis as read-only** (Rob, November 4,
  2026). The only things it writes are: the results schema and the five
  tables it creates in it, rows in those tables, runs of the scanner job
  (started from the app), and the CSV run output attached to each scanner
  run. It never creates, edits, shares, archives, or deletes any other Civis
  object, including the scanner job's settings and the Service. Every SQL
  statement is CREATE SCHEMA IF NOT EXISTS, CREATE TABLE IF NOT EXISTS,
  SELECT, INSERT, or DELETE in the results schema (`tests/test_sql_scope.py`).
- **Security and privacy requirements** are in [SECURITY.md](SECURITY.md). Code
  must follow them: Civis content (code, logs, notebook outputs) stays in
  memory and only object ID, rule ID, file, and line number are stored; the
  scanner logs only through `scanner/scan_log.py`; credentials are never
  printed, logged, or stored; every SQL identifier and value is validated or
  escaped; every value from Civis is escaped in the web app.

## Git workflow

- Default branch is `main`. Remote is `origin`
  (`ExpeditionData/python-migration-scanner-for-civis-by-ed`). The repository is public
  (since November 2, 2026), because both Civis objects clone it with no git
  credential. Rob changes its visibility himself; never change it from a
  session.
- Releases are git tags, protected by the "release tags" ruleset. The first
  live test set up on `v0.1.0-rc5` and runs its scans on `v0.1.0-rc6`. Create a tag or a
  GitHub Release only when Rob's prompt says to.
- Commit after each completed step, with a clear message that says what changed
  and why.
- Before every commit, review the staged files (`git diff --cached --name-only`
  and `git diff --cached`) for secrets, client details, and anything from
  `Context/`. Stop and tell the user if anything looks unsafe.
- The pre-commit hook in `.githooks/` also blocks staged `Context/` files and
  likely secrets (gitleaks if installed, detect-secrets from `.venv`). Enable it
  in each clone with `git config core.hooksPath .githooks`. Mark fake secrets in
  tests with `# pragma: allowlist secret  gitleaks:allow`. Never bypass the hook.
- Push to `main` after each commit.

## Writing style for docs and UI text

- American English.
- Month-first dates, for example "October 9, 2026" or "10/09/2026".
- No em dashes. Use commas, periods, colons, or parentheses instead.
- Plain, direct language. Short sentences. No marketing tone.
- Fixed legal text is used exactly as written, not reworded to this style:
  `DISCLAIMER` and `NON_AFFILIATION` in `common/disclaimer.py` (and their
  README copies), NOTICE, the README trademark sentences, and the "By design"
  section of SECURITY.md. Change them only when Rob provides new text.
- Describe the tool's behavior as design ("is designed to", "does not"), not as
  a promise ("never"), in SECURITY.md, the README, and app text. "Never" is
  fine in instructions to admins and in contributor rules.

## Architecture

The approved design is in [docs/PLAN.md](docs/PLAN.md) (approved October 9,
2026). Where the plan and older notes differ, the plan wins. Summary:

- **Three packages, one repo.** `scanner/` (container script, `python -m scanner`),
  `app/` (Flask Service, `gunicorn "app:create_app()"`), and `common/` (shared
  rules, models, table schemas, URL builders, disclaimer). Civis clones the repo
  to `/app` for both objects.
- **Scanner job.** Runs on `civisanalytics/datascience-python:9.1.1` (Python
  3.14.6, civis 2.10.0). Installs nothing at run time. Inventories Python scripts,
  container scripts, custom scripts, notebooks, Services, and workflows; reads
  code (inline, notebook file, the own git file of each git-backed Python script
  and workflow through the Civis API, and whole repositories from GitHub only
  when a token is attached; no token is the normal setup); runs AST rules with
  regex fallback; reads
  the most recent run log; writes results to the user's schema plus a CSV run
  output. Uses the `get_*` civis method names so it also runs on civis v3.
- **Web app.** Runs on `civisanalytics/civis-services-flask:0.1.0` (Python
  3.12.11, Flask 3.1.1, gunicorn 22.0.0, civis 2.7.1, PyYAML 6.0.2) with no
  installs at start. The image's `/entrypoint.sh` runs `start_service.sh` from
  the repo root, which execs gunicorn on port 3838 with two gthread workers
  of four threads each.
  It only runs the scanner job the admin created (`SCANNER_JOB_ID`), using the
  job's saved parameters. It never creates or edits scripts. It reads the
  results database, credential, and schema from that job's `RESULTS_DB` and
  `RESULTS_SCHEMA` parameters (`app/job_settings.py`, cached 5 minutes per
  worker). Its only writes are the checklist INSERT and the "Set up results
  storage" button (CREATE SCHEMA IF NOT EXISTS when the schema is missing,
  then the five CREATE TABLE IF NOT EXISTS). A read-only Setup page runs ten
  checks, and its "Build test summary" page gives counts, fixed labels, and
  error types only, from an allow list (`app/summary.py`). Shared with Civis
  admins only.
- **Two safety guards** (docs/PLAN.md 17.3). (1) Run scan starts only this
  tool's scanner job: inside the POST, under the start lock, the app reads
  the job fresh and starts it only if `common/release.scanner_job_mismatches`
  finds its repository, command, and image are this tool's. The same
  function drives Setup lines 1 and 2, and a job that fails it gives no usable
  settings (no results reads, no storage setup, no runs or outputs read).
  (2) The results schema must hold only this tool's tables:
  `common/schema.schema_objects_sql` and `other_object_count` (pg_catalog
  tables and views) are checked by the setup button, Setup line 9, and the
  scanner before it writes anything. The scanner logs one `FATAL
  error=SchemaNotDedicated` line and exits 2.
- **Connection.** No direct link between app and scanner. The app reads the
  job's settings, starts runs, and reads status and `PROGRESS` log lines
  through the Civis API, and reads results from the tables. Checklist status
  is append-only.
- **Service settings.** `SCANNER_JOB_ID`, `CIVIS_SERVICE_HEALTH_PATH`
  (`/healthz`), and `CIVIS_FRAME_ANCESTORS_REPORT_ONLY`, plus the optional
  `CIVIS_PLATFORM_URL` and `CIVIS_FRAME_ANCESTORS`. There are no results
  settings on the Service.
- **Data storage.** Five tables in the user's schema: `scan_runs`, `objects`,
  `findings`, `coverage`, `checklist_status`. Every row carries a run ID.
- **Rules.** YAML files in `common/rules/` (`civis_v3.yaml`, `python315.yaml`),
  loaded and validated by `common/rule_loader.py`. Matcher kinds are
  implemented in `scanner/engine.py`. Findings carry file, line number, and
  rule ID only.
- **Status.** Phases 2 to 5 are done: the scanner (rules, engine,
  inventory, code retrieval, log scan, request budget, output, checkpoints,
  resume, dry runs) and the Flask app (Overview, Scan history, Inventory,
  Findings and checklist, scan status API, local preview). Phase 6 is built
  and approved: `start_service.sh`, the Docker test images, the report-only
  frame rule, README setup steps, and TESTING.md. On October 26, 2026 the
  product and repository were renamed and the legal texts were updated
  (docs/PLAN.md section 16). Phase 7 is the first live test, following
  TESTING.md. On November 2, 2026, Phase 7 added reading git-backed Python
  scripts' and workflows' own files through Civis without a GitHub token, and
  marked objects with only a log read as partial (docs/PLAN.md section 17).
  On November 4, 2026, Phase 7 added results storage setup from the app, job
  settings read from the scanner job, the Setup page, the test summary, and
  the SQL scope test (docs/PLAN.md 17.2).
  On November 5, 2026, setup of `v0.1.0-rc4` in the live test found two
  bugs, fixed for `v0.1.0-rc5`: reads that return no rows, and the shape of
  the Civis sharing lists (docs/PLAN.md 17.4). Setup of `v0.1.0-rc5` passed;
  its first scan failed in inventory, fixed for `v0.1.0-rc6` (docs/PLAN.md
  17.5): the users list fallback, no hidden counts, shorter retries, and the
  FATAL step. The build under test is `v0.1.0-rc6`.
  Implementation choices not in the approved plan are in docs/PLAN.md
  sections 10 to 17.
- **App flow.** `app/__init__.create_app` -> `app/services.py` (Civis or local
  data layer, job settings cache) -> `app/pages.py` routes -> `app/views.py`
  (pure shaping) -> `app/templates/`. Data layer:
  `app/backends/civis_backend.py` (civis 2.7.1 calls only: `CivisResults`,
  `CivisJobs`, `CivisSetup`), `local_backend.py` (dry-run preview files and
  a made-up job and Service), `cache.py`. `app/job_settings.py` parses the
  job's parameters, `app/setup.py` gathers and evaluates the Setup checks,
  `app/summary.py` builds the test summary, and `common/release.py` holds
  what the README sets (pinned images read from the requirements files).
  Styles in `app/static/css/app.css`, scripts in `app/static/js/`, fonts in
  `app/static/fonts/`, the logo in `app/static/img/`.
- **Look.** Expedition Data's style: navy `--primary` (#26304F) for headings,
  labels, links, nav, and primary buttons; cream page background; tan borders;
  orange `--accent` (#DD8A33) for decoration only (header rule, section heading
  rules, active nav bar, link underlines), never for text or focus outlines.
  Semantic colors (warning, error, success, affected, advisory, fixed) are
  unchanged. Font stack `Calibri, Carlito, system-ui, sans-serif`: Carlito is
  vendored (OFL), Calibri is never vendored, and only weights 400 and 700 are
  used. IBM Plex Mono is vendored for rule IDs and file locations. The header
  logo and the footer "Expedition Data LLC" link to https://www.expedition-data.com/
  with `target="_blank" rel="noopener noreferrer"`; those are the only external
  links in the templates (object links to Civis Platform come from scan data).
  Contrast is checked by `tests/test_app.py`.
- **Scanner flow.** `scanner/__main__.py` -> `runner.run_scan` -> inventory ->
  `process.process_item` per object (retrieval, engine, logs) ->
  `checkpoint.Checkpointer` -> `output` sinks. Every Civis API call goes
  through `ratelimit.BudgetedClient`; every other HTTP call goes through
  `retrieval/http.SafeHttp` and its host allow list. `retrieval/civis_git.CivisGitFiles`
  reads an item's own git file through the Civis API: a git-backed workflow's
  definition during inventory (`workflows.get_git` is called for every active
  workflow), and a git-backed Python script's file in `process_item` when
  there is no token or GitHub fails. An object whose code was not read but
  whose log was is partial. The final `RUN` log line adds `objects_total`,
  `objects_scanned`, `api_calls`, `github_tarball_requests`,
  `github_cache_peak_bytes`, `github_cache_evictions`, `civis_git_reads`, and
  `civis_git_failures`, so calls per object, memory use, and Civis git reads
  can be measured in a live test. Failed Civis git reads also get a
  `CIVIS_GIT_FAILED` line. The GitHub file cache is capped at 1 GB of text
  (`CACHE_MAX_BYTES`) with least-recently-used eviction.
- **Scanner users, hidden objects, retries, FATAL (docs/PLAN.md 17.5).**
  `inventory/users.load_users` lists `account_status="all"`; on HTTP 403 or
  404 it lists active users (an owner not in that list is unknown, never
  False); if that fails too, there is no user lookup. A `USERS
  source=all|active|none [status=N]` log line and, when not "all", a coverage
  row with `row_kind` `users` and a fixed note record it; the Overview shows
  the note. Hidden objects are skipped and not counted: no list call sends
  `hidden`, and `hidden_skipped_count` is written NULL (the column stays).
  `live_run` gives `civis.APIClient` the `ratelimit.civis_retrying` policy (4
  tries or 120 seconds, waits up to 30 seconds; tenacity is pinned in
  `requirements-scanner.txt`). civis 2.10 holds one class-level lock for each
  whole request, retries included, so a slow call blocks every other API call
  in the scan, and a request with no response holds it with no time limit
  (civis sets no timeout; not handled yet). `run_scan` tracks the step and
  logs `FATAL error=<type> [status=N] step=<step>` (steps in
  `scan_log.FATAL_STEPS`), then appends a final "failed" `scan_runs` row if
  the "running" row was written and the tables are usable, and raises
  `ScanFailed`. `SchemaNotDedicated` still writes nothing.
- **Live test.** TESTING.md is the checklist for the first live test. Records
  are pass or fail and aggregate counts only.

Constraints to keep in mind when writing code:

- `app/` and `common/` may only call civis methods that exist in civis 2.7.1. A
  unit test enforces this.
- `common/` must run on Python 3.12 and 3.14.
- Never store, log, or output code snippets. Fetched code stays in memory or a
  temp directory deleted at the end of the run.
- Front-end libraries and fonts are vendored into `app/static/`. No CDN links
  and no external `src` attributes. The Expedition Data name and logo are
  trademarks and are not Apache-licensed (see NOTICE).
- Every value in a SQL insert is validated and escaped, including request headers.
- Unit tests use synthetic fixtures only. Never record fixtures from a real Civis
  instance, and never commit real object names, user names, or code.
- On Windows, PowerShell 5.1 mangles double quotes in `git commit -m`. Write
  multi-line commit messages to a temp file and use `git commit -F <file>`.

## Setup commands

Local development uses Python 3.14 (the scanner image's version). Run from the
repo root:

```
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
python3.12 -m venv .venv312
.venv312\Scripts\python.exe -m pip install -r requirements-dev-service.txt
git config core.hooksPath .githooks
copy .env.example .env
```

- `.venv` (Python 3.14) mirrors the scanner image: the scanner's pinned
  packages plus Flask, pytest, and detect-secrets.
- `.venv312` (Python 3.12) mirrors the Service image: civis 2.7.1, Flask,
  gunicorn, plus pytest. Python 3.12 is installed per user with
  `pymanager install 3.12`.
- The Service's civis 2.7.1 limit is also checked statically by a test.

To rebuild the civis 2.7.1 API list used by that test (only if the Service
image changes), download the civis wheel to a temp folder, unzip it, and run
`tools/build_civis_api_fixture.py <unzipped>/civis tests/fixtures/civis_2_7_1_api.json`.

## Dry run

Runs the whole scanner pipeline on the synthetic instance in
`scanner/synthetic/`, with no network calls, and writes `results.sqlite` and
the CSV report to a local folder (ignored by git):

```
.venv\Scripts\python.exe -m scanner --dry-run --out dry_run_output
.venv\Scripts\python.exe -m scanner --dry-run --out dry_run_output --stop-after 10
.venv\Scripts\python.exe -m scanner --dry-run --out dry_run_output --run-id <run id from the log>
```

The last two commands stop a run partway and then resume it. A live scan
(`python -m scanner` with no flags) refuses to run outside Civis.

## Local app preview

Seeds made-up data (four scans: completed, failed, completed with fixes, and
one still running, all with no GitHub token, the normal setup), then serves the
app on 127.0.0.1 only:

```
.venv\Scripts\python.exe -m scanner --dry-run --preview --out dry_run_output
.venv312\Scripts\python.exe -m app.local --results dry_run_output
```

Open http://127.0.0.1:8050. Add `--state no-scan` (or `first-scan-running`,
`failed`, `tables-failed`, `schema-missing`, `no-job-id`, `job-unreadable`,
`database-unrecognized`, `schema-refused`, `tables-missing`,
`setup-failures`, `not-scanner-job`, or `schema-shared`) to see the empty and
error states. `not-scanner-job` points SCANNER_JOB_ID at another job, and
`schema-shared` has a results schema that holds other tables. `schema-missing`,
`schema-refused`, and `tables-missing` look like a new install with the "Set
up results storage" button (`schema-refused` shows the statement for a
database admin); `setup-failures` makes several Setup page lines fail. Local
preview refuses to start if `CIVIS_API_KEY` is set.

## Testing commands

Run the full suite on both Python versions before every commit:

```
.venv\Scripts\python.exe -m pytest
.venv312\Scripts\python.exe -m pytest
```

- `tests/test_security.py` proves the SECURITY.md requirements: no secret or
  personal data from logs, code, or notebook output reaches any output,
  malicious schema names are rejected, keys in exceptions are redacted, and
  values from Civis render as text in the web app.

- Unit tests make no Civis or network calls. Inventory tests use the
  synthetic client in `scanner/synthetic/` (re-exported by
  `tests/fake_civis.py`) with the made-up instance in
  `scanner/synthetic/synthetic_instance.json`. Like Civis in the live test,
  its `users.list` refuses `account_status` "all" and "inactive" with HTTP
  404 (`users_list_errors`), so the dry run and the preview use the active
  list; tests set `users_list_errors` to `{}` for the "all" shape.
- `tests/test_fatal.py` covers the FATAL line and the final "failed" row, and
  `tests/test_retries.py` drives the retries policy through civis-python's
  own `retry_request` (it skips on civis 2.7.1).
- Every rule needs a positive and a negative fixture in `tests/test_rules.py`.
  A test fails if a rule has none.
- `tests/test_self_scan.py` runs the tool's own rules over this repo.
- `tests/test_civis_271_compat.py` fails if `app/` or `common/` calls a civis
  method missing from civis 2.7.1, including calls through an injected
  `civis.io` module.
- `tests/test_local_time.py` runs `app/static/js/local-time.js` under Node (skipped if Node is not installed) to check local times and the UTC fallback.
- `tests/test_job_settings.py`, `tests/test_storage_setup.py`,
  `tests/test_setup_page.py`, and `tests/test_summary.py` cover the job
  settings, the setup button, the Setup page, and the test summary (including
  the marker test and the TESTING 4.8 match). `tests/test_sql_scope.py` checks
  every SQL statement the tool sends. `tests/test_start_guard.py` and
  `tests/test_schema_guard.py` cover the two safety guards. `tests/civis_fakes.py` holds the made-up
  civis client and recording `civis.io`.
- The fakes match two Civis behaviors confirmed in the live test (docs/PLAN.md
  17.4). `civis.io.read_civis_sql` raises `civis.base.EmptyResultError` for
  every query with zero rows (Civis writes no file for an empty result), and
  the app and the scanner treat that error as zero rows
  (`common/civis_errors.py`). Every fake `read_civis_sql` raises it the same
  way by default, and `RecordingIO.empty_reads` lists those reads.
  `tests/test_zero_rows.py` covers each path that can read zero rows. The
  Civis shares calls (`scripts.list_containers_shares`,
  `services.list_shares`) return one object with readers, writers, and
  owners, not a list, and the fake shares do the same.
- `tests/test_app.py` covers every page and API, CSRF on every POST, escaping
  of hostile object names, security headers, the concurrent-run guard, the
  batched checklist save, the empty and error states, text contrast, and local
  preview mode.
- `tests/test_deploy.py` checks `start_service.sh` (executable bit, LF endings,
  no installs), the Dockerfiles, `.dockerignore`, and `docker/check_image.py`.
  Two of its tests run only inside the test images.

### Tests inside the pinned images (Docker)

The images in `docker/` are for local testing only. Each build fails if the
image's Python or package versions differ from `requirements-scanner.txt` or
`requirements-service.txt`, or if adding pytest changes any package that came
with the image. `.dockerignore` keeps `Context/`, `.env` files, virtual
environments, dry-run output, `.git`, and caches out of the build. Pull by
digest first (Docker Hub only); the builds also download pytest from PyPI.

```
docker pull civisanalytics/datascience-python@sha256:d6903fdcf6b13801417621ad7522c09a16e1d2c01d11eb398f75ad0f73d974ef
docker pull civisanalytics/civis-services-flask@sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f
docker build -f docker/scanner.Dockerfile -t pmsc-scanner-test .
docker build -f docker/service.Dockerfile -t pmsc-service-test .
docker run --rm --network none pmsc-scanner-test
docker run --rm --network none pmsc-service-test
```

The scanner image has no Flask, so the web app tests skip there and run in the
Service image. The Service image has no pandas or Node, so those few tests skip.

Smoke test of the Service: start it the way Civis does (the image's own
`/entrypoint.sh`, which runs `start_service.sh`), with no network and no
`CIVIS_API_KEY`, then check `/healthz` on port 3838, the log, and that no
package files changed:

```
docker run -d --name pmsc-smoke --network none -w / -e APP_DIR=/app -e REPO_PATH_DIR= -e FLASK_SECRET_KEY=smoke-test-only-not-a-real-secret-key pmsc-service-test /bin/sh -c ./entrypoint.sh
docker exec pmsc-smoke python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:3838/healthz').status)"
docker logs pmsc-smoke
docker diff pmsc-smoke
docker rm -f pmsc-smoke
```

Expected: `200`, a log with "Listening at: http://0.0.0.0:3838", "Using
worker: gthread", and two workers, and no `site-packages` paths in `docker diff`.

Held-request check: start a second container that points civis at a local
listener that accepts and never answers, so page loads hang the way a stuck
Civis query would. Nothing is added to the shipped app; the listener and the
held requests run through `docker exec`. The API key is a fake value for this
check only.

```
docker run -d --name pmsc-hold --network none -w / -e APP_DIR=/app -e REPO_PATH_DIR= -e FLASK_SECRET_KEY=smoke-test-only-not-a-real-secret-key -e CIVIS_API_KEY=smoke-test-fake-key -e CIVIS_API_ENDPOINT=http://127.0.0.1:8099/ -e SCANNER_JOB_ID=1 pmsc-service-test /bin/sh -c ./entrypoint.sh
docker exec -d pmsc-hold python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 8099)); s.listen(50); held=[s.accept()[0] for _ in range(50)]"
docker exec -d pmsc-hold python -c "import urllib.request as u, threading as t; [t.Thread(target=u.urlopen, args=('http://127.0.0.1:3838' + p,), kwargs={'timeout': 400}).start() for p in ('/', '/inventory')]"
docker exec pmsc-hold python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:3838/healthz', timeout=10).status)"
docker logs pmsc-hold
docker rm -f pmsc-hold
```

Run the `/healthz` line after 35 seconds and again after 125 seconds. Expected:
`200` each time, right away, and no "WORKER TIMEOUT" in the log.

## Keeping docs current

Update this file, `README.md`, and any other docs in the repo as the project
changes. When architecture, setup, or testing steps change, update the matching
section above in the same commit.
