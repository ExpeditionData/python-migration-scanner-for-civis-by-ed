# Python Migration Scanner for Civis Platform by Expedition Data

This tool is independent and is not affiliated with, endorsed by, sponsored
by, or supported by Civis Analytics. "Civis" and "Civis Platform" are
trademarks of Civis Analytics, used here only to identify the platform this
tool works with.

**Status: release candidate.** This version is still being tested in Civis Platform. Wait for the v0.1.0 release before you use it.

A free, open-source tool that Civis Platform customers run inside their own
Civis environment. It finds scripts, notebooks, workflows, Services, and
container scripts affected by the February 2027 Civis default image update
(Python 3.14 to 3.15, and civis-python v2 to v3), and shows what to fix.

It has two parts:

- **A scanner job.** A Civis container script that finds the affected objects
  and writes results to a schema you choose.
- **A web app.** A Civis Service where admins start scans, review results, and
  track a fix checklist.

> **Disclaimer.** This tool is provided free and "as is," without warranty of
> any kind, under the Apache License 2.0. It scans for known patterns
> associated with the February 2027 Civis Platform update (Python 3.15 and
> civis-python v3). It is not a complete audit. It can miss affected code and
> can flag code that is not affected. Results depend on the permissions of the
> Civis user the scan runs as; objects that user cannot see are not scanned.
> Test every job before February 2027. Expedition Data LLC is not liable for
> any damages arising from use of this tool and has no obligation to provide
> support or updates. This is not professional advice.

Setup takes about 30 minutes and is described in [Setup](#setup) below. Field
names are the ones used in Civis help articles. Where a name could not be
confirmed against the Civis web interface, it is followed by "(label to
confirm)".

## Security and privacy

Read [SECURITY.md](SECURITY.md) before you install the tool. It links each
statement below to the code and tests that implement it. By design:

- **Runs inside Civis.** Both parts are designed to run inside your Civis
  environment. The tool sends no telemetry and no data to Expedition Data LLC.
- **Read-only on your objects.** The tool reads your Civis objects and does not
  modify them. The web app starts runs of the scanner job you created.
- **Locations, not code.** For each finding, the tool records the object ID,
  rule ID, file name, and line number, not code, log text, or notebook output.

These statements describe intended behavior. The full source code is available
for your review.

### Setup guidance for admins

- **Run as the Robot user.** The scanner sees only what its user can see.
- **Use a dedicated results schema.** Choose a schema used only for this tool.
  When the app creates it (or a database admin creates it with
  `AUTHORIZATION`), the database user behind the scanner job's `RESULTS_DB`
  credential owns it. Other database users have no access by default, apart
  from superusers, so admins read results through the app. To query the
  tables directly, use that credential, or ask a database admin to grant read
  access. The tool does not run `GRANT` statements.
- **Share the web app with Civis admins only.** Anyone who can open it sees the
  names, owners, and links of every object the scanning user can see, including
  private objects.
- **Share the scanner job with Civis admins only.** Each scan saves the full
  report as a CSV run output on the job, so anyone who can read the job can
  download it.
- **GitHub token (optional).** The normal setup has no token (see "Before you
  start" below). If you add one:
  - Use a fine-grained token with read-only "Contents" permission.
  - Limit it to the repositories your Civis objects use.
  - Give it an expiration date.
  - Attach it to the scanner job only, never to the Service.
- **Retention.** All scan runs are kept by default. To delete old runs
  automatically, set the scanner job's `RETENTION_DAYS` parameter. To delete runs
  by hand, use the SQL in [SECURITY.md](SECURITY.md#keeping-and-deleting-results).
- **Downloads.** "Download CSV" in the web app saves the full report to the
  viewer's computer, outside Civis. Decide whether your admins should use it.
- **Framing rule.** The app is designed to allow framing only by itself and
  Civis Platform, and to enforce this rule by default. For troubleshooting only,
  the optional Service setting `CIVIS_FRAME_ANCESTORS_REPORT_ONLY=true` turns a
  wrong framing rule into a browser console warning instead of a blank page.
  Set it back to `false` once the app shows inside Civis.

## Setup

Point both parts at a **release tag** of this repository, never at `main`. In
the steps below, replace `<release tag>` with the newest release listed on
this repository's Releases page, for example `v1.0.0`. Use the same tag for
both parts.

### Before you start

You need:

1. **A Civis admin** to do the setup and to be the first person the app is
   shared with.
2. **The Robot user.** The scanner sees only what the user who runs it can see,
   so it should run as your organization's Robot user (or another service
   account that can read every object you want scanned). The web app's Civis
   API key belongs to the Service's owner, and scans started from the app run
   as that user, so the Robot user should own the Service too, and the
   database credential you choose for the results should be the Robot user's.

   **If you have no Robot user**, use a dedicated service account, not a
   personal account. If a person's account is deactivated, the Service stops
   working, and the results schema belongs to that person's database user, so
   a database admin then has to reassign it. The app's Setup page checks that
   one account owns the scanner job, the Service, and the database credential.
3. **A name for the results schema**: a new schema, used only for this tool,
   with letters, numbers, and underscores only (for example
   `python_migration_results`). The tool refuses a schema that holds anything
   else (any other table or view), so do not reuse an existing schema. You do
   not need to create it. The web app
   creates the schema and its five tables when you click **Set up results
   storage** (see "Set up results storage and check the setup" below). A
   database admin is needed only if the app says the database user cannot
   create schemas; the app then shows the one statement for them to run.
4. **A GitHub token is optional.** The normal setup has no GitHub token. Without one, the
   scanner reads:
   - inline code: Python script source, container commands, template code,
     and inline workflow tasks
   - notebook files
   - the file set in Civis for each git-backed Python script and workflow,
     through the Civis API. Civis reads the file from the repository, so the
     scanner does not contact GitHub for it.
   - the most recent run log of each script, and each Service's current
     deployment log

   Without a token, the scanner does not read:
   - the rest of each repository, such as other modules, `requirements.txt`,
     `pyproject.toml`, and `Dockerfile`
   - any repository code for git-backed container scripts, Services, and dbt
     scripts

   An object whose code was not fully read is marked "partial" (for example,
   when only its run log was read) or "not scanned", with the reason, so you
   can review its code manually. Run logs show most civis-python v2
   deprecation warnings, but they miss most Python 3.15 changes, because the
   code still runs on the current image.

   **If you add a token**, it adds the rest of each git-backed repository:
   the other Python files in the object's folder and its dependency files, and
   the repository code of git-backed container scripts, Services, and dbt
   scripts. Workflows are read through Civis either way. The token is only for
   reading **your own** repositories that your Civis objects use. This tool's
   own code is public and needs no token. If you add one:
   - Create a **fine-grained** GitHub token with read-only **Contents**
     permission and no other permissions, limited to only those repositories,
     with an expiration date.
   - Store it in Civis as a **Custom Credential**. Put the token in the
     password field; the user name can be anything, such as `github`.
   - Create the credential while signed in as the Robot user, or share it
     with the Robot user (label to confirm), so scans that run as the Robot
     user can use it.
   - Attach it to the scanner job only, as described below. Never attach it to
     the Service.

### Create the scanner job

1. Sign in as the Robot user if you can, and make every later change to the
   job the same way (see "Who the job runs as" below). Otherwise, create the
   job yourself and transfer ownership of it to the Robot user afterward
   (Transfer Ownership). If Civis runs a job as the user who last updated it,
   a job you created or edited yourself runs as you, not the Robot user; see
   "Who the job runs as".
2. From the **Code** menu, select **Scripts**, click **New Script**, and choose
   **Container**.
3. Fill in the script settings:

   | Field | Value |
   |---|---|
   | **Docker Image Name** | `civisanalytics/datascience-python` |
   | **Docker Image Tag** | `9.1.1` |
   | **GitHub Repository URL** | `https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git` |
   | **GitHub Repository Reference** | `<release tag>` |
   | Git credential (label to confirm) | Leave blank. The repository is public. |
   | **Command** | `cd /app && python -m scanner` |
   | **CPU** | `1000` (one core) |
   | **Memory** | `4096` (MB) |
   | Disk Space (label to confirm) | `4` (GB) |

   The image tag is pinned on purpose. Do not leave it blank or set it to
   `latest`. See [Pinned Docker images](#pinned-docker-images) to confirm the
   image has not changed.

4. Click **Set Parameters** and add these parameters. For each, set the
   Parameter Name and Type (labels to confirm) and **Required** as shown. The
   **Input Display Name** and **Input Helper Text** are up to you.

   | Parameter Name | Type | Required | Meaning |
   |---|---|---|---|
   | `RESULTS_DB` | Database | Yes | Database and credential for the result tables. Choose the Robot user's credential. The web app reads this setting from the job too. |
   | `RESULTS_SCHEMA` | String | Yes | The results schema name you chose. Letters, numbers, and underscores only. The web app reads this setting from the job too. |
   | `GITHUB` | Custom Credential | No | Optional, and not part of the normal setup. A GitHub token for your own repositories. Adds the rest of each git-backed repository and the code of git-backed container scripts, Services, and dbt scripts. |

   **Optional parameters (add only to change the default).** Leave these out
   unless you need a different value. When a parameter does not exist, the
   scanner uses its default.

   | Parameter Name | Type | Default | Meaning |
   |---|---|---|---|
   | `LOOKBACK_DAYS` | Integer | `400` | Check only scripts that ran or changed in this many days, and read their most recent run log (1 to 3,650). |
   | `MAX_CONCURRENCY` | Integer | `4` | Worker threads (1 to 16). For each thread above 4, add 512 MB to **Memory**. |
   | `REQUEST_BUDGET` | Integer | `500` | Civis API requests per 5 minutes (10 to 1,000). Civis allows 1,000 per user, shared with the web app and any other jobs that run as the Robot user. |
   | `RETENTION_DAYS` | Integer | blank (keep every scan) | A number deletes scans older than that many days. Blank or 0 keeps every scan. The newest scan is not deleted. |
   | `RUN_ID` | String | blank (start a new scan) | A run ID resumes that scan (see below). Add it only to resume. |

   Click **Add** after each parameter, then **Save**. The names must match
   exactly; `DATABASE` is reserved by Civis, which is why the database
   parameter is `RESULTS_DB`. The app's Setup page flags any other parameter
   name, to catch typos.

5. Set the parameter values on the script page: choose the database and its
   credential for `RESULTS_DB`, type the schema name for `RESULTS_SCHEMA`, and,
   if you made one, choose the GitHub credential for `GITHUB`. Save the script.
6. Note the job's ID: it is the number at the end of the script page's address
   (`.../spa/#/scripts/containers/<ID>`).
7. Share the job with Civis admins only. Do not schedule it.

**Which scripts a scan checks (`LOOKBACK_DAYS`).** A scan lists every script
the scanning user can see. It then checks only the Python, container, custom,
and dbt scripts that ran or changed in the last `LOOKBACK_DAYS` days, and
reads the most recent run log only for those. Notebooks, Services, and
workflows are always checked: Civis's lists do not show when they were last
used, and there are usually few of them. Scripts outside the window are
counted on the Overview (for example "1,234 scripts that have not run or
changed in the last 400 days were left out") but not checked.

- **Default: 400 days.** A script that has not run or changed in that time is
  very likely unused. 400 days, not 365, keeps jobs that run once a year, such
  as year-end or fiscal-year jobs, even when a yearly run happens a few weeks
  late.
- **To change it,** add the `LOOKBACK_DAYS` parameter to the scanner job
  (Integer, not required) and save the job. The value can be the parameter's
  default or the value saved on the job; when both are set, the job's saved
  value is used. Use any whole number from 1 to 3650. Civis starts the job
  with its saved parameters, so **Run scan** always uses that value; the app
  cannot pass a different window. The Overview shows, next to **Run scan**,
  which window the next scan will use.
- **A full audit.** Once before the February 2027 update, consider one scan
  with `LOOKBACK_DAYS` set to `3650`. It checks every script, at the cost of a
  much longer scan. Then set it back.

**Who the job runs as.** The scan sees only what the user it runs as can see.
Civis's help describes that user two ways: as the user who runs the script, and
as the user who last updated it. Until this is confirmed, set things up so both
give the same answer:

- Create and edit the scanner job while signed in as the Robot user.
- Start scans with **Run scan** in the web app, which starts them through the
  Service's owner (the Robot user). Do not start the job from its own page.

**Resuming a stopped scan.** If a scan stops partway (for example, it was
cancelled or hit the 5-day limit for container scripts):

1. Sign in as the Robot user if you can. On the scanner job, add the `RUN_ID`
   parameter (String, not required) if the job does not have it, set it to
   the run ID from the `RUN run_id=... status=running` line near the top of
   the stopped run's log, and save.
2. Click **Run scan** in the web app. The scan skips the objects it already
   finished.
3. When it finishes, clear `RUN_ID` the same way (signed in as the Robot user)
   and save. The web app starts the job with its saved parameters, so a
   leftover `RUN_ID` would make every later scan try to resume a finished run.
   The Setup page warns while `RUN_ID` has a value.

### Create the Service

1. Sign in as the Robot user if you can, so the Robot user owns the Service.
   The app uses its owner's Civis API key for everything it does.
2. From the **Publish** menu, click **Service Deployment**.
3. In the **Docker** section:
   - **Image Name:** `civisanalytics/civis-services-flask`
   - **Tag:** `0.1.0`
4. In the **Git Connection** section:
   - **Repository:** `https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git`
   - **Branch:** `<release tag>` (the same tag as the scanner job)
   - **Path:** leave blank.
5. Under **Environment Variables**, add these key and value pairs, then click
   **Save**. They are not secrets, and anyone who can read the Service can see
   them, so never put a token or password here.

   | Key | Value |
   |---|---|
   | `SCANNER_JOB_ID` | The scanner job's ID from step 6 above. The app reads the results database, credential, and schema from the job's `RESULTS_DB` and `RESULTS_SCHEMA` parameters. |
   | `CIVIS_SERVICE_HEALTH_PATH` | `/healthz` |
   | `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` | Optional (default `false`). Leave it out. For troubleshooting only: if the app does not show inside Civis, set it to `true`, then back to `false` (step 10). |
   | `CIVIS_PLATFORM_URL` | Optional. Leave it out unless your Civis web interface is not at `https://platform.civisanalytics.com`. |
   | `CIVIS_FRAME_ANCESTORS` | Optional. Leave it out unless Civis shows Services from a different address. |

6. Do not attach any credentials under **Security and Access**, then
   **Credentials**. Credentials attached to a Service become environment
   variables inside the app. The app needs none, and the GitHub token must
   never be attached here. If the deploying user's Civis profile has GitHub
   credentials, Civis adds their token to the Service as `REPO_HTTP_TOKEN`;
   the app does not read it, and to keep it out, deploy as a user with no
   GitHub credentials.
7. Leave the resources at their defaults with one replica. Under
   **Multischedule**, choose **On Demand**. The app sleeps after an hour
   without viewers and wakes when someone opens it.
8. Click **Start Deployment** and wait for the app to show in the preview.
9. **Share the Service with Civis admins only.** Civis shares a new Service
   with your default group automatically, so remove that group: open the
   Service report, leave full-screen mode, click the Sharing icon, and keep only
   Civis admins.
10. Check the frame rule. With the browser's developer console open, reload
    the Service's page in Civis. The app shows in the preview pane, and the
    console has no Content Security Policy errors from the app. If the pane is
    blank and the console names a `frame-ancestors` error with another parent
    address, add that address to `CIVIS_FRAME_ANCESTORS` and click **Update
    Deployment**. To troubleshoot further, set
    `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` to `true` and click **Update
    Deployment**: the browser then reports a wrong rule as a console warning
    (Chrome, Edge, or Firefox; Safari ignores it) and shows the app. Set it
    back to `false` (or remove it) and click **Update Deployment** once the app
    shows, because while it is `true` other websites could embed the app.

### Set up results storage and check the setup

1. Open the app: after **Start Deployment**, Civis shows it in the preview
   pane on the Service's page. The Overview page says the
   results schema does not exist yet, and **Run scan** is off until it does.
2. Click **Set up results storage**. The app creates the schema named in the
   scanner job's `RESULTS_SCHEMA` parameter and its five tables, with the
   `RESULTS_DB` database and credential. It runs no `GRANT`, `DROP`, or
   `ALTER` statements.
3. If the app could not create the schema (most often because the database
   user cannot create schemas), send the one statement it shows to a database
   admin. It has the form
   `CREATE SCHEMA <schema> AUTHORIZATION <database user>;`. After they run it,
   click **Set up results storage** again to create the tables. If the schema
   already existed and the app could not create the tables in it, the
   statement is `ALTER SCHEMA <schema> OWNER TO <database user>;`, which makes
   the `RESULTS_DB` database user the schema's owner, as described in
   [Security and privacy](#security-and-privacy).
4. Open the **Setup** page. It reads the scanner job, the Service, and the
   results storage, changes nothing, and shows each check as pass, warning,
   fail, or review, with the fix. Fix any line that is not a pass. The sharing
   line is always "review": confirm that only Civis admins have access. The
   frame rule line passes while the rule is enforced, and is a warning while
   `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` is `true`.
5. If you need help, click **Build test summary** on the Setup page, copy the
   text, and send it to whoever supports your setup. It holds counts, fixed
   labels, and error types only. Do not send the Setup page itself, which can
   list user and group names.

### Run the first scan

1. Open the app in the preview pane on the Service's page in Civis.
2. On the Overview page, click **Run scan**. The button changes to "Scan
   running" and stays off until the scan ends, so only one scan runs at a
   time.
3. The Scan history page shows progress: Inventory, Code and log scan, and
   Save results. How long a scan takes depends mostly on how many scripts ran
   or changed in the `LOOKBACK_DAYS` window, not on how many exist. Listing
   every script takes a few minutes on its own. For example, in a test
   organization with about 11,000 scripts, of which about 420 ran or changed
   in the last year, a scan was estimated at about 20 minutes with the
   default request budget. A full audit (`LOOKBACK_DAYS` of 3650) of an
   organization that size can take several hours. You can close the app; the
   scan keeps running.
4. When the scan finishes, review the Overview, Inventory, and Findings pages.
   Change checklist statuses on the Findings page and click **Save changes**.
   Each change is saved with your Civis user name and the time.
5. **Download CSV** saves the full report to your computer (see
   [Security and privacy](#security-and-privacy)).

**Custom scripts and templates.** A custom script runs its template's code:
it has no code of its own and takes every change made to the template's
backing script. So a fix in the backing script fixes every custom script made
from that template. Inventory and Findings show each custom script's template
ID and backing script ID (the backing script is linked when the scan also
checked it), and both pages can be filtered by template ID. The Overview says
how many templates, used by how many custom scripts, have findings. Civis
also lists each custom script under its backing script's type; the scan
counts it once, as a custom script.

**Affected and Advisory.** A finding is "Affected in February" when its
object runs on a Civis default image with a blank tag, `latest`, the current
default tag, or that tag's major part (`datascience-python` `9.1` or `9`,
`civis-jupyter-python3` `5.1` or `5`). The current tag counts because Civis
saves it on new objects that do not choose one. A patch tag such as `9.1.1`
names a fixed image, so its findings are Advisory. The Findings page's "How
to read this page" explains impact, confidence, and status.

**If progress stops updating.** Civis has asked for the page to be reloaded:
Civis's proxy in front of the Service uses a cookie that expires after some
minutes, and only a page load renews it. When that happens, the app reloads
the page itself, about once a minute at most, and after a few tries it asks
you to reload the page. Civis's "Cookie expired." message on the Service's
page is the same thing: reload the page.

### The CSV report

The CSV report starts with two notice lines: line 1 is the disclaimer and line
2 is the non-affiliation statement. The column names are on line 3. When you
load the file with code, skip the first two lines, for example
`pandas.read_csv(path, skiprows=2, encoding="utf-8-sig")`. The result tables
in your schema are the machine-readable copy of the same results.

If the scan fails, open the scanner job's run log. It is designed to contain
only progress counts, object IDs, rule IDs, and error types. Read it before you
share it with whoever supports your setup.

### Update to a new release

1. Read the release notes for the new tag. They say whether a parameter or an
   environment variable was added or changed.
2. Sign in as the Robot user if you can, since changing the tag also updates
   the job (see "Who the job runs as"). On the scanner job, change **GitHub
   Repository Reference** to the new tag and save. The next scan uses it.
3. On the Service, change **Branch** to the same tag, save, and click
   **Update Deployment**.
4. Open the app's **Setup** page and check that every line still passes. Line 7
   compares the Service's tag with the scanner job's. It is "review" instead
   of a pass if Civis added the deploying user's GitHub token to the Service
   as `REPO_HTTP_TOKEN` (step 6 of "Create the Service").
5. Run a new scan. Earlier scans and checklist statuses are kept.

Always change both parts to the same tag.

### Remove the tool

1. Archive the scanner job: click the file box (Archive) icon at the top right
   of its page. Archive the Service the same way (label to confirm for
   Services).
2. The CSV reports stay attached to the archived job's runs. Keep the archived
   job shared with Civis admins only.
3. Drop the results schema, for example `DROP SCHEMA results_schema CASCADE;`
   with your schema's name. Run it with the `RESULTS_DB` credential (its
   database user owns the schema), or ask a database admin.
4. If you made a GitHub token, delete the Custom Credential in Civis and revoke
   the token in GitHub.

## Pinned Docker images

The tool is designed to run only on these exact images and to install nothing
at run time. To
confirm an image has not changed, compare its digest with the one below, for
example with `docker pull <image>@<digest>`.

| Part | Image and tag | Digest (linux/amd64) | Pushed |
|---|---|---|---|
| Scanner job | `civisanalytics/datascience-python:9.1.1` | `sha256:d6903fdcf6b13801417621ad7522c09a16e1d2c01d11eb398f75ad0f73d974ef` | September 17, 2026 |
| Web app Service | `civisanalytics/civis-services-flask:0.1.0` | `sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f` | August 6, 2025 |

The digests were read from Docker Hub on October 13, 2026, and both images
were pulled by digest and checked again on October 22, 2026.

## For developers

- **Setup and test commands:** see [CLAUDE.md](CLAUDE.md).
- **Design and decisions:** see [docs/PLAN.md](docs/PLAN.md).
- **Live test checklist:** see [TESTING.md](TESTING.md).
- **Unit tests** use synthetic data only and make no Civis or network calls.
  They also run inside both pinned images (see CLAUDE.md).
- **Dry run.** `python -m scanner --dry-run --out dry_run_output` runs the whole
  scanner on made-up data and writes a local SQLite file and CSV report. It
  does not contact Civis or GitHub.
- **Local app preview.** `python -m scanner --dry-run --preview --out dry_run_output`,
  then `python -m app.local --results dry_run_output`, and open
  http://127.0.0.1:8050. It shows made-up data only, listens on this machine
  only, and refuses to start if `CIVIS_API_KEY` is set.
- **Secret scanning.** Turn on the secret-scanning hook in each clone:
  `git config core.hooksPath .githooks`.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).

The Expedition Data name and logo are trademarks of Expedition Data LLC and are not licensed under the Apache License 2.0. Civis and Civis Platform are trademarks of Civis Analytics; Expedition Data LLC is not affiliated with Civis Analytics.
