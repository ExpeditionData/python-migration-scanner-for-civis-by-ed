# Civis Python Version Search

A free, open-source tool that Civis Platform customers run inside their own
Civis environment. It finds scripts, notebooks, workflows, Services, and
container scripts affected by the February 2027 Civis default image update
(Python 3.14 to 3.15, and civis-python v2 to v3), and shows what to fix.

It has two parts:

- **A scanner job.** A Civis container script that finds the affected objects
  and writes results to a schema you choose.
- **A web app.** A Civis Service where admins start scans, review results, and
  track a fix checklist.

> **Disclaimer.** This tool flags known code patterns that may break when Civis
> Platform updates its default Python images in February 2027 (Python 3.14 to
> 3.15, and civis-python v2 to v3). It is not a complete audit. It can miss
> problems, and it can flag code that is fine. Test every job before February
> 2027. The tool is provided under the Apache License 2.0, without warranty of
> any kind.

Setup takes about 30 minutes and is described in [Setup](#setup) below. Field
names are the ones used in Civis help articles. Where a name could not be
confirmed against the Civis web interface, it is followed by "(label to
confirm)".

## Security and privacy

Read [SECURITY.md](SECURITY.md) before you install the tool. In short:

- **Stays inside Civis.** Everything runs inside your Civis environment, and the
  tool sends nothing out of it.
- **Read-only.** The tool reads your Civis objects but never changes them.
- **No code stored.** For each finding, the tool records the object ID, rule ID,
  file name, and line number. It never stores code, log text, or notebook output.

### Setup guidance for admins

- **Run as the Robot user.** The scanner sees only what its user can see.
- **Use a dedicated results schema.** Choose a schema used only for this tool,
  and grant read access to Civis admins only. The tool never runs `GRANT`
  statements.
- **Share the web app with Civis admins only.** Anyone who can open it sees the
  names, owners, and links of every object the scanning user can see, including
  private objects.
- **Share the scanner job with Civis admins only.** Each scan saves the full
  report as a CSV run output on the job, so anyone who can read the job can
  download it.
- **GitHub token (optional).**
  - Use a fine-grained token with read-only "Contents" permission.
  - Limit it to the repositories your Civis objects use.
  - Give it an expiration date.
  - Attach it to the scanner job only, never to the Service.
- **Retention.** All scan runs are kept by default. To delete old runs
  automatically, set the scanner job's `RETENTION_DAYS` parameter. To delete runs
  by hand, use the SQL in [SECURITY.md](SECURITY.md#keeping-and-deleting-results).
- **Downloads.** "Download CSV" in the web app saves the full report to the
  viewer's computer, outside Civis. Decide whether your admins should use it.
- **Framing check during setup.** The Service setting
  `CIVIS_FRAME_ANCESTORS_REPORT_ONLY=true` turns a wrong framing rule into a
  browser console warning instead of a blank page. Turn it off once the app
  shows correctly inside Civis.

## Setup

Both parts run code from this repository at a **release tag**, never from
`main`. In the steps below, replace `<release tag>` with the newest release
listed on this repository's Releases page, for example `v1.0.0`. Use the same
tag for both parts.

### Before you start

You need:

1. **A Civis admin** to do the setup and to be the first person the app is
   shared with.
2. **The Robot user.** The scanner sees only what the user who runs it can see,
   so it should run as your organization's Robot user (or another service
   account that can read every object you want scanned). The web app's Civis
   API key belongs to the Service's owner, and scans started from the app run
   as that user, so the Robot user should own the Service too.
3. **A dedicated results schema**, created by a database admin. The scanner
   never creates schemas. The user behind the scanner's database credential
   needs to create tables in the schema and to select, insert, and delete rows
   in them (delete is used only by `RETENTION_DAYS`). Grant read access to
   Civis admins only. The scanner creates its five tables on the first run.
4. **A GitHub token (optional).** Without one, git-backed objects are listed as
   "not scanned, review manually." The token is only for reading **your own**
   repositories that your Civis objects use. This tool's own code is public and
   needs no token. If you add one:
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
   | **GitHub Repository URL** | `https://github.com/ExpeditionData/civis-python-version-search.git` |
   | **GitHub Repository Reference** | `<release tag>` |
   | Git credential (label to confirm) | Leave blank. The repository is public. |
   | **Command** | `cd /app && python -m scanner` |
   | **CPU** | `1000` (one core) |
   | **Memory** | `4096` (MB) |
   | Disk Space (label to confirm) | `4` (GB) |

   The image tag is pinned on purpose. Do not leave it blank or set it to
   `latest`. See [Pinned Docker images](#pinned-docker-images) to confirm the
   image has not changed.

4. Click **Set Parameters** and add these parameters, in this order. For each,
   set the Parameter Name and Type (labels to confirm), **Required**, and
   **Default Value** as shown. The **Input Display Name** and **Input Helper
   Text** are up to you.

   | Parameter Name | Type | Required | Default Value | Meaning |
   |---|---|---|---|---|
   | `RESULTS_DB` | Database | Yes | (none) | Database and credential for the result tables. |
   | `RESULTS_SCHEMA` | String | Yes | (none) | The dedicated results schema. Letters, numbers, and underscores only. |
   | `GITHUB` | Custom Credential | No | (none) | Optional GitHub token for your own repositories. |
   | `LOOKBACK_DAYS` | Integer | No | `200` | Read the most recent run log only for objects that ran in this many days (1 to 3,650). |
   | `MAX_CONCURRENCY` | Integer | No | `4` | Worker threads (1 to 16). For each thread above 4, add 512 MB to **Memory**. |
   | `REQUEST_BUDGET` | Integer | No | `500` | Civis API requests per 5 minutes (10 to 1,000). Civis allows 1,000 per user, shared with the web app and any other jobs that run as the Robot user. |
   | `RETENTION_DAYS` | Integer | No | (blank) | Blank or 0 keeps every scan. A number deletes scans older than that many days. The newest scan is never deleted. |
   | `RUN_ID` | String | No | (blank) | Blank starts a new scan. A run ID resumes that scan (see below). |

   Click **Add** after each parameter, then **Save**. The names must match
   exactly; `DATABASE` is reserved by Civis, which is why the database
   parameter is `RESULTS_DB`.

5. Set the parameter values on the script page: choose the database and its
   credential for `RESULTS_DB`, type the schema name for `RESULTS_SCHEMA`, and,
   if you made one, choose the GitHub credential for `GITHUB`. Leave the others
   at their defaults. Save the script.
6. Note the job's ID: it is the number at the end of the script page's address
   (`.../spa/#/scripts/containers/<ID>`).
7. Share the job with Civis admins only. Do not schedule it.

**Who the job runs as.** The scan sees only what the user it runs as can see.
Civis's help describes that user two ways: as the user who runs the script, and
as the user who last updated it. Until this is confirmed, set things up so both
give the same answer:

- Create and edit the scanner job while signed in as the Robot user.
- Start scans with **Run scan** in the web app, which starts them through the
  Service's owner (the Robot user). Do not start the job from its own page.

**Resuming a stopped scan.** If a scan stops partway (for example, it was
cancelled or hit the 5-day limit for container scripts):

1. Sign in as the Robot user if you can. On the scanner job, set `RUN_ID` to
   the run ID from the `RUN run_id=... status=running` line near the top of
   the stopped run's log, and save.
2. Click **Run scan** in the web app. The scan skips the objects it already
   finished.
3. When it finishes, clear `RUN_ID` the same way (signed in as the Robot user)
   and save. The web app starts the job with its saved parameters, so a
   leftover `RUN_ID` would make every later scan try to resume a finished run.

### Create the Service

1. Sign in as the Robot user if you can, so the Robot user owns the Service.
   The app uses its owner's Civis API key for everything it does.
2. From the **Publish** menu, click **Service Deployment**.
3. In the **Docker** section:
   - **Image Name:** `civisanalytics/civis-services-flask`
   - **Tag:** `0.1.0`
4. In the **Git Connection** section:
   - **Repository:** `https://github.com/ExpeditionData/civis-python-version-search.git`
   - **Branch:** `<release tag>` (the same tag as the scanner job)
   - **Path:** leave blank.
5. Under **Environment Variables**, add these key and value pairs, then click
   **Save**. They are not secrets, and anyone who can read the Service can see
   them, so never put a token or password here.

   | Key | Value |
   |---|---|
   | `SCANNER_JOB_ID` | The scanner job's ID from step 6 above. |
   | `RESULTS_DATABASE` | The name of the database chosen in the job's `RESULTS_DB` parameter. |
   | `RESULTS_SCHEMA` | The same schema as the job's `RESULTS_SCHEMA` parameter. |
   | `CIVIS_SERVICE_HEALTH_PATH` | `/healthz` |
   | `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` | `true` for the first deployment only. Change it to `false` once the app shows correctly inside Civis (see below). |
   | `RESULTS_CREDENTIAL_ID` | Optional. A database credential ID for reading results. Leave it out to use the Robot user's default credential. |
   | `CIVIS_PLATFORM_URL` | Optional. Leave it out unless your Civis web interface is not at `https://platform.civisanalytics.com`. |
   | `CIVIS_FRAME_ANCESTORS` | Optional. Leave it out unless Civis shows Services from a different address. |

6. Do not attach any credentials under **Security and Access**, then
   **Credentials**. Credentials attached to a Service become environment
   variables inside the app. The app needs none, and the GitHub token must
   never be attached here.
7. Leave the resources at their defaults with one replica. Under
   **Multischedule**, choose **On Demand**. The app sleeps after an hour
   without viewers and wakes when someone opens it.
8. Click **Start Deployment** and wait for the app to show in the preview.
9. **Share the Service with Civis admins only.** Civis shares a new Service
   with your default group automatically, so remove that group: open the
   Service report, leave full-screen mode, click the Sharing icon, and keep only
   Civis admins.
10. Check the frame rule. Open the app in Chrome, Edge, or Firefox (Safari
    ignores the report-only rule) and open the browser's developer console. If
    the app shows and the console has no "frame-ancestors" warning from the app,
    change `CIVIS_FRAME_ANCESTORS_REPORT_ONLY` to `false` and click **Update
    Deployment**. If there is a warning, note the address it names and set
    `CIVIS_FRAME_ANCESTORS` to include it before you turn report-only off.

### Run the first scan

1. Open the app (**View Report** on the Service).
2. On the Overview page, click **Run scan**. The button is off while a scan is
   queued or running, so only one scan runs at a time.
3. The Scan history page shows progress: Inventory, Code and log scan, and
   Save results. Expect about 20 minutes for 500 objects and about 70 minutes
   for 2,000 at the default request budget. You can close the app; the scan
   keeps running.
4. When the scan finishes, review the Overview, Inventory, and Findings pages.
   Change checklist statuses on the Findings page and click **Save changes**.
   Each change is saved with your Civis user name and the time.
5. **Download CSV** saves the full report to your computer (see
   [Security and privacy](#security-and-privacy)).

If the scan fails, open the scanner job's run log. It contains only progress
counts, object IDs, rule IDs, and error types, so it is safe to share with
whoever supports your setup.

### Update to a new release

1. Read the release notes for the new tag. They say whether a parameter or an
   environment variable was added or changed.
2. Sign in as the Robot user if you can, since changing the tag also updates
   the job (see "Who the job runs as"). On the scanner job, change **GitHub
   Repository Reference** to the new tag and save. The next scan uses it.
3. On the Service, change **Branch** to the same tag, save, and click
   **Update Deployment**.
4. Run a new scan. Earlier scans and checklist statuses are kept.

Always change both parts to the same tag.

### Remove the tool

1. Archive the scanner job: click the file box (Archive) icon at the top right
   of its page. Archive the Service the same way (label to confirm for
   Services).
2. The CSV reports stay attached to the archived job's runs. Keep the archived
   job shared with Civis admins only.
3. Ask a database admin to drop the results schema, for example
   `DROP SCHEMA results_schema CASCADE;` with your schema's name.
4. If you made a GitHub token, delete the Custom Credential in Civis and revoke
   the token in GitHub.

## Pinned Docker images

The tool runs only on these exact images and installs nothing at run time. To
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
  never contacts Civis or GitHub.
- **Local app preview.** `python -m scanner --dry-run --preview --out dry_run_output`,
  then `python -m app.local --results dry_run_output`, and open
  http://127.0.0.1:8050. It shows made-up data only, listens on this machine
  only, and refuses to start if `CIVIS_API_KEY` is set.
- **Secret scanning.** Turn on the secret-scanning hook in each clone:
  `git config core.hooksPath .githooks`.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).

The Expedition Data name and logo are trademarks of Expedition Data LLC. They are not licensed under the Apache License 2.0.
