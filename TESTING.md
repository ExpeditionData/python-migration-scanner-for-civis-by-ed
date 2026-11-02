# First live test

A step-by-step checklist for the first live test of Python Migration Scanner
inside a **test organization's** Civis environment. It checks the
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
- **Release tags:** setup (section 1) can start on `v0.1.0-rc1`. Every scan
  uses `v0.1.0-rc2`: item 5.6 moves both objects to it before the first scan.
  Use the tag in place wherever the README says `<release tag>`.
- **Browser:** Chrome, Edge, or Firefox. Safari ignores the report-only frame
  rule (item 2.1).

## Order

1. Section 1 (setup) on `v0.1.0-rc1`, with no GitHub token attached to the
   scanner job.
2. Section 2 (frame rule).
3. Item 5.6, first part: update both objects to `v0.1.0-rc2`.
4. Section 3 (the first scan, with no GitHub token) and the first-scan part
   of item 4.8.
5. Items 4.1 to 4.3, 4.6, 4.7, and 5.1.
6. Attach the GitHub token (item 4.8), then item 5.2 (the second scan), the
   second-scan part of item 4.8, and item 1.4.
7. Items 4.4, 4.5, 5.3 to 5.5, and the rest of item 5.6.

## Prepare the test organization

Before item 1.1, make sure the test organization has at least one of each of
these, so every check below has something to find. Each Python object should
contain one known pattern, such as `client.users.list_me()`.

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

- **Steps:** Follow README "Before you start": confirm the Robot user and have
  a database admin create the results schema with the privileges listed. Do
  not attach a GitHub token yet: the first scan runs without one on purpose
  (item 4.8). The GitHub Custom Credential can be created now or at item 4.8.
- **Expected:** Each step works as written.
- **Record:** pass or fail per step.

### 1.2 Create the scanner job

- **Steps:** Follow README "Create the scanner job", steps 1 to 7.
- **Expected:** The job saves with all eight parameters. **GitHub Repository
  URL** accepts the typed public URL (the help article describes picking a
  repository from a list).
- **Record:** pass or fail per step; whether the URL could be typed (yes or no).

### 1.3 Labels to confirm

- **Steps:** For each field the README marks "(label to confirm)", find it in
  the web interface.

  | # | README text | What to find |
  |---|---|---|
  | a | Git credential | The container script field for a git credential. |
  | b | Disk Space | The container script field for disk space, and its unit. |
  | c | Parameter Name and Type | The two field labels in **Set Parameters**. Also confirm the type list offers Database, String, Integer, and Custom Credential. |
  | d | Share the credential with the Robot user | How a Custom Credential is shared with, or created as, another user. |
  | e | Archive for Services | How a Service is archived. |

- **Expected:** The labels match the README.
- **Record:** for each, "matches" or the label the interface shows. Civis
  interface labels are not client data.

### 1.4 Scanner resources

- **Steps:** Use the README values (CPU `1000`, Memory `4096`, Disk Space `4`).
  After the first scan (item 3.4) and the second scan (item 5.2), check each
  run for memory or disk errors. Read the GitHub counts on the last line of
  the run log of the first scan with the token attached (the second scan,
  item 4.8). The first scan has no token, so its GitHub counts are zero.
- **Expected:** No out-of-memory or disk-full failure. `github_cache_peak_bytes`
  is at most 1073741824 (the 1 GB cache cap).
- **Record:** pass or fail; peak memory if Civis shows it;
  `github_tarball_requests`, `github_cache_peak_bytes`, and
  `github_cache_evictions`.

### 1.5 Create the Service

- **Steps:** Follow README "Create the Service", steps 1 to 9, with
  `CIVIS_FRAME_ANCESTORS_REPORT_ONLY=true`.
- **Expected:** The deployment starts and stays running. Civis's health check
  uses `/healthz`.
- **Record:** pass or fail per step; minutes from **Start Deployment** to running.

### 1.6 Nothing is installed at start

- **Steps:** Open the Service's deployment log.
- **Expected:** A gunicorn start ("Listening at: http://0.0.0.0:3838", "Using
  worker: gthread", and two "Booting worker" lines) and no "pip",
  "Collecting", or "Downloading" lines.
- **Record:** pass or fail.

### 1.7 Sharing

- **Steps:** Check the Service's and the scanner job's sharing.
- **Expected:** Only Civis admins (and the Robot user) have access. The default
  group was removed from the Service.
- **Record:** pass or fail for each object.

### 1.8 Results schema check before the first scan

- **Steps:** Open the app before any scan.
- **Expected:** "No scan yet". This confirms the `information_schema.schemata`
  check finds the schema on Redshift (PLAN.md 14.11).
- **Record:** pass or fail; the heading shown if different.

## 2. Frame rule (PLAN.md 11.3)

### 2.1 Report-only first

- **Steps:** With `CIVIS_FRAME_ANCESTORS_REPORT_ONLY=true`, open the app from
  the Service (**View Report**) and open the browser's developer console.
  Reload the page.
- **Expected:** The app shows. The console has no "[Report Only]" warning
  about `frame-ancestors` from the app.
- **Record:** pass or fail; the number of such warnings. If there is one,
  record the parent address it names. Civis host names are fine to record;
  replace any part that names the organization with `<org>`.

### 2.2 Then enforced

- **Steps:** Set `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` to `false` (add
  `CIVIS_FRAME_ANCESTORS` first if item 2.1 found another parent address),
  click **Update Deployment**, and reload the app with the console open.
- **Expected:** The app shows, and the console has no Content Security Policy
  errors.
- **Record:** pass or fail.

### 2.3 Safari (optional)

- **Steps:** Repeat 2.1 in Safari.
- **Expected:** Safari logs that `frame-ancestors` "is ignored when delivered
  in a report-only policy." This is a known Safari limit, not a failure.
- **Record:** seen or not seen.

## 3. First scan from the app

### 3.1 The Service starts the scanner job

- **Steps:** On the Overview page, click **Run scan**. Then click it again, or
  reload and look at the button.
- **Expected:** A run starts on the scanner job (its Run History shows it).
  The button shows "Scan running" and a second scan cannot start.
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

- **Steps:** Watch the Scan history page.
- **Expected:** Inventory, then Code and log scan with a moving count, then
  Save results. The page updates without reloading.
- **Record:** pass or fail.

### 3.4 Scan results

- **Steps:** When the scan finishes, read the last line of the scanner job's
  run log. It has the form
  `RUN run_id=... status=succeeded objects_total=N objects_scanned=N api_calls=N
  github_tarball_requests=N github_cache_peak_bytes=N github_cache_evictions=N
  civis_git_reads=N civis_git_failures=N`.
  Then read the counts on the Overview page.
- **Expected:** Status `succeeded`; Overview counts match the run log.
- **Record:** status; minutes from start to finish; `objects_total`,
  `objects_scanned`, `api_calls`, `civis_git_reads`, `civis_git_failures`;
  objects scanned, partly scanned, and not scanned; affected and advisory
  counts; total findings.

### 3.5 Calls per object (PLAN.md section 5)

- **Steps:** Divide `api_calls` by `objects_total`. `api_calls` counts every
  Civis API request the scanner sent through its request budget. Table writes
  and the CSV upload are not counted (a few calls per checkpoint).
- **Expected:** About 3 to 4 per object (section 5 estimates 3, plus about 1
  for each Python script, PLAN.md 10.4), plus 1 for each active workflow
  (`workflows.get_git`, including workflows that only run other jobs and so
  are not counted in `objects_total`), plus up to 2 for each git-backed Python
  script or workflow read through Civis (`civis_git_reads` plus
  `civis_git_failures`; PLAN.md 17.1). At the default budget, about 20 minutes
  per 500 objects.
- **Record:** calls per object; minutes per 100 objects;
  `github_tarball_requests` and `github_cache_evictions` (GitHub requests do
  not count against the Civis limit, but evictions mean repeat downloads).

### 3.6 The run log holds only structured lines

- **Steps:** Scan the scanner's run log without copying it. Scanner lines
  start with `RUN`, `PHASE`, `PROGRESS`, `CHECKPOINT`, `FINDINGS`,
  `OBJECT_ERROR`, `CIVIS_GIT_FAILED`, `PARSE_ERROR`, `TABLE_WRITE_FAILED`,
  `OUTPUT_FAILED`, or `FATAL`.
- **Expected:** No other lines, apart from Civis's own container messages. No
  "pip" lines.
- **Record:** the number of other lines, and the count of each error type in
  `OBJECT_ERROR`, `CIVIS_GIT_FAILED`, and `PARSE_ERROR` lines (for example
  `CivisAPIError status=403: 2`).

### 3.7 First page loads after a scan

- **Steps:** Right after the scan, open Overview, Inventory, and Findings for
  the first time and time each. Each of the Service's two worker processes
  keeps its own cache, so a page may be slow once per worker.
- **Expected:** Each page loads. The first load reads the result tables, so it
  can take longer than later loads. The Service stays healthy while it loads.
- **Record:** seconds per page; the slowest first page load in seconds; whether
  any page load failed or timed out (yes or no, with the error code if shown).

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

### 4.2 Notebook download host (PLAN.md 12.4)

- **Steps:** On the Inventory page, filter to notebooks. Count notebooks that
  were scanned and notebooks whose reason is "The notebook file could not be
  downloaded. The link points to a host the scanner is not allowed to
  contact."
- **Expected:** Every notebook with a file was scanned; none refused for its host.
- **Record:** both counts. If any were refused, open one notebook in Civis with
  the browser's Network tab open, find the request that downloads the `.ipynb`
  file, and record only the shape of its host with the account parts replaced
  (for example `<bucket>.s3.<region>.amazonaws.com`).

### 4.3 Results schema check on Redshift (PLAN.md 14.11)

- **Steps:** Item 1.8 checked the "No scan yet" state. Now set the Service's
  `RESULTS_SCHEMA` to a schema that does not exist (for example
  `pmsc_no_such_schema`), click **Update Deployment**, and open the app. Then
  set it back and update again.
- **Expected:** "The results schema does not exist yet". After setting it
  back, the results show again.
- **Record:** pass or fail for each state.

### 4.4 `DELETE ... USING` on Redshift (PLAN.md 11.2)

- **Steps:** After at least two scans, pick the older run's ID (the
  `run_id=` value in the `RUN run_id=... status=running` line near the top of
  that run's log). In a Civis query
  against the results database, count that run's rows in each of the five
  tables, run the manual deletion SQL from SECURITY.md for that run ID, and
  count again.
- **Expected:** All five statements succeed. The run's rows are gone from
  `findings`, `coverage`, `objects`, and `scan_runs`. In `checklist_status`, only
  rows that have a newer row for the same object and rule are deleted.
- **Record:** pass or fail per statement; row counts before and after per table.

### 4.5 `RETENTION_DAYS` (PLAN.md 11.2)

- **Steps:** Do item 5.1 first. On a later day than an earlier scan, set the scanner job's
  `RETENTION_DAYS` to `1` and click **Run scan** in the app. Count rows per run
  in `scan_runs` before and after. Then clear `RETENTION_DAYS`.
- **Expected:** Scans started more than one day earlier are deleted from all
  five tables; the newest scan is kept; checklist statuses set in item 5.1 are
  still shown in the app.
- **Record:** pass or fail; number of runs before and after.

### 4.6 Archived jobs in active workflows (PLAN.md 10.7)

- **Steps:** Use the workflow prepared above, which runs three archived scripts
  (Python, container, custom). Find those scripts on the Inventory page.
- **Expected:** Each appears as archived, lists the workflow, and has its
  finding. This confirms the job type keyword matching.
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
     it, read `civis_git_reads` and `civis_git_failures` on the last line of
     its run log, and count the `CIVIS_GIT_FAILED` lines by error type and
     status (for example `CivisAPIError status=403: 3`), as in item 3.6.
  2. In a Civis query against the results database, run this with the scan's
     run ID (the `run_id=` value on its `RUN` lines) and your schema's name:

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
  3. Create the GitHub Custom Credential if you have not, attach it to the
     scanner job's `GITHUB` parameter (README "Before you start", item 4),
     signed in as the Robot user, and save.
  4. The second scan (item 5.2) runs with the token. Repeat steps 1 and 2 for
     it. For Python scripts, the number read from GitHub is `git_backed` minus
     `read_through_civis` minus `civis_read_failed`.
- **Expected:** Without the token, git-backed Python scripts and workflows are
  read through Civis, with few or no failures. With the token, most
  git-backed Python scripts are read from GitHub, and only those GitHub cannot
  read are read through Civis. Workflows are read through Civis in both scans.
  If every read fails with the same error, record it: it may show which
  GitHub connection Civis uses for these endpoints, or that the Robot user
  cannot call them.
- **Record:** for each scan: git-backed Python scripts and git-backed workflows
  (`git_backed`); how many of each were read through Civis;
  `civis_git_reads` and `civis_git_failures`; the error type and status of each
  kind of `CIVIS_GIT_FAILED` line, with counts. For the second scan, also how
  many git-backed Python scripts were read from GitHub.

## 5. App functions

### 5.1 Checklist save with the Civis-Username header

- **Steps:** On the Findings page, change two statuses and click **Save
  changes**. Reload. In a Civis query, count `checklist_status` rows where
  `changed_by` is not `scanner`.
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
- **Expected:** The file downloads and opens with accents shown correctly. It
  has one row per finding plus one row for each object with no findings.
  Delete the file when done.
- **Record:** pass or fail; the row count.

### 5.4 Resume a stopped scan

- **Steps:**
  1. Click **Run scan** in the app. When the Scan history page shows Code and
     log scan partway done, cancel the run: open the job's Run Log and click
     the "x" next to the spinner.
  2. Copy the `run_id=` value from the `RUN run_id=... status=running` line
     near the top of that run's log. It is a generated ID, not client data.
  3. Sign in as yourself, not the Robot user. Set the job's `RUN_ID`
     parameter to the run ID, save, then click **Run scan** in the app.
  4. When it finishes, read the Scan history page's "Started ... as <user>"
     line for the resumed run. Then clear `RUN_ID`, signed in as the Robot
     user if you can (otherwise as yourself), and save.
  5. Click **Run scan** in the app again and, when it starts, read the same
     line for this next run.
- **Expected:** The resumed run succeeds. Its last log line shows
  `objects_scanned` lower than `objects_total`. The Inventory total matches the
  earlier full scan. The next **Run scan** starts a new run.
- **Record:** pass or fail; `objects_total`, `objects_scanned`, and `api_calls`
  for the resumed run; yes or no: the Scan history page shows the Robot user
  for the resumed run; yes or no: it shows the Robot user for the next run;
  and who was signed in for each edit (step 3: Robot user or not; step 4:
  Robot user or not). No names.
- **What it means:** Civis's help describes the runner two ways. If the resumed
  run shows the person who edited `RUN_ID` in step 3, the rule is "the user who
  last updated it". If it shows the Robot user, the rule is "the user who runs
  it". The next run checks the same rule after the step 4 edit.

### 5.5 Service sleep and wake

- **Steps:** Leave the Service and its report unopened for more than one hour.
  Then open the app and click **Run scan**.
- **Expected:** The app wakes, pages load, and the scan starts (the app uses
  the new API key Civis gives it on wake).
- **Record:** pass or fail; minutes to wake.

### 5.6 Update to a new release, and removal

- **Steps:**
  1. After section 2 and before the first scan (item 3.1), follow README
     "Update to a new release" to move both objects from `v0.1.0-rc1` to
     `v0.1.0-rc2`. Its step 4 ("Run a new scan") is item 3.1.
  2. Optional, at the end of the test: follow README "Remove the tool".
- **Expected:** Each step works as written. After the update, the Service
  starts again and both objects show `v0.1.0-rc2`.
- **Record:** pass or fail per step.

## Results to report back

Copy this template, fill it in, and send it. Counts and redacted error types
only.

```
Python Migration Scanner: first live test results
Release tags: setup on v0.1.0-rc1, scans on v0.1.0-rc2
Date (MM/DD/YYYY):
Browser:

Prepared objects by type: python_script=  container_script=  custom_script=
  notebook=  service=  workflow=  dbt_script=  archived_in_workflow=
  git_python_branch=  git_python_tag_or_commit=  git_workflow=

Item  Result (pass/fail/skipped)  Counts or note
1.1
1.2   URL typed: yes/no
1.3   a=  b=  c=  d=  e=   (matches, or the label shown)
1.4   peak memory (scan 1, scan 2):   from scan 2: github_tarball_requests=  github_cache_peak_bytes=  github_cache_evictions=
1.5   minutes to start:
1.6
1.7   Service:  job:
1.8
2.1   report-only warnings:   parent address (if any):
2.2
2.3   seen/not seen
3.1   guard:
3.2   Scan history shows Robot user: yes/no   job run started by Robot user: yes/no
3.3
3.4   status=  minutes=  objects_total=  objects_scanned=  api_calls=
      civis_git_reads=  civis_git_failures=
      scanned=  partly scanned=  not scanned=  affected=  advisory=  findings=
3.5   calls per object=  minutes per 100 objects=  github_tarball_requests=  github_cache_evictions=
3.6   other lines=   error types: (line type, error type, and count)
3.7   seconds: overview=  inventory=  findings=  slowest=   any failed or timed out: yes/no
4.1   python3=  custom=  notebooks=  services=  workflows=  dbt=
4.2   notebooks scanned=  refused for host=   host shape (if any):
4.3   missing schema state:   restored:
4.4   per table rows before/after:
4.5   runs before=  after=
4.6   python=  container=  custom=
4.7   image and tag:
4.8   scan 1 (no token): python git_backed=  read_through_civis=  civis_read_failed=
                         workflow git_backed=  civis_read_failed=
                         civis_git_reads=  civis_git_failures=   failures by type:
      scan 2 (token):    python git_backed=  read_through_civis=  civis_read_failed=  read_from_github=
                         workflow git_backed=  civis_read_failed=
                         civis_git_reads=  civis_git_failures=   failures by type:
5.1   rows=
5.2
5.3   rows=
5.4   objects_total=  objects_scanned=  api_calls=
      step 3 edit by Robot user: yes/no   resumed run shows Robot user: yes/no
      step 4 edit by Robot user: yes/no   next run shows Robot user: yes/no
5.5   minutes to wake=
5.6   update to rc2:   removal:

Error types seen (from the scanner's log only), with counts:
Anything that did not match the README (general terms only):
```
