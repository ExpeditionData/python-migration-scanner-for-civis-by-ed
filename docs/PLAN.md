# Build Plan: Civis Python Version Search

Status: **approved October 9, 2026.** Written the same day, after checking the build brief against current documentation. The decisions on every open question are recorded in section 9. Where this plan and the build brief differ, this plan wins.

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
civis-python-version-search/
  scanner/                  Container script entry point: python -m scanner
    __main__.py             Reads job parameters, runs the scan
    inventory/              One module per object type (python3, containers, custom,
                            notebooks, services, workflows, users), plus base.py
                            for shared helpers and __init__.py for build_inventory()
    retrieval/              Inline code, notebook download, GitHub fetch, template fallback
    logs.py                 Log scan (most recent run only)
    engine.py               Runs AST and regex rules over code and log text
    checkpoint.py           Resume by run ID
    output.py               Table writes, CSV run output
    ratelimit.py            Shared request budget across worker threads
  app/                      Flask Service: gunicorn "app:create_app()"
    __init__.py             create_app()
    routes/                 Landing, dashboard, inventory, findings, checklist,
                            history, CSV download, /healthz, /api/scan-status
    data.py                 Reads result tables (cached per run ID)
    templates/
    static/                 Vendored JS, CSS, and fonts. No CDN links.
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
    disclaimer.py           The one disclaimer string used everywhere
    version.py
  tools/
    build_civis_api_fixture.py  Builds the civis 2.7.1 API list from the civis wheel
  tests/
    fake_civis.py           Fake civis client serving synthetic API responses
    fixtures/               Synthetic API responses and the civis 2.7.1 API list
    test_rules_civis_v3.py
    test_rules_python315.py
    test_inventory.py, test_engine.py, test_app.py, ...
    test_civis_271_compat.py  Fails if app/ or common/ calls a civis method missing from 2.7.1
  docker/
    scanner.Dockerfile      FROM the pinned scanner image. Local testing only.
    service.Dockerfile      FROM the pinned Flask image. Local testing only.
  docs/
    PLAN.md
  start_service.sh          Started by the Flask image. Launches gunicorn on port 3838.
  requirements-scanner.txt  Pins matching the scanner image (documentation and local tests)
  requirements-service.txt  Pins matching the Flask image
  requirements-dev.txt      pytest and similar. Local only.
  README.md  TESTING.md  CLAUDE.md  LICENSE  NOTICE  .env.example  .gitignore
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
- **Service.** Repo ref: the same release tag. Path left blank. `start_service.sh` runs `exec gunicorn -b 0.0.0.0:${CIVIS_SERVICE_PORT:-3838} -w 2 "app:create_app()"`.
- **Local testing.** The two Dockerfiles in `docker/` are `FROM` these exact tags and are used only for local tests.
- **Requirements files.** They record the versions inside each image, so tests run against the same versions.
- **Image digests.** Recorded in the README on October 13, 2026 (linux/amd64): `datascience-python:9.1.1` is `sha256:d6903fdcf6b13801417621ad7522c09a16e1d2c01d11eb398f75ad0f73d974ef`, and `civis-services-flask:0.1.0` is `sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f`.

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
| 12.11 | The CSV report has one row per finding, plus one row for every object with no findings, so every inventoried object appears. Cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading `'`. UTF-8 with a byte order mark, so Excel reads accents correctly. | Object names and paths are user-controlled (spreadsheet formula injection). |
| 12.12 | New "open" checklist rows use `changed_by = scanner`. | Shows they came from a scan, not a person. |
| 12.13 | Table writes and the CSV upload use civis-python's own helpers with the unwrapped client, so they are not charged to the request budget. They are a few calls per checkpoint. | civis.io helpers make their own internal calls. |
| 12.14 | `python -m scanner` refuses a live scan unless Civis has set `CIVIS_JOB_ID` and `CIVIS_RUN_ID`. `--dry-run` uses the synthetic instance, a local SQLite file, and a local CSV. | Requirement 16. |
| 12.15 | The synthetic client, synthetic HTTP session, and synthetic instance moved to `scanner/synthetic/`, so dry runs work from the scanner package. They contain made-up data only. | Dry runs need them outside the test folder. |
| 12.16 | The SQLite sink runs the retention cleanup with an `EXISTS` subquery, because SQLite has no `DELETE ... USING`. The Redshift form is still to be confirmed in Phase 7 (11.2). | Same behavior on both. |

---

## 13. Phase 4 questions (applied October 14, 2026, pending Rob's review)

**Conclusion: each question from the Phase 4 report has its recommended option applied. None of them loosens a rule in SECURITY.md or CLAUDE.md. Each is marked "applied, pending Rob's review."**

| # | Question | Recommended option | Status |
|---|---|---|---|
| 13.1 | Should a run whose table writes failed show as failed in Civis, even though the CSV report was saved? | Yes. Keep exit code 3, so the Civis run shows failed with the CSV attached and the problem is not missed. | Applied (no change), pending Rob's review |
| 13.2 | Should the scanner create the results schema if it is missing? | No. Creating schemas stays the admin's decision, like granting access. A missing schema ends the run as `tables_failed` with the CSV saved. | Applied (no change), pending Rob's review |
| 13.3 | May gitleaks be installed? | Yes. Installed for the current user with `winget install Gitleaks.Gitleaks` (8.30.1). The pre-commit hook now runs gitleaks and detect-secrets. One fake test value already in history is listed in `.gitleaksignore`. | Applied, pending Rob's review |
| 13.4 | Is a blank or `latest` tag on a dbt script the Civis default Python image? | Treat it as the default image, as now. The dbt script API returns only a tag. Confirm in Phase 7. | Applied (no change), pending Rob's review |
| 13.5 | Should dbt scripts that are not created from a template follow the same image rule as dbt templates? | Yes. New object type `dbt_script`: inventoried and scanned (repo and most recent run log) on a Civis default image, otherwise counted as not Python with language `dbt`. Its Civis link path (`/spa/#/scripts/dbt/<id>`) is checked in Phase 7. | Applied, pending Rob's review |
| 13.6 | What contact should SECURITY.md give for reporting security issues? | GitHub private vulnerability reporting on the public repository, so no email address is published. Rob must turn it on in the repository settings (Security, "Private vulnerability reporting") before the public release. | Applied in SECURITY.md, pending Rob's review |

---

## 14. Implementation notes (Phase 5, October 14, 2026)

**Conclusion: the web app is built to the mockups and tested on made-up data only. These choices were not covered by the plan.**

### Service settings

| Variable | Meaning |
|---|---|
| `SCANNER_JOB_ID` | ID of the scanner container script. Without it, Run scan is off and a banner says so. |
| `RESULTS_DATABASE` | Database that holds the results schema. |
| `RESULTS_SCHEMA` | The results schema. Must be a plain SQL identifier. |
| `RESULTS_CREDENTIAL_ID` | Optional database credential ID. Blank uses the Service owner's default credential. |
| `CIVIS_PLATFORM_URL`, `CIVIS_FRAME_ANCESTORS` | Optional, as before (sections 10.14 and 11.3). |

### Choices

| # | Choice | Why |
|---|---|---|
| 14.1 | The findings page shows one row per object and rule, the unit the checklist tracks, with every location listed (up to five, then "and N more"). | One status dropdown per checklist item. Two findings for the same object and rule would otherwise show two dropdowns for one status. |
| 14.2 | The scan progress card shows three steps (Inventory, Code and log scan, Save results), not the mockup's four. | The scanner reads each object's code and most recent log together, so a separate "Log scan" step would never show real progress. |
| 14.3 | "Affected" counts objects with at least one affected finding; "Advisory" counts objects with findings, none affected. "Not scanned" counts not-scanned objects, with partly scanned objects as a note under it. | Matches the mockup's tile wording. |
| 14.4 | Scans are numbered Run 1, Run 2, and so on, by start time in the `scan_runs` table. The history table shows counts for the 10 most recent completed scans. | Each table read in Civis starts a query run, so older scans are listed without counts. |
| 14.5 | Finished scans' tables are cached in memory (12 tables). The scan list, checklist, and schema check are cached for 20 seconds; a checklist save clears the checklist cache at once. A new civis client is made for each request. | Section 4 and 1.10. |
| 14.6 | Every page shows the full disclaimer from `common/disclaimer.py` plus the admin-only note, not the mockup's shorter sentence. | The same disclaimer must appear in the README, every report, and the app. |
| 14.7 | Times are shown in UTC, month first ("October 12, 2026 at 2:14 PM UTC"). | Civis stores UTC, and the server does not know the viewer's time zone. |
| 14.8 | Filters and search are forms that submit to the server (50 rows per page), with an Apply filters button. The mockup's type-to-filter search needs JavaScript; this works without it and with a keyboard. | Accessibility and very large inventories. |
| 14.9 | Download CSV sends the browser to the Civis file link for the scanner run's CSV output. Local preview serves the file from the dry-run folder. The app does not copy the report itself. | The file stays in Civis file storage; downloading is the viewer's choice (SECURITY.md). |
| 14.10 | Saving checklist changes needs the `Civis-Username` header. Without it, the dropdowns are disabled and a note explains why. The Civis save is one validated and escaped INSERT (query_civis has no bound parameters); local preview uses bound parameters. | Requirement 4 and the "recorded with the Civis-Username header" rule. |
| 14.11 | The results schema check uses `information_schema.schemata` and `information_schema.tables`. To confirm on Redshift in Phase 7. | Distinguishes "schema missing" from "no scan yet." |
| 14.12 | A failed Civis run shows as "Failed" only. The run's error text is never shown. | Error text can hold job output. |
| 14.13 | The time left estimate uses the timestamps on the scanner's PROGRESS log lines. | No extra API calls. |
| 14.14 | Local preview: `python -m scanner --dry-run --preview` seeds made-up data, including one object with a hostile name; `python -m app.local` serves it on 127.0.0.1 only, refuses to start if `CIVIS_API_KEY` is set, and `--state` shows each empty and error state. | Lets Rob click through every page on this machine without Civis. |
| 14.15 | The civis 2.7.1 compatibility test now also checks calls through an injected `civis.io` module (`self.io.read_civis_sql(...)`). | The app passes civis.io in, so the plain `civis.io.` check would miss those calls. |
