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
names are the ones the Civis web interface showed in a live test, or the ones
used in Civis help articles. Where a name could not be confirmed against the
Civis web interface, it is followed by "(label to confirm)".

## Security and privacy

Read [SECURITY.md](SECURITY.md) before you install the tool. It links each
statement below to the code and tests that implement it. By design:

- **Runs inside Civis.** Both parts are designed to run inside your Civis
  environment. The tool sends no telemetry and no data to Expedition Data LLC.
- **Read-only on your objects.** The tool reads your Civis objects and does not
  modify them. The web app starts runs of the scanner job you created.
- **One install script.** An admin runs it by hand to create and update the
  scanner job and the Service. It changes only those two objects, and only
  the fields it lists.
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
  tables directly, use that credential, or give a group read access (see
  [Read access to the result tables](#read-access-to-the-result-tables)). The
  tool does not run `GRANT` statements.
- **Share the web app with Civis admins only.** Anyone who can open it sees the
  names, owners, and links of every object the scanning user can see, including
  private objects.
- **Share the scanner job with Civis admins only.** Each scan saves the full
  report as a CSV run output on the job, so anyone who can read the job can
  download it.
- **GitHub token (optional).** The normal setup has no token (see "Before you
  start" below). If you add one:
  - Use a fine-grained token with read-only "Contents" permission.
  - Limit it to the repositories your Civis objects use. This still limits
    which private repositories it can read, but GitHub gives every
    fine-grained token read access to all public repositories, whatever
    repositories it is limited to. With a token attached, the scan also
    downloads public repositories that your Civis objects use, so those
    objects are scanned from GitHub.
  - Give it an expiration date.
  - Set it in the install script, which attaches it to the scanner job only,
    never to the Service.
- **Retention.** All scan runs are kept by default. To delete old runs
  automatically, set `RETENTION_DAYS` in the install script and run an update
  (see [Update to a new release](#update-to-a-new-release)). To delete runs
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
this repository's Releases page, for example `v1.0.0`. The install script
puts the same tag on both parts.

An install script creates and updates the scanner job and the Service for
you, through the Civis API. You paste it into a Civis Python script, set a
few values at its top, and run it. It is the only part of the tool that
creates or changes a script or a Service in Civis, and it changes only these
two objects (see [SECURITY.md](SECURITY.md#the-install-script)).

**Do not save the scanner job's page in Civis.** The page's **Repository**
field cannot show this repository's address, and a save can clear it. Make
every change to the scanner job, and to the Service fields the script sets,
with the install script (see
[Update to a new release](#update-to-a-new-release)). The Service's optional
environment variables (step 10 of [Install](#install)) are the only settings
you change on a page. Sharing an object does not save its page.

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
   The install script makes the user who runs it the owner of both objects,
   so run it as the Robot user.

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
     and inline workflow tasks. In a docker command (of a container script, a
     template, or a workflow task), Python run with `python -c "..."` or from
     a heredoc on `python` (for example `python - <<EOF`) is checked with the
     Python rules too, as are the same forms in notebook shell lines and
     Dockerfiles. Python piped in from another command, `bash -c` holding a
     `python -c`, and a script run by name (`python run.py`) are not read
     this way; a script file is read from its repository only when a token is
     attached.
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
     with an expiration date. GitHub gives every fine-grained token read
     access to all public repositories, whatever repositories it is limited
     to, so with a token attached the scan also downloads public repositories
     that your Civis objects use, and those objects are scanned from GitHub.
     The limit still decides which private repositories it can read.
   - Store it in Civis as a **Custom Credential**. Put the token in the
     password field; the user name can be anything, such as `github`.
   - Create the credential while signed in as the Robot user, or share it
     with the Robot user (label to confirm), so scans that run as the Robot
     user can use it.
   - Set the credential's ID as `GITHUB_CREDENTIAL` in the install script
     (step 4 of [Install](#install)), instead of attaching the credential by
     hand. The script checks that it is a Custom credential, attaches it to
     the scanner job only, and gives the job more resources for the GitHub
     cache. It never attaches it to the Service.

### Install

1. Sign in to Civis as the Robot user. The install script makes the user who
   runs it the owner of the scanner job and the Service, and an update
   changes only objects that user owns.
2. From the **Code** menu, select **Scripts**, click **New Script**, and choose
   **Python** (label to confirm).
3. Open the install script at your release tag:
   `https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed/blob/<release tag>/install/civis_install.py`.
   Copy the whole file and paste it as the Python script's code. Read the
   comment at its top: it says what the script does and what it cannot do.
   Always use the copy from the tag you install.
4. Set the values at the top of the script, above the line "Do not change
   anything below this line":

   | Setting | Value |
   |---|---|
   | `MODE` | `"check"` |
   | `TAG` | `"<release tag>"`, the tag the file came from |
   | `RESULTS_DATABASE` | The results database's name in quotes, or its ID |
   | `RESULTS_CREDENTIAL` | The ID of the Robot user's database credential for that database (Civis's credential list shows each credential's ID; label to confirm) |
   | `RESULTS_SCHEMA` | The results schema name you chose, in quotes |
   | `GITHUB_CREDENTIAL` | `None` (no token, the normal setup), or your Custom credential's ID |
   | `LOOKBACK_DAYS` | `None` (400 days), or a whole number from 1 to 3650 |
   | `RETENTION_DAYS` | `None` (keep every scan), or a number of days |

   Leave `JOB_ID`, `SERVICE_ID`, and `INSTALL_SERVICE_ONLY` as they are.
5. Run the script with `MODE` `"check"`. It reads the values and your Civis,
   and changes nothing. Every `CHECK` line should say `pass`, the `PLAN`
   lines say what install will create, and the last line says "Nothing
   changed." If a `CHECK` line says `fail`, fix the value it names and run
   it again.
6. Set `MODE` to `"install"` and run the script again. It creates the scanner
   job, then the Service, reads both back, and prints one `RESULT` line per
   setting. Every `RESULT` line should say `pass`. If a step fails, the
   `RECOVER` lines say what was created and what to do.
7. Note the two IDs on the line that starts with `KEEP IN CIVIS:`. You need
   them for every update. They stay in this script's run log in Civis, and
   each object's ID is also the number at the end of its page's address. Do
   not send them to anyone who supports your setup.
8. Share both objects with Civis admins only. Civis shares a new job and a
   new Service with the default group of the user who creates them, so on
   each object's page, open its sharing settings (label to confirm), and
   remove that group and everyone else who is not a Civis admin. Adding Civis
   admins is optional. The Setup page's sharing line (step 4 of
   [Set up results storage and check the setup](#set-up-results-storage-and-check-the-setup))
   shows who has access. Do not schedule the scanner job.
9. On the Service's page, click **Start Deployment** and wait for the app to
   show in Civis's preview pane on that page (the "PREVIEW" label above the
   app is Civis's). The Service is On Demand: it sleeps when no one uses it
   and takes about a minute to wake when someone opens it.
10. Check the frame rule. With the browser's developer console open, reload
    the Service's page in Civis. The app shows in the preview pane, and the
    console has no Content Security Policy errors from the app. If the pane is
    blank and the console names a `frame-ancestors` error with another parent
    address, add that address to the Service's `CIVIS_FRAME_ANCESTORS`
    environment variable and click **Update Deployment**. To troubleshoot
    further, set `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` to `true` and click
    **Update Deployment**: the browser then reports a wrong rule as a console
    warning (Chrome, Edge, or Firefox; Safari ignores it) and shows the app.
    Set it back to `false` (or remove it) and click **Update Deployment** once
    the app shows, because while it is `true` other websites could embed the
    app. Changing the Service's variables on its page is fine: in a live
    test, its **Repository** field showed this repository's address. Only the
    scanner job's page must not be saved.

If the script stops after it created the scanner job but before the Service
was finished, its `RECOVER` lines say so. To create only the Service, set
`JOB_ID` to the scanner job's ID, set `INSTALL_SERVICE_ONLY` to `True`, and
run `MODE` `"install"` again.

### What the install script sets

You can compare these with the objects' pages in Civis. "Install" means the
script sets the field when it creates the object; "update" means `MODE`
`"update"` sets it again.

| Object | Field on the Civis page | Value | When |
|---|---|---|---|
| Scanner job | Name | `Python Migration Scanner: scanner job` | install |
| Scanner job | **Image** (Docker section) | `civisanalytics/datascience-python` | install |
| Scanner job | **Tag** (Docker section) | `9.2.0` | install, update |
| Scanner job | **Repository** (**GIT CONNECTION** section) | `https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git`. The page shows the field blank, because its dropdown cannot show an address that is not in its list. | install, update |
| Scanner job | **Branch** (**GIT CONNECTION** section) | `<release tag>` | install, update |
| Scanner job | **Command** | `cd /app && python -m scanner` | install |
| Scanner job | **CPU (m)**, **Memory (MB)**, **Disk Space (GB)** | Without a GitHub token: `500`, `2048`, `1`. With one: `1000`, `4096`, `4` (room for the GitHub file cache). Memory gets 512 MB more for each `MAX_CONCURRENCY` thread above 4. | install, update |
| Scanner job | Parameters `RESULTS_DB` (Database, required) and `RESULTS_SCHEMA` (String, required) | The database, the credential, and the schema from the script | install; an update keeps them exactly as they are |
| Scanner job | Parameters `GITHUB` (Custom Credential), `LOOKBACK_DAYS` and `RETENTION_DAYS` (Integer), not required | Only when the script's setting has a value. In an update, `"clear"` removes `GITHUB` or `RETENTION_DAYS`. | install, update |
| Scanner job | Parameter `RUN_ID` | Removed, if an earlier release left it | update |
| Scanner job | Git credential | Not set: the repository is public | never |
| Service | Name | `Python Migration Scanner: web app` | install |
| Service | **Image Name** and **Tag** (Docker section) | `civisanalytics/civis-services-flask`, `0.1.0` | install; update sets the tag |
| Service | **Repository** | `https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git` | install, update |
| Service | **Initial Branch or Tag** | `<release tag>` | install, update |
| Service | **Path/Directory** | Blank | never |
| Service | **ENVIRONMENT VARIABLES** | `SCANNER_JOB_ID` (the scanner job's ID) and `CIVIS_SERVICE_HEALTH_PATH` (`/healthz`); install also adds `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` (`false`). An update keeps every other variable as it is. | install, update |
| Service | Schedule | On Demand. The form has no such setting. | install, update |
| Service | **CREDENTIALS** | None | install, update |
| Service | Resources | Not set: Civis's defaults | never |

The environment variables are not secrets, and anyone who can read the
Service can see them, so never put a token or password there. The optional
ones are `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` (default `false`; for
troubleshooting only, see step 10 of [Install](#install)),
`CIVIS_PLATFORM_URL` (only if your Civis web interface is not at
`https://platform.civisanalytics.com`), and `CIVIS_FRAME_ANCESTORS` (only if
Civis shows Services from a different address). If the deploying user's Civis
profile has GitHub credentials, Civis adds their token to the Service as
`REPO_HTTP_TOKEN`; the app does not read it, and to keep it out, deploy as a
user with no GitHub credentials. The app uses no service tokens.

**What the parameters mean.** The app reads `RESULTS_DB` and `RESULTS_SCHEMA`
from the scanner job too, so the Service needs no copy of them. The image tag
is pinned on purpose; see [Pinned Docker images](#pinned-docker-images) to
confirm the image has not changed.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `RESULTS_DB` | Database | none (required) | Database and credential for the result tables. |
| `RESULTS_SCHEMA` | String | none (required) | The results schema name. Letters, numbers, and underscores only. |
| `GITHUB` | Custom Credential | none | Optional, and not part of the normal setup. A GitHub token for your own repositories. |
| `LOOKBACK_DAYS` | Integer | `400` | Check only scripts that ran or changed in this many days, and read their most recent run log (1 to 3,650). |
| `MAX_CONCURRENCY` | Integer | `4` | Worker threads (1 to 16). The install script does not set it. |
| `REQUEST_BUDGET` | Integer | `500` | Civis API requests per 5 minutes (10 to 1,000). Civis allows 1,000 per user, shared with the web app and any other jobs that run as the Robot user. The install script does not set it. |
| `RETENTION_DAYS` | Integer | blank (keep every scan) | A number deletes scans older than that many days. Blank or 0 keeps every scan. The newest scan is not deleted. |

The defaults of `MAX_CONCURRENCY` and `REQUEST_BUDGET` fit most organizations.
An update keeps them if the job already has them. The app's Setup page flags
any parameter name not in this table, to catch typos.

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
- **To change it,** set `LOOKBACK_DAYS` in the install script to any whole
  number from 1 to 3650 and run `MODE` `"update"` with the tag you have (see
  [Update to a new release](#update-to-a-new-release)). Civis starts the job
  with its saved parameters, so **Run scan** always uses that value; the app
  cannot pass a different window. The Overview shows, next to **Run scan**,
  which window the next scan will use.
- **A full audit.** Once before the February 2027 update, consider one scan
  with `LOOKBACK_DAYS` set to `3650`. It checks every script, at the cost of a
  much longer scan. Then set it back. Both changes are an update with the
  install script.

### Who the job runs as

The scan sees only what the user it runs as can see. Civis's help describes
that user two ways: as the user who runs the script, and as the user who last
updated it. Civis does not show who started a job run. The app's Scan history
page does: its line "Started ... as <user>" names the user the scan ran as. In
a live test, every scan was started with **Run scan** in the app, the job was
last updated as the Robot user, and that line named the Robot user. That test
did not separate the two descriptions, so set things up so both give the same
answer:

- The install script creates and updates the scanner job, so run it signed in
  as the Robot user, every time.
- Start scans with **Run scan** in the web app, which starts them through the
  Service's owner (the Robot user). Do not start the job from its own page.

### Set up results storage and check the setup

1. Open the app: after **Start Deployment**, Civis shows it in the preview
   pane on the Service's page. The Overview page says the
   results schema does not exist yet, and **Run scan** is off until it does.
2. Click **Set up results storage**. The app creates the schema named in the
   scanner job's `RESULTS_SCHEMA` parameter and its five tables, with the
   `RESULTS_DB` database and credential. It runs no `GRANT`, `DROP`, or
   `ALTER` statements. This can take a while. Wait for the app's message
   about the result before you click again or leave the page. Civis's own
   schema lists may not show the new schema; search for its name to find it.
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
   `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` is `true`. Line 10 (latest scan) reads
   the new scanner job's runs: if the results schema was used by an earlier
   install, the Overview shows that install's last scan, while line 10 says
   "No scan has run yet." until the new scanner job's first scan.
5. If you need help, click **Build test summary** on the Setup page, copy the
   text, and send it to whoever supports your setup. It holds counts, fixed
   labels, and error types only. Do not send the Setup page itself, which can
   list user and group names, and do not send the install script's output.
6. Archive the install script's Python script in Civis. For each update, you
   paste a new copy from the new tag.

#### Read access to the result tables

By default, only the schema's owner (the `RESULTS_DB` database user) and
superusers can read the result tables. The schema's owner or a database admin
can give a group of admins read access:

```sql
GRANT USAGE ON SCHEMA <schema> TO GROUP <group>;
GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO GROUP <group>;
ALTER DEFAULT PRIVILEGES FOR USER <database user> IN SCHEMA <schema> GRANT SELECT ON TABLES TO GROUP <group>;
```

The first two statements cover the five tables the app creates at setup. The
third covers tables a later release adds. Anyone with this access sees the
same object names, owners, and locations the app shows, so give it only to
people the app is shared with. The tool itself runs no `GRANT` statement.

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
   scan keeps running. Scan history labels each scan by the Civis run ID of
   the scanner run that started it (for example "Civis run 12345"), so the
   label stays the same when older runs are deleted.
4. When the scan finishes, review the Overview, Inventory, and Findings pages.
   Change checklist statuses on the Findings page and click **Save changes**.
   Each change is saved with your Civis user name and the time.
5. To get the report, click **Download CSV** in the app. It saves the full
   report to your computer (see [Security and privacy](#security-and-privacy)).

**Custom scripts and templates.** A custom script runs its template's code:
it has no code of its own and takes every change made to the template's
backing script. So a fix in the backing script fixes every custom script made
from that template. Inventory and Findings show each custom script's template
ID and backing script ID (the backing script is linked when the scan also
checked it), and both pages can be filtered by template ID. The Overview says
how many templates, used by how many custom scripts, have findings. Civis
also lists each custom script under its backing script's type; the scan
counts it once, as a custom script.

**If a list stops partway.** The scan first lists every object type from
Civis. If Civis stops returning one type's list partway (for example with an
HTTP 503), the scan keeps the rows it read, goes on with the other types, and
still ends as succeeded. The Overview then shows a warning under "By object
type" for that type, saying how many rows were read. Objects after that point
were not found or scanned, so run a new scan to check them. If every list
fails, the scan stops and Scan history shows it as failed.

**Affected and Advisory.** A finding is "Affected in February" when its
object runs on a Civis default image with one of these tags:

- blank or `latest`
- a tag Civis has saved as its default on new objects: `datascience-python`
  `9.1` and `9.2`, `civis-jupyter-python3` `5.1` and `5.2`
- the major part of those tags: `9` and `5`
- a newer minor tag in the same major, such as `9.3` or `5.3`, because Civis
  may have moved its default to it since this release

Civis saves its current default tag on new objects that do not choose one.
Civis has not said whether objects saved on these tags move to the new image
in February. A patch tag such as `9.1.1`, or an older minor tag such as
`9.0`, names a fixed image, so its findings are Advisory. A Streamlit Service
counts only with a blank or `latest` tag. The Findings page's "How to read
this page" explains impact, confidence, and status.

**Resuming a stopped scan.** A scan can stop partway: it was canceled, it
failed (for example on a Civis error), or it hit the 5-day limit for
container scripts. Nobody needs to edit the scanner job to pick it up. Each
time the scanner starts, it looks at the newest scan in the results tables
and resumes it when all of these are true:

- The newest scan did not finish (Scan history shows it as Canceled, Failed,
  or Did not finish). An older stopped scan is never resumed.
- Its last Civis run ended less than 24 hours ago.
- It was resumed fewer than 2 times before.
- The tool's version, and the scanner job's `LOOKBACK_DAYS` and `GITHUB`
  settings, are the same as when it started.
- It had not saved its results yet.
- None of its Civis runs is still queued or running.

Otherwise **Run scan** starts a new scan. A resumed scan keeps its run ID and
its label (the Civis run that started it), skips the objects it already
saved, and keeps the rows it already saved. It checks the same scripts as
before, because its `LOOKBACK_DAYS` window is measured from its first start.
The scanner saves its progress every 500 objects or every 10 minutes of the
code scan, and logs a `CHECKPOINT` line each time. A scan stopped before its
first save keeps its label and run ID when resumed, but scans every object
again, so its `objects_scanned` equals `objects_total`.
There is no button to start a new scan instead while a resume applies;
waiting until the 24 hours pass does that.

How to tell what happens:

- **Before you click.** While **Run scan** would resume the stopped scan, the
  Overview says so under **Run scan**, with the time (in your local time)
  after which **Run scan** starts a new scan instead. The app cannot see
  whether the scan already saved its results, so in rare cases the scanner
  starts a new scan although the Overview showed the line.
- **While it runs.** The progress card starts with "Resuming Civis run <ID>."
- **Afterward.** Scan history shows the scan once, with "Resumed 1 time" (or
  2 times) under its label. Near the top of the scanner's run log, one line
  says what the scanner decided: `RESUME decision=resume ...`, or
  `RESUME decision=new reason=<reason>` with a fixed reason such as
  `too_old` or `settings_changed`.

**`RUN_ID` from an earlier release.** The scanner no longer uses a `RUN_ID`
parameter. An update with the install script removes it and prints
`run_id_removed=yes`. Until then, the scanner ignores it and logs
`RUN_ID_IGNORED`, and line 4 on the Setup page shows "review".

**If progress stops updating.** Live progress updates on its own for a few
minutes after the Civis page loads. Then Civis's session for the page
expires: Civis's proxy in front of the Service uses a cookie that lasts about
5 to 6 minutes from the whole-page load, and only a reload of the whole Civis
page renews it. Clicks inside the app do not extend it (measured November
18, 2026). The app then stops updating and says so. To see the latest progress, reload the whole
Civis page with the browser's reload button (F5 on Windows). Reloading only
the app's frame does not help. After the reload, the app opens on the
Overview page. Civis's "Cookie expired." message on the Service's page means
the same thing: once the cookie has expired, any click in the app shows
"Cookie expired." in the frame until you reload the whole Civis page. Press
F5 before you change checklist statuses on the Findings page: a save after
the cookie expires shows "Cookie expired." and the unsaved statuses are lost.

### The CSV report

To get the CSV report, click **Download CSV** in the app (on the Overview or
Findings page, or beside a scan on the Scan history page).

A scan that stopped before it finished may still have saved its report. Scan
history shows its CSV link once Civis shows the run as ended. If the scan
stopped before saving a report, the link opens a page that says so.

The CSV report starts with two notice lines: line 1 is the disclaimer and line
2 is the non-affiliation statement. The column names are on line 3. When you
load the file with code, skip the first two lines, for example
`pandas.read_csv(path, skiprows=2, encoding="utf-8-sig")`. The result tables
in your schema are the machine-readable copy of the same results.

If the scan fails, open the scanner job's run log. It is designed to contain
only progress counts, object IDs, rule IDs, and error types. Read it before you
share it with whoever supports your setup.

### Update to a new release

An update changes only the fields listed in
[What the install script sets](#what-the-install-script-sets), on the two
objects whose IDs you give it. It keeps `RESULTS_DB` and `RESULTS_SCHEMA`
exactly as they are. Do not change the tag on the scanner job's page.

1. Read the release notes for the new tag. They say whether a setting, a
   parameter, or an environment variable was added or changed.
2. Sign in to Civis as the Robot user. Create a new Python script, as in
   steps 2 and 3 of [Install](#install), and paste the install script from
   the **new** tag.
3. Set `MODE` to `"check"`, `TAG` to the new tag, and `JOB_ID` and
   `SERVICE_ID` to the IDs from the `KEEP IN CIVIS:` line. Leave
   `RESULTS_DATABASE`, `RESULTS_CREDENTIAL`, and `RESULTS_SCHEMA` as `None`.
   `GITHUB_CREDENTIAL`, `LOOKBACK_DAYS`, and `RETENTION_DAYS` set to `None`
   leave the job's values as they are; set one only to change it
   (`"clear"` removes `GITHUB_CREDENTIAL` or `RETENTION_DAYS`).
4. Run the script. Every `CHECK` line should say `pass`. The `CHECK
   scanner_job` and `CHECK service` lines confirm that both objects are this
   tool's and that you own them. The `PLAN` lines list the fields the update
   will change, and the last line says "Nothing changed."
5. Set `MODE` to `"update"` and run it again. Every `RESULT` line should say
   `pass`, including `results_kept`. `run_id_removed` says `yes` if an earlier
   release left a `RUN_ID` parameter, and `no_parameter` if not.
   `other_fields_changed` lists any other field Civis changed, by name only.
6. On the Service's page, click **Update Deployment**, or **Start
   Deployment** if the Service is stopped.
7. Open the app's **Setup** page and check that every line still passes. Line 7
   compares the Service's tag with the scanner job's. It is "review" instead
   of a pass if Civis added the deploying user's GitHub token to the Service
   as `REPO_HTTP_TOKEN` (see
   [What the install script sets](#what-the-install-script-sets)). While
   another install of this tool in the organization uses the same repository
   (for example an old install not yet removed), line 7's Service lookup
   finds several matches: line 7 is "Review", says "2 Services use this
   repository, so the app cannot tell which one it is." (with the number of
   matches), and shows "Service lookup: several_matches (2 matches)" under
   it. Check the Service by hand, or remove the old install (see
   [Remove the tool](#remove-the-tool)).
8. Archive the install script's Python script in Civis.
9. Run a new scan. Earlier scans and checklist statuses are kept.

To add or remove a GitHub token, or to change `LOOKBACK_DAYS` or
`RETENTION_DAYS`, run the same steps with the tag you already have.

### Remove the tool

1. Archive the scanner job: click the file box (Archive) icon at the top right
   of its page. To archive the Service, open the three-dot menu at the top
   right of its page and choose **Archive**, the last item.
2. The CSV reports stay attached to the archived job's runs. Keep the archived
   job shared with Civis admins only.
3. Drop the results schema, for example `DROP SCHEMA results_schema CASCADE;`
   with your schema's name. Run it with the `RESULTS_DB` credential (its
   database user owns the schema), or ask a database admin.
4. If you made a GitHub token, delete the Custom Credential in Civis and revoke
   the token in GitHub.
5. Archive any copy of the install script's Python script that is still in
   Civis.

## Pinned Docker images

The tool is designed to run only on these exact images and to install nothing
at run time. To
confirm an image has not changed, compare its digest with the one below, for
example with `docker pull <image>@<digest>`.

| Part | Image and tag | Digest (linux/amd64) | Pushed |
|---|---|---|---|
| Scanner job | `civisanalytics/datascience-python:9.2.0` | `sha256:8a5a9fb50b93cdcd5280acf170ca9d7b1bcfdd9c9d50e13decdd1a18ef5b2956` | November 9, 2026 |
| Web app Service | `civisanalytics/civis-services-flask:0.1.0` | `sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f` | August 6, 2025 |

The Service image's digest was read from Docker Hub on October 13, 2026, and
the image was pulled by digest and checked again on October 22, 2026. The
scanner image's digest was read from Docker Hub, and the image pulled by
digest and checked, on November 19, 2026. The scanner image moved from `9.1.1`
to `9.2.0` in `v0.1.0-rc13` for its updated CA certificates (Python 3.14.10,
civis-python 2.11.0).

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
