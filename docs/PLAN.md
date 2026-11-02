# Build Plan: Python Migration Scanner for Civis Platform by Expedition Data

Status: **approved October 9, 2026.** Written the same day, after checking the build brief against current documentation. The decisions on every open question are recorded in section 9. Where this plan and the build brief differ, this plan wins.

This file records design decisions and intended behavior as they were made. It is not a statement of guarantees. [SECURITY.md](../SECURITY.md) describes how the tool behaves, and [LICENSE](../LICENSE) holds the warranty disclaimer.

Sources checked (all read October 9, 2026):

- civis-python v3 changelog (`v3-changelog` branch), `main` branch source, PyPI release history
- Python 3.15 "What's New", including "Porting to Python 3.15" and "Removed" (Python 3.15.0 was released October 9, 2026)
- civis-python docs on Read the Docs and the API spec bundled with civis-python (`src/civis/resources/civis_api_spec.json`)
- Civis support articles: API Basics, Civis Service Deployment, Service environment variables, Public Civis Docker Images, Hidden Objects, User Deactivation, Release Notes
- Civis public repos and Docker Hub images: `civis-services-flask`, `civis-services-streamlit`, `datascience-python`, `civis-jupyter-python3`

Facts marked "unverified" could not be confirmed from documentation and need a live test in Civis.

---

## 1. Where the docs differ from the brief

**Conclusion: the brief's architecture holds up. Twelve details need to change. None of them block the build.**

| # | Brief says | Docs say | Proposed fix |
|---|---|---|---|
| 1.1 | Deploy the Service with Civis's Flask Service image. | The image exists (`civisanalytics/civis-services-flask`), but it is stale. The newest tag, `0.1.0` (August 6, 2025), ships Python 3.12.11, Flask 3.1.1, gunicorn 22.0.0, and civis 2.7.1. The image does not install `requirements.txt` itself. Our repo must provide an executable `start_service.sh`, and the example script runs `pip install -r requirements.txt`, which downloads from PyPI on every start. | Use `civis-services-flask:0.1.0` with **no pip install at start**. The web app uses only what the image already has: Flask, gunicorn, and civis 2.7.1. This keeps the network rule intact (no PyPI calls). See section 8 and decision 1. |
| 1.2 | Network calls are limited to the Civis API, Civis file links, and GitHub. | The standard Flask Service pattern installs packages from PyPI at every start. | Same fix as 1.1. The scanner image already has every package we need, so the scanner makes no PyPI calls either. |
| 1.3 | Services are in scope as "affected." | Civis's February 2027 notice covers container scripts, Python scripts, Python notebooks, and **Streamlit**. It does not mention Flask, Shiny, or other Service images. Also, a new Service defaults to the Shiny image, not to a Python image. | Mark a Service as "affected" only when it uses `civis-services-streamlit` with a blank or `latest` tag. Mark all other Services "advisory." See decision 12. |
| 1.4 | The default-image rule is "blank or `latest` tag on a known Civis image." | Each object type stores its image differently. Python scripts have a `docker_image_tag` but **no image name field** (they always use `datascience-python`). Notebooks use `civis-jupyter-python3` and keep their tag until someone changes it. Custom scripts have no image fields; they use their template's backing script. | Known default images: `civisanalytics/datascience-python`, `civisanalytics/civis-jupyter-python3`, `civisanalytics/civis-services-streamlit`. For Python scripts, the image name is implied. For custom scripts, read the image from the backing script. If the backing script is unreadable, mark impact "unknown." |
| 1.5 | The `feature_flags` fix is `client.users.list_me()["feature_flags"]` (from context.md). | `list_me` is itself removed in v3. The client's own warning says `client.users.get_me()["feature_flags"]`. | Use `get_me` in the fix guidance. |
| 1.6 | Seed rules with every removal in the changelog. | The changelog misses one breaking change. In v3, the default for `civis.utils.run_template(..., return_as=...)` changes from `"files"` to `"future"`. This is stated in the source (`utils/_jobs.py`), not in the changelog, and no warning is emitted for it. | Add rule `CV3-RUN-TEMPLATE-DEFAULT` at medium confidence. See decision 7. |
| 1.7 | The log scan is medium confidence because the changelog gives no exact warning text. | civis-python's source defines exact warning strings. All v3 deprecations raise `FutureWarning`, which Python shows by default. Since civis 2.9.3, every message contains "civis-python v3.0.0." | civis log matches can be **high** confidence evidence that the job used a removed feature on its last run. Python 3.15 log matches stay medium, because Python hides `DeprecationWarning` by default outside the main script. See decision 6. |
| 1.8 | The scanner job parameters include a schema name and a database credential. | Writing tables also needs a database. Civis has a "Database" parameter type that supplies the database name and credential together (`<NAME>_NAME`, `<NAME>_CREDENTIAL_ID`, and so on). | Replace "database credential" with one Database parameter, plus the schema name. |
| 1.9 | The `/` route must return quickly. | Confirmed. Platform checks for HTTP 200 on `/`, and the path can be changed with the `CIVIS_SERVICE_HEALTH_PATH` environment variable. | Add a `/healthz` route and set `CIVIS_SERVICE_HEALTH_PATH=/healthz`. `/` returns the page shell right away, and the scan summary loads in the browser afterward. |
| 1.10 | The Service sleeps after about one hour without traffic. | Confirmed for the "On Demand" plan. The idle time is not configurable. "Always On" Services are redeployed weekly with a new API key. A Service that does not start within one hour is terminated. | No change to the design. Never cache an API client for longer than one request cycle. The key changes on wake and on redeploy. |
| 1.11 | Workflows are scanned only to map which jobs they run. | Workflow YAML can define jobs inline (for example a `civis.scripts.python3` task with a `source` field). That code exists nowhere else. | Also scan inline code in workflow definitions. See decision 10. |
| 1.12 | No mention of hidden objects. | Workflow-created job copies and `read_civis` query jobs are "hidden" and only appear when listed with `hidden=True`. | Skip hidden objects, but count them in coverage. See decision 11. |

Facts from the brief that the docs confirm:

- The Service's `CIVIS_API_KEY` belongs to the **Service owner**, not the viewer.
- Credentials attached to a Service or script appear as environment variables (`<NAME>_USERNAME`, `<NAME>_PASSWORD`, `<NAME>_ID`).
- Port 3838 is correct.
- civis-python 2.8.0 and later waits at least the `Retry-After` time on HTTP 429.
- Container scripts run with the API key of the user who runs them. The default `user_context` is `runner`.
- Deactivated users are included when listing with `account_status="all"`.

New facts the brief did not cover:

- **Viewer identity is available.** Civis adds `Civis-User-Id`, `Civis-Username`, and `Civis-User-Org-Id` headers to every request to a Service. We will use these to record who changed a checklist item.
- **Service environment variables are visible to anyone with read access to the Service.** Never put a token in them. The GitHub token stays a credential on the scanner job only.
- **Run outputs expire.** Files uploaded with `civis.io.file_to_civis` expire after 30 days by default. Pass `expires_at=None` for the CSV report.
- **Container scripts run for at most 5 days.**
- **No civis-python v3 prerelease exists yet.** The latest release is 2.10.1 (October 6, 2026), and v3.0.0 is scheduled for February 2027. "Test against v3" is blocked until a release candidate is published.

### Can a Service's API key create, update, and run a container script?

**Conclusion: probably yes, but it is unverified. Design so that the app only needs to *run* an existing job.**

- Civis documents no scope limit on the key injected into a Service. The key is the owner's, so it should have the owner's permissions.
- API key scopes do exist, but only for keys a user creates by hand.
- Proposed design: the admin creates the scanner container script once, following the README. The Service gets its job ID through a `SCANNER_JOB_ID` environment variable.
- The app then calls only `post_containers_runs` (start), `get_containers_runs` and `list_containers_runs` (status), log reads, and run output reads. It never creates or edits scripts. See decision 2.
- TESTING.md will include a one-time check that the deployed Service can start the scanner job.

---

## 2. Endpoints by object type

**Conclusion: every object type can be inventoried and its code retrieved with documented civis-python methods.**

Only 5 of the methods we need are renamed in v3:

| v2 name | v3 name |
|---|---|
| `users.list_me` | `users.get_me` |
| `scripts.list_python3_git` | `scripts.get_python3_git` |
| `notebooks.list_git` | `notebooks.get_git` |
| `notebooks.list_update_links` | `notebooks.get_update_links` |
| `workflows.list_git` | `workflows.get_git` |

The scanner uses the `get_*` names, which exist from civis 2.8.0. Every other method we use keeps its name in v3.

All list calls use `iterator=True`, `limit=50` (the maximum for most list endpoints), and `archived="false"`. Field names below are as civis-python returns them (snake_case).

| Object type | List | Detail | How we get the code | Image and git fields | Log scan |
|---|---|---|---|---|---|
| Python script | `scripts.list(type="python3")` | `scripts.get_python3(id)` | `source` field. If git-backed, `scripts.get_python3_git(id)` gives the repo, ref, and path, and we fetch from GitHub. | `docker_image_tag` only; the image name is always `datascience-python` | `scripts.list_python3_runs_logs(id, last_run.id)` |
| Container script | `scripts.list(type="containers")` | `scripts.get_containers(id)` | `docker_command` text. If `repo_http_uri` is set, fetch from GitHub at `repo_ref` and scan every `.py` file up to a file cap. | `docker_image_name`, `docker_image_tag`, `repo_http_uri`, `repo_ref`, `git_credential_id` | `scripts.list_containers_runs_logs(id, last_run.id)` |
| Custom script | `scripts.list_custom()` | `scripts.get_custom(id)` | `from_template_id`, then `templates.get_scripts(template_id)` to get the backing `script_id`, then fetch that script as above (cached per template). If the backing script is unreadable, use `code_preview`. | Taken from the backing script | `scripts.list_custom_runs_logs(id, last_run.id)` |
| Notebook | `notebooks.list()` | `notebooks.get(id)` | Download the `.ipynb` from `notebook_url` (a time-limited Civis file link) and scan the code cells. If git-backed, use `git_repo_url`, `git_ref`, and `git_path`. | `docker_image_name`, `docker_image_tag` | No log scan. Scan saved cell outputs for warning text instead (see decision 13). |
| Service (including Streamlit) | `services.list()` | `services.get(id)` | Services have no inline code. Fetch from GitHub with `git_repo_url`, `git_repo_ref`, and `git_path_dir`. | `docker_image_name`, `docker_image_tag`; `current_deployment` | `services.list_deployments_logs(id, current_deployment.deployment_id)` |
| Workflow | `workflows.list()` | `workflows.get(id)` | Parse the `definition` YAML. `civis.run_job` tasks reference `input.job_id`. `civis.workflows.execute` references `input.workflow_id`. Inline script tasks (`civis.scripts.python3`, `civis.scripts.container`, `civis.scripts.custom`) carry code in `input`. | None on the workflow itself | None. Referenced jobs are scanned as jobs. |
| Archived job in an active workflow | Found through workflow references | `jobs.get(id)`, then the type-specific `get_*` | Same as its type | Same as its type | Same as its type |
| Users | `users.list(account_status="all", limit=10000)` once per scan | `users.get_me()` for the scanning user | n/a | n/a | n/a |
| Cross-check | `jobs.list()` | n/a | n/a | n/a | n/a |

Notes:

- **Last run.** `last_run` (id, state, finished_at) comes back on the detail response, so finding the most recent run costs no extra call. Log endpoints return up to 10,000 lines per call and page with `last_id`.
- **Rows from scan context.** Owner name, deactivated flag, and Civis URL come from the users list and the object ID.
- **To confirm in phase 2:** which field marks a Python script as git-backed (so we can skip `get_python3_git` for inline scripts), and whether `hidden=True` returns hidden items only or all items.
- **Unparseable code.** The scanner parses code with Python 3.14's `ast` module. Files that do not parse fall back to regex rules and are recorded in coverage as "partial."
- **GitHub.** We use the REST API: one tarball or tree request per repo and ref, cached for the whole scan. Non-GitHub hosts are marked "not scanned, review manually" (see decision 14).
- **Fetched code stays in memory.** Code from GitHub and notebooks is held in memory, or in a temporary directory deleted when the run ends. It is never written to run outputs, tables, or logs.
- **Writing results.** We use `civis.io.dataframe_to_civis` for tables, and `civis.io.file_to_civis` plus `scripts.post_containers_runs_outputs(..., "File", file_id)` for the CSV.

---

## 3. Repo layout

**Conclusion: one repo with three Python packages (`scanner`, `app`, `common`). Both Civis objects clone the same repo to `/app` and run from there.**

```
python-migration-scanner-for-civis-by-ed/
  scanner/                  Container script entry point: python -m scanner
    __main__.py             Reads job parameters, runs a live scan or a dry run
    config.py               Job parameters from environment variables
    runner.py               One scan from start to finish
    process.py              Per object: code retrieval, rules, log scan
    inventory/              One module per object type (python3, containers, custom,
                            dbt, notebooks, services, workflows, users), plus base.py
                            for shared helpers and __init__.py for build_inventory()
    retrieval/              Inline code (code.py), notebook download, GitHub fetch,
                            http.py (SafeHttp and its host allow list), tempdir.py
    logs.py                 Log scan (most recent run only)
    engine.py               Runs AST and regex rules over code and log text
    checkpoint.py           Resume by run ID
    output.py               Table writes (Civis and SQLite), CSV run output
    ratelimit.py            Shared request budget across worker threads
    scan_log.py             The scanner's only log writer (structured lines)
    synthetic/              Made-up Civis instance, client, and HTTP for dry runs
                            and tests; preview.py seeds the local app preview
  app/                      Flask Service: gunicorn "app:create_app()"
    __init__.py             create_app()
    pages.py                Routes: Overview, Scan history, Inventory, Findings and
                            checklist, CSV download, /healthz, /api/scan-status
    services.py             Connects pages to the Civis or local data layer
    views.py                Pure shaping of rows for templates
    settings.py             Service environment variables
    security.py             Headers, CSP, CSRF, safe JSON and links
    local.py                Local preview on 127.0.0.1 (python -m app.local)
    backends/               base.py, civis_backend.py (civis 2.7.1 calls only),
                            local_backend.py (dry-run files), cache.py
    templates/
    static/                 Vendored CSS, JS, fonts, and the logo. No CDN links.
  common/                   Shared by both. Must stay compatible with Python 3.12
                            (Service image) and 3.14 (scanner image).
    rules/
      civis_v3.yaml
      python315.yaml
    rule_loader.py          Loads and validates the YAML rule files. (Not rules.py,
                            which would clash with the rules/ folder.)
    sql.py                  Validated, escaped SQL literals and identifiers
    models.py               Object, Finding, CoverageItem, ChecklistEntry
    schema.py               Table definitions and column names
    urls.py                 Civis URL builders
    redact.py               Redacting log filter
    retention.py            RETENTION_DAYS cleanup
    disclaimer.py           The one disclaimer string used everywhere
    version.py
  tools/
    build_civis_api_fixture.py  Builds the civis 2.7.1 API list from the civis wheel
  tests/
    fake_civis.py           Re-exports the synthetic client from scanner/synthetic/
    fixtures/               The civis 2.7.1 API list
    test_rules.py, test_engine.py, test_inventory.py, test_retrieval.py,
    test_output_and_runs.py, test_app.py, test_security.py, test_deploy.py, ...
    test_civis_271_compat.py  Fails if app/ or common/ calls a civis method missing from 2.7.1
  docker/
    scanner.Dockerfile      FROM the pinned scanner image by digest. Local testing only.
    service.Dockerfile      FROM the pinned Flask image by digest. Local testing only.
    check_image.py          Fails a build if image versions differ from the requirements
                            files, or if adding pytest changes a package
  docs/
    PLAN.md
  .githooks/pre-commit      Blocks Context/ files and likely secrets
  start_service.sh          Run by the Flask image's entrypoint. Execs gunicorn on port 3838.
  requirements-scanner.txt  Pins matching the scanner image (documentation and local tests)
  requirements-service.txt  Pins matching the Flask image
  requirements-dev.txt      Local Python 3.14 environment (scanner pins, Flask, pytest)
  requirements-dev-service.txt  Local Python 3.12 environment (Service pins, pytest)
  README.md  TESTING.md  SECURITY.md  CLAUDE.md  LICENSE  NOTICE
  .env.example  .gitignore  .gitattributes  .dockerignore
```

---

## 4. How the web app and the scanner job connect

**Conclusion: the app and the scanner never talk to each other directly. They share two things: the scanner job in Civis, which the app starts and watches through the API, and the result tables in the client's schema, which the scanner writes and the app reads.**

| Action | How it works |
|---|---|
| **Find the scanner job** | The admin creates the scanner container script once, then sets these Service environment variables: `SCANNER_JOB_ID`, `RESULTS_DATABASE`, `RESULTS_SCHEMA`. None of them are secret. |
| **Launch a scan** | "Run scan" calls `scripts.post_containers_runs(SCANNER_JOB_ID)`. The scan uses the parameters saved on the job. Before starting, the app checks `list_containers_runs` and refuses to start a second scan while one is queued or running. The run executes as the Service owner (the Robot user), because the app's key belongs to that user. |
| **Show progress** | The scanner writes a short structured log line about every 30 seconds, for example `PROGRESS phase=code objects_done=420 objects_total=2000`. The browser polls `/api/scan-status` every 15 seconds. The app reads the run state (`get_containers_runs`) and the newest log lines (`list_containers_runs_logs`) and caches the answer for 10 seconds. These are cheap read calls, and they create no query runs. |
| **Read results** | After a run succeeds, the app loads that run's rows from `scan_runs`, `objects`, `findings`, and `coverage` with `civis.io.read_civis_sql(..., return_as="list")`. It caches them in memory by run ID; a finished run never changes. Filtering and search happen in Python, not in SQL. |
| **Download CSV** | Links to the CSV run output file (`list_containers_runs_outputs`, then `files.get` for a download URL). This needs no query. |
| **Write checklist status** | `checklist_status` is **append-only**. Each change inserts a row: object ID, rule ID, status, changed by (from the `Civis-Username` header), and changed at. The current status is the newest row per object and rule. Users change several items, then click "Save"; one save runs one `civis.io.query_civis` insert. The scanner only inserts "open" rows for object and rule pairs that have no row yet. Neither side ever updates or deletes. **Every value in an insert is validated and escaped, including values from Civis request headers** (see section 9, additional requirements). |
| **Health and sleep** | `/healthz` returns 200 without calling Civis. If the Service sleeps during a long scan, nothing is lost, because all state lives in the run and the tables. |
| **Who can see what** | Every viewer sees what the Robot user can see. That includes the names, owners, and links of private objects. Access to the app is controlled by Civis sharing on the Service. **Share it with Civis admins only.** The README and a short note on the landing page say so. See decision 4. |

Each `read_civis_sql` and `query_civis` call starts a query run, and Civis limits those separately (see section 5). That is why reads are cached per run ID and checklist writes are batched.

---

## 5. Estimated scan runtime and default concurrency

**Conclusion:** at a safe share of the rate limit, expect about **20 minutes for 500 objects, 70 minutes for 2,000, and 5.5 hours for 10,000**.

- **Default max concurrency: 4 worker threads**, plus a shared request budget of **500 requests per 5 minutes** (half the published limit).
- **The rate limit, not concurrency, sets the speed.** More threads do not make a scan faster once the budget is in use.

**Published limit** (Civis support, "API Basics"): "API requests are limited to 1000 general requests or 100 Run-creating requests per 300 seconds." Civis returns HTTP 429 when the limit is exceeded and reports the current limit in the `X-RateLimit-Limit` and `X-RateLimit-Remaining` headers. The article says the limit is subject to change. Separate limits per endpoint are not published.

**Why a budget below the limit:**

- The scanner, the web app, and any production jobs that run as the Robot user all share one budget.
- A scan that uses the whole limit could cause 429 errors in those other jobs.
- The scanner also reads `X-RateLimit-Remaining` (`response.calls_remaining`) and slows down further if it drops below 20 percent.

**Calls per object (estimate, to be measured in phase 7):**

| Step | General API calls |
|---|---|
| Listing (50 per page) | about 0.02 |
| Detail (`get_*`) | 1 |
| Extra lookups (git settings, template, workflow references) | about 0.5 on average |
| Log read for objects that ran in the lookback window | about 1 (most logs fit in one call) |
| Checkpoint and output writes, amortized | about 0.2 |
| **Total** | **about 3 per object** |

**Added November 2, 2026 (17.1):** one more call for each active workflow (`workflows.get_git`), and up to two more for each git-backed Python script or workflow whose own file is read through Civis (the commits list, skipped for a commit SHA ref, and the file). Without a GitHub token, the normal setup, every git-backed Python script is read this way. In an organization with few git-backed objects the totals below barely change; one where most Python scripts are git-backed should expect about 2 more calls for each of them.

Notebook file downloads and GitHub requests do not count against the Civis API limit. GitHub allows 5,000 requests per hour with a token, and we make one request per repo and ref.

| Objects | Calls (about) | At the 500 per 5 min budget (default) | At the full 1,000 per 5 min limit |
|---|---|---|---|
| 500 | 1,500 | about 20 minutes | about 10 minutes |
| 2,000 | 6,000 | about 70 minutes | about 35 minutes |
| 10,000 | 30,000 | about 5.5 hours | about 2.75 hours |

These times include about 5 to 20 minutes of fixed overhead: listing, the user list, and table writes.

**Run-creating limit (100 per 5 minutes):**

- The scanner creates runs only when it writes tables.
- It checkpoints every 500 objects or every 10 minutes, whichever comes first. That uses fewer than 10 runs per checkpoint.
- A 10,000-object scan uses about 150 run-creating calls in total.

---

## 6. Python 3.15 rules (approved)

**Conclusion:**

- **Ship 25 rules: 24 source rules plus one log rule.** The original 23 source rules are high or medium confidence, with low false-positive risk. `PY315-PINNED-PACKAGE-INSTALL` was added at approval as a low-confidence advisory.
- **Leave out 7 changes.** They are too noisy, test-only, or warnings rather than breakage.
- **Do not flag two things that look deprecated but are not:** `locale.getdefaultlocale` (no longer deprecated in 3.15) and `re.match` (soft-deprecated only, with no removal planned).

The three most likely to appear in data and ETL code are `strptime` without a year, `load_module()`, and positional arguments to `sqlite3.connect`.

Most of these changes have warned since 3.13 or 3.14. But Python hides `DeprecationWarning` by default unless the call is in the main script or a notebook cell. So logs miss most of them, and source rules matter most.

| Rule ID | Change in 3.15 | Detection method | Confidence | False-positive risk | Ship? |
|---|---|---|---|---|---|
| PY315-STRPTIME-NO-YEAR | `strptime` raises `ValueError` when the format has `%d` but no `%Y`, `%y`, or `%G`. Applies to `datetime`, `date`, and `time.strptime`. | AST: a `strptime` call whose format argument is a string literal (or a module-level string constant) with `%d` and no year directive | High | Low | Yes |
| PY315-LOAD-MODULE | `Loader.load_module()` removed; the import system no longer calls it | AST: a `.load_module(` call, or a class that subclasses an importlib Loader and defines only `load_module` | High | Low | Yes |
| PY315-ZIPIMPORT-LOAD-MODULE | `zipimporter.load_module()` removed | AST: `zipimporter(...).load_module(` | High | Low | Yes |
| PY315-SQLITE-CONNECT-POSITIONAL | `sqlite3.connect` accepts only `database` positionally | AST: `sqlite3.connect` called with more than one positional argument | High | Low | Yes |
| PY315-SQLITE-METHOD-KEYWORDS | `create_function`, `create_aggregate`, `set_authorizer`, `set_progress_handler`, `set_trace_callback`: leading parameters are now positional-only | AST: those methods called with the old keyword names (`name`, `narg`, `func`, `n_arg`, `aggregate_class`, `authorizer_callback`, `progress_handler`, `trace_callback`) | Medium | Low | Yes |
| PY315-ARGPARSE-DEST | `add_argument('-f', '-foo')` now sets `dest` to `foo` instead of `f`. This breaks silently. | AST: `add_argument` with a one-dash short option and a one-dash long option, no `--` option, and no `dest=` | High | Low | Yes |
| PY315-SRE-MODULES | `sre_compile`, `sre_constants`, `sre_parse` removed | AST: import | High | Low | Yes |
| PY315-GLOB-GLOB0-GLOB1 | `glob.glob0` and `glob.glob1` removed | AST: attribute or import | High | Low | Yes |
| PY315-CGI-HANDLER | `http.server.CGIHTTPRequestHandler` and `--cgi` removed | AST: name or import. Regex: `http.server --cgi` in commands | High | Low | Yes |
| PY315-RESOURCES-FILES-PACKAGE | `importlib.resources.files(package=...)` keyword removed | AST: `files(` called with `package=` | High | Low | Yes |
| PY315-PATH-IS-RESERVED | `PurePath.is_reserved()` removed | AST: an `.is_reserved()` call. The `ipaddress` attribute is a property and is never called, so it does not match. | High | Low | Yes |
| PY315-PLATFORM-JAVA-VER | `platform.java_ver()` removed | AST | High | Low | Yes |
| PY315-CTYPES-SETPOINTERTYPE | `ctypes.SetPointerType` removed | AST | High | Low | Yes |
| PY315-SYSCONFIG-CHECK-HOME | `sysconfig.is_python_build(check_home=...)` removed | AST | High | Low | Yes |
| PY315-RLOCK-ARGS | `threading.RLock()` no longer accepts arguments | AST: `RLock(` called with any argument | High | Low | Yes |
| PY315-CO-LNOTAB | `codeobject.co_lnotab` removed | AST: `.co_lnotab` | High | Low | Yes |
| PY315-MODULE-CACHED | Module `__cached__` is no longer set | AST: the name `__cached__` | High | Low | Yes |
| PY315-TYPING-NAMEDTUPLE-KWARGS | `NamedTuple("P", x=int)` keyword syntax removed | AST | High | Low | Yes |
| PY315-TYPING-TYPEDDICT-NO-FIELDS | `TypedDict("TD")` and `TypedDict("TD", None)` removed | AST | High | Low | Yes |
| PY315-TYPING-NO-TYPE-CHECK-DECORATOR | `typing.no_type_check_decorator` removed | AST | High | Low | Yes |
| PY315-BYTESTRING-STAR-IMPORT | `ByteString` dropped from `__all__` of `collections.abc` and `typing` | AST: `from collections.abc import *` or `from typing import *`, together with use of `ByteString` | High | Low | Yes |
| PY315-WAVE-MARKERS | `getmark`, `setmark`, `getmarkers` removed from `wave` | AST: those method calls in a file that imports `wave` | Medium | Low | Yes |
| PY315-RLIMIT-NEGATIVE | `resource.RLIM_INFINITY` is always positive. Passing a negative limit is deprecated. | AST: `setrlimit` or `prlimit` with a negative literal, or `RLIM_INFINITY` compared to a negative number | Medium | Low | Yes (low severity) |
| PY315-PINNED-PACKAGE-INSTALL | Runtime installs with exact pins (`==`) or upper bounds often have no Python 3.15 wheel and fail to install. Added at approval. | Regex on container commands, notebook `!pip` and `%pip` cells, and `requirements.txt`, `pyproject.toml`, and `Dockerfile` in git repos. Fires only on objects that run on a Civis default image. Always advisory, low severity. Fix: confirm each pinned version supports Python 3.15, or loosen the pin. | Low | Medium | Yes |
| PY315-LOG-DEPRECATION | Any of the above, seen as a warning in a run log | Regex on log text for warnings that say "removed in Python 3.15", "slated for removal in Python 3.15", "will become an error in Python 3.15", "keyword-only ... in Python 3.15", or the `strptime` leap-day message. `locale.getdefaultlocale` is excluded. | Medium | Low | Yes |
| (none) | AST node constructors reject missing or unknown arguments | AST: compare `ast.<Node>(...)` arguments with `_fields` | Medium | Medium | No. Rare in data code; the log rule covers it. |
| (none) | `mmap.resize` removed on some platforms | AST: `.resize(` (receiver type unknown) | Low | Medium | No |
| (none) | Unclosed `ElementTree.iterparse` emits `ResourceWarning` | AST | Medium | Medium | No. A hidden warning, not breakage. |
| (none) | `base64.urlsafe_b64decode` no longer requires padding | AST: only calls inside `try` blocks that catch `binascii.Error` | Low | High | No |
| (none) | `assertWarns` no longer swallows unmatched warnings | AST | High | Low | No. Test code only. |
| (none) | UTF-8 is the default encoding for `open()` (PEP 686) | AST: `open` without `encoding=` | Low | High | No. Civis containers already run Linux with a UTF-8 locale, so nothing changes. |
| (none) | `ContextDecorator` on generator or async functions stays open across iteration | AST | Low | High | No |

---

## 7. civis-python v3 rules (approved)

**Conclusion: ship 33 rules.**

- **11 for removals:** the 8 non-rename changelog items, with the four removed classes getting one rule each.
- **1 for the undocumented `run_template` default change.**
- **19 renames:** one rule per `list_*` to `get_*` rename, generated from one table in `civis_v3.yaml`.
- **1 for unpinned civis installs.**
- **1 log rule.**

The 19 renames match the changelog exactly. I also rebuilt the list from the client's own code and the bundled API spec, and the two agree.

### Removals and behavior changes

| Rule ID | What breaks in v3 | Detection method | Confidence | Fix guidance |
|---|---|---|---|---|
| CV3-USE-PANDAS | `use_pandas` keyword removed from `civis.io.read_civis` and `read_civis_sql` | AST: those calls with `use_pandas=` | High | Use `return_as="pandas"`. |
| CV3-DEFAULT-CREDENTIAL | `APIClient.default_credential` removed | AST: `.default_credential` attribute | High | Use `client.default_database_credential_id`. |
| CV3-GET-DATABASE-CREDENTIAL-ID | `APIClient.get_database_credential_id` removed, with no replacement | AST: `.get_database_credential_id(` | High | Use `default_database_credential_id`, or look up the credential ID once and pass it explicitly. |
| CV3-FEATURE-FLAGS | `APIClient.feature_flags` removed | AST: `.feature_flags` attribute (the dictionary key `["feature_flags"]` does not match) | Medium | Use `client.users.get_me()["feature_flags"]`. |
| CV3-PARALLEL | `civis.parallel` removed | AST: imports of or attribute access on `civis.parallel` | High | Migrate to `CivisFuture`. |
| CV3-ML | `civis.ml` removed (CivisML templates in Platform are unaffected) | AST: imports of or attribute access on `civis.ml` | High | Pin `civis<3`, or move to CivisML templates. |
| CV3-CONTAINERFUTURE | `ContainerFuture` removed | AST: name or import | High | Use `CivisFuture`, which now supports `.cancel()` and `max_retries`. |
| CV3-SERVICECLIENT | `ServiceClient` removed, with no replacement | AST: name or import | High | Call the Service over HTTP directly. |
| CV3-POLLABLERESULT | `PollableResult` becomes private (`_PollableResult`) | AST: name or import | High | Use `CivisFuture`. |
| CV3-CIVISASYNCRESULTBASE | `CivisAsyncResultBase` becomes private | AST: name or import | High | Use `CivisFuture`. |
| CV3-RUN-TEMPLATE-JSONVALUE | `JSONValue` parameter of `civis.utils.run_template` removed | AST: `run_template` with `JSONValue=` or a third positional argument | High (keyword), Medium (positional) | Use `return_as="JSONValue"`. |
| CV3-RUN-TEMPLATE-DEFAULT | Default `return_as` of `run_template` changes from `"files"` to `"future"`. Not in the changelog; stated in the civis-python source. | AST: `run_template` with neither `return_as=` nor `JSONValue` | Medium | Pass `return_as="files"` to keep current behavior. |
| CV3-UNPINNED-INSTALL | Code that installs civis at run time without an upper bound gets v3 in February, **even on a custom or pinned image** | Regex on container commands, notebook `!pip`/`%pip` cells, and `requirements.txt` and `pyproject.toml` in git repos: `civis` with no version or with no `<3` bound | Medium | Pin `civis>=2.8,<3` until the code is updated. |
| CV3-LOG-FUTUREWARNING | Any removed feature used on the last run | Regex on log text: `FutureWarning: .*civis-python v3\.0\.0`. The method name is parsed from ``The method name ``<client>.X.Y`` is deprecated``. The unrelated `tables/:id/refresh` warning is excluded. | High (see decision 6) | Shown per matched feature |

### Full `list_*` to `get_*` rename list (one rule each, ID `CV3-RENAME-<ENDPOINT>-<METHOD>`)

Detection is AST: a call to `<anything>.<endpoint>.<old_name>(`. All are high confidence, except `endpoints.list` and `notifications.list`, which are medium because `.list(` is a common name. Fix guidance is "rename to the v3 name; the arguments are unchanged."

| # | v2 name (removed in v3) | v3 name |
|---|---|---|
| 1 | `client.clusters.list_kubernetes_instance_configs_historical_graphs` | `client.clusters.get_kubernetes_instance_configs_historical_graphs` |
| 2 | `client.clusters.list_kubernetes_instance_configs_historical_metrics` | `client.clusters.get_kubernetes_instance_configs_historical_metrics` |
| 3 | `client.databases.list_advanced_settings` | `client.databases.get_advanced_settings` |
| 4 | `client.endpoints.list` | `client.endpoints.get` |
| 5 | `client.git_repos.list_refs` | `client.git_repos.get_refs` |
| 6 | `client.groups.list_child_groups` | `client.groups.get_child_groups` |
| 7 | `client.models.list_schedules` | `client.models.get_schedules` |
| 8 | `client.notebooks.list_update_links` | `client.notebooks.get_update_links` |
| 9 | `client.notebooks.list_git` | `client.notebooks.get_git` |
| 10 | `client.notifications.list` | `client.notifications.get` |
| 11 | `client.predictions.list_schedules` | `client.predictions.get_schedules` |
| 12 | `client.reports.list_git` | `client.reports.get_git` |
| 13 | `client.scripts.list_sql_git` | `client.scripts.get_sql_git` |
| 14 | `client.scripts.list_javascript_git` | `client.scripts.get_javascript_git` |
| 15 | `client.scripts.list_python3_git` | `client.scripts.get_python3_git` |
| 16 | `client.scripts.list_r_git` | `client.scripts.get_r_git` |
| 17 | `client.usage.list_llm_organization_summary` | `client.usage.get_llm_organization_summary` |
| 18 | `client.users.list_me` | `client.users.get_me` |
| 19 | `client.workflows.list_git` | `client.workflows.get_git` |

### Pending renames (not active yet)

civis-python already contains code to rename 37 more methods, all `list_*_shares` methods plus `search.list`. The renames only switch on when Civis changes its API so those endpoints return a single object instead of a list. They are not in the v3 changelog, and calling them today raises no warning.

**Decision: left out for now (decision 9). Revisit when a civis 3.0 release candidate is published.** At the same time, re-check `CV3-RUN-TEMPLATE-DEFAULT` against that release candidate (decision 7).

---

## 8. Docker images and versions to pin

**Conclusion: pin Civis's own public images by exact tag. We build and publish no image of our own.**

| Use | Image and tag | What is inside | Why |
|---|---|---|---|
| Scanner job | `civisanalytics/datascience-python:9.1.1` (September 17, 2026) | Python 3.14.6, civis 2.10.0, pandas, PyYAML, requests | Includes every package the scanner needs, so the scanner installs nothing at run time. The exact tag never changes, so the February update does not affect it. |
| Web app Service | `civisanalytics/civis-services-flask:0.1.0` (August 6, 2025) | Python 3.12.11, Flask 3.1.1, gunicorn 22.0.0, civis 2.7.1 | Civis's supported Flask image. With no pip install at start, the app is limited to these packages, which is enough. The app calls no renamed v2 or v3 methods. |
| v3 compatibility test | Local image: `datascience-python:9.1.1` plus `civis==3.0.0rcN` once a release candidate is on PyPI. Later, the first Civis image that ships Python 3.15 and civis v3. | n/a | Lets us confirm the scanner runs unchanged after the update, before clients need to rescan. |

How each object runs:

- **Scanner job.** Docker command: `cd /app && python -m scanner`. Repo ref: a release tag of this repo, for example `v1.0.0`, never `main`.
- **Service.** Repo ref: the same release tag. Path left blank. `start_service.sh` runs `exec gunicorn --bind "0.0.0.0:${CIVIS_SERVICE_PORT:-3838}" --worker-class gthread --workers 2 --threads 4 --timeout 120 "app:create_app()"`. **Changed October 22, 2026 (16.21):** it was two sync workers with gunicorn's 30-second default timeout. Each of the two worker processes keeps its own result cache; the four threads in a worker share it.
- **Local testing.** The two Dockerfiles in `docker/` are `FROM` these exact images by digest (tag in a comment) and are used only for local tests (section 16).
- **Requirements files.** They record the versions inside each image, so tests run against the same versions.
- **Image digests.** Recorded in the README on October 13, 2026 (linux/amd64): `datascience-python:9.1.1` is `sha256:d6903fdcf6b13801417621ad7522c09a16e1d2c01d11eb398f75ad0f73d974ef`, and `civis-services-flask:0.1.0` is `sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f`.

**How the Flask image starts (checked October 22, 2026, from the image pulled by digest).**

- `docker image inspect` shows no `ENTRYPOINT`, `CMD ["/bin/sh", "-c", "./entrypoint.sh"]`, no working directory (so `/`), and the root user.
- The image's `/entrypoint.sh` (fixed in the image) is a bash script that runs `set -e`, then `cd "${APP_DIR}/${REPO_PATH_DIR}"`, exits with an error if `start_service.sh` is missing there, and otherwise runs `./start_service.sh`. Civis sets `APP_DIR` to the clone folder and `REPO_PATH_DIR` to the Service's path, which we leave blank.
- So `start_service.sh` sits at the repo root, is run directly (not through `sh`), and needs a shebang, the executable bit, and LF line endings. `tests/test_deploy.py` checks all three.
- The image also has a `/requirements.txt` (`flask~=3.0`, `gunicorn~=22.0`, `civis~=2.2`) that it does not install itself. Our start script installs nothing.
- Installed versions: Python 3.12.11, pip 25.2, Flask 3.1.1, gunicorn 22.0.0, civis 2.7.1, PyYAML 6.0.2, requests 2.32.4, on Debian 12. PyYAML 6.0.2 was unconfirmed before and is now pinned in `requirements-service.txt`.
- The scanner image (`datascience-python:9.1.1`) has `CMD ["python3"]`, runs as root, and has Python 3.14.6, civis 2.10.0, pandas 3.0.5, PyYAML 6.0.3, and requests 2.34.2. It has no Flask, so the web app tests skip inside it and run in the Service image instead.

---

## 9. Decisions and additional requirements

**Conclusion: all 16 open questions were answered on October 9, 2026. The plan is approved with the choices below, one added rule, and four added requirements.**

| # | Question | Decision |
|---|---|---|
| 1 | Service image | `civis-services-flask:0.1.0` as is. No package installs at start. |
| 2 | App permissions | The app only runs the scanner job the admin creates. It never creates or edits container scripts. |
| 3 | Scan settings in the app | "Run scan" uses the parameters saved on the scanner job. |
| 4 | App audience | Civis admins only. The README and a short note on the landing page say so. |
| 5 | Request budget | Scanner parameter, default 500 requests per 5 minutes. Default max concurrency 4. |
| 6 | civis log confidence | civis-python log matches are high confidence. Python 3.15 log matches stay medium. |
| 7 | Undocumented v3 change | Ship `CV3-RUN-TEMPLATE-DEFAULT` at medium confidence. Re-check it against the first civis 3.0 release candidate. |
| 8 | Unpinned installs | Ship `CV3-UNPINNED-INSTALL`. Read `requirements.txt`, `pyproject.toml`, and `Dockerfile` from git repos. |
| 9 | Pending renames | Leave the 37 pending renames out. Revisit when a civis 3.0 release candidate is published. |
| 10 | Workflow inline code | Scan it. |
| 11 | Hidden objects | Skip them, but count them in coverage. |
| 12 | Service impact | Only Streamlit Services on a blank or `latest` tag are "affected." All other Services are "advisory." |
| 13 | Notebook logs | No deployment log scan. Check saved cell outputs in the `.ipynb` file instead. |
| 14 | Non-GitHub repos | Mark them "not scanned, review manually." |
| 15 | Active workflow | Any workflow that is not archived. |
| 16 | Python 3.15 rules | Section 6 ship list approved, plus the new rule `PY315-PINNED-PACKAGE-INSTALL`. |

### Additional requirements

1. **civis 2.7.1 compatibility.** The app runs on civis 2.7.1, and the `get_*` method names exist only from civis 2.8.0. Code in `app/` and `common/` may only call civis methods and arguments that exist in 2.7.1. A unit test fails if `app/` or `common/` calls a civis method missing from 2.7.1. The scanner runs on civis 2.10.0 and uses the `get_*` names, so it can run on v3 later.
2. **No CDN links.** Every front-end library (JavaScript, CSS, fonts) is vendored into `app/static/`. Viewers on locked-down networks must still get a working app.
3. **Safe checklist inserts.** Every value in a checklist insert is validated and escaped, including values from Civis request headers. `civis.io.query_civis` sends plain SQL text and does not support bound parameters, so values are validated against strict patterns (numeric IDs, known rule IDs, the three status values) and string values are escaped as SQL literals.
4. **Code never leaves memory.** Code fetched from GitHub or from notebooks stays in memory, or in a temporary directory that is deleted when the run ends. It is never written to run outputs, tables, or logs.

### To revisit when a civis 3.0 release candidate is published

- Re-check `CV3-RUN-TEMPLATE-DEFAULT`.
- Decide whether to add the 37 pending `list_*_shares` and `search.list` renames.
- Run the scanner's unit tests and a live scan against the release candidate.

---

## 10. Implementation notes (Phase 2)

**Conclusion: Phase 2 needed these choices that the approved plan did not cover. All were approved on October 13, 2026, with the changes to 10.4, 10.5, and 10.6 shown below.**

| # | Choice | Why |
|---|---|---|
| 10.1 | The rule loader is `common/rule_loader.py`, not `common/rules.py`. | A module and a folder named `rules` in the same package clash on import. |
| 10.2 | The civis 2.7.1 check is static. `tools/build_civis_api_fixture.py` reads the civis 2.7.1 wheel's typed stub (`client.pyi`) and the `civis.io` and `civis.utils` source, without running them, into `tests/fixtures/civis_2_7_1_api.json`. | Keeps one local environment. The test checks endpoint methods, `civis.io` and `civis.utils` functions, and keyword arguments. |
| 10.3 | SQL string values reject backslashes and control characters instead of escaping them. Single quotes are doubled. | Redshift treats a backslash in a literal as an escape character and PostgreSQL does not, so no single escape is safe on both. Civis user names do not normally contain backslashes. |
| 10.4 | `scripts.get_python3_git` is called for every Python script. Approved: completeness matters more than the extra call. | The Python script detail response has no field that says whether it is git-backed. A git-backed script with old saved source would otherwise give wrong results. This adds about one call per Python script to the section 5 estimate. |
| 10.5 | Objects that cannot run Python are left out of the inventory and counted in coverage rows of kind `not_python`, broken down by object type and language: R notebooks; custom scripts backed by SQL, R, or JavaScript templates; and Services on Civis's Shiny image, including Services with no image set (Shiny is Civis's default for Services). **dbt exception:** Civis runs dbt inside a Python container, so a custom script on a dbt template is inventoried like a container script when the backing dbt script runs on a Civis default Python image. Otherwise it is counted as not Python with language `dbt`. The dbt script API returns only an image tag, so a blank or `latest` tag is treated as the default image. | Keeps the inventory to Python-based objects while an admin can see exactly what was left out and why. |
| 10.6 | Only workflows with inline Python or container tasks appear as objects. Workflows that only run other jobs are counted and parsed, not listed. Every job that an active workflow runs carries that workflow's ID and name in a `workflows` JSON column on its `objects` row and on each of its `findings` rows. A workflow's own impact comes from its inline tasks' images. | A workflow that only runs other jobs has no code of its own. An admin still sees which workflows break when a job breaks. |
| 10.7 | For the archived-job exception, the job type from `jobs.get` is matched by keyword ("python", "container", "custom", or a template ID). | Civis does not document the `JobTypes::` values. To confirm in Phase 7. |
| 10.8 | civis-python log warnings map to the specific rule they describe (for example the `list_me` warning reports `CV3-RENAME-USERS-LIST-ME`). The catch-all log rules fire only for lines no specific rule in the same family explains. Each rule is reported once per log, at its first line. | Gives the specific fix instead of "see the log," and avoids one finding per repeated warning line. |
| 10.9 | Notebook findings use the label `<notebook> [cell N]` with the line inside the cell, and `<notebook> [cell N output]` for saved outputs. Imports are shared across cells. `!` and `%` lines and `%%bash`-style cells are checked as shell. | Notebooks have no single line numbering, and imports usually sit in the first cell. |
| 10.10 | Files that do not parse as Python, or are larger than 2 million characters, use regex fallback at one lower confidence. The object is marked "partial." | Bounded memory and a visible coverage gap instead of a silent skip. |
| 10.11 | `CV3-UNPINNED-INSTALL` does not flag `civis==3.x` or `civis>=3`, because those ask for v3 on purpose. `civis!=2.9.0` alone is flagged. | Only unbounded installs pick up v3 by surprise. |
| 10.12 | `PY315-PINNED-PACKAGE-INSTALL` treats `==`, `===`, `<`, `<=`, `~=`, and Poetry `^`, `~`, and exact versions as pins or upper bounds. | Each of these can block a Python 3.15-compatible release. |
| 10.13 | Severity per rule: removed civis features and Python removals that raise errors are high; `run_template` default, unpinned civis, `argparse` dest, and the `ByteString` star import are medium; negative rlimits and pinned packages are low. | The plan gave confidence but not severity. |
| 10.14 | Civis object URLs use `/spa/#/scripts/python3/<id>`, `/scripts/containers/<id>`, `/scripts/custom/<id>`, `/notebooks/<id>`, `/services/<id>`, and `/workflows/<id>`, on a base URL that `CIVIS_PLATFORM_URL` can change. | Civis does not document these paths. **Container scripts confirmed October 13, 2026:** `https://platform.civisanalytics.com/spa/#/scripts/containers/<id>`. The other paths still need a check in Phase 7. |
| 10.15 | Inline custom-script tasks in workflows (`civis.scripts.custom`) are recorded, but their template code is not read. | The template's backing script is scanned through the custom scripts that use it. Revisit in Phase 3 if needed. |
| 10.16 | A `.gitattributes` file forces LF line endings for `.sh` files and Dockerfiles. | `start_service.sh` must run in Linux even when committed from Windows. |

---

## 11. Security and privacy requirements (added October 13, 2026)

**Conclusion: the 18 security and privacy requirements are in [SECURITY.md](../SECURITY.md), which is written for clients. They apply to all code. The design choices below came from applying them.**

| # | Choice | Why |
|---|---|---|
| 11.1 | The scanner logs only through `scanner/scan_log.py`. Its functions accept progress counts, object types and IDs, rule IDs, and exception type names, and validate each one. A redacting log filter (`common/redact.py`) is a second layer. | Requirements 1, 2, and 5. No function takes free text, so Civis content cannot reach the log by accident. |
| 11.2 | New scanner job parameter `RETENTION_DAYS`. Blank or 0 keeps every run (the default). The most recent run is never deleted. Old checklist rows are deleted only when a newer row exists for the same object and rule. The `DELETE ... USING` form must be confirmed on Redshift in Phase 7. | Requirement 9. |
| 11.3 | The Service frame rule is `frame-ancestors 'self' https://platform.civisanalytics.com`, changeable with `CIVIS_FRAME_ANCESTORS` (https origins only). No `X-Frame-Options` header. | Requirement 12. Civis shows Services in a cross-site iframe on platform.civisanalytics.com (Civis help articles "Civis Service Deployment", "Advanced Services", and "Third-Party Cookies in Services and Notebooks"). The exact parent pages must be confirmed in Phase 7 with a report-only policy first. |
| 11.4 | The app sets no cookies. CSRF tokens are stateless: an HMAC over the viewer's `Civis-User-Id` header and a timestamp, valid for 8 hours, sent in a form field or the `X-CSRF-Token` header. Requests with a cross-site `Origin` are refused. | Requirement 11. Cookies inside a cross-site iframe are third-party cookies and are often blocked. Civis's proxy sets and overwrites the user headers, so another site cannot forge a viewer's token. |
| 11.5 | The app's Flask secret is `FLASK_SECRET_KEY` if set (local runs), otherwise derived one-way from the Service's `CIVIS_API_KEY`. | No extra secret to store; every gunicorn worker gets the same key; it rotates when Civis rotates the API key. |
| 11.6 | The app refuses to start if `FLASK_DEBUG` is set, and forces debug off whatever its configuration says. `CIVIS_API_KEY` was removed from `.env.example`. | Requirements 11 and 16. |
| 11.7 | JSON responses escape `<`, `>`, `&`, and `'`. Templates rely on Jinja autoescaping. Links pass through a filter that allows only http and https. | Requirement 10. |
| 11.8 | Secret scanning runs in `.githooks/pre-commit`: gitleaks when installed, plus detect-secrets 1.5.0 from `.venv`. The baseline is empty; fake secrets in tests carry inline allow comments. | Requirement 13. |
| 11.9 | The full test suite runs on Python 3.14 (`.venv`, scanner packages) and Python 3.12 (`.venv312`, the Service image's packages, including civis 2.7.1). | Phase 2 answer 1. |

---

## 12. Implementation notes (Phases 3 and 4, October 13, 2026)

**Conclusion: Phases 3 and 4 are built and tested on synthetic data only. These choices were not covered by the plan.**

### Scanner job parameters

| Parameter | Civis type | Environment variables read | Default and limits |
|---|---|---|---|
| RESULTS_DB | Database | `RESULTS_DB_NAME` (or `RESULTS_DB_ID`), `RESULTS_DB_CREDENTIAL_ID` | Required |
| RESULTS_SCHEMA | String | `RESULTS_SCHEMA` | Required. Must be a plain SQL identifier. |
| GITHUB | Custom credential, optional | `GITHUB_PASSWORD` (the token) | None |
| LOOKBACK_DAYS | Integer | `LOOKBACK_DAYS` | 200 (1 to 3,650) |
| MAX_CONCURRENCY | Integer | `MAX_CONCURRENCY` | 4 (1 to 16) |
| REQUEST_BUDGET | Integer, requests per 5 minutes | `REQUEST_BUDGET` | 500 (10 to 1,000) |
| RETENTION_DAYS | Integer | `RETENTION_DAYS` | Blank or 0 keeps every run |
| RUN_ID | String, optional | `RUN_ID` | Blank starts a new run; a value resumes that run |

`DATABASE` is a reserved name in Civis, so the Database parameter is `RESULTS_DB`.

### Choices

| # | Choice | Why |
|---|---|---|
| 12.1 | Inventory runs in one thread. `MAX_CONCURRENCY` applies to the code and log phase, where most time goes. A resumed run repeats the inventory (about one call per object), then skips finished objects. | The request budget, not thread count, sets the speed (section 5). Inventory must be complete to know what is left. |
| 12.2 | Git-backed Python scripts are scanned from the repository only, never from their saved source. Notebooks are scanned from the Civis notebook file, not from git. | Phase 2 answer 2: saved source can be out of date. The notebook file is what Civis runs. |
| 12.3 | A Service with no git repository is "not scanned, review manually." | Its code is inside its Docker image, which the scanner cannot read. |
| 12.4 | Notebook links are downloaded only over https from Civis hosts (`*.civisanalytics.com`, `*.civis.io`) and Amazon S3 hostnames: `s3.amazonaws.com`, `s3.<region>.amazonaws.com`, `<bucket>.s3.amazonaws.com`, and `<bucket>.s3.<region>.amazonaws.com`. Other `amazonaws.com` hosts and look-alike domains are refused and reported. **Tightened October 14, 2026. The exact host Civis uses is confirmed in Phase 7.** | Requirement 18. The host Civis uses for file links is not documented. |
| 12.5 | GitHub tarballs (up to 200 MB) are streamed into the private temp folder, the needed files (up to 2 MB each, 100 MB per repo) are read into memory without extracting, and the tarball is deleted at once. Links and special files are skipped. After a rate limit, no GitHub requests are sent until GitHub's reset time. Dependency files are read from the object's folder and the repo root. | Requirement 3 and safe archive handling. |
| 12.6 | Job logs are read in pages of 10,000 lines with `last_id`, up to 200,000 lines. Service deployment logs are read in one call of up to 10,000 lines. Longer logs make the object "partial." | Bounded memory and API calls. |
| 12.7 | `scan_runs` gets two rows per run (start and end) and a new `recorded_at` column. The current status is the newest row, preferring a final status over "running" on a tie. Statuses: `running`, `succeeded`, `tables_failed`, `failed`. | Append-only by run ID. |
| 12.8 | If the tables cannot be written, the scan continues, saves the CSV report, records `tables_failed` where it can, and exits with code 3, so the Civis run shows as failed with the CSV attached. Exit codes: 0 succeeded, 3 tables failed, 2 configuration problem, 1 other failure. | "Record the failure in the run status." |
| 12.9 | The results schema is never created. A missing schema means the table writes fail (see 12.8). | Creating schemas is the admin's decision, like granting access. |
| 12.10 | If a run stops between writing an object's findings and its object row, the rerun writes those findings again. The CSV report and readers drop exact duplicate finding rows. | Losing findings would be worse than a duplicate. |
| 12.11 | The CSV report has one row per finding, plus one row for every object with no findings, so every inventoried object appears. Cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading `'`. UTF-8 with a byte order mark, so Excel reads accents correctly. Since October 26, 2026, the file starts with two notice lines (the disclaimer, then the non-affiliation statement, each one quoted cell); the column names are on line 3 (16.24). | Object names and paths are user-controlled (spreadsheet formula injection). |
| 12.12 | New "open" checklist rows use `changed_by = scanner`. | Shows they came from a scan, not a person. |
| 12.13 | Table writes and the CSV upload use civis-python's own helpers with the unwrapped client, so they are not charged to the request budget. They are a few calls per checkpoint. | civis.io helpers make their own internal calls. |
| 12.14 | `python -m scanner` refuses a live scan unless Civis has set `CIVIS_JOB_ID` and `CIVIS_RUN_ID`. `--dry-run` uses the synthetic instance, a local SQLite file, and a local CSV. | Requirement 16. |
| 12.15 | The synthetic client, synthetic HTTP session, and synthetic instance moved to `scanner/synthetic/`, so dry runs work from the scanner package. They contain made-up data only. | Dry runs need them outside the test folder. |
| 12.16 | The SQLite sink runs the retention cleanup with an `EXISTS` subquery, because SQLite has no `DELETE ... USING`. The Redshift form is still to be confirmed in Phase 7 (11.2). | Same behavior on both. |

---

## 13. Phase 4 questions (approved October 14, 2026)

**Conclusion: each question from the Phase 4 report has its recommended option applied, and Rob approved all six on October 14, 2026. None of them loosens a rule in SECURITY.md or CLAUDE.md. Rob turns on private vulnerability reporting (13.6) before the public release.**

| # | Question | Recommended option | Status |
|---|---|---|---|
| 13.1 | Should a run whose table writes failed show as failed in Civis, even though the CSV report was saved? | Yes. Keep exit code 3, so the Civis run shows failed with the CSV attached and the problem is not missed. | Approved |
| 13.2 | Should the scanner create the results schema if it is missing? | No. Creating schemas stays the admin's decision, like granting access. A missing schema ends the run as `tables_failed` with the CSV saved. | Approved |
| 13.3 | May gitleaks be installed? | Yes. Installed for the current user with `winget install Gitleaks.Gitleaks` (8.30.1). The pre-commit hook now runs gitleaks and detect-secrets. One fake test value already in history is listed in `.gitleaksignore`. | Approved |
| 13.4 | Is a blank or `latest` tag on a dbt script the Civis default Python image? | Treat it as the default image, as now. The dbt script API returns only a tag. Confirm in Phase 7. | Approved |
| 13.5 | Should dbt scripts that are not created from a template follow the same image rule as dbt templates? | Yes. New object type `dbt_script`: inventoried and scanned (repo and most recent run log) on a Civis default image, otherwise counted as not Python with language `dbt`. Its Civis link path (`/spa/#/scripts/dbt/<id>`) is checked in Phase 7. | Approved |
| 13.6 | What contact should SECURITY.md give for reporting security issues? | GitHub private vulnerability reporting on the public repository, so no email address is published. Rob must turn it on in the repository settings (Security, "Private vulnerability reporting") before the public release. | Approved |

---

## 14. Implementation notes (Phase 5, October 14, 2026)

**Conclusion: the web app is built to the mockups and tested on made-up data only. These choices were not covered by the plan. Rob approved them on October 14, 2026, with the change to 14.7 below.**

### Service settings

| Variable | Meaning |
|---|---|
| `SCANNER_JOB_ID` | ID of the scanner container script. Without it, Run scan is off and a banner says so. |
| `RESULTS_DATABASE` | Database that holds the results schema. |
| `RESULTS_SCHEMA` | The results schema. Must be a plain SQL identifier. |
| `RESULTS_CREDENTIAL_ID` | Optional database credential ID. Blank uses the Service owner's default credential. |
| `CIVIS_PLATFORM_URL`, `CIVIS_FRAME_ANCESTORS` | Optional, as before (sections 10.14 and 11.3). |
| `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` | Optional, `true` or `false` (default). Added in Phase 6 as a setup aid (section 16). |

### Choices

| # | Choice | Why |
|---|---|---|
| 14.1 | The findings page shows one row per object and rule, the unit the checklist tracks, with every location listed (up to five, then "and N more"). | One status dropdown per checklist item. Two findings for the same object and rule would otherwise show two dropdowns for one status. |
| 14.2 | The scan progress card shows three steps (Inventory, Code and log scan, Save results), not the mockup's four. | The scanner reads each object's code and most recent log together, so a separate "Log scan" step would never show real progress. |
| 14.3 | "Affected" counts objects with at least one affected finding; "Advisory" counts objects with findings, none affected. "Not scanned" counts not-scanned objects, with partly scanned objects as a note under it. | Matches the mockup's tile wording. |
| 14.4 | Scans are numbered Run 1, Run 2, and so on, by start time in the `scan_runs` table. The history table shows counts for the 10 most recent completed scans. | Each table read in Civis starts a query run, so older scans are listed without counts. |
| 14.5 | Finished scans' tables are cached in memory (12 tables). The scan list, checklist, and schema check are cached for 20 seconds; a checklist save clears the checklist cache at once. A new civis client is made for each request. | Section 4 and 1.10. |
| 14.6 | Every page shows the full disclaimer from `common/disclaimer.py` plus the admin-only note, not the mockup's shorter sentence. | The same disclaimer must appear in the README, every report, and the app. |
| 14.7 | Times are shown in each viewer's local time, month first, with the time zone abbreviation ("October 12, 2026 at 10:14 AM EDT"). The server writes each time as a `<time>` element with UTC text, and `app/static/js/local-time.js` (vendored, no inline script, CSP unchanged) converts it in the browser. Without JavaScript, the page shows UTC. Dates without a time (last run, checklist changes) are converted too. **Changed October 14, 2026 at Rob's request (was UTC only).** | Civis stores UTC; only the browser knows the viewer's time zone. |
| 14.8 | Filters and search are forms that submit to the server (50 rows per page), with an Apply filters button. The mockup's type-to-filter search needs JavaScript; this works without it and with a keyboard. | Accessibility and very large inventories. |
| 14.9 | Download CSV sends the browser to the Civis file link for the scanner run's CSV output. The app never streams client data through itself. Local preview serves the file from the dry-run folder. **Confirmed by Rob October 14, 2026.** | The file stays in Civis file storage; downloading is the viewer's choice (SECURITY.md). |
| 14.10 | Saving checklist changes needs the `Civis-Username` header. Without it, the dropdowns are disabled and a note explains why. The Civis save is one validated and escaped INSERT (query_civis has no bound parameters); local preview uses bound parameters. | Requirement 4 and the "recorded with the Civis-Username header" rule. |
| 14.11 | The results schema check uses `information_schema.schemata` and `information_schema.tables`. To confirm on Redshift in Phase 7. | Distinguishes "schema missing" from "no scan yet." |
| 14.12 | A failed Civis run shows as "Failed" only. The run's error text is never shown. | Error text can hold job output. |
| 14.13 | The time left estimate uses the timestamps on the scanner's PROGRESS log lines. | No extra API calls. |
| 14.14 | Local preview: `python -m scanner --dry-run --preview` seeds made-up data, including one object with a hostile name; `python -m app.local` serves it on 127.0.0.1 only, refuses to start if `CIVIS_API_KEY` is set, and `--state` shows each empty and error state. | Lets Rob click through every page on this machine without Civis. |
| 14.15 | The civis 2.7.1 compatibility test now also checks calls through an injected `civis.io` module (`self.io.read_civis_sql(...)`). | The app passes civis.io in, so the plain `civis.io.` check would miss those calls. |

---

## 15. Rebrand to Expedition Data's style (October 14, 2026)

**Conclusion: the web app now uses Expedition Data's palette, type, and logo. Behavior, routes, and data are unchanged.**

| # | Choice | Why |
|---|---|---|
| 15.1 | Navy primary, cream background, tan borders, and orange used only for decoration. Semantic colors are unchanged. Info banners (a scan is running) use navy text on a pale blue-gray (`#EEF0F6`, border `#C5CBDB`), since the teal they used is gone and orange would read as a warning. | Rob's palette; orange on white is 2.7 to 1. |
| 15.2 | Font stack `Calibri, Carlito, system-ui, sans-serif`. Carlito 5.3.0 (regular, bold, italic; latin woff2) is vendored from `@fontsource/carlito` with its OFL license. Calibri is never vendored. Weights 500 and 600 became 400 and 700. IBM Plex Sans was removed; IBM Plex Mono stays for rule IDs and file locations. | Carlito is metric-compatible with Calibri and free to redistribute. |
| 15.3 | The header logo and the footer "Expedition Data LLC" link to https://www.expedition-data.com/ in a new tab with `rel="noopener noreferrer"`. | The app runs inside a Civis iframe; a same-tab link would replace it, and noreferrer keeps the Service URL private. |
| 15.4 | Links to objects in Civis Platform (Inventory and Findings) stay. They are built from scan data, not written into the templates. The test allows only the two Expedition Data links and Civis Platform object links as external hrefs, and no external `src` at all. **Confirmed by Rob October 14, 2026:** the external-link rule covers our own site and external sources, not links to the client's own Civis. | Removing them would change behavior. |
| 15.5 | Table header cells inside the body (row headers such as object types on the Overview) keep the body style; only column headers get the navy fill. | The mockup's navy fill is for column headers. |
| 15.6 | The logo SVG is a clean copy of Expedition Data's logo: no metadata element and no namespace other than SVG. A content-credentials record that was added when the file was saved locally was removed at Rob's direction on October 14, 2026. A test fails if the logo has a script, foreignObject, image, href, event handler, metadata element, or any non-SVG namespace. | The logo must be inert and carry nothing but the artwork. |


---

## 16. Phase 6: deployment files, setup, and test plan (October 22, 2026)

**Conclusion: the deployment files, local image tests, setup steps, and live test checklist are built. Rob's four Phase 6 decisions are applied, and the choices below were not covered by the plan. Nothing here has run in Civis yet; Phase 7 does that, following TESTING.md.**

### Decisions from Rob (October 22, 2026)

| # | Decision | How it is applied |
|---|---|---|
| 16.1 | The repository is made public before live testing. Rob changes the visibility himself after this phase. | Both objects clone the public repo with no git credential, and the README says to leave the scanner job's git credential blank. The client's optional GitHub token (the `GITHUB` parameter) is attached to the scanner job only. From the civis-python 2.10.1 API spec: container scripts accept `gitCredentialId`; Services accept no git credential, and a credential attached to a Service through its credentials list becomes an environment variable in the app. So the README never tells admins to attach the token to the Service, and step 6 of "Create the Service" says to attach no credentials at all. |
| 16.2 | Both objects run a release tag, never `main`. The first live test uses `v0.1.0-rc1`, which Rob creates after approving this phase. | The README uses a `<release tag>` placeholder and says to use the same tag on both objects. TESTING.md names `v0.1.0-rc1`. |
| 16.3 | Setup instructions are text only, with no screenshots. Field names come from the Civis help articles or the API spec. Unconfirmed labels carry "(label to confirm)" and go on the TESTING.md checklist. | Five labels are marked: the container script git credential field, Disk Space, the Parameter Name and Type labels in Set Parameters, sharing a Custom Credential with the Robot user, and archiving a Service. TESTING.md item 1.3 checks each. Sources are listed below. |
| 16.4 | Docker is approved. Pull both pinned images by digest (Docker Hub only). | Both images were pulled by digest on October 22, 2026, and their start commands and package versions were read inside them (section 8). |

### Choices made in Phase 6

| # | Choice | Why |
|---|---|---|
| 16.5 | `start_service.sh` runs `cd "$(dirname "$0")"` before `exec gunicorn ...`. Otherwise it is exactly the section 8 command. | The image's entrypoint already changes to the repo root; the `cd` makes the script work from any directory and costs nothing. |
| 16.6 | The Flask image's PID 1 is `/bin/sh -c ./entrypoint.sh`, and `entrypoint.sh` runs `./start_service.sh` without `exec`. So a stop signal never reaches gunicorn: in the local smoke test, `docker stop` waited the full grace period and then killed the container (exit 137). Left as is. | It is fixed inside Civis's image, and changing it would mean building our own image. The app is read-only and keeps no state in the process, so a hard stop loses nothing. |
| 16.7 | The test images may add only pytest and the packages it pulls in (`pytest`, `pluggy`, `iniconfig`, `pygments`, `packaging`). `docker/check_image.py` fails the build if any package that came with the image is removed or changes version, or if anything else is added. pytest is downloaded from PyPI during the local build only. | "The test images may add pytest, but nothing else may change." |
| 16.8 | The Python version for each image is read from the "(Python X.Y.Z)" in its requirements file's header, and every `name==version` pin is compared with the installed package. The check runs during the build and again as a test inside each image (`PMSC_TEST_IMAGE`). | One source of truth for versions. |
| 16.9 | The scanner image has no Flask, so the web app tests skip there (module skips in `test_app.py` and `test_local_time.py`, and a `needs_flask` mark in `test_security.py`) and run in the Service image. The Service image has no pandas or Node, so those few tests skip there. | Installing Flask into the scanner image would break 16.7. |
| 16.10 | PyYAML 6.0.2 (read inside the Service image) is pinned in `requirements-service.txt`, and `.venv312` was updated to match. | It was marked unconfirmed in Phase 5. |
| 16.11 | `.dockerignore` also leaves out `*.sqlite`, `*.db`, `*.log`, `*.png`, `.claude`, and editor folders. A test inside each image fails if `Context`, `.env` files, `.venv*`, `dry_run_output`, `Claude outputs`, `.git`, or `.pytest_cache` exist under `/app`. | Defense in depth for files that must never reach an image layer. |
| 16.12 | The scanner's final log line is now `RUN run_id=... status=... objects_total=N objects_scanned=N api_calls=N`. `api_calls` is the request budget's count, so it includes every page of a list call and every call made through `BudgetedClient`. Table writes and the CSV upload use civis-python's helpers and are not counted (12.13). On a resumed run, the counts cover that run only, including its repeated inventory. | TESTING.md item 3.5 measures calls per object against section 5. A test checks the count equals the calls the synthetic client received. |
| 16.13 | Scanner job resources: CPU `1000` (one core), Memory `4096` MB, Disk Space `4` GB. **Approved by Rob October 22, 2026.** The memory estimate was redone with the GitHub cache cap (16.20); see "Scanner memory estimate" below. | The request budget, not CPU, sets the speed (section 5), and the four threads mostly wait on the API. With the cap, memory is about 2 GB in a typical large scan and about 3.4 GB in the worst case, both under 4 GB. Disk: up to four GitHub tarballs of up to 200 MB each in the temp folder at once. |
| 16.14 | The README says to share the scanner job with Civis admins only. | Each scan saves the full report as a CSV run output on the job (no expiration), so anyone who can read the job can download it. SECURITY.md says the same. |
| 16.15 | The README tells admins to remove the default group from the new Service's sharing. | Civis shares a new Service with the creator's default group automatically ("Civis Service Deployment"). |
| 16.16 | The README asks admins to create both objects as the Robot user, or to transfer the scanner job to it ("Transfer Ownership" is documented for scripts, not Services). | The Service's API key belongs to its owner, and scans started from the app run as that user. |
| 16.17 | The README says to clear `RUN_ID` after a resume. | The app starts the job with its saved parameters, so a leftover `RUN_ID` would make every later scan try to resume a finished run. |
| 16.18 | The Service keeps Civis's default resources with one replica and the "On Demand" plan. | Nothing in the app needs more, and On Demand matches section 1.10. TESTING.md item 3.7 times the first page loads. |
| 16.19 | "Remove the tool" archives both objects and drops the schema, and notes that the CSV run outputs stay with the archived job. | Run outputs are saved with no expiration (section 1), and Civis documents no way to delete a file. |
| 16.20 | The GitHub file cache keeps at most `CACHE_MAX_BYTES` (1 GB, a constant in `scanner/retrieval/github.py`, not a job parameter) of kept file text across all repositories. When a new repository and ref would push it over, the least recently used ones are evicted; an object that needs an evicted repository again downloads it again. Bytes are counted as the in-memory size of the decoded strings (`sys.getsizeof`), which is at least the raw size. Eviction drops only the cache's reference: `process.process_item` holds the repository it got from the cache, and the file texts it selected, until the object is finished, and threads waiting for a download receive the result directly, not through the cache. The final `RUN` log line adds `github_tarball_requests`, `github_cache_peak_bytes`, and `github_cache_evictions`. **Requested by Rob October 22, 2026.** | Other organizations will scan more repositories than the test organization, so one measurement cannot bound their runs, and an out-of-memory kill ends a scan with no clear error (a resume would hit the same limit). One repository is capped at 100 MB of raw text (at most about 400 MB as Python strings), so any single repository always fits. Tests cover eviction order, a download again after eviction, an object keeping its files after eviction, the total never going over the cap, and four threads using the cache at once. The count covers the file strings, not the small dictionary overhead per file, so 1 GB is a close bound rather than an exact one. |
| 16.21 | The Service runs gunicorn with `--worker-class gthread --workers 2 --threads 4 --timeout 120`. Before the change, every piece of per-process state was checked for thread safety: (1) `CachedResults` already locked its dictionaries, but two threads missing the same key both ran the Civis query, a checklist read that started before a save could store the older list after the save cleared it, and its read counter was not locked; now one thread loads each key while the others wait for its result, a generation number keeps older reads from being stored after a save, and the counter is locked. (2) `Services.progress` was locked, but two polls could apply the same log lines twice or apply older lines after newer ones; `Progress.feed` now skips lines it has already seen. (3) The "no scan running" check and the start were not atomic; a `start_lock` per worker now covers both. (4) Everything else at module level in `app/` is a constant, a compiled pattern, or the rule set loaded once and only read. Flask's `g` (the per-request civis client) is per request. Cached row lists are shared and never changed in place (the views build new lists). **Each worker process keeps its own cache**, so a page can be loaded once per worker. The "one scan at a time" guard still cannot see a start in the other worker, so two clicks in the same second on different workers could still start two scans; each would get its own run ID. **Requested by Rob October 22, 2026.** | With two sync workers, two slow first page loads tie up both workers and `/healthz` waits behind them. With gthread, a slow request holds one of eight threads. Note: under gthread, `--timeout` does not limit how long a request runs (the worker keeps checking in while its threads wait); it restarts a worker only if the whole worker stops responding. In the local smoke test, two requests held open for more than 125 seconds caused no worker timeout, and `/healthz` answered in 0.02 seconds the whole time. Tests cover concurrent first loads, a read from before a save, out-of-order progress lines, and four concurrent Run scan posts in one worker (one starts; the test fails without the lock). |
| 16.22 | The README does not state who the scanner job runs as as a fact. It says Civis's help describes it two ways (the user who runs the script, and the user who last updated it) and tells admins to create and edit the job as the Robot user, to start every scan with Run scan in the app, to set and clear `RUN_ID` as the Robot user and start the resume from the app, and to change the release tag as the Robot user. **After Phase 7 confirms the behavior (TESTING items 3.2 and 5.4), these README lines are rewritten to state the confirmed rule.** **Requested by Rob October 22, 2026.** | Until Phase 7, either rule gives the same result if the Robot user both edits and starts the job. The earlier text stated one rule as fact and told admins to resume from the job's own page, which runs it as whoever clicks. |

### Scanner memory estimate (with the cache cap, October 22, 2026)

Measured inside the scanner image: Python with civis, pandas, and PyYAML loaded peaks at 93 MB, and a full dry run at 97 MB. A live run also loads civis's API spec and keeps result rows for the CSV, so 0.3 GB is allowed for the base. "Per thread" means each of the `MAX_CONCURRENCY` worker threads (default 4), which each work on one object at a time.

| Part | Typical large scan | Worst case |
|---|---|---|
| Python, civis, pandas, scanner code, result rows | 0.3 GB | 0.3 GB |
| GitHub file cache (the cap) | up to 1.0 GB | 1.0 GB |
| One repository per thread held outside the cache (being read, or evicted while still in use) | 4 x 0.1 GB = 0.4 GB | 4 x 0.4 GB = 1.6 GB, only if every file has characters outside Latin-1 |
| Run log lines per thread (up to 200,000) | 4 x 0.05 GB = 0.2 GB | 4 x 0.1 GB = 0.4 GB |
| Notebooks (50 MB file cap) | within the per-thread repository share, since a notebook object has no repository | same |
| CSV report and checkpoint frames | under 0.1 GB for 10,000 objects | 0.1 GB |
| **Total** | **about 2 GB** | **about 3.4 GB** |

Both fit in the approved 4,096 MB. Each worker thread above 4 can add up to about 0.5 GB in the worst case, so the README tells admins to add 512 MB of Memory for each thread above 4. Run log lines are not capped by length; a log of very long lines could use more, and TESTING item 1.4 records peak memory and the cache counts.

### Report-only frame rule (11.3)

New Service setting `CIVIS_FRAME_ANCESTORS_REPORT_ONLY`, `true` or `false` (case is ignored; blank or unset means `false`). Any other value stops the app at start, like a bad `CIVIS_FRAME_ANCESTORS`: the Service start log shows "ValueError: CIVIS_FRAME_ANCESTORS_REPORT_ONLY must be true or false" and gunicorn stops with "Worker failed to boot" (checked in the Service image October 22, 2026). **Rob confirmed October 22, 2026: keep stopping the app**, because a failed start shows the typo during setup and a silent fallback would hide it. A test checks the whole message. When it is `true`, the enforced `Content-Security-Policy` keeps every directive except `frame-ancestors`, and the frame rule is sent alone in `Content-Security-Policy-Report-Only`. There is no `report-uri` or `report-to`, so nothing is sent anywhere; the browser shows the warning in its console. It is a setup aid for the first live test and is turned off once the frame rule is confirmed, because while it is on, other sites could frame the app.

Browser support, checked October 22, 2026:

- MDN, "Content-Security-Policy-Report-Only" (last modified March 22, 2026): the header "supports all Content-Security-Policy directives except `sandbox`, which is ignored."
- W3C, Content Security Policy Level 3 (Working Draft, September 16, 2026): section 4.2.5 checks every policy on a navigation response, reports each violation, and blocks only when the policy's disposition is "enforce". Section 6.3.2 excludes `sandbox` from report-only policies; `frame-ancestors` (6.4.2) has no such exclusion.
- Firefox: Mozilla bug 1380755, "Support frame-ancestors in Content-Security-Policy-Report-Only," fixed in Firefox 58 (examines and reports, does not block).
- Chrome and Edge (Chromium): honor it and report violations, as stated in Mozilla bug 1380755 and WebKit bug 271377 and checked by web-platform-tests.
- **Safari does not.** WebKit bug 271377 (open since March 21, 2024): Safari logs "The Content Security Policy directive 'frame-ancestors' is ignored when delivered in a report-only policy." So the report-only check in TESTING.md uses Chrome, Edge, or Firefox.

### Sources for the setup steps (read October 22, 2026)

Field labels come from these public Civis help articles at support.civisanalytics.com, read through the help center's public article feed. No Civis instance or Civis API was contacted.

- "Container Scripts" (article 218200643): **Code** menu, **Scripts**, **New Script**, **Container**; Docker Image Name, Docker Image Tag, GitHub Repository URL, GitHub Repository Reference, Command, CPU, Memory.
- "Script Parameters" (article 37891904188301): **Set Parameters**, **Add**, Input Display Name, Input Helper Text, Required, Default Value; types String, Integer, Database, Custom Credential; the environment variables each type sets; reserved names, including `DATABASE`.
- "Welcome To Scripts" (article 360050939391): cancelling a run (the "x" next to the spinner in the Run Log) and run outputs in Run History.
- "Civis Service Deployment" (article 360001335031): **Publish** menu, **Service Deployment**; the Docker section (Image Name, Tag); the Git Connection section (Repository, Branch, Path); **Start Deployment**, **Update Deployment**, **View Report**; **Multischedule** with On Demand; sleep after an hour without viewers; automatic sharing with the default group.
- "Advanced Services" (article 360004026012): **Environment Variables**, visible to anyone with read access; `CIVIS_SERVICE_HEALTH_PATH`; `CIVIS_SERVICE_PORT` 3838; credentials under **Security and Access**, **Credentials**, set as environment variables.
- "Archive" (article 8273655115917) and "Transfer Ownership" (article 8300594652813): the file box icon; transfer documented for scripts but not Services.
- Release notes 2018: the container script "Disk Space" field (named only there, so marked "label to confirm").
- civis-python 2.10.0 API spec (bundled with the package in `.venv`): `requiredResources` units (CPU shares, 1,000 per core; memory in MB; disk in GB), `gitCredentialId` on container scripts only, and no git credential on Services.

### Legal texts and rename (October 26, 2026)

**Conclusion: on October 26, 2026, Rob applied new legal texts and a rename: a new product and repository name, a new disclaimer, a non-affiliation statement, a new NOTICE, and behavior statements written as design rather than promises. Behavior, routes, and data are unchanged, except that the CSV report gains two notice lines.**

| # | Change | Why |
|---|---|---|
| 16.23 | **Rename.** The product, formerly "Civis Python Version Search", is now "Python Migration Scanner for Civis Platform by Expedition Data". The repository is `ExpeditionData/python-migration-scanner-for-civis-by-ed` (renamed on GitHub by Rob; the local folder keeps its name). The full name opens the README title, the first line of NOTICE, and the first mention in SECURITY.md; later mentions say "Python Migration Scanner" or "the tool". The app header shows "Python Migration Scanner" as the brand with a smaller "for Civis Platform" under it, beside the Expedition Data logo, and each browser tab reads "<Page> \| Python Migration Scanner for Civis Platform". The internal short prefix from the old name is now `pmsc` (Docker test image and container names, build temp folder, `PMSC_TEST_IMAGE`, the scanner's temp folder prefix, the Flask extension key, and a TESTING.md example). The CSV report is named `python-migration-scanner-<run_id>.csv`. The HMAC labels for the Flask secret and the CSRF key use the new repository name, which only invalidates outstanding CSRF tokens. `tests/test_names.py` fails if the old product name appears in any tracked file other than this one, or if the old repository name or prefix appears anywhere. | Our name leads; "Civis" appears only as a descriptor. The repo holds no Civis logo, icon, or color values: the only image is the Expedition Data logo, and the only icon is an inline warning triangle drawn in `base.html`. |
| 16.24 | **Disclaimer and non-affiliation statement.** `DISCLAIMER` and the new `NON_AFFILIATION` in `common/disclaimer.py` hold the texts below. The disclaimer appears in the README, the banner on every page, the `scan_runs` table, and line 1 of the CSV report. The non-affiliation statement appears right under the README title, on its own line in every page footer (including error pages), and as line 2 of the CSV report. Each CSV notice line is one quoted cell written as is (the formula guard applies to data cells only), the UTF-8 byte order mark stays first, and the column names are on line 3 (`REPORT_NOTICE_LINES` in `scanner/output.py`). The README's "The CSV report" section says to skip the first two lines when loading the file with code, and that the result tables are the machine-readable copy. Tests check the exact texts in the README, every footer, and the first two CSV lines, and that the disclaimer fits its 2,048-byte column. | One change from the suggested text is Rob's: "the Civis user the scan runs as" replaces "the user who runs it", because Phase 7 is still testing which user Civis runs a job as (16.22). |
| 16.25 | **NOTICE and trademarks.** NOTICE opens with the text below, followed by the unchanged fonts paragraph. The README License section carries the same trademark sentences. Tests check both. | Our name and logo are not Apache-licensed, and Civis's trademarks are named as Civis Analytics'. |
| 16.26 | **Behavior statements as design.** SECURITY.md opens with the "By design" section below (the notebook storage and scanner-run lines are Rob's additions to the suggested example). Each bullet links to the code and tests that implement it. The rest of SECURITY.md, the README "Security and privacy" section, and app text describe design ("is designed to", "does not") instead of promising behavior; "How we build and test it" describes our practice; "Not a complete audit" became a pointer to the disclaimer; and "Expedition Data LLC reviews every report" became "Reports go privately to Expedition Data LLC." "Never" stays where it instructs admins (for example, "Never attach it to the Service") and in contributor rules. New tests back two bullets: `test_civis_writes_are_limited_to_the_scanner_job_and_the_results_schema` and `test_only_safe_http_sends_http_requests_and_only_to_the_allowed_hosts`. Another checks the fixed text, and another fails if SECURITY.md names a test that does not exist. | A "never" about the tool's behavior reads as a promise, and a bug would make it false. Describing design and pointing to the code and tests is accurate. "Reports go privately" does not conflict with "no obligation to provide support". |

**Disclaimer (`DISCLAIMER`):**

> This tool is provided free and "as is," without warranty of any kind, under the Apache License 2.0. It scans for known patterns associated with the February 2027 Civis Platform update (Python 3.15 and civis-python v3). It is not a complete audit. It can miss affected code and can flag code that is not affected. Results depend on the permissions of the Civis user the scan runs as; objects that user cannot see are not scanned. Test every job before February 2027. Expedition Data LLC is not liable for any damages arising from use of this tool and has no obligation to provide support or updates. This is not professional advice.

**Non-affiliation statement (`NON_AFFILIATION`):**

> This tool is independent and is not affiliated with, endorsed by, sponsored by, or supported by Civis Analytics. "Civis" and "Civis Platform" are trademarks of Civis Analytics, used here only to identify the platform this tool works with.

**NOTICE, before the fonts paragraph:**

```text
Python Migration Scanner for Civis Platform by Expedition Data
Copyright 2026 Expedition Data LLC.

This product includes software developed by Expedition Data LLC.

Licensed under the Apache License, Version 2.0. See the LICENSE file for the full license text.

The Expedition Data name and logo are trademarks of Expedition Data LLC and are not licensed under the Apache License 2.0. Civis and Civis Platform are trademarks of Civis Analytics; Expedition Data LLC is not affiliated with Civis Analytics.
```

**SECURITY.md, "By design":**

> By design, this tool:
>
> - Reads Civis objects and does not modify them. The web app starts runs of the scanner job you created.
> - Writes only to the schema you specify and to its own job run outputs
> - Makes network calls only to the Civis API, to Civis file storage through the time-limited links the Civis API provides (to download notebook files), and, if you provide a token, to GitHub
> - Sends no telemetry and no data to Expedition Data LLC
> - Reports object IDs, rule IDs, file names, and line numbers, not code contents
>
> These statements describe intended behavior. The full source code is available for your review. See LICENSE for the warranty disclaimer.

### Public-release readiness (October 22, 2026)

- **gitleaks 8.30.1 over the full git history:** 33 commits scanned, no leaks found. Rerun before this checklist was committed: 40 commits, no leaks found. One fake test value from Phase 3 stays allowlisted in `.gitleaksignore` (13.3). History was not changed.
- **detect-secrets 1.5.0 over the working tree:** all 117 tracked files scanned, 0 findings (the same on the rerun). Fake secrets in tests carry inline allow comments.

### Release checklist (Rob, in this order)

On the organization's GitHub Free plan, private vulnerability reporting and rulesets work only on public repositories. Checked October 22, 2026 with read-only GitHub API calls while the repository was private: the private vulnerability reporting endpoint returned 404, and the rulesets endpoint returned "Upgrade to GitHub Pro or make this repository public to enable this feature." So steps 2 and 3 come right after the visibility change, before the repository is shared or announced and before any tag exists.

1. **Make the repository public** (Settings, General, Danger Zone, Change visibility).
2. **Turn on private vulnerability reporting** (13.6): Settings, then Advanced Security under "Security and quality", then **Enable** next to "Private vulnerability reporting". SECURITY.md already points reporters to it.
3. **Add a tag ruleset so release tags cannot be moved or deleted:** Settings, Rules, Rulesets, **New ruleset**, **New tag ruleset**.
   - Ruleset name: `release tags`. Enforcement status: **Active**.
   - Target tags: include the pattern `v*`.
   - Tag protections: **Restrict updates** (no one can move a tag), **Restrict deletions**, and **Block force pushes**.
   - Leave **Restrict creations** off, so new release tags can still be created.
   - Leave the bypass list empty, so not even an admin can move or delete a release tag without first changing the ruleset.
4. **Create `v0.1.0-rc1`** from the newest commit on `main` after the October 26, 2026 naming and notice changes, and push it. Then confirm the ruleset: `git push origin :refs/tags/v0.1.0-rc1` must be rejected. If the delete goes through, recreate the tag on the same commit, push it again, and fix the ruleset before going further.

---

## 17. Phase 7 changes

**Conclusion: changes made during Phase 7, after the first release candidate and before the first scan. Each one is retested in the live test.**

### 17.1 Git-backed files read through Civis (November 2, 2026)

**Why.** Most organizations will not give the scanner a GitHub token, so the setup without a token is the normal path, and the scanner should read as much as it can through Civis alone. Before this change, every git-backed object was "not scanned" without a token. The civis-python 2.10 API spec has two read endpoints for an item stored in git: `GET .../git/commits` (the commits on the item's current branch) and `GET .../git/commits/{commit_hash}` ("Get file contents at git ref", which returns `content`, `type`, `size`, and `fileHash`). They exist for Python scripts, workflows, notebooks, R, SQL, and JavaScript scripts, and reports, but not for container scripts, Services, or dbt scripts. Civis fetches the file itself, so the scanner reads it from the Civis API with no GitHub token and no call to GitHub. Requested by Rob, November 2, 2026.

| # | Choice | Why |
|---|---|---|
| 17.1.1 | **Python scripts.** Today's order stays: with a GitHub token, the repository is read from GitHub. With no token, or if GitHub fails for any reason (`RepoUnavailable`), the script's own file is read through Civis (`scanner/retrieval/civis_git.py`) and scanned as a Python file named by its `git_path`. Such a script is "partial", with a note that other files in its repository (other modules, `requirements.txt`, `pyproject.toml`, `Dockerfile`) were not checked. | One file is not the whole repository, and the note says what a token would add. A non-GitHub host also falls back to Civis, since Civis reads the file. |
| 17.1.2 | **Commit.** A `git_ref` of 7 to 40 hex characters is used as the commit as is. Any other ref (branch or tag) is resolved with the commits endpoint, and the newest commit by `date` is used (the first one if a date is missing). If the endpoint returns no commit, the reason says so, and for a tag it says the ref is a tag. | The file endpoint takes a commit SHA, not a branch or tag name. |
| 17.1.3 | **Response shape.** The spec says the commits endpoint returns one object (`GitFileGitCommit`), but civis-python lists `scripts/python3/{id}/git/commits` and `workflows/{id}/git/commits` in `GET_PATHS_WITH_ARRAY_RESPONSE` ("Civis API says these paths ... should return an object, but they actually return an array"). Both shapes are accepted. | Either one may come back in the live test. |
| 17.1.4 | **One-file rules.** The file gets the GitHub path's rules for one file: at most 2 MB (`MAX_FILE_BYTES`, checked against both `size` and the content), decoded as UTF-8 with bad bytes replaced. If the content is base64 (only base64 characters, no spaces or tabs, and it decodes to valid UTF-8), it is decoded first. Code and YAML always have spaces or punctuation outside the base64 alphabet. | Civis may pass GitHub's contents encoding through; the spec does not say. Decoding costs nothing if it does not. |
| 17.1.5 | **Failures.** Any API error, an empty file, a file over 2 MB, or no commit becomes a short reason in the note, and the object falls back to its behavior before this change. Each failure is logged as `CIVIS_GIT_FAILED object_type=... object_id=... error=<type> [status=<HTTP status>]` through `scanner/scan_log.py`. The non-API error types are `GitCommitNotFound`, `GitFileEmpty`, and `GitFileTooLarge`. | Error type and status only, like `OBJECT_ERROR`. A separate line type lets TESTING item 4.8 count these failures apart from objects that could not be read at all. |
| 17.1.6 | **Method names.** `scripts.list_python3_git_commits` and `scripts.get_python3_git_commits`; `workflows.list_git_commits` and `workflows.get_git_commits`. They are the same in civis 2.10 and v3, so no fallback helper is needed. civis-python names a GET `list_` when its path is on the array list, and v3 removes only the legacy-name list (`LEGACY_PATHS_WITH_LIST_NAME`), not the array list. A `get_` name for the commits list would also clash with the file method. None of the four is in section 7's 19 renames or its 37 pending renames. `tests/test_retrieval.py` runs civis-python's own `parse_method_name` with and without legacy names to check all four. The v3 changelog was not read again for this change (no GitHub calls from this session); section 7 records its renames. | The scanner uses names that work on 2.10 and v3. |
| 17.1.7 | **Workflows.** The workflow detail response has no git field, so `workflows.get_git` is called for every active workflow. A git-backed workflow's saved definition can be out of date, like a git-backed Python script's saved source: the git metadata has `pull_from_git`, so Civis copies from git only when set to. A git-backed workflow's definition is read through Civis during inventory and parsed instead of the saved one, for both its inline tasks and the jobs it runs. | The git file is what the workflow should run. |
| 17.1.8 | **Workflow status.** A workflow read through Civis is "scanned", not "partial": its git file is the whole workflow, and a token would add nothing (workflows are never read from GitHub). If the read fails, the saved definition is parsed, with a note that it may not match git: on the object row ("partial") when the workflow has inline code, or as a coverage row when it only runs other jobs. | The Python script note ("other files were not checked") would be wrong for a workflow. **This differs from the request, which asked for the same status rules as Python scripts. To review.** |
| 17.1.9 | **Log only (part C).** If an object's code could not be read but its most recent run log or deployment log was read, it is "partial", and its note adds "Only its <log> was checked." after the reason the code was not read. If neither was read, it stays "not scanned". The missing-token reason now says no token is the normal setup. | A log was checked, so "not scanned" hid work the scan did. Without a token this is the usual case for git-backed container scripts, Services, and dbt scripts. |
| 17.1.10 | **Overview.** The "By object type" table gains a "Partly scanned" column, and the Not scanned tile links to both the not scanned and the partly scanned objects. | Partial objects were in no column, so rows did not add up to Found; more objects are partial now. |
| 17.1.11 | **Counts.** The final `RUN` line adds `civis_git_reads` and `civis_git_failures`. | TESTING item 4.8 measures the change with counts only. |
| 17.1.12 | **Preview.** The local preview seeds every scan without a GitHub token. | The normal setup is what most admins will see. |

**Call cost** (added to section 5):

| Object | Extra Civis API calls |
|---|---|
| Every active workflow | 1 (`workflows.get_git`), including workflows that only run other jobs and so are not objects |
| Git-backed workflow | 2 more (commits list and file; 1 for a commit SHA ref), so 3 in all |
| Git-backed Python script, no token or GitHub fails | 2 (commits list and file; 1 for a commit SHA ref) |
| Git-backed Python script read from GitHub | 0 |
| Every other object | 0 |

**Open questions, confirmed by TESTING item 4.8:**

- Which GitHub connection Civis uses to read these files, and whether the Robot user can call these endpoints. If every read fails with the same error, the error type and status show which.
- Whether the commits endpoint returns an array or one object, and whether its newest commit for a tag is the tagged commit or the newest on the branch.
- Whether `content` is plain text or base64.
