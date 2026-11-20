# First live test

A step-by-step checklist for the first live test of Python Migration Scanner
inside a **test organization's** Civis environment, and for its retest on
`v0.1.0-rc11`, `v0.1.0-rc12`, and `v0.1.0-rc13` ("Retest on v0.1.0-rc11",
"Default tags on v0.1.0-rc12", and "Scanner image on v0.1.0-rc13" under
"Order"). It checks the
README setup and every fact that could not be confirmed from documentation
(docs/PLAN.md marks these "confirm in Phase 7").

## Rules for this test

- **Run it only inside Civis**, in a test organization, with that
  organization's agreement. Nothing in this checklist runs on a local machine
  or calls the Civis API from outside Civis.
- **Record only pass or fail and aggregate counts.** Never record or share
  object names, user names, code, log text, notebook output, or table
  contents. Error types from the scanner's run log (for example
  `error=CivisAPIError status=403`) are fine to record; they hold no content.
- If something you would need to record looks like client content, write
  "content withheld" and describe it in general terms instead.
- **Release tag:** `v0.1.0-rc13` moves the scanner image to
  `datascience-python:9.2.0` (docs/PLAN.md 17.16) and adds
  CV3-CIVIS-TO-CSV-UNQUOTED. `v0.1.0-rc12` follows `v0.1.0-rc11` with the default-tag
  rule (docs/PLAN.md 17.15) and docs from the retest. The retest's job and
  Service move to `v0.1.0-rc12` with the install script's update mode during
  step 6, with the token still attached. `v0.1.0-rc11` follows
  `v0.1.0-rc10` with the post-test build (docs/PLAN.md 17.11 to 17.14): a
  quieter busy cue and "Canceled" in
  the app's text, Python found in shell commands, a timeout on Civis
  requests, a partial inventory when one object type's list fails, resume of
  a stopped scan without editing the job, and an install script that creates
  and updates the scanner job and the Service. The retest uses
  `v0.1.0-rc11` on a fresh install: a new scanner job and a new Service made
  with `install/civis_install.py` (section 1), `LOOKBACK_DAYS` set to `30` by
  the install script, and the first test's results schema, reused (the new
  job's `RESULTS_DB` and `RESULTS_SCHEMA` name it). The Overview then says a
  scan checks scripts that ran or changed in the last 30 days. The job and
  the Service from the first test are moved to `v0.1.0-rc11` once for item
  5.6, then removed. The schema stays. Use `v0.1.0-rc11` wherever the README
  says `<release tag>`, `v0.1.0-rc12` from "Default tags on
  v0.1.0-rc12" on, and `v0.1.0-rc13` from "Scanner image on v0.1.0-rc13"
  on. The first test set up on `v0.1.0-rc5` and ran its
  scans on `v0.1.0-rc10`, with `LOOKBACK_DAYS` `30`. `v0.1.0-rc10` follows
  `v0.1.0-rc9` because scan C on `v0.1.0-rc9`
  succeeded but every notebook was refused with HTTP 403 and live progress
  stopped when Civis's "Cookie expired." page replaced the app frame
  (docs/PLAN.md 17.9): the scanner now asks Civis for a fresh notebook link
  right before each download, live progress stops with one line instead of
  reloading the app frame, scans are labeled by their Civis run ID, and
  every same-tab app link shows a short "Loading" cue.
  `v0.1.0-rc9` follows `v0.1.0-rc8` because scans A and B on
  `v0.1.0-rc8` succeeded but found four problems and two small requests
  (docs/PLAN.md 17.8): notebook files on Amazon S3 FIPS hosts are now
  downloaded, the progress pages reload themselves when Civis's proxy turns a
  status request away, objects saved on the current default image tag or its
  major tag count as on the default image, an archived custom script that a
  workflow runs is read as a custom script, the Scan history link shows the
  "Working" message, and Findings explains how to read the page.
  `v0.1.0-rc8` followed `v0.1.0-rc7` because Scan A on `v0.1.0-rc7`
  finished its inventory in about 19 minutes but ended `tables_failed`, and
  read no git-backed file through Civis (docs/PLAN.md 17.7): whole numbers
  are now written as whole numbers in the result tables, the git file is read
  from the list Civis returns, the Run scan message shows under the button,
  and the Overview says "No completed scan yet" after a canceled scan.
  `v0.1.0-rc7` follows `v0.1.0-rc6` because Scan A on `v0.1.0-rc6` was still
  in Inventory after about 1.5 hours and was canceled (docs/PLAN.md 17.6):
  template copies are inventoried once, only scripts that ran or changed in
  the `LOOKBACK_DAYS` window are read (default 400 days), inventory logs its
  counts and progress, the app reads small results faster and shows progress
  messages, and the app shows each custom script's template.
  `v0.1.0-rc6` followed
  `v0.1.0-rc5` because of fixes from the first scan on `v0.1.0-rc5`
  (docs/PLAN.md 17.5): a scanning user who cannot list deactivated users,
  hidden objects no longer counted, shorter Civis API retries, and the step
  in the FATAL line. `v0.1.0-rc5` followed `v0.1.0-rc4` because of two fixes
  from the first setup on `v0.1.0-rc4` (docs/PLAN.md 17.4): reads that return
  no rows, and the shape of the Civis sharing lists. `v0.1.0-rc4` followed
  `v0.1.0-rc3` because of two safety guards: Run scan starts only this tool's
  scanner job, and the tool refuses a results schema that holds anything
  else.
- **Browser:** Chrome, Edge, or Firefox. Safari ignores the report-only frame
  rule (the optional items 2.2 and 2.3).

## Order

### First test

1. Section 1 (setup) on `v0.1.0-rc5`, with no GitHub token attached to the
   scanner job. Done.
2. Section 2 (frame rule), then the Setup page again (end of item 1.10).
   Then move the scanner job and the Service to `v0.1.0-rc10` (tag only), check
   that the scanner job's `LOOKBACK_DAYS` is `30`, and check that the Setup
   page's lines 2 and 4 still pass. Line 7 passes too, or is "review" instead
   of a pass when Civis added the deploying user's GitHub token to the Service
   as `REPO_HTTP_TOKEN` (item 1.10).
3. Section 3 (the first scan, with no GitHub token) and the first-scan part
   of item 4.8.
4. Items 4.1 to 4.3, 4.6, 4.7, and 5.1.
5. Attach the GitHub token (item 4.8), then item 5.2 (the second scan), the
   second-scan part of item 4.8, and item 1.4.
6. Items 4.4, 4.5, and 5.3 to 5.5.
7. Item 5.7: build the final support summary, copy it, and send it with these
   results.

Item 5.6 was not part of the first test. Its step 1 is part of the retest and
moves the first test's job and Service from `v0.1.0-rc10` to `v0.1.0-rc11`;
its step 2 removes them at the end of the retest.

### Retest on v0.1.0-rc11

1. Item 5.6 step 1 on the first test's job and Service: README "Update to a
   new release" with the install script's update mode, from `v0.1.0-rc10` to
   `v0.1.0-rc11`. Record the result lines without IDs, `run_id_removed`, and
   Setup page lines 2, 4, and 7. Skip the new scan at the end of that README
   section, so the newest scan in the results schema stays a finished scan
   from the first test. Then stop that Service's deployment.
2. Section 1 with a fresh install: the install script in check mode, then
   install mode, with the first test's results schema, and items 1.2 to 1.10,
   including the Setup page line for the resource profile. On the reused
   schema, items 1.8 and 1.9 expect the five tables already there (no "Set up
   results storage" button, Setup line 9 passes), and Scan history on the new
   Service lists the first test's scans.
3. Section 3 on the new install: items 3.1, 3.3 (the Inventory progress reads
   "Inventory: checking N of M listed objects", then "N Python objects
   found", docs/PLAN.md 17.12), 3.4, and 3.7 (the busy cue as changed in
   docs/PLAN.md 17.11). The first scan's log has
   `RESUME decision=new reason=last_scan_finished`, because the newest scan in
   the reused schema is a finished scan from the first test.
4. Item 5.4 (resume from the app, as docs/PLAN.md 17.13 rewrote it).
5. Item 4.6 (the container script now has findings from `python -c`,
   docs/PLAN.md 17.12), Findings at 390 px wide (item 3.7), and item 5.3's
   CSV link for a scan left with only a "running" row, if one occurs.
6. The GitHub test on the new install. A fine-grained GitHub token, read-only
   and limited to one private test repository that holds synthetic files
   only, is stored in a Custom credential made while signed in as the Robot
   user. Test objects that use that repository are made first: a git-backed
   Python script on a branch, one on a tag, and a container script with the
   repository URL. Attach the credential with the install script's update
   mode (`GITHUB_CREDENTIAL` set to the credential's ID), never by saving the
   job's page. Record the result lines without IDs, and Setup page lines 2
   (the with-token resource profile) and 4. Run one scan, then items 1.1 (the
   GitHub step), 1.3 d, 1.4 (peak memory and CPU, and the GitHub counts),
   3.5, and 4.8 step 4 for that scan. GitHub gives every fine-grained token
   read access to all public repositories, whatever repositories it is
   limited to, so with the token attached the scan also downloads public
   repositories that Civis objects use, and those objects are scanned from
   GitHub. The retest saw this: the scan with the token read public
   repositories as well as the private test repository. Git-backed Python
   scripts in other private repositories get a GitHub 404 and are read
   through Civis. Then "Default tags on v0.1.0-rc12" below runs before the
   token is removed. Then
   run update mode with `GITHUB_CREDENTIAL` `"clear"`, check that Setup line
   2 is back on the no-token profile, delete the Custom credential, and
   revoke the token.
7. Item 5.6 step 2 for the first test's job and Service: README "Remove the
   tool" steps 1 and 2. Step 3 (drop the results schema) does not apply,
   because the new install uses that schema.
8. Item 5.7: the support summary for the retest.

### Default tags on v0.1.0-rc12

Run this after step 6's scan with the token and before the token is removed
(docs/PLAN.md 17.15).

1. README "Update to a new release" from `v0.1.0-rc11` to `v0.1.0-rc12` on
   the retest's job and Service, check mode first, with `GITHUB_CREDENTIAL`
   `None` so the token stays attached. Record the result lines without IDs.
2. One scan with the token. On Inventory, the GitHub test container script
   (made with no tag after Civis moved its default) shows Default image Yes,
   and the first test's container script (on `9.1`) still shows Yes. On
   Findings, the GitHub test container's rows include
   `PY315-PINNED-PACKAGE-INSTALL` (Advisory, the impact that rule sets for
   itself), and its rows from other rules are Affected. Count the notebooks
   whose tag is `5.2` or newer, and whether each shows Default image Yes.
3. Then "Scanner image on v0.1.0-rc13" below.

### Scanner image on v0.1.0-rc13

Run this after the rc12 scan with the token and before the token is removed
(docs/PLAN.md 17.16).

1. README "Update to a new release" from `v0.1.0-rc12` to `v0.1.0-rc13` on
   the retest's job and Service, check mode first, with `GITHUB_CREDENTIAL`
   `None` so the token stays attached. Expected: every `CHECK` line passes,
   the job's plan line starts `PLAN scanner_job: update
   fields=docker_image_tag,repo_ref profile=with_token`, `PLAN service: update
   fields=git_repo_ref`, and in update mode every `RESULT` line passes,
   `image_set` included. Record the result lines without IDs.
2. On the Service's page in Civis, click Update Deployment, or Start
   Deployment if the Service is stopped, then press F5. Setup line 2 passes
   with the token's profile and tag `9.2.0`.
3. One scan with the token. The run log has `REQUEST_TIMEOUT installed=yes
   connect=15 read=120`, and the final `RUN` line has `status=succeeded`.
   Record its counters and the minutes. The GitHub test container script
   still shows Default image Yes and the same rule IDs as the rc12 scan.
   CV3-CIVIS-TO-CSV-UNQUOTED has no live test object; unit tests cover it.
4. The rest of step 6 (update mode with `GITHUB_CREDENTIAL` `"clear"`, delete
   the Custom credential, and revoke the token), then step 8 (item 5.7) on
   this scan. If this scan cannot run before the token expires, run the rest
   of step 6 and step 8 on the rc12 scan instead, then update to
   `v0.1.0-rc13` and run one scan without the token, checking (2) with the
   no-token profile and (3) without the GitHub checks.

## Prepare the test organization

Before item 1.1, make sure the test organization has at least one of each of
these, so every check below has something to find. Each Python object should
contain one known pattern, such as `client.users.list_me()`. In a container
script, the pattern can be in Python run from its docker command, for example
`python -c "import civis; civis.APIClient().users.list_me()"` (docs/PLAN.md
17.12).

- a Python script, a container script, a custom script (from a Python
  template), a notebook, a Service with a git repository, and a workflow with
  an inline Python task
- a dbt script with a blank image tag, if the organization has dbt
- a workflow (not archived) that runs a Python script, a container script, and
  a custom script, after which those three scripts are **archived** (item 4.6)
- if the organization uses git: a git-backed Python script on a branch, a
  git-backed Python script on a tag or a commit, and a git-backed workflow
  (item 4.8)

Record: how many of each type you prepared.

## 1. Setup, following the README exactly

Follow the README word for word. If a step does not match what you see,
finish the step the way the interface allows and record the difference.

### 1.1 Before you start

- **Steps:** Follow README "Before you start": confirm the Robot user (or a
  dedicated service account) and choose the results schema name. Find the
  results database's name or ID and the ID of the Robot user's database
  credential. Do not create the schema: the app does that in item 1.9. Do not
  set a GitHub credential yet: the first scan runs without one on purpose
  (item 4.8). The GitHub Custom Credential can be created now or at item 4.8.
- **Expected:** Each step works as written.
- **Record:** pass or fail per step; Robot user or dedicated service account.

### 1.2 Install script, check mode

- **Steps:** Follow README "Install", steps 1 to 5. Set `GITHUB_CREDENTIAL`
  to `None`, `LOOKBACK_DAYS` to `30` (the value the first live test used), and
  `RETENTION_DAYS` to `None`.
- **Expected:** Every `CHECK` line says `pass`: `settings` (with
  `mode=check plan=install`), `running_user`, `results_credential`,
  `results_database`, `results_schema`, and `github_credential` (`pass (none
  set)`). Two `PLAN` lines name the scanner job's and the Service's fields,
  with `profile=without_token`. The last line starts with "Nothing changed."
  Civis shows no new scanner job or Service.
- **Record:** pass or fail for each `CHECK` line, by its label; whether
  `RESULTS_DATABASE` was a name or an ID. If a `CHECK` line failed, its
  label and the `reason=` or `error=` text (fixed labels and error types
  only).

### 1.3 Labels to confirm

- **Steps:** For each field the README marks "(label to confirm)", find it in
  the web interface.

  | # | README text | What to find |
  |---|---|---|
  | a | removed | The container script page is no longer used for setup; the first live test found no git credential field (docs/PLAN.md 17.10). |
  | b | removed | The install script sets Disk Space. |
  | c | removed | The install script sets the parameters. |
  | d | Share the credential with the Robot user | How a Custom Credential is shared with, or created as, another user. |
  | e | removed | The first live test confirmed how a Service is archived (docs/PLAN.md 17.10). |
  | f | Python (New Script) | The script type to choose for the install script. |
  | g | The credential list shows each credential's ID | Where Civis shows a credential's ID. |
  | h | Sharing settings | The control on a script's and a Service's page that opens its sharing. |

- **Expected:** The labels match the README.
- **Record:** for each row that is not removed, "matches" or the label the
  interface shows. Civis interface labels are not client data.

### 1.4 Scanner resources

- **Steps:** After item 1.5, read the scanner job's **CPU (m)**, **Memory
  (MB)**, and **Disk Space (GB)** on its page (do not save the page). After
  item 4.8 adds the token with the install script's update, read them again.
  After the first scan (item 3.4) and the second scan (item 5.2), check each
  run for memory or disk errors, and read its peak memory and CPU if Civis
  shows them. Read the GitHub counts on the last line of the run log of the
  first scan with the token attached (the second scan, item 4.8), or from the
  support summary's `run_line` for that scan (item 5.7). The first scan has no
  token, so its GitHub counts are zero.
  On the retest, also open the Setup page after each update: with the token,
  and again after the update with `GITHUB_CREDENTIAL` `"clear"`.
- **Expected:** Without the token: `500`, `2048`, and `1`. With it: `1000`,
  `4096`, and `4`. Setup line 2 passes and shows "Resource profile:
  with_token (1000 CPU, 4096 MB, 4 GB)" with the token, and "Resource
  profile: without_token (500 CPU, 2048 MB, 1 GB)" again after the clear. No
  out-of-memory or disk-full failure. Peak memory stays
  under 2048 MB without the token and under 4096 MB with it.
  `github_cache_peak_bytes` is at most 1073741824 (the 1 GB cache cap).
- **Record:** pass or fail; the three values for each profile; peak memory
  and CPU per scan if Civis shows them; `github_tarball_requests`,
  `github_cache_peak_bytes`, and `github_cache_evictions`.

### 1.5 Install script, install mode

- **Steps:** Follow README "Install", steps 6 and 7.
- **Expected:** `WRITE scanner_job: created`, `WRITE scanner_job: patched`,
  `WRITE service: created`, and `WRITE service: patched`, in that order. Every
  `RESULT` line says `pass`: for the scanner job `repository_set`,
  `tag_set`, `image_set`, `resources_set` (`profile=without_token`), and
  `params` (`names=RESULTS_DB,RESULTS_SCHEMA,LOOKBACK_DAYS`); for the Service
  `repository_set`, `tag_set`, `image_set`, `env_set`, `on_demand`, and
  `credentials_none`. `RESULT scanner_job results_db_shape` is `object`. One
  `KEEP IN CIVIS:` line, then the `NEXT` lines. Civis lists one new scanner
  job and one new Service, with the names in README "What the install script
  sets", and the scanner job's page shows **Branch** as the release tag and
  **Repository** blank.
- **Record:** pass or fail for each `RESULT` line, by its label; the field
  names on each `other_fields_changed` line (or `none`); the
  `results_db_shape` value; whether the `KEEP IN CIVIS:` line was printed (yes
  or no; never record the IDs); minutes the script ran. If a step failed, the
  `WRITE` line's error type and whether the `RECOVER` lines were clear.

### 1.6 Start Deployment, and nothing installed at start

- **Steps:** Follow README "Install", step 9. Then open the Service's
  deployment log.
- **Expected:** The deployment starts and stays running. Civis's health check
  uses `/healthz`. The log shows a gunicorn start ("Listening at:
  http://0.0.0.0:3838", "Using worker: gthread", and two "Booting worker"
  lines) and no "pip", "Collecting", or "Downloading" lines.
- **Record:** pass or fail; minutes from **Start Deployment** to running.

### 1.7 Sharing

- **Steps:** Follow README "Install", step 8, for the scanner job and the
  Service, before item 1.6.
- **Expected:** Only Civis admins (and the Robot user) have access. The
  default group was removed from both objects. Sharing the scanner job did not
  change its repository: the Setup page's line 2 passes in item 1.10.
- **Record:** pass or fail for each object. The user and group counts come
  from the Setup page's line 6 (item 1.10) and the support summary.

### 1.8 Results schema check before setup

- **Steps:** Open the app before clicking anything. On the retest, which
  reuses the first test's schema, also open Scan history and click the
  Download CSV link of one of the first test's scans.
- **Expected:** "The results schema does not exist yet", a **Set up results
  storage** button, and **Run scan** off with one line pointing to the button.
  This confirms the `pg_catalog.pg_namespace` check on Redshift for a
  missing schema (PLAN.md 14.11 and 17.2.21). On the retest: no button, the
  Overview shows the first test's newest finished scan with **Run scan** on
  and no resume line, and Scan history lists the first test's scans by their
  "Civis run <ID>" labels. Their Download CSV links open the app's plain
  "not found" page: those runs belong to the first test's job, and the app
  reads run outputs only through the new job.
- **Record:** pass or fail; the heading shown if different.

### 1.9 Set up results storage

- **Steps:** Follow README "Set up results storage and check the setup",
  steps 2 and 3. One of two outcomes happens:
  - **a. Created by the app.** The page says "Results storage is set up."
  - **b. Refused.** The page says the database user cannot create schemas
    (step: schema), shows an error type, and shows one statement of the form
    `CREATE SCHEMA <schema> AUTHORIZATION <database user>;`. A database admin
    runs that statement as shown. Then click **Set up results storage**
    again: the page says the schema already existed and its five tables are
    ready.
  If a database admin created the schema earlier without `AUTHORIZATION`, the
  app may instead say the schema already existed but its tables could not be
  created (step: tables) and show
  `ALTER SCHEMA <schema> OWNER TO <database user>;`. Record that as outcome c:
  the admin runs it, then click the button again. On the retest, which reuses
  the first test's schema, there is no button: skip steps 2 and 3, and open
  the Setup page.
- **Expected:** After any outcome, the Overview page shows "No scan yet"
  and **Run scan** is on. On the retest, Setup line 9 passes (the schema
  holds only this tool's five tables) and the support summary shows
  `results_schema_dedicated: yes`. This confirms the check finds the schema on
  Redshift. Nothing else changes in Civis.
- **Record:** whether the first click reported that the schema already existed
  (expected: no, because the schema is new); outcome a, b, or c; for b and c,
  the error type shown, whether
  the statement showed a database user name or the `<database user>`
  placeholder (yes or no, never the name), and pass or fail for the admin's
  statement and for the second click; if a table step failed, the step and error type. Do
  not record the schema name or the user name.

### 1.10 Setup page

- **Steps:** Follow README "Set up results storage and check the setup",
  step 4: open the **Setup** page. Do not copy or record the user and group
  names that line 6 lists. After item 2.1, open the Setup page again. Then
  follow step 6: archive the install script's Python script.
- **Expected:** Lines 1 to 5, 7, and 9 pass. Line 2 shows "Resource profile:
  without_token (500 CPU, 2048 MB, 1 GB)" under it. Line 4 shows the
  `RESULTS_DB` value shape `object`, the same as item 1.5. Line 9 says the
  schema holds only this tool's five tables (it is dedicated), and the support
  summary shows `results_schema_dedicated: yes`. Line 7 is "review" instead
  of a pass if Civis added the deploying user's GitHub token to the Service as
  `REPO_HTTP_TOKEN`. Line 6 (sharing) is "review". Line 8 (frame rule) is a
  pass (a warning only while `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` is `true`,
  item 2.2). Line 10 (latest scan) is "review" before the first scan. On a
  results schema used by an earlier install, the Overview shows that
  install's last scan, while line 10 says "No scan has run yet." until the
  new scanner job's first scan.
- **Record:** the result of each line (1 to 10), first visit and after item
  2.1; the resource profile shown under line 2; the `RESULTS_DB` value shape
  shown under line 4; the Service lookup method shown under line 7; the user
  and group counts in line 6; whether line 7 reports the deploying user's
  GitHub token (yes or no; the summary's `repo_http_token_present`); whether
  line 9 shows the schema is dedicated (yes or no). For any line that is not
  as expected, its reason in general terms.

## 2. Frame rule (PLAN.md 11.3)

### 2.1 Enforced rule

- **Steps:** With `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` unset or `false` (the
  default), open the Service's page in Civis, where the app shows in the
  preview pane, open the browser's developer console, and reload the page.
- **Expected:** The app shows, and the console has no Content Security Policy
  errors from the app. The Setup page's line 8 is a pass. This follows README
  "Install", step 10.
- **Record:** pass or fail; the number of Content Security Policy errors from
  the app. If there is a `frame-ancestors` error, record the parent address it
  names. Civis host names are fine to record; replace any part that names the
  organization with `<org>`.

### 2.2 Report-only (optional)

Only for troubleshooting, or if item 2.1 failed.

- **Steps:** Set `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` to `true`, click **Update
  Deployment**, and reload the Service's page with the console open. Then set
  it back to `false`, click **Update Deployment**, and open the Setup page.
- **Expected:** While it is `true`, the app shows, the console has no "[Report
  Only]" warning about `frame-ancestors` from the app, and the Setup page's
  line 8 is a warning. After it is `false` again, line 8 is a pass.
- **Record:** done or skipped; the number of such warnings, and the parent
  address if there is one.

### 2.3 Safari (optional)

- **Steps:** Repeat 2.2 in Safari.
- **Expected:** Safari logs that `frame-ancestors` "is ignored when delivered
  in a report-only policy." This is a known Safari limit, not a failure.
- **Record:** seen or not seen.

## 3. First scan from the app

### 3.1 The Service starts the scanner job

- **Steps:** On the Overview page, click **Run scan**. Then click it again, or
  reload and look at the button.
- **Expected:** A run starts on the scanner job (its Run History shows it).
  The button changes to "Scan running" without a reload, and a second scan
  cannot start.
- **Record:** pass or fail; pass or fail for the second-scan guard.

### 3.2 The scan runs as the Robot user

- **Steps:**
  1. Main check: on the app's Scan history page, read the line "Started ...
     as <user>" for this scan. The scanner fills it in from Civis
     (`users.get_me`), so it shows the user the scan actually ran as. Read it
     before another scan starts, because the line shows the newest scan.
  2. Second check: on the scanner job's run in Civis, check who started it.
- **Expected:** Both show the Robot user (the Service's owner).
- **Record:** yes or no for each check. Do not record the user name.

### 3.3 Progress

- **Steps:** Open the Scan history page when the scan starts and watch it.
  When the stop line shows, reload the whole Civis page with the browser's
  reload button (F5 on Windows), not only the app's frame, and watch again.
- **Expected:** Inventory with a moving count (the headline reads
  "Inventory: checking N of M listed objects" and "1. Inventory" shows "N of
  M listed objects checked" while it checks the scripts in the window, the
  notebooks, the Services, and the workflows, after a few minutes of
  listing). Then "1. Inventory" shows "N Python objects found" and Code and
  log scan has a moving count ("Scanning code and logs: N of M Python
  objects"), then Save results. The Python objects found can be fewer or
  more than the listed objects checked: Services and workflows with no
  Python and custom scripts on other templates drop out, and archived jobs
  that workflows run are added (docs/PLAN.md 17.12). The Inventory count does
  not show during the first minutes of listing, and the first stop line can
  come before it does.
  The card updates on its own for the first few minutes after the Civis page
  loads. When Civis's session for the page expires (its proxy cookie lasts
  about 5 to 6 minutes), the card stops updating and shows "Progress stopped
  updating because the Civis session for this page expired. To see the
  latest progress, reload the whole Civis page with the browser's reload
  button (F5 on Windows). Reloading only this frame does not help." The app
  does not replace its frame with Civis's "Cookie expired." page
  (docs/PLAN.md 17.9). A reload of the whole Civis page brings current counts,
  and the card updates on its own again for a few minutes. On the Overview,
  the Run scan button comes back when the scan ends, or the same stop line
  shows under it.
- **Record:** pass or fail for each: the inventory count moved, the code scan
  count moved, the Overview button came back (or its stop line showed). Also
  yes or no: the card updated on its own at first; the stop line appeared;
  the frame showed "Cookie expired."; a reload of the whole Civis page
  brought current counts.

### 3.4 Scan results

- **Steps:** When the scan finishes, press F5 (reload the whole Civis page),
  then build the support summary (item 5.7) for this scan; without the reload,
  the frame can show "Cookie expired.". Its `run_line`, `objects_by_type`, and `findings` lines give the
  counts below, so you do not need to copy them from the run log. Then read
  the counts on the Overview page.
- **Expected:** Status `succeeded`; Overview counts match the summary.
- **Record:** status; minutes from start to finish; `lookback_days`;
  `objects_total`, `objects_scanned`, `api_calls`, `civis_git_reads`,
  `civis_git_failures`, `request_timeouts`; the summary's `inventory` lines
  (listed, kept, outside_window, template_copies, and not_python per type)
  and `list_failed` lines (`none`, or the rows read for each type whose list
  stopped partway; the Overview then shows a warning for that type,
  docs/PLAN.md 17.12); objects scanned,
  partly scanned, and not scanned; affected and advisory counts; total
  findings; the Overview's "templates, used by custom scripts, have findings"
  line.

### 3.5 Calls per object (PLAN.md section 5)

- **Steps:** Divide `api_calls` by `objects_total`. `api_calls` counts every
  Civis API request the scanner sent through its request budget. Table writes
  and the CSV upload are not counted (a few calls per checkpoint).
- **Expected:** About 3 to 4 per object (section 5 estimates 3, plus about 1
  for each Python script, PLAN.md 10.4), plus 1 for each Python notebook
  (its fresh file link, `notebook_links`; PLAN.md 17.9), plus 1 for each active workflow
  (`workflows.get_git`, including workflows that only run other jobs and so
  are not counted in `objects_total`), plus up to 2 for each git-backed Python
  script or workflow read through Civis (`civis_git_reads` plus
  `civis_git_failures`; PLAN.md 17.1). At the default budget, about 20 minutes
  per 500 objects.
- **Record:** calls per object; minutes per 100 objects;
  `github_tarball_requests` and `github_cache_evictions` (GitHub requests do
  not count against the Civis limit, but evictions mean repeat downloads).

### 3.6 The run log holds only structured lines

- **Steps:** The support summary's `log_errors` and `log_other_lines` lines count
  these for you. Scanner lines start with `RUN`, `PHASE`, `PROGRESS`,
  `CHECKPOINT`, `FINDINGS`, `OBJECT_ERROR`, `CIVIS_GIT_FAILED`,
  `PARSE_ERROR`, `TABLE_WRITE_FAILED`, `OUTPUT_FAILED`, `FATAL`, `USERS`,
  `INVENTORY`, `LIST_FAILED`, or `REQUEST_TIMEOUT` (one line near the top:
  `REQUEST_TIMEOUT installed=yes connect=15 read=120`).
  If `log_other_lines` is not 0, scan the run log without copying it to see
  what kind of lines they are. The summary's `users_list` line says which user
  list the scan used (`all`, or `active` or `none` with an HTTP status or
  `reason=timeout`, docs/PLAN.md 17.12.24), and
  `fatal_step` says where a scan stopped, if it did.
- **Expected:** No other lines, apart from Civis's own container messages. No
  "pip" lines.
- **Record:** `log_other_lines` and what kind of lines they are in general
  terms, and the count of each error type in `OBJECT_ERROR`,
  `CIVIS_GIT_FAILED`, `PARSE_ERROR`, and `LIST_FAILED` lines (for example
  `CivisAPIError status=403: 2`), from the summary.

### 3.7 First page loads after a scan

- **Steps:** Right after the scan, open Overview, Inventory, and Findings for
  the first time and time each. Each of the Service's two worker processes
  keeps its own cache, so a page may be slow once per worker.
- **Expected:** Each page loads. The first load reads the result tables, so it
  can take longer than later loads. The Service stays healthy while it loads.
- **Busy cue:** while you click through, check that a nav link, an Apply
  filters button, and a Next page link turn gray at once, with the progress
  cursor and the page content dimmed, until the next page arrives
  (docs/PLAN.md 17.9 and 17.11). A Download CSV link shows no cue.
- **Phone width:** in the browser's developer tools, set the page to 390 px
  wide (the device toolbar) and open Findings. Expected: the page does not
  scroll sideways as a whole; only the table scrolls inside its box
  (docs/PLAN.md 17.11).
- **Record:** seconds per page; the slowest first page load in seconds; whether
  any page load failed or timed out (yes or no, with the error code if shown);
  the busy cue and phone width lines in the results template.

## 4. Facts to confirm (PLAN.md "confirm in Phase 7")

### 4.1 Links to Civis objects (PLAN.md 10.14 and 13.5)

- **Steps:** On the Inventory page, click one object's link for each type
  except container scripts (already confirmed): Python script
  (`/spa/#/scripts/python3/<id>`), custom script (`/spa/#/scripts/custom/<id>`),
  notebook (`/spa/#/notebooks/<id>`), Service (`/spa/#/services/<id>`),
  workflow (`/spa/#/workflows/<id>`), and dbt script (`/spa/#/scripts/dbt/<id>`).
- **Expected:** Each link opens that object's page in Civis.
- **Record:** pass, fail, or "none to test" per type. If one fails, record the
  path Civis uses for that type, with the ID replaced by `<id>`.

### 4.2 Notebook downloads (PLAN.md 12.4 and 17.9)

- **Steps:** On the Inventory page, filter to notebooks. Count notebooks that
  were scanned, notebooks whose reason is "The notebook file could not be
  downloaded. The link points to a host the scanner is not allowed to
  contact.", notebooks whose reason is "The notebook file could not be
  downloaded. The request was refused (HTTP 403).", and notebooks with any
  other reason. Read `notebook_links` and `notebook_link_retries` from the
  support summary's `run_line`.
- **Expected:** Python notebooks with a file are scanned; none refused for
  their host or with HTTP 403. `notebook_links` is about the number of Python
  notebooks (one fresh link each, right before its download), and
  `notebook_link_retries` is 0 or small (a second link after a refused
  download). On `v0.1.0-rc8`, all 58 notebooks were refused for the FIPS
  host form, which `v0.1.0-rc9` accepts. On `v0.1.0-rc9`, all 58 were refused
  with HTTP 403, because the link read during Inventory had expired 5 minutes
  after Civis issued it, long before the download.
- **Record:** the four counts; `notebook_links` and `notebook_link_retries`.
  If any were refused for their host, open one notebook in Civis with the
  browser's Network tab open, find the request that downloads the `.ipynb`
  file, and record only the shape of its host with the account parts replaced
  (for example `<bucket>.s3.<region>.amazonaws.com` or
  `<bucket>.s3-fips.<region>.amazonaws.com`). For any other reason, record it
  in general terms (it holds no content).

### 4.3 Missing schema after setup (PLAN.md 14.11 and 17.2)

- **Steps:** Item 1.8 checked a missing schema before setup. Now, signed in as
  the Robot user, set the scanner job's `RESULTS_SCHEMA` parameter to a schema
  that does not exist (for example `pmsc_no_such_schema`) and save. Open the
  app's Setup page (it reads the job's settings at once), then the Overview
  page. **Do not click Set up results storage**, which would create that
  schema. Then set the parameter back, save, and open the Setup page again.
  The install script keeps `RESULTS_SCHEMA` as it is, so this item edits the
  job's page by hand. A save of that page can clear the job's repository, so
  afterward run the install script's update with the same tag (README "Update
  to a new release") and check that Setup lines 1 and 2 pass.
- **Expected:** The Setup page's line 9 fails with "The results schema does
  not exist." The Overview page says "The results schema does not exist yet",
  with **Run scan** off. The other worker process may show the old schema for
  up to 5 minutes. After setting it back, line 9 passes and the results show
  again.
- **Record:** pass or fail for each state.

### 4.4 `DELETE ... USING` on Redshift (PLAN.md 11.2)

- **Steps:** After at least two scans, pick the older run's ID (the
  `run_id=` value in the `RUN run_id=... status=running` line near the top of
  that run's log, or from the support summary's select list, which shows each
  scan as "Civis run <ID> (<run ID>)"). In a Civis query
  against the results database, count that run's rows in each of the five
  tables, run the manual deletion SQL from SECURITY.md for that run ID, and
  count again. Run the query with the `RESULTS_DB` credential, since its
  database user owns the schema.
- **Expected:** All five statements succeed. The run's rows are gone from
  `findings`, `coverage`, `objects`, and `scan_runs`. In `checklist_status`, only
  rows that have a newer row for the same object and rule are deleted.
- **Record:** pass or fail per statement; row counts before and after per table.

### 4.5 `RETENTION_DAYS` (PLAN.md 11.2)

- **Steps:** Do item 5.1 first. On a later day than an earlier scan, run the
  install script with `MODE` `"update"`, the same tag, and `RETENTION_DAYS`
  set to `1` (README "Update to a new release"), and click **Run scan** in the
  app. Read `runs_in_scan_runs` from the support summary before and after; it
  replaces a manual count of runs in `scan_runs`. Then run the update again
  with `RETENTION_DAYS` set to `"clear"`.
- **Expected:** Scans started more than one day earlier are deleted from all
  five tables; the newest scan is kept; checklist statuses set in item 5.1 are
  still shown in the app.
- **Record:** pass or fail; number of runs before and after.

### 4.6 Archived jobs in active workflows (PLAN.md 10.7)

- **Steps:** Use the workflow prepared above, which runs three archived scripts
  (Python, container, custom). Find those scripts on the Inventory page.
- **Expected:** Each appears as archived, lists the workflow, and has its
  finding. The archived custom script appears as a custom script (not as a
  Python script) with its template's finding (docs/PLAN.md 17.8). This
  confirms the job type matching. The archived container script's finding
  comes from the Python in its docker command (`python -c "..."`), with the
  label "docker command" and the line where that Python sits (docs/PLAN.md
  17.12). On `v0.1.0-rc10`, both test container scripts had 0 findings,
  because the docker command was read as shell only.
- **Record:** pass or fail per script type.

### 4.7 dbt default image (PLAN.md 13.4)

- **Steps:** Run the dbt script with a blank image tag once. Check which image
  it ran on (its run log or settings).
- **Expected:** A Civis default Python image, so the scanner is right to treat
  a blank tag as the default image.
- **Record:** the image name and tag (public Civis image names only), or "not
  shown". If not shown, ask Civis support.

### 4.8 Git-backed files read through Civis (PLAN.md 17.1)

- **Steps:**
  1. The first scan (item 3.1) runs with no GitHub token, on purpose. After
     it, read `civis_git_reads` and `civis_git_failures` from the support
     summary's `run_line`, and the `CIVIS_GIT_FAILED` counts by error type and
     status from its `log_errors` lines (for example
     `CIVIS_GIT_FAILED CivisAPIError status=403: 3`).
  2. Read the summary's `git_backed_objects` lines for the scan. They give
     `git_backed`, `read_through_civis`, and `civis_read_failed` per object
     type, computed the same way as the query below. **Once, for the first
     scan only,** also run the query as a manual cross-check, in a Civis
     query against the results database with the `RESULTS_DB` credential,
     with the scan's run ID (the `run_id=` value on its `RUN` lines, or the
     summary's `scan:` line) and your schema's name, and confirm it matches
     the summary:

     ```sql
     SELECT object_type,
            COUNT(*) AS git_backed,
            SUM(CASE WHEN scan_note LIKE '%own file was checked, read through Civis%' THEN 1 ELSE 0 END) AS read_through_civis,
            SUM(CASE WHEN scan_note LIKE '%could not be read through Civis%' THEN 1 ELSE 0 END) AS civis_read_failed
     FROM results_schema.objects
     WHERE run_id = '<run_id>' AND git_repo IS NOT NULL
       AND object_type IN ('python_script', 'workflow')
     GROUP BY object_type;
     ```

     For Python scripts, `read_through_civis` is the number read through
     Civis. A workflow read through Civis has no note, or only a note that its
     tag was read at the branch's newest commit, so for workflows the number
     read is `git_backed` minus `civis_read_failed`. Workflows that
     only run other jobs have no row here; they are in the `RUN` line counts.
  3. Create the GitHub Custom Credential if you have not (README "Before you
     start", item 4). Then, signed in as the Robot user, run the install
     script with `MODE` `"update"`, the same tag, and `GITHUB_CREDENTIAL` set
     to the credential's ID (README "Update to a new release"). Do not save
     the scanner job's page. Read the job's resources for item 1.4.
  4. The second scan (item 5.2) runs with the token. Repeat steps 1 and 2 for
     it, from the summary only. For Python scripts, the number read from
     GitHub is `git_backed` minus `read_through_civis` minus
     `civis_read_failed`. On the retest, the scan with the token is the one
     scan of "Retest on v0.1.0-rc11" step 6. The token is limited to the
     private test repository, but GitHub gives every fine-grained token read
     access to all public repositories, whatever repositories it is limited
     to, so with the token attached the scan also downloads public
     repositories that Civis objects use, and those objects are scanned from
     GitHub. The retest saw this: the scan with the token read public
     repositories as well as the private test repository. Expected: the test
     objects (the Python script on a branch, the one on a tag, and the
     container script) and objects in public repositories are read from
     GitHub, and the container script is scanned rather than partial;
     git-backed Python scripts in other private repositories get a GitHub 404
     and are read through Civis.
- **Expected:** Without the token, git-backed Python scripts and workflows are
  read through Civis, with few or no failures. From `v0.1.0-rc8`, an item on a
  branch is read through the list Civis returns for its file. A workflow whose
  saved git reference is a commit Civis cannot find, or whose branch has no
  commits, still falls back to its saved definition with a note
  (docs/PLAN.md 17.7). With the token, most
  git-backed Python scripts are read from GitHub, and only those GitHub cannot
  read are read through Civis. Workflows are read through Civis in both scans.
  The manual query matches the summary. If every read fails with the same
  error, record it: it may show which GitHub connection Civis uses for these
  endpoints, or that the Robot user cannot call them.
- **Record:** for each scan: git-backed Python scripts and git-backed workflows
  (`git_backed`); how many of each were read through Civis;
  `civis_git_reads` and `civis_git_failures`; the error type and status of each
  kind of `CIVIS_GIT_FAILED` line, with counts. For the second scan, also how
  many git-backed Python scripts were read from GitHub. For the first scan,
  whether the manual query matched the summary (yes or no).

## 5. App functions

### 5.1 Checklist save with the Civis-Username header

- **Steps:** On the Findings page, change two statuses and click **Save
  changes**. Reload. Read `checklist_rows_by_people` from the support summary; it
  replaces a manual count of `checklist_status` rows where `changed_by` is not
  `scanner`.
- **Expected:** The dropdowns are enabled (the app sees your user name), the
  page says "Saved 2 status changes.", the statuses stay after reload, and the
  count is 2.
- **Record:** pass or fail; the count.

### 5.2 A new scan keeps checklist statuses

- **Steps:** Run another scan from the app.
- **Expected:** The two statuses from 5.1 are unchanged.
- **Record:** pass or fail.

### 5.3 Download CSV

- **Steps:** Click **Download CSV**. Open the file in a spreadsheet program.
  If a scan is left with only a "running" row after its Civis run ended (for
  example the canceled run in item 5.4 step 1, before it is resumed), also
  click its CSV link on the Scan history page.
- **Expected:** The file downloads and opens with accents shown correctly. It
  has one row per finding plus one row for each object with no findings.
  Delete the file when done. The left-behind scan's link downloads its CSV if
  the scanner saved one before it stopped, and otherwise opens "This scan has
  no CSV report. It stopped before saving one." (docs/PLAN.md 17.11).
- **Record:** pass or fail; the row count.

### 5.4 Resume a stopped scan

Nobody edits the scanner job in this item: the scanner decides by itself
whether to resume (README "Resuming a stopped scan", docs/PLAN.md 17.13).

- **Steps:**
  1. Click **Run scan** in the app. When the Scan history page shows Code and
     log scan partway done, cancel the run: open the job's Run Log and click
     the "x" next to the spinner. Note the scan's label in Scan history
     ("Civis run <ID>").
  2. Reload the whole Civis page (F5). On the Overview, read the line under
     **Run scan**: "The last scan stopped before it finished. Run scan picks
     it up where it stopped, ... After <time>, Run scan starts a new scan."
  3. Click **Run scan**. On the Scan history page, read the progress card's
     first line.
  4. When the run finishes, read its run log: the `RESUME` line near the top
     and the final `RUN` line. Then read Scan history.
  5. Click **Run scan** again and read the `RESUME` line near the top of that
     run's log. Let it finish, or cancel it if time is short.
- **Expected:** Step 2 shows the line, with a time about 24 hours after the
  cancel. Step 3's first line starts with "Resuming Civis run <ID>." with the
  ID from step 1. The resumed run's log has
  `RESUME decision=resume run_id=... resumes=1` right before its
  `RUN ... status=running` line, with the run ID from the canceled run's own
  `RUN ... status=running` line. It succeeds. The scanner saves its progress
  every 500 objects or every 10 minutes of the code scan, with a
  `CHECKPOINT` line each time, so its last log line shows `objects_scanned`
  lower than `objects_total` only if the canceled run logged a `CHECKPOINT`
  line; a scan stopped before its first save keeps its label and run ID but
  scans every object again, so the two are equal. The Inventory total matches
  the earlier full scan. Scan history shows the scan once, with the same
  "Civis run <ID>" label as in step 1 and "Resumed 1 time" under it. The run
  in step 5 starts a new scan: its log has
  `RESUME decision=new reason=last_scan_finished`.
- **Record:** pass or fail; yes or no: the Overview line in step 2, "Resuming"
  in step 3, the same label, and "Resumed 1 time"; whether the canceled
  run's log has a `CHECKPOINT` line; `objects_total`, `objects_scanned`, and
  `api_calls` for the resumed run; the `RESUME` reason
  in step 5. No names.

### 5.5 Service sleep and wake

- **Steps:** Leave the Service and its report unopened for more than one hour.
  Then open the app and click **Run scan**.
- **Expected:** The app wakes, pages load, and the scan starts (the app uses
  the new API key Civis gives it on wake).
- **Record:** pass or fail; minutes to wake.

### 5.6 Update to a new release, and removal

Step 1 is part of the retest and moves the first test's job and Service from
`v0.1.0-rc10` to `v0.1.0-rc11`; step 2 removes them at the end of the retest.

- **Steps:**
  1. Follow README "Update to a new release" to move both objects from
     `v0.1.0-rc10` to `v0.1.0-rc11`. Skip its last step ("Run a new scan"),
     so the newest scan in the results schema stays a finished scan from the
     first test. Then stop that Service's deployment.
  2. At the end of the retest: README "Remove the tool" steps 1 and 2. Step 3
     (drop the results schema) does not apply, because the new install uses
     that schema.
- **Expected:** Each step works as written. Every `CHECK` and `RESULT` line
  says `pass`, including `results_kept`; `run_id_removed` is `yes` if the
  first test's job still had `RUN_ID`, else `no_parameter`. After **Update
  Deployment**, the Service starts again, both objects show the new tag, and
  the Setup page's lines 2, 4, and 7 pass (line 4 is "review" while a
  `RUN_ID` is left, and line 7 is "review" instead when the Service has
  `REPO_HTTP_TOKEN`, as in item 1.10).
- **Record:** pass or fail per step; the `RESULT` lines by label (no IDs);
  `run_id_removed`; Setup lines 2, 4, and 7.

### 5.7 Support summary

- **Steps:** Press F5 (reload the whole Civis page) first; without it, the
  frame can show "Cookie expired." when you click **Support summary**.
  On the Setup page, click **Support summary**. Choose the scan
  in the select list (the newest finished scan is chosen by default; a running
  scan can be chosen too, but has no `run_line` yet) and click **Build
  summary**. Read the text before you copy it. Click **Copy** (or select the
  text and copy it by hand if the button reports that copying did not work),
  and paste it below the results template when you send the results.
- **Expected:** The summary builds and holds only counts, fixed labels, run
  IDs, and error types. It names no object, user, group, database, schema, or
  credential. If the log has more than 50,000 lines, it says "log partly
  read". Its setup lines match the Setup page.
- **Record:** pass or fail for built, copied (with the button or by hand), and
  sent; yes or no: any value looked like a name or other content (if yes,
  "content withheld" and do not send that summary).

## Results to report back

Copy this template, fill it in, and send it with the final support summary from
item 5.7 pasted below it. Counts and redacted error types only.

```
Python Migration Scanner: live test results (first test or retest)
Release tag: first test v0.1.0-rc5 (setup), v0.1.0-rc10 (scans, LOOKBACK_DAYS 30); retest v0.1.0-rc11 (fresh install, LOOKBACK_DAYS 30); v0.1.0-rc12 (default tags) for the last token scan and the summary; v0.1.0-rc13 (scanner image 9.2.0)
Date (MM/DD/YYYY):
Browser:

Prepared objects by type: python_script=  container_script=  custom_script=
  notebook=  service=  workflow=  dbt_script=  archived_in_workflow=
  git_python_branch=  git_python_tag_or_commit=  git_workflow=

Item  Result (pass/fail/skipped)  Counts or note
1.1   Robot user or service account:
1.2   CHECK lines (label=pass/fail):   RESULTS_DATABASE given as: name/ID
1.3   a=removed  b=removed  c=removed  d=  e=removed  f=  g=  h=   (matches, or the label shown)
1.4   without token: cpu=  memory=  disk=   with token: cpu=  memory=  disk=
      peak memory and CPU (scan 1, scan 2):   from scan 2: github_tarball_requests=  github_cache_peak_bytes=  github_cache_evictions=
1.5   RESULT lines (label=pass/fail):   other_fields_changed (job, Service):   results_db_shape=
      KEEP IN CIVIS line printed: yes/no   minutes:
1.6   minutes to start:
1.7   Service:  job:
1.8   heading (if different):
1.9   first click said the schema already existed: yes/no   outcome (a/b/c):   b or c: error type=  user name shown: yes/no  admin statement:  second click:
      table step failed (step, error type):
1.10  first visit: 1=  2=  3=  4=  5=  6=  7=  8=  9=  10=
      after 2.1:   1=  2=  3=  4=  5=  6=  7=  8=  9=  10=
      line 2 profile:   RESULTS_DB shape:   Service lookup:   line 6 job users=  groups=  Service users=  groups=
      repo_http_token_present: yes/no   line 9 dedicated: yes/no
2.1   CSP errors from the app:   parent address (if any):
2.2   done/skipped   report-only warnings:   parent address (if any):
2.3   seen/not seen
3.1   guard:
3.2   Scan history shows Robot user: yes/no   job run started by Robot user: yes/no
3.3   inventory count moved:  code scan count moved:  Overview button came back (or stop line):
      updated on its own at first: yes/no   stop line appeared: yes/no
      frame showed "Cookie expired.": yes/no   whole-page reload brought current counts: yes/no
3.4   status=  minutes=  lookback_days=  objects_total=  objects_scanned=  api_calls=
      civis_git_reads=  civis_git_failures=  request_timeouts=
      inventory (from the summary, per type): listed=  kept=  outside_window=  template_copies=  not_python=
      list_failed (from the summary): none, or type=rows read
      scanned=  partly scanned=  not scanned=  affected=  advisory=  findings=
      objects saved on the current default tag or its major tag counted as Affected: yes/no
      templates with findings=  custom scripts using them=
3.5   calls per object=  minutes per 100 objects=  github_tarball_requests=  github_cache_evictions=
3.6   other lines=   kind (general terms):   error types: (line type, error type, and count)
3.7   seconds: overview=  inventory=  findings=  slowest=   any failed or timed out: yes/no
4.1   python3=  custom=  notebooks=  services=  workflows=  dbt=
4.2   notebooks scanned=  refused for host=  refused HTTP 403=  other reason=   host shape (if any):
      notebook_links=  notebook_link_retries=
4.3   missing schema state (Setup line 9, Overview):   restored:
4.4   per table rows before/after:
4.5   runs before=  after=
4.6   python=  container=  custom=   container finding from python -c in the docker command: yes/no
4.7   image and tag:
4.8   scan 1 (no token): python git_backed=  read_through_civis=  civis_read_failed=
                         workflow git_backed=  civis_read_failed=
                         civis_git_reads=  civis_git_failures=   failures by type:
                         manual query matches summary: yes/no
      scan 2 (token):    python git_backed=  read_through_civis=  civis_read_failed=  read_from_github=
                         workflow git_backed=  civis_read_failed=
                         civis_git_reads=  civis_git_failures=   failures by type:
rc12 tags: github container default=  rule IDs (count)=  first-test container default=  notebooks on 5.2 or newer=  shown as default=
rc13 image: update fields=  setup line 2=  token=  status=  minutes=  objects_total=  api_calls=  request_timeouts=  timeout line=
5.1   rows=
5.2
5.3   rows=
5.4   objects_total=  objects_scanned=  api_calls=   CHECKPOINT in the canceled run: yes/no
      Overview line: yes/no   Resuming: yes/no   same label: yes/no   Resumed 1 time: yes/no
      step 5 RESUME reason=
5.5   minutes to wake=
5.6   step 1 RESULT lines:   run_id_removed=   Setup 2=  4=  7=   step 2 (end of retest):
5.7   built:  copied (button/by hand):  sent:  looked like a name: yes/no
Busy cue: a nav link, a filter, and a pagination link turn gray at once: yes/no
Phone width: Findings at 390 px scrolls sideways as a whole page: yes/no

Error types seen (from the scanner's log only), with counts:
Anything that did not match the README (general terms only):

Support summary (item 5.7):
```
