# Security and privacy

This page is for Civis Platform customers deciding whether to run
Python Migration Scanner for Civis Platform by Expedition Data in their own
Civis environment. It explains what the tool reads, what it stores, where data
goes, and how to set it up safely.

**By design, this tool:**

- Reads Civis objects and does not modify them. The web app starts runs of the scanner job you created.
  - Code: every Civis API call goes through [scanner/ratelimit.py](scanner/ratelimit.py) (scanner) or [app/backends/civis_backend.py](app/backends/civis_backend.py) (web app). Test: `test_civis_writes_are_limited_to_the_scanner_job_and_the_results_schema` in [tests/test_security.py](tests/test_security.py) lists every write call in the code.
- Writes only to the schema you specify and to its own job run outputs
  - Code: [scanner/output.py](scanner/output.py) (result tables and the CSV run output) and the checklist save in [app/backends/civis_backend.py](app/backends/civis_backend.py). Tests: the same test, and `test_no_code_runs_grant_statements` in [tests/test_security.py](tests/test_security.py).
- Makes network calls only to the Civis API, to Civis file storage through the time-limited links the Civis API provides (to download notebook files), and, if you provide a token, to GitHub
  - Code: the host allow lists in [scanner/retrieval/http.py](scanner/retrieval/http.py). Tests: `test_only_safe_http_sends_http_requests_and_only_to_the_allowed_hosts` in [tests/test_security.py](tests/test_security.py), and the host tests in [tests/test_retrieval.py](tests/test_retrieval.py).
- Sends no telemetry and no data to Expedition Data LLC
  - Tests: the same network test, and `test_every_page_has_the_logo_link_and_only_local_sources` in [tests/test_app.py](tests/test_app.py) (the only Expedition Data addresses are two links a viewer can click).
- Reports object IDs, rule IDs, file names, and line numbers, not code contents
  - Code: the `findings` table in [common/schema.py](common/schema.py) and the scanner's log in [scanner/scan_log.py](scanner/scan_log.py). Tests: `test_code_and_notebook_output_text_never_reach_findings` and `test_log_with_password_and_email_leaves_no_trace` in [tests/test_security.py](tests/test_security.py), and `test_no_log_or_code_text_in_results` in [tests/test_retrieval.py](tests/test_retrieval.py).

These statements describe intended behavior. The full source code is available for your review. See [LICENSE](LICENSE) for the warranty disclaimer.

## Where the tool runs and what it contacts

The tool has two parts, and both are designed to run inside your Civis
environment:

- **The scanner** runs as a Civis container script.
- **The web app** runs as a Civis Service.

By design, the tool sends no data to Expedition Data LLC or to any other third
party. It has no telemetry, no usage tracking, and no external logging.

Its outbound calls are designed to be reads from:

1. **The Civis API**, to list objects and read their settings, code, and run logs.
2. **Civis file storage**, to download notebook files through the time-limited
   links the Civis API provides.
3. **Your own GitHub repositories**, to read the rest of each git-backed
   object's repository. This happens only if you attach a GitHub token.

For git-backed Python scripts and workflows, the Civis API also returns the
file set in Civis. Civis reads that file from the repository itself, so the
tool does not contact GitHub for those files, and it needs no token for them
([scanner/retrieval/civis_git.py](scanner/retrieval/civis_git.py)). The file
is handled like other code: in memory only
(`test_git_files_read_through_civis_never_reach_any_output` in
[tests/test_security.py](tests/test_security.py)).

The web app also shows two links to the Expedition Data website. They open only
when a viewer clicks them, and they send no referrer.

By design, the tool writes to two places, both inside Civis:

- **The schema you name**, where it saves its result tables.
- **The scanner job's own run outputs**, where it saves a CSV copy of the report.

The web app starts runs of the scanner job you created, through the Civis API.

Both parts run on pinned Civis Docker images and are designed to install
nothing at run time, so they do not contact PyPI or any other package index
while they run ([start_service.sh](start_service.sh) and
[tests/test_deploy.py](tests/test_deploy.py)).

## What the tool reads and changes

**Reads:**

- Python scripts, container scripts, custom scripts, notebooks, Services, and
  workflows that the scanning user can see.
- Their code, settings, and the most recent run log.

**Changes:** the tool is designed to be read-only on every Civis object. It
does not edit, archive, share, or delete your scripts, notebooks, Services, or
workflows. The only job it runs is the scanner job you created, when someone
clicks **Run scan** in the web app.

**Writes:** to the schema you name and to the scanner job's run outputs.

**Sees:** what the Civis user the scan runs as can see, and no more. We
recommend running it as your Robot user.

## Sensitive content in code, logs, and notebooks

Code, run logs, and notebook outputs often contain passwords, API keys, and
personal data about donors, members, or voters. The tool is designed to treat
all of it as sensitive.

- **Memory only.** By design, this content is processed in memory. Code
  checked out from git goes into a temporary folder that only the scanner's
  user can open, and the scanner deletes the folder when the scan ends,
  including when the scan fails ([scanner/retrieval/tempdir.py](scanner/retrieval/tempdir.py)).
- **Locations only.** For each finding, the tool records the object ID, the
  rule ID, the file name, and the line number. It does not record the code on
  that line, the log text, or the notebook output.
- **Not in outputs.** The tool is designed so that no text from your code,
  logs, or notebooks is written to the result tables, the CSV report, the
  scanner's own log, or error messages ([tests/test_security.py](tests/test_security.py)).
- **Errors.** When a file cannot be parsed, the tool logs the object ID and the
  type of error, such as `SyntaxError`, not the text that failed.
- **The scanner's own log.** It is designed to contain only progress counts,
  object IDs, rule IDs, and error types. The scanner writes it only through
  [scanner/scan_log.py](scanner/scan_log.py), which accepts no free text.

## Credentials

- **Civis API key.** Civis provides the scanner job and the Service with an API
  key automatically. The tool is designed not to print, log, or store it. As a
  second layer, every log line passes through a filter
  ([common/redact.py](common/redact.py)) that removes:
  - the API key
  - attached credential passwords
  - GitHub tokens
  - common secret patterns, such as `password=`, bearer tokens, credentials
    inside URLs, and signed URL parameters
- **GitHub token (optional).** The normal setup has no token. Without one, the
  tool reads the file set in Civis for each git-backed Python script and
  workflow through the Civis API, as described above, and does not read the
  rest of the repository. Git-backed container scripts, Services, and dbt
  scripts are marked for manual review: "partial" if their most recent run log
  was read, "not scanned" if not. A token lets the tool read the whole
  repository from GitHub. If you add one:
  - Use a **fine-grained** GitHub token, not a classic token.
  - Give it **read-only "Contents"** permission and no other permissions.
  - Limit it to **only the repositories** your Civis objects use.
  - Set an **expiration date**.
  - Store it as a Civis credential and attach it to the **scanner job only**.
    Never attach it to the Service. The web app does not need it and does not
    read it (`test_app_never_reads_a_github_token` in
    [tests/test_security.py](tests/test_security.py)).
- **Database credential.** The scanner writes results with the Civis database
  credential you choose in its Database parameter. The tool is designed not to
  print, log, or store it.

## Who can see the results

The results list the names, owners, and links of every object the scanning user
can see, including private objects. Treat them as internal and confidential.

- **Use a dedicated schema.** Write results to a schema used only for this tool.
  Grant read access to Civis admins only.
- **The tool does not change permissions.** It does not run `GRANT` statements
  (`test_no_code_runs_grant_statements` in
  [tests/test_security.py](tests/test_security.py)). You decide who can read
  the schema.
- **Share the web app with Civis admins only.** Anyone who can open the app sees
  everything the scanning user can see. The app's landing page says this too.
- **Share the scanner job with Civis admins only.** Each scan saves the full
  report as a CSV file on the job's run, so anyone who can read the job can
  download it.
- **Downloading results is your choice.** The app's "Download CSV" button saves
  the full report to the viewer's computer. At that point the data is outside
  Civis, under your organization's own policies. Whether admins use the button
  is your admin team's choice.

## Keeping and deleting results

- **Default: keep everything.** Every scan adds new rows, tagged with a run ID,
  so you can track progress across rescans. By default, no rows are deleted.
- **Automatic deletion.** Set the scanner job's `RETENTION_DAYS` parameter to a
  number of days, and each scan deletes older runs from all five result tables
  ([common/retention.py](common/retention.py)).
  - The most recent run is not deleted.
  - Checklist history is kept where it matters: an old checklist row is deleted
    only if a newer row exists for the same object and rule, so the status your
    team set is kept.
- **Manual deletion.** To delete one run yourself, run these statements in the
  schema you chose. Replace `results_schema` and `<run_id>`, and run them in this
  order:

```sql
DELETE FROM results_schema.findings WHERE run_id IN ('<run_id>');
DELETE FROM results_schema.coverage WHERE run_id IN ('<run_id>');
DELETE FROM results_schema.objects WHERE run_id IN ('<run_id>');
DELETE FROM results_schema.checklist_status USING results_schema.checklist_status AS newer
  WHERE results_schema.checklist_status.run_id IN ('<run_id>')
  AND newer.object_id = results_schema.checklist_status.object_id
  AND newer.rule_id = results_schema.checklist_status.rule_id
  AND newer.changed_at > results_schema.checklist_status.changed_at;
DELETE FROM results_schema.scan_runs WHERE run_id IN ('<run_id>');
```

  To remove everything, drop the schema.

## Web app protections

These are described in [app/security.py](app/security.py) and tested in
[tests/test_security.py](tests/test_security.py) and
[tests/test_app.py](tests/test_app.py).

- **Displaying names safely.** Object names, user names, and file paths come
  from Civis and can contain anything, including HTML or script.
  - Every page is designed to escape them, so they show as plain text.
  - JSON sent to the browser escapes `<`, `>`, `&`, and `'`.
  - Links are allowed only if they start with `https://` or `http://`.
- **Content Security Policy.** The app is designed to load scripts, styles, and
  fonts only from itself:
  - It has no inline script or style, and no content delivery network (CDN).
  - Every front-end file is bundled with the app, so it works on locked-down
    networks.
  - Responses also send `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
- **Framing.** Civis Platform shows Services inside a frame on
  `platform.civisanalytics.com`. The app is designed to allow framing only by
  itself and by that address, so other websites cannot embed it. You can change the list with
  the `CIVIS_FRAME_ANCESTORS` setting.
  - **Setup aid.** While you set up the app, you can set
    `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` to `true`. The browser then reports a
    wrong framing rule as a console warning instead of showing a blank page.
    Every other protection on this list stays on, and no report is sent
    anywhere. Set it back to `false` (or remove it) once the app shows
    correctly inside Civis. While it is on, other websites could embed the app.
- **Request forgery protection.** Every form submission needs a token tied to
  the viewer's Civis user ID, and the app is designed to refuse requests from
  other websites. The app sets no cookies of its own.
- **Debug mode is off.** The app is designed to refuse to start if Flask debug
  mode is turned on.
- **Local preview uses made-up data.** The developer preview
  (`python -m app.local`) is designed to read made-up dry-run files, listen
  only on the developer's own computer, and refuse to start if a Civis API key
  is set.
- **Who is viewing.** The app identifies the viewer from the headers Civis adds
  to each request. It is designed to record that user name only on checklist
  changes.
- **Safe database writes.** Every value written to the database is designed to
  be checked and escaped, including the viewer's user name. Schema and table
  names are checked against a strict pattern before use.

## Software supply chain

- **Pinned images.** The scanner and the app run on exact, pinned Civis public
  Docker images. The README lists each image's tag and digest, so you can
  confirm the image has not changed.
- **No installs at run time.** Neither part is designed to download packages
  while it runs.
- **Secret scanning.** Our practice is to check every commit to this repository
  for secrets, both automatically (gitleaks or detect-secrets) and by review.
- **Synthetic test data.** Our practice is to use made-up data only in unit
  tests and to keep customer object names, user names, code, and results out
  of the repository.

## How we build and test it

- **No customer data in development.** Our practice is not to run the scanner
  or the app against a customer's Civis environment from a developer's
  computer, and not to call a customer's Civis API from outside Civis.
- **Live testing inside Civis.** Our practice is to do live testing only by
  running the scanner job and the Service inside a customer's own Civis
  environment, with that customer's agreement.
- **No customer results when we help.** Our practice is not to read customer
  scan results, run logs, or code during development, or when we help with a
  problem. If we need information to look into a problem, we ask for counts or
  error messages with customer details removed.

## Limits

- **Access.** The scanner sees only what the Civis user the scan runs as can
  see. Objects that user can list but cannot read are recorded in the coverage
  table as "not scanned," with the reason. Objects that user cannot see at all
  are not scanned and do not appear in the results.
- **Platform security.** The security of Civis Platform itself, including its
  authentication, storage, and network, is the responsibility of Civis
  Analytics and your organization.
- **Disclaimer.** See the disclaimer at the top of the [README](README.md). The
  app and every report show the same text, from
  [common/disclaimer.py](common/disclaimer.py).

## Reporting a security issue

Please report security issues privately through GitHub's private vulnerability
reporting: on this repository's page, open the **Security** tab and choose
**Report a vulnerability**. Do not open a public GitHub issue. Reports go
privately to Expedition Data LLC.
