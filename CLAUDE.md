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
fix checklist. The scanner and the app are read-only on every Civis object; an
install script that an admin runs by hand creates and updates the scanner job
and the Service. The code is licensed
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
- **The one exception: the install script** (Rob, November 13, 2026).
  `install/civis_install.py` is not the scanner or the app. An admin reads it,
  sets its values, and runs it by hand as a Civis Python script, once to
  install and once per update. It creates the scanner job and the Service,
  and on update changes only the fields it lists, on the two objects whose
  IDs the admin gives. It never deletes, archives, or shares anything, and
  never starts a run or a deployment. Its only write calls are
  `scripts.post_containers`, `scripts.patch_containers`, `services.post`, and
  `services.patch`; a patch goes only to an object that passed the identity
  check in the same run (or that the same run created); it sends no SQL and
  never calls `civis.io` (docs/PLAN.md 17.14). The scanner and the app stay
  read-only as above and never import the install script
  (`tests/test_install_boundary.py`).
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
  credential and admins copy the install script from a release tag. Rob
  changes its visibility himself; never change it from a session.
- Releases are git tags, protected by the "release tags" ruleset. The first
  live test set up on `v0.1.0-rc5` and ran its scans on `v0.1.0-rc10`, with
  `LOOKBACK_DAYS` 30; it is finished. The post-test build waits for tag
  `v0.1.0-rc11` and a retest with a fresh install. Create a tag or a GitHub
  Release only when Rob's prompt says to.
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

- **Three packages and an install script, one repo.** `scanner/` (container
  script, `python -m scanner`), `app/` (Flask Service,
  `gunicorn "app:create_app()"`), and `common/` (shared rules, models, table
  schemas, URL builders, disclaimer). Civis clones the repo to `/app` for both
  objects. The fourth part, `install/civis_install.py`, is not a package: an
  admin pastes it into a Civis Python script and runs it by hand.
- **Install script (docs/PLAN.md 17.14).** Standard library and `civis` only
  (civis 2.10 on Civis's default Python image), no import from this repo,
  `main(client, settings=None)`. Settings at the top (`MODE` "check" by
  default, "install", or "update"; `TAG`; `RESULTS_DATABASE`,
  `RESULTS_CREDENTIAL`, `RESULTS_SCHEMA` for install; `GITHUB_CREDENTIAL`,
  `LOOKBACK_DAYS`, `RETENTION_DAYS`; `JOB_ID` and `SERVICE_ID` for update;
  `INSTALL_SERVICE_ONLY`). Every check runs before the first write. Install:
  `scripts.post_containers` (name, `required_resources`, image, tag,
  command), `scripts.patch_containers` (repository, tag, `params`,
  `arguments` with `RESULTS_DB` in the `object` shape), `services.post`
  (name, image, tag), `services.patch` (repository, tag, three variables,
  `schedule` `{"runtimePlan": "on_demand", "recurrences": []}`, no
  credentials). civis passes nested dicts as given; the script sends the
  forms the live test saved, `disk_space` in `required_resources` and
  `runtimePlan` in `schedule`, and `allowedValues` only when Civis already
  returns allowed values for a parameter. Update: identity checks read fresh before each patch, only the
  fields that differ, `RESULTS_DB` and `RESULTS_SCHEMA` kept and checked,
  `RUN_ID` removed. Output: `CHECK`, `PLAN`, `WRITE`, `RESULT`, `RECOVER`,
  `NEXT`, and one `KEEP IN CIVIS:` line with the new IDs. Its constants must
  equal `common/release.py` (a test checks each).
- **Resource profiles** (`common/release.ResourceProfile`). With a GitHub
  token: CPU 1000, memory 4096 MB, disk 4 GB. Without one: CPU 500, memory
  2048 MB, disk 1 GB. Both add 512 MB per `MAX_CONCURRENCY` thread above 4.
  The install script picks one from `GITHUB_CREDENTIAL` (on update with
  `None`, from the job's `GITHUB` value), and Setup line 2 checks the job
  against the one that matches its `GITHUB` value (below fail, above
  warning).
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
  then the five CREATE TABLE IF NOT EXISTS). Resuming a stopped scan needs no
  app write and no job edit: the scanner decides by itself (docs/PLAN.md
  17.13). A read-only Setup page runs ten
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
- **Service settings.** `SCANNER_JOB_ID` and `CIVIS_SERVICE_HEALTH_PATH`
  (`/healthz`), plus the optional `CIVIS_FRAME_ANCESTORS_REPORT_ONLY`
  (default false; blank or unset is false; the frame rule is enforced by
  default, and true is for troubleshooting only), `CIVIS_PLATFORM_URL`, and
  `CIVIS_FRAME_ANCESTORS`. There are no results settings on the Service.
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
  FATAL step. Scan A on `v0.1.0-rc6` was still in Inventory after about 1.5
  hours, so the November 6, 2026 speed build (docs/PLAN.md 17.6) inventories
  template copies once, reads only scripts in the `LOOKBACK_DAYS` window,
  logs inventory counts and progress, reads small results with
  `query_civis`, adds progress messages and a live Run scan button, and shows
  templates. Scan A on `v0.1.0-rc7` finished inventory in about 19 minutes
  but ended `tables_failed` and read no git file through Civis, fixed for
  `v0.1.0-rc8` (docs/PLAN.md 17.7): typed result frames (whole numbers as
  `Int64`, booleans as `true`/`false`), the git file read from the list
  Civis returns, the Run scan message under the button, "No completed scan
  yet" after a canceled scan, and the frame rule enforced by default.
  Scans A and B on `v0.1.0-rc8` succeeded with identical counts, and found
  four problems and two small requests, fixed for `v0.1.0-rc9` (docs/PLAN.md
  17.8): notebook files on S3 FIPS hosts, the progress pages reloading
  themselves when Civis's proxy turns a status request away, the current
  default image tag and its major tag counted as the default image, an
  archived custom script run by a workflow read as a custom script, the
  Scan history link's working message, and Findings' "How to read this
  page". Scan C on `v0.1.0-rc9` succeeded in 22 minutes, but every
  notebook was refused with HTTP 403 and live progress stopped when Civis's
  "Cookie expired." page replaced the app frame. Fixed for `v0.1.0-rc10`
  (docs/PLAN.md 17.9): a fresh notebook link right before each download,
  live progress that stops with one line instead of reloading the frame,
  run labels by Civis run ID that stay the same after retention, and a busy
  cue on every same-tab app link. The build under test is `v0.1.0-rc10`,
  with `LOOKBACK_DAYS` 30. Phase 7 testing on `v0.1.0-rc10` is done except
  a scan with a GitHub token, and the docs were updated from the live test
  (docs/PLAN.md 17.10). Post-test build part 1 (the busy cue's look,
  "Canceled" in UI text, the Findings sideways scroll at phone width, and
  the CSV link for a run left running) is done (docs/PLAN.md 17.11).
  Post-test build part 2 (Python in shell commands, one type's failed list,
  a timeout on Civis requests, and the Inventory progress wording) is done
  (docs/PLAN.md 17.12). Post-test build part 3 (resume a stopped scan
  without editing the job; `RUN_ID` is no longer used) is done
  (docs/PLAN.md 17.13). Post-test build part 4 (the install script and the
  setup rewrite) is done (docs/PLAN.md 17.14); the build waits for tag
  `v0.1.0-rc11` and a retest with a fresh install.
  Implementation choices not in the approved plan are in docs/PLAN.md
  sections 10 to 17.
- **App flow.** `app/__init__.create_app` -> `app/services.py` (Civis or local
  data layer, job settings cache) -> `app/pages.py` routes -> `app/views.py`
  (pure shaping) -> `app/templates/`. Data layer:
  `app/backends/civis_backend.py` (civis 2.7.1 calls only: `CivisResults`,
  `CivisJobs`, `CivisSetup`), `local_backend.py` (dry-run preview files and
  a made-up job and Service), `cache.py`. Reads that are small by design
  (one catalog read for the existence and dedicated-schema checks, Setup line
  9's counts, and `scan_runs`) use `query_civis` with 100 preview rows
  (text values, converted by column type; `read_civis_sql` when a result
  has 100 rows or more, so `scan_runs` after 50 scans) and a per-request memo on `g`, cleared by any write; the
  objects, findings, coverage, and checklist rows use `read_civis_sql`, and
  each page reads only the tables it shows (docs/PLAN.md 17.6). Inventory
  and Findings show each custom script's template and backing script IDs,
  link the backing script only when it is in the same scan (its stored
  link; no URL pattern is guessed), and filter by template ID; the Overview
  shows the next scan's window, the scripts left out, and the templates with
  findings. While Run scan would resume the newest stopped scan, the Overview
  says so under Run scan, with the end of the 24-hour window in local time
  (`views.resume_window`: `common/resume.decide` on the `scan_runs` rows,
  `jobs.runs()`, `__version__`, the job's `LOOKBACK_DAYS`, and
  `JobSettings.github_attached`; it cannot check `results_saved`, so the
  scanner decides; `Services.wall_clock` is the time). The failed and
  canceled empty states say the same. `Progress` parses the `RESUME` line:
  a resumed run's progress headline starts with "Resuming <label>.", and
  Scan history shows a resumed scan once with "Resumed <n> time(s)"
  (`ScanRun.civis_run_ids`). Setup line 4 is "review" while the job has a
  `RUN_ID` (any value or none), and the test summary adds
  `resume: <decision> [reason=<reason>]` (docs/PLAN.md 17.13). Each scan is
  labeled "Civis run <ID>" by the Civis run that
  started it (`ScanRun.first_civis_run_id`, from the earliest `scan_runs` row
  with one, so a resumed scan keeps it), or "Scan <run ID>" with none; the
  label stays the same when retention removes older runs, and
  `ScanRun.civis_run_id` (the newest row's) still drives Download CSV
  (docs/PLAN.md 17.9). `app/static/js/busy.js` shows a short busy cue for a
  plain click on a same-origin link that loads another app page in the same
  tab, and for every form sent except Run scan: the `is-busy-trigger` class
  on the clicked link or the form's submit button (gray `--busy-trigger`, no
  underline or hover change, progress cursor; a primary button gets a gray
  background; the button is never disabled), `aria-busy` on main, and the
  `is-loading` body class (progress cursor, main at 0.7 opacity, the lowest
  that keeps every text pair in main at 3 to 1; no text is added;
  docs/PLAN.md 17.11). Download CSV links have
  `data-no-busy`; new-tab, modified, hash, and download links get no cue,
  nor does anything while the checklist has unsaved changes. Links and forms
  with `data-busy` (the Setup and Scan history links among them) also show
  "Working. This can take up to a minute." Coming back from history clears
  everything, the trigger's class too (docs/PLAN.md 17.9). Scan history also
  links the CSV of a scan whose newest `scan_runs` row is "running" once
  Civis has ended its run, and a run with no CSV output gets the error page
  "This scan has no CSV report. It stopped before saving one." (status 404;
  docs/PLAN.md 17.11). `.table-wrap` is `position: relative`, so it clips
  the `.sr-only` text in its table and no page scrolls sideways at phone
  width. `app/static/js/run-scan.js` starts a scan
  from the Overview without a reload (`POST /scan/start` answers JSON when
  asked) and polls the status API only while a run is starting or active.
  `app/static/js/status-requests.js`, loaded before `run-scan.js` and
  `scan-status.js`, sends their requests. A request is turned away when
  Civis's proxy refuses it (its cookie, which only a reload of the whole
  Civis page renews, expired; a rejected fetch, a response that is not ok,
  not JSON, or not a JSON object). The page never reloads itself because a
  request was turned away: after 2 turned-away polls in a row it stops
  polling and shows the stop line (reload the whole Civis page, F5; reloading
  only the frame does not help), a success resets the count, a hidden page
  behaves the same, and nothing uses `sessionStorage` (docs/PLAN.md 17.9;
  17.8's frame reload is gone). Scan history still reloads once when a scan
  finishes, right after a successful poll. Findings has a closed "How to read this page" section whose tag
  values come from `common/release.py` (`views.default_tag_help`). `app/job_settings.py` parses the
  job's parameters, `app/setup.py` gathers and evaluates the Setup checks,
  `app/summary.py` builds the test summary, and `common/release.py` holds
  what the README sets (pinned images read from the requirements files) and
  the current default image tags (`CURRENT_DEFAULT_TAGS`).
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
  `checkpoint.Checkpointer` -> `output` sinks. **Resume (docs/PLAN.md
  17.13).** After the schema check and `ensure_tables`, before the "running"
  row, `runner._resume_decision` reads the `scan_runs` rows
  (`sink.scan_run_rows`), the stopped scan's newest Civis run of this job
  (`scripts.get_containers_runs` through the `BudgetedClient`; state and end
  time only), and `sink.has_summary_rows`, each only when needed, and runs
  `common/resume.decide` (only the newest scan; `RESUME_WINDOW_HOURS` 24,
  `MAX_RESUMES` 2; fixed reasons in `common.resume.REASONS`). A resume keeps
  the run ID, skips finished objects, keeps earlier rows, and uses the first
  start. One `RESUME decision=resume run_id=<id> resumes=<n>` or
  `RESUME decision=new reason=<reason>` line comes right before
  `RUN ... status=running`. A failed read starts a new scan (`read_failed`),
  never stops it. `ScanConfig.from_env` no longer reads `RUN_ID`; a live run
  that still has one logs `RUN_ID_IGNORED`. The dry run keeps `--run-id`.
  The engine also scans Python
  passed inline to an interpreter in shell text (`engine.python_in_shell`,
  docs/PLAN.md 17.12): `python`, `python3`, or `python3.N` (a path or `env`
  in front, options before `-c`) with `-c <code>`, split with `shlex`, and a
  heredoc on the interpreter's stdin (`python - <<EOF`, `python3 <<'PY'`,
  `<<-END`), whose body is not also checked as shell. It applies to
  `scan_shell` (container, template, and workflow task docker commands,
  notebook shell cell magics), notebook `!` lines, and Dockerfile RUN lines.
  Lines map to the shell text (quoted multi-line `-c` counts from the line
  where the quote opens), labels are the unit's, code that does not parse
  uses the regex fallback (partial, `PARSE_ERROR`), and a rule matched as
  Python and shell on one line is reported once. Not covered: piped code,
  `bash -c` holding `python -c`, here-strings, `%%script python3`, and
  `python script.py`; a line `shlex` cannot split gets the shell rules only.
  Every Civis API call goes
  through `ratelimit.BudgetedClient`; every other HTTP call goes through
  `retrieval/http.SafeHttp` and its host allow list. `retrieval/civis_git.CivisGitFiles`
  reads an item's own git file through the Civis API: a git-backed workflow's
  definition during inventory (`workflows.get_git` is called for every active
  workflow), and a git-backed Python script's file in `process_item` when
  there is no token or GitHub fails. A notebook's file is downloaded from a
  link asked for right before the download (`retrieval/notebooks.NotebookLinks`
  calls `notebooks.get` through the `BudgetedClient`): each link expires 5
  minutes after Civis issues it, so inventory does not keep `notebook_url`. A
  download refused with HTTP 403 or 400 gets one more link and one more
  download; a failed `notebooks.get` leaves the notebook not scanned with a
  short reason (docs/PLAN.md 17.9). The file call returns a JSON array with
  one object (`content` as plain text, `fileHash`, `size`, `type` "file"),
  which civis 2.10 turns into a `ListResponse`; `_file_text` accepts it and
  the spec's one object (several items: the one file with content, else
  `GitFileAmbiguous`; docs/PLAN.md 17.7). `CivisSink.append` writes the
  frame from `output.table_frame`, typed from the table's columns, because
  civis writes it with `df.to_csv(index=False)` and a float in a
  whole-number column fails the load. An object whose code was not read but
  whose log was is partial. The final `RUN` log line adds `objects_total`,
  `objects_scanned`, `api_calls`, `github_tarball_requests`,
  `github_cache_peak_bytes`, `github_cache_evictions`, `civis_git_reads`,
  `civis_git_failures`, `notebook_links` (`notebooks.get` calls for
  downloads, retries included), `notebook_link_retries` (second links
  after a refused download), and `request_timeouts` (Civis API requests that
  timed out), so calls per object, memory use, Civis git reads, notebook
  links, and timeouts can be measured in a live test. Failed Civis git reads also get a
  `CIVIS_GIT_FAILED` line. The GitHub file cache is capped at 1 GB of text
  (`CACHE_MAX_BYTES`) with least-recently-used eviction.
- **Scanner users, hidden objects, retries, FATAL (docs/PLAN.md 17.5).**
  `inventory/users.load_users` lists `account_status="all"`; on HTTP 403 or
  404, or a request timeout (`ratelimit.is_request_timeout`, docs/PLAN.md
  17.12.24), it lists active users (an owner not in that list is unknown,
  never False); any other error on "all" still raises; if the active list
  fails too, there is no user lookup. A `USERS source=all|active|none
  [status=N | reason=timeout]` log line (for "none", the active list's own
  cause) and, when not "all", a coverage row with `row_kind` `users` and a
  fixed note ("(HTTP 404)" or "(the request timed out)") record it; the
  Overview shows the note. Hidden objects are skipped and not counted: no list call sends
  `hidden`, and `hidden_skipped_count` is written NULL (the column stays).
  `live_run` gives `civis.APIClient` the `ratelimit.civis_retrying` policy (4
  tries or 120 seconds, waits up to 30 seconds; tenacity is pinned in
  `requirements-scanner.txt`). civis 2.10 holds one class-level lock for each
  whole request, retries included, so a slow call blocks every other API call
  in the scan. civis sets no request timeout, so `live_run` first calls
  `ratelimit.install_request_timeout()` (docs/PLAN.md 17.12): it wraps
  `open_session` in `civis.base` and `civis.resources._resources` so every
  session's `send` gets `timeout=(15, 120)` (connect, read) when the caller
  gave none, counts timed-out requests (`request_timeouts`), and logs
  `REQUEST_TIMEOUT installed=yes connect=15 read=120` (or `installed=no`
  when civis has no `open_session`). requests is never patched globally. A
  timeout raises at once (civis retries only on status codes), so one
  request holds the lock about 285 seconds at most (a `Retry-After` header
  or a server that keeps sending slowly can make it longer). A list call
  that fails keeps the rows read (`base.ListFailed`), logs `LIST_FAILED
  type=<type> rows_read=N error=<type> [status=N]`, adds a coverage row with
  `row_kind` `list_failed` (rows read in `found_count`, a fixed note from
  `common/models.list_failed_note`; the Overview shows one warning per row,
  and the test summary adds `list_failed:` lines), and the scan goes on
  (docs/PLAN.md 17.12). `run_scan` tracks the step and
  logs `FATAL error=<type> [status=N] step=<step>` (steps in
  `scan_log.FATAL_STEPS`; a list step only when all seven lists failed, with
  the first failure's error and step), then appends a final "failed"
  `scan_runs` row if the "running" row was written and the tables are
  usable, and raises `ScanFailed`. `SchemaNotDedicated` still writes
  nothing.
- **Inventory order and the window (docs/PLAN.md 17.6).** `build_inventory`
  lists every type first (all pages; no early stop, because `updated_at` does
  not change on a run), then chooses rows: a Python, container, or dbt row
  with `from_template_id` whose ID the custom list also returned is a
  template copy (skipped, counted once as a custom script), then the list-row
  not-Python checks, then the window: a Python, container, custom, or dbt
  script is kept when its last run time (finished, else started, else
  created) or `updated_at` is within `LOOKBACK_DAYS` of the scan's start (a
  resume uses its first start); notebooks, Services, and workflows are always
  kept. Then the detail calls. `LOOKBACK_DAYS` defaults to 400
  (`common/release.DEFAULT_LOOKBACK_DAYS`, range 1 to 3650) and still decides
  whose run log is read. One object ID is one inventory item. Coverage gets
  `outside_window` and `template_copies` rows per type (`found_count`, no
  column change). One `INVENTORY type=... listed= kept= outside_window=
  template_copies= not_python=` line per listed type, then `PROGRESS
  phase=inventory` every 50 objects or 30 seconds during the detail calls;
  the FATAL step for detail calls is `inventory_details`. Every script ID
  listed is known to workflow references, so no `jobs.get` for it.
- **Default-image rule (docs/PLAN.md 1.4 and 17.8).** `classify_image` in
  `scanner/inventory/base.py` (every object type goes through it) counts a
  known Civis default image as the default when its tag is blank, "latest",
  the image's current default tag, or that tag's major part
  (`is_default_tag(image, tag)`). The current tags are in
  `common/release.CURRENT_DEFAULT_TAGS` (`datascience-python` `9.1` and
  `civis-jupyter-python3` `5.1`, the tags Civis saved on new objects made
  with no tag in the live test); update them there when Civis changes its
  default images. The major tag is derived, never stored. A patch tag
  (`9.1.1`) or an older minor tag is pinned. Streamlit has no known current
  tag, so Services keep blank or "latest". dbt scripts are checked as
  datascience-python tags. An archived job that an active workflow runs is
  a custom script when its `jobs.get` response has `from_template_id`
  (checked before the type words), and is built with `custom_item` and the
  scan's one `TemplateCache`.
- **Live test.** TESTING.md is the checklist for the first live test. Records
  are pass or fail and aggregate counts only.

Constraints to keep in mind when writing code:

- `app/` and `common/` may only call civis methods that exist in civis 2.7.1. A
  unit test enforces this. `install/civis_install.py` runs on civis 2.10 and
  may call only its four write methods and the listed reads
  (`tests/test_install_script.py`).
- App text that cites the README names the section in quotes and its step
  numbers (README "Install", step 8). `tests/test_readme_citations.py` fails if
  a cited section or step is missing, so change citations in the same commit
  as the README.
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
one canceled by Civis run 504 that Civis run 505, still running, resumed by
the scanner's own decision; all with no GitHub token, the normal setup), then
serves the app on 127.0.0.1 only:

```
.venv\Scripts\python.exe -m scanner --dry-run --preview --out dry_run_output
.venv312\Scripts\python.exe -m app.local --results dry_run_output
```

Open http://127.0.0.1:8050. Add `--state no-scan` (or `first-scan-running`, `first-scan-cancelled`,
`failed`, `tables-failed`, `schema-missing`, `no-job-id`, `job-unreadable`,
`database-unrecognized`, `schema-refused`, `tables-missing`,
`setup-failures`, `not-scanner-job`, `schema-shared`, or `list-failed`) to see the empty and
error states. `not-scanner-job` points SCANNER_JOB_ID at another job, and
`schema-shared` has a results schema that holds other tables. `schema-missing`,
`schema-refused`, and `tables-missing` look like a new install with the "Set
up results storage" button (`schema-refused` shows the statement for a
database admin); `setup-failures` makes several Setup page lines fail.
`list-failed` gives every scan a made-up `list_failed` coverage row (custom
scripts, 150 rows, HTTP 503), so the Overview shows its warning.
`first-scan-cancelled` also shows the Overview's resume line for 24 hours
after the preview was built. Local preview refuses to start if
`CIVIS_API_KEY` is set.

## Testing commands

Run the full suite on both Python versions before every commit:

```
.venv\Scripts\python.exe -m pytest
.venv312\Scripts\python.exe -m pytest
```

- `tests/test_security.py` proves the SECURITY.md requirements: no secret or
  personal data from logs, code, or notebook output reaches any output,
  no text from Python run with `python -c` or a heredoc in a docker command
  (parsed or not) reaches any output (docs/PLAN.md 17.12),
  no notebook link (host, bucket, query string, or signature) reaches a log
  line, table row, CSV line, or exception text, malicious schema names are
  rejected, keys in exceptions are redacted, and values from Civis render as
  text in the web app.
- `tests/test_notebook_links.py` covers the fresh notebook link (docs/PLAN.md
  17.9): the synthetic `notebooks.get` signs a new link on every call on a
  `SyntheticClock` (`scanner/synthetic/links.py`), and the synthetic file
  host refuses a link more than 300 seconds old with 403 AccessDenied
  ("Request has expired"). `dry_run(..., file_clock=)` shares one clock;
  `notebook_download_statuses` and a notebook's `_get_errors` force answers.
  The tests cover a dry run whose code scan starts 301 seconds after
  inventory, the 403 and 400 retry, no retry for other statuses, a failed
  link call, the counts, a resumed run, and no extra calls for other types.

- Unit tests make no Civis or network calls. Inventory tests use the
  synthetic client in `scanner/synthetic/` (re-exported by
  `tests/fake_civis.py`) with the made-up instance in
  `scanner/synthetic/synthetic_instance.json`. Like Civis in the live test,
  its `users.list` refuses `account_status` "all" and "inactive" with HTTP
  404 (`users_list_errors`), so the dry run and the preview use the active
  list; tests set `users_list_errors` to `{}` for the "all" shape. Its
  `list_errors` setting (keyed by object type, `{"status": N, "after": N}`)
  makes one type's list return some rows and then fail.
- `tests/test_fatal.py` covers the FATAL line and the final "failed" row
  (including every list failing, and one failed list no longer stopping the
  scan), and `tests/test_retries.py` drives the retries policy through
  civis-python's own `retry_request` (it skips on civis 2.7.1).
- `tests/test_shell_python.py` covers Python in shell text (docs/PLAN.md
  17.12): each `-c` and heredoc form, quoted multi-line `-c`, separators, a
  line `shlex` cannot split, the regex fallback, the dedupe, negatives,
  Dockerfiles, notebooks, and synthetic container script 212 and workflow
  601's container task.
- `tests/test_list_failed.py` covers a list that fails on the first page or
  partway (rows kept, `LIST_FAILED`, the `list_failed` row and note, other
  types scanned, status succeeded), the custom and workflow lists, the test
  summary lines, and no exception text in any output.
- `tests/test_resume.py` covers the resume decision (docs/PLAN.md 17.13):
  every reason with made-up rows and times (the window edge, one and two
  resumes, version, lookback, a token added or removed, saved summary rows,
  an active run), and dry runs that resume a canceled and a failed scan
  (finished objects skipped, the first Civis run's label kept), start a new
  scan after a resumed one finishes, refuse a third resume, start a new scan
  on a failed or timed-out read, unusable tables, or another job's run,
  charge the read to the budget, ignore `RUN_ID` with `RUN_ID_IGNORED`, and
  keep names and Civis text out of the `RESUME` line. The synthetic
  `scripts.get_containers_runs` answers from `container_runs`
  (`"<job ID>/<run ID>": {state, finished_at}`; 404 when missing, `_error`
  for a status or timeout).
- `tests/test_install_script.py` drives the install script with a recording
  fake civis client (18 scenarios): every call exists in the civis 2.10 API
  spec and binds with its arguments (skipped on civis 2.7.1), only the four
  write calls and the listed reads, every patch to an object read right
  before it or just created, refused jobs and Services with no write, check
  mode writing nothing, install's post and patch fields for each token case,
  update keeping the results settings and removing `RUN_ID`, the recovery
  lines, no marker from names, credentials, or error messages in any printed
  line, no SQL or `civis.io`, the constants against `common/release.py`, and
  standard-library and `civis` imports only. `tests/test_install_boundary.py`
  checks that `scanner/`, `app/`, and `common/` never import it.
- `tests/test_readme_citations.py` parses every README section and numbered
  step that a string in `app/` cites (README "<section>", step N) and fails
  if one is missing or a citation leaves its section unquoted.
- `tests/test_users_timeout.py` covers a timeout on the "all" users list
  (falls back to active, `reason=timeout`), on both lists, a timeout then a
  404, a 500 still FATAL, the summary's `users_list` values, and no
  exception text in any output. The synthetic `users_list_errors` takes
  `"read_timeout"`, `"connect_timeout"`, or `"body_read_timeout"`.
- `tests/test_request_timeout.py` runs civis 2.10's own client with
  `requests.adapters.HTTPAdapter.send` stubbed (the bundled API spec for
  `/endpoints`, a made-up host): every request carries the timeout, a
  caller's own is kept, a timeout raises, is counted, and releases civis's
  lock, a missing `open_session` installs nothing, a second call wraps
  nothing twice, and the RUN line and test summary carry `request_timeouts`.
  It skips on civis 2.7.1.
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
- The fakes also match two shapes confirmed in scan A on `v0.1.0-rc7`
  (docs/PLAN.md 17.7). Every fake `dataframe_to_civis` renders the frame with
  `df.to_csv(index=False)`, as civis 2.10 does, and checks every typed cell
  (`tests/civis_fakes.load_frame_like_civis`: INTEGER and BIGINT digits or
  empty, BOOLEAN `true`, `false`, or empty); a bad cell makes the load's
  future raise `CivisJobFailure`, so the scan ends `tables_failed`.
  `RecordingIO.loaded` holds each load's CSV text. The synthetic client's
  git file call answers a list with one object by default (`"file_shape":
  "object"` gives the spec's shape). `tests/test_typed_columns.py`,
  `tests/test_left_behind_run.py`, and `tests/test_empty_states.py` cover
  the typed frames, a run left by the old failure (and its CSV link and
  no-report page, docs/PLAN.md 17.11), and the Overview's empty states.
- `tests/test_app.py` covers every page and API, CSRF on every POST, escaping
  of hostile object names, security headers, the concurrent-run guard, the
  batched checklist save, the empty and error states, text contrast (4.5 to
  1; in the busy cue's dimmed state, 3 to 1 for every text color in main,
  with the opacity the lowest that keeps it; the trigger gray at 4.5 to 1 on
  the header and nav), the trigger CSS rules, `data-no-busy` on every
  download link, no "cancelled" in any page's shown text in any preview
  state, every table and its `.sr-only` text inside a `.table-wrap`, the
  preview's canceled scan's CSV link and no-report page, the Overview's
  list warning in the `list-failed` state (escaped, from `list_failed` rows
  only), and local preview mode, and the templates display and filter.
- `tests/test_run_labels.py` covers the run labels (docs/PLAN.md 17.9): the
  same after the oldest runs are removed, a resumed scan's first Civis run
  ID, the "Scan <run ID>" fallback, the preview's four scans (Civis runs 501
  to 504), and no numbered "Run N" label left.
- `tests/test_read_counts.py` checks how many `civis.io` reads each page
  sends, cold and warm, and the `query_civis` preview limit, fallback,
  conversion, and request memo. The recording fake's `query_civis` answers a
  SELECT like civis 2.7.1 (`result_columns` and text `result_rows`, zero rows
  normal); its `reads` holds every read, `full_reads` and `small_reads` split
  them, and `queries` holds writes only.
- `tests/test_scripts_js.py` runs `busy.js`, `run-scan.js`, and
  `scan-status.js` (the last two after `status-requests.js`) under Node with
  a stand-in page (skipped if Node is not installed) and checks the markup
  and the JSON start. The stand-in page has `sessionStorage` (working,
  throwing, or missing, with a count of every use), a clock (`now`, the
  `advance` step), `document.hidden` (the `visibility` step), intervals,
  answers held until a `release` step, and a reload counter, for the
  status-requests tests: each kind of turned-away answer counts, one keeps
  polling and two in a row stop with the stop line, a success resets the
  count, no reload and no `sessionStorage` use on either page (hidden or
  visible, every storage mode), a turned-away POST shows its message and is
  sent once, and the reload when a scan finishes (docs/PLAN.md 17.9). The
  stand-in page also has a small DOM (nesting, `createElement`,
  `insertBefore`, `classList`, `closest`, tag and attribute selectors) and a
  page address, for the busy cue tests: nav, pagination, and Clear filters
  links and the submit buttons of a filter form and Save changes get the
  `is-busy-trigger` class, the buttons stay enabled, and nothing is added
  to the page (no " Loading" text); new-tab, modified, hash, and download
  links and the Run scan form get no cue; `data-busy` gets the cue and the
  message; pageshow clears it; a second click adds nothing (docs/PLAN.md
  17.11).
- `tests/test_default_tags.py` covers the default-image rule for every
  object type (current tag, major tag, patch and older tags, tags on the
  wrong image, spaces, a tag inside the image name), the
  `default_image_only` rule, the synthetic objects saved on the current
  tags, the preview, and that the README states the same tags.
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
