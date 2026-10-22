# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

Civis Python Version Search is a free, open-source tool that Civis Platform
customers run inside their own Civis environment. It finds scripts, notebooks,
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
- **Security and privacy requirements** are in [SECURITY.md](SECURITY.md). Code
  must follow them: Civis content (code, logs, notebook outputs) stays in
  memory and only object ID, rule ID, file, and line number are stored; the
  scanner logs only through `scanner/scan_log.py`; credentials are never
  printed, logged, or stored; every SQL identifier and value is validated or
  escaped; every value from Civis is escaped in the web app.

## Git workflow

- Default branch is `main`. Remote is `origin`
  (`ExpeditionData/civis-python-version-search`). The repository is private
  until the public release. Rob changes its visibility himself; never change it
  from a session. Live tests run only after it is public, because both Civis
  objects clone it with no git credential.
- Releases are git tags (the first live test uses `v0.1.0-rc1`). Rob creates
  tags; never create one from a session.
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
  code (inline, notebook file, GitHub); runs AST rules with regex fallback; reads
  the most recent run log; writes results to the user's schema plus a CSV run
  output. Uses the `get_*` civis method names so it also runs on civis v3.
- **Web app.** Runs on `civisanalytics/civis-services-flask:0.1.0` (Python
  3.12.11, Flask 3.1.1, gunicorn 22.0.0, civis 2.7.1, PyYAML 6.0.2) with no
  installs at start. The image's `/entrypoint.sh` runs `start_service.sh` from
  the repo root, which execs gunicorn on port 3838 with 2 workers.
  It only runs the scanner job the admin created (`SCANNER_JOB_ID`), using the
  job's saved parameters. It never creates or edits scripts. Shared with Civis
  admins only.
- **Connection.** No direct link between app and scanner. The app starts runs and
  reads status and `PROGRESS` log lines through the Civis API, and reads results
  from the tables. Checklist status is append-only.
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
  and waiting for Rob's approval: `start_service.sh`, the Docker test images,
  the report-only frame rule, README setup steps, and TESTING.md. Phase 7 is
  the first live test, following TESTING.md. Implementation choices not in
  the approved plan are in docs/PLAN.md sections 10 to 16.
- **App flow.** `app/__init__.create_app` -> `app/services.py` (Civis or local
  data layer) -> `app/pages.py` routes -> `app/views.py` (pure shaping) ->
  `app/templates/`. Data layer: `app/backends/civis_backend.py` (civis 2.7.1
  calls only), `local_backend.py` (dry-run preview files), `cache.py`.
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
  `retrieval/http.SafeHttp` and its host allow list. The final `RUN` log line
  adds `objects_total`, `objects_scanned`, `api_calls`,
  `github_tarball_requests`, `github_cache_peak_bytes`, and
  `github_cache_evictions`, so calls per object and memory use can be measured
  in a live test. The GitHub file cache is capped at 1 GB of text
  (`CACHE_MAX_BYTES`) with least-recently-used eviction.
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
one still running), then serves the app on 127.0.0.1 only:

```
.venv\Scripts\python.exe -m scanner --dry-run --preview --out dry_run_output
.venv312\Scripts\python.exe -m app.local --results dry_run_output
```

Open http://127.0.0.1:8050. Add `--state no-scan` (or `first-scan-running`,
`failed`, `tables-failed`, `schema-missing`, `no-job-id`) to see the empty and
error states. Local preview refuses to start if `CIVIS_API_KEY` is set.

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
  `scanner/synthetic/synthetic_instance.json`.
- Every rule needs a positive and a negative fixture in `tests/test_rules.py`.
  A test fails if a rule has none.
- `tests/test_self_scan.py` runs the tool's own rules over this repo.
- `tests/test_civis_271_compat.py` fails if `app/` or `common/` calls a civis
  method missing from civis 2.7.1, including calls through an injected
  `civis.io` module.
- `tests/test_local_time.py` runs `app/static/js/local-time.js` under Node (skipped if Node is not installed) to check local times and the UTC fallback.
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
docker build -f docker/scanner.Dockerfile -t cpvs-scanner-test .
docker build -f docker/service.Dockerfile -t cpvs-service-test .
docker run --rm --network none cpvs-scanner-test
docker run --rm --network none cpvs-service-test
```

The scanner image has no Flask, so the web app tests skip there and run in the
Service image. The Service image has no pandas or Node, so those few tests skip.

Smoke test of the Service: start it the way Civis does (the image's own
`/entrypoint.sh`, which runs `start_service.sh`), with no network and no
`CIVIS_API_KEY`, then check `/healthz` on port 3838, the log, and that no
package files changed:

```
docker run -d --name cpvs-smoke --network none -w / -e APP_DIR=/app -e REPO_PATH_DIR= -e FLASK_SECRET_KEY=smoke-test-only-not-a-real-secret-key cpvs-service-test /bin/sh -c ./entrypoint.sh
docker exec cpvs-smoke python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:3838/healthz').status)"
docker logs cpvs-smoke
docker diff cpvs-smoke
docker rm -f cpvs-smoke
```

Expected: `200`, a log with "Listening at: http://0.0.0.0:3838", "Using
worker: gthread", and two workers, and no `site-packages` paths in `docker diff`.

Held-request check: start a second container that points civis at a local
listener that accepts and never answers, so page loads hang the way a stuck
Civis query would. Nothing is added to the shipped app; the listener and the
held requests run through `docker exec`. The API key is a fake value for this
check only.

```
docker run -d --name cpvs-hold --network none -w / -e APP_DIR=/app -e REPO_PATH_DIR= -e FLASK_SECRET_KEY=smoke-test-only-not-a-real-secret-key -e CIVIS_API_KEY=smoke-test-fake-key -e CIVIS_API_ENDPOINT=http://127.0.0.1:8099/ -e SCANNER_JOB_ID=1 -e RESULTS_DATABASE=smoke -e RESULTS_SCHEMA=smoke cpvs-service-test /bin/sh -c ./entrypoint.sh
docker exec -d cpvs-hold python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 8099)); s.listen(50); held=[s.accept()[0] for _ in range(50)]"
docker exec -d cpvs-hold python -c "import urllib.request as u, threading as t; [t.Thread(target=u.urlopen, args=('http://127.0.0.1:3838' + p,), kwargs={'timeout': 400}).start() for p in ('/', '/inventory')]"
docker exec cpvs-hold python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:3838/healthz', timeout=10).status)"
docker logs cpvs-hold
docker rm -f cpvs-hold
```

Run the `/healthz` line after 35 seconds and again after 125 seconds. Expected:
`200` each time, right away, and no "WORKER TIMEOUT" in the log.

## Keeping docs current

Update this file, `README.md`, and any other docs in the repo as the project
changes. When architecture, setup, or testing steps change, update the matching
section above in the same commit.
