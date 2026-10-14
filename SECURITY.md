# Security and privacy

This page is for Civis Platform customers deciding whether to run Civis Python
Version Search in their own Civis environment. It explains what the tool reads,
what it stores, where data goes, and how to set it up safely.

**In short:**

- The tool runs entirely inside your Civis environment and sends nothing out of it.
- It reads your Civis objects but never changes them.
- It stores only object IDs, rule IDs, file names, and line numbers about your
  code. It never stores code, log text, or notebook output.
- Results go to a schema you choose, and only people you grant access can read them.

## Nothing leaves your Civis environment

The tool has two parts, and both run inside your Civis environment:

- **The scanner** runs as a Civis container script.
- **The web app** runs as a Civis Service.

The tool sends nothing to Expedition Data or to any other third party. There is
no telemetry, no usage tracking, and no external logging.

Its only outbound calls are reads from:

1. **The Civis API**, to list objects and read their settings, code, and run logs.
2. **Civis file storage**, to download notebook files through the time-limited
   links the Civis API provides.
3. **Your own GitHub repositories**, to read code for git-backed objects. This
   only happens if you attach a GitHub token.

The tool writes to two places only, and both are inside Civis:

- **The schema you name**, where it saves its result tables.
- **The scanner job's own run outputs**, where it saves a CSV copy of the report.

The web app starts runs of the scanner job you created, through the Civis API.

Both parts run on pinned Civis Docker images and install nothing at run time, so
they do not contact PyPI or any other package index while they run.

## What the tool reads and changes

**Reads:**

- Python scripts, container scripts, custom scripts, notebooks, Services, and
  workflows that the scanning user can see.
- Their code, settings, and the most recent run log.

**Changes:** nothing. The tool is read-only on every Civis object. It never
edits, runs, archives, shares, or deletes your scripts, notebooks, Services, or
workflows.

**Writes:** only to the schema you name and to the scanner job's run outputs.

**Sees:** what the user who runs the scanner can see, and no more. We recommend
running it as your Robot user.

## Sensitive content in code, logs, and notebooks

Code, run logs, and notebook outputs often contain passwords, API keys, and
personal data about donors, members, or voters. The tool treats all of it as
sensitive.

- **Memory only.** All of this content is processed in memory. Code checked out
  from git goes into a temporary folder that only the scanner's user can open.
  The folder is deleted when the scan ends, including when the scan fails.
- **Locations only.** For each finding, the tool records the object ID, the rule
  ID, the file name, and the line number. It never records the code on that
  line, the log text, or the notebook output.
- **Nowhere else.** No text from your code, logs, or notebooks is written to the
  result tables, the CSV report, the scanner's own log, or any error message.
- **Errors.** When a file cannot be parsed, the tool logs only the object ID and
  the type of error, such as `SyntaxError`. It never logs the text that failed.
- **The scanner's own log.** It contains only progress counts, object IDs, rule
  IDs, and error types.

## Credentials

- **Civis API key.** Civis provides the scanner job and the Service with an API
  key automatically. The tool never prints, logs, or stores it. As a second
  layer, every log line passes through a filter that removes:
  - the API key
  - attached credential passwords
  - GitHub tokens
  - common secret patterns, such as `password=`, bearer tokens, credentials
    inside URLs, and signed URL parameters
- **GitHub token (optional).** Without a token, git-backed objects are listed as
  "not scanned, review manually." If you add one:
  - Use a **fine-grained** GitHub token, not a classic token.
  - Give it **read-only "Contents"** permission and no other permissions.
  - Limit it to **only the repositories** your Civis objects use.
  - Set an **expiration date**.
  - Store it as a Civis credential and attach it to the **scanner job only**.
    Never attach it to the Service. The web app does not need it and never reads it.
- **Database credential.** The scanner writes results with the Civis database
  credential you choose in its Database parameter. The tool never prints, logs,
  or stores it.

## Who can see the results

The results list the names, owners, and links of every object the scanning user
can see, including private objects. Treat them as internal and confidential.

- **Use a dedicated schema.** Write results to a schema used only for this tool.
  Grant read access to Civis admins only.
- **The tool never changes permissions.** It never runs `GRANT` statements. You
  decide who can read the schema.
- **Share the web app with Civis admins only.** Anyone who can open the app sees
  everything the scanning user can see. The app's landing page says this too.
- **Downloading results is your choice.** The app's "Download CSV" button saves
  the full report to the viewer's computer. At that point the data is outside
  Civis, under your organization's own policies. Whether admins use the button
  is your admin team's choice.

## Keeping and deleting results

- **Default: keep everything.** Every scan adds new rows, tagged with a run ID,
  so you can track progress across rescans. Nothing is deleted.
- **Automatic deletion.** Set the scanner job's `RETENTION_DAYS` parameter to a
  number of days, and each scan deletes older runs from all five result tables.
  - The most recent run is never deleted.
  - Checklist history is kept where it matters: an old checklist row is deleted
    only if a newer row exists for the same object and rule, so the status your
    team set is never lost.
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

- **Displaying names safely.** Object names, user names, and file paths come
  from Civis and can contain anything, including HTML or script.
  - Every page escapes them, so they always show as plain text.
  - JSON sent to the browser escapes `<`, `>`, `&`, and `'`.
  - Links are allowed only if they start with `https://` or `http://`.
- **Content Security Policy.** The app loads scripts, styles, and fonts only from
  itself:
  - There is no inline script or style, and no content delivery network (CDN).
  - Every front-end file is bundled with the app, so it works on locked-down
    networks.
  - Responses also send `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
- **Framing.** Civis Platform shows Services inside a frame on
  `platform.civisanalytics.com`. The app allows framing only by itself and by
  that address, so other websites cannot embed it. You can change the list with
  the `CIVIS_FRAME_ANCESTORS` setting.
- **Request forgery protection.** Every form submission needs a token tied to
  the viewer's Civis user ID. Requests from other websites are refused. The app
  sets no cookies of its own.
- **Debug mode is always off.** The app refuses to start if Flask debug mode is
  turned on.
- **Who is viewing.** The app identifies the viewer from the headers Civis adds
  to each request. It records that user name only on checklist changes.
- **Safe database writes.** Every value written to the database is checked and
  escaped, including the viewer's user name. Schema and table names are checked
  against a strict pattern before use.

## Software supply chain

- **Pinned images.** The scanner and the app run on exact, pinned Civis public
  Docker images. The README lists each image's tag and digest, so you can
  confirm the image has not changed.
- **No installs at run time.** Neither part downloads packages while it runs.
- **Secret scanning.** Every commit to this repository is checked for secrets,
  both automatically (gitleaks or detect-secrets) and by review.
- **Synthetic test data.** Unit tests use made-up data only. The repository
  contains no customer object names, user names, code, or results.

## How we build and test it

- **No customer data in development.** We never run the scanner or the app
  against a customer's Civis environment from a developer's computer, and we
  never call a customer's Civis API from outside Civis.
- **Live testing inside Civis.** Live testing happens only by running the
  scanner job and the Service inside a customer's own Civis environment, with
  that customer's agreement.
- **No customer results in support.** We do not read customer scan results, run
  logs, or code during development or support. If we need information to fix a
  problem, we ask for counts or error messages with customer details removed.

## Limits

- **Access.** The scanner sees only what its user can see. Objects that user
  cannot read are listed in the coverage table as "not scanned," with the reason.
- **Platform security.** The security of Civis Platform itself, including its
  authentication, storage, and network, is the responsibility of Civis
  Analytics and your organization.
- **Not a complete audit.** This tool flags known code patterns. It can miss
  problems and can flag code that is fine. Test every job before February 2027.

## Reporting a security issue

Please report security issues privately through GitHub's private vulnerability
reporting: on this repository's page, open the **Security** tab and choose
**Report a vulnerability**. Do not open a public GitHub issue. Expedition Data
LLC reviews every report.
