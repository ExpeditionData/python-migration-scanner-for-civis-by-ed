# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

Civis Python Version Search is a free, open-source tool that Civis Platform
customers run inside their own Civis environment. It finds scripts, notebooks,
workflows, Services, and container scripts affected by the February 2027 Civis
default image update (Python 3.14 to 3.15, civis-python v2 to v3). A scanner
job (a Civis container script) inventories objects, reads their code and recent
logs, and writes findings to a schema the user chooses. A Flask web app (a Civis
Service) launches scans, shows progress and results, and lets the team track a
fix checklist. The tool is read-only on every Civis object. The code is licensed
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

## Git workflow

- Default branch is `main`. Remote is `origin`
  (`ExpeditionData/civis-python-version-search`, private).
- Commit after each completed step, with a clear message that says what changed
  and why.
- Before every commit, review the staged files (`git diff --cached --name-only`
  and `git diff --cached`) for secrets, client details, and anything from
  `Context/`. Stop and tell the user if anything looks unsafe.
- Push to `main` after each commit.

## Writing style for docs and UI text

- American English.
- Month-first dates, for example "October 9, 2026" or "10/09/2026".
- No em dashes. Use commas, periods, colons, or parentheses instead.
- Plain, direct language. Short sentences. No marketing tone.

## Architecture

The approved design is in [docs/PLAN.md](docs/PLAN.md) (approved October 9,
2026). Where the plan and older notes differ, the plan wins. Summary:

- **Three packages, one repo.** `scanner/` (container script, `python -m scanner`),
  `app/` (Flask Service, `gunicorn "app:create_app()"`), and `common/` (shared
  rules, models, table schemas, URL builders, disclaimer). Civis clones the repo
  to `/app` for both objects.
- **Scanner job.** Runs on `civisanalytics/datascience-python:9.1.1` (Python
  3.14.6, civis 2.10.0). Installs nothing at run time. Inventories Python scripts,
  container scripts, custom scripts, notebooks, Services, and workflows; reads
  code (inline, notebook file, GitHub); runs AST rules with regex fallback; reads
  the most recent run log; writes results to the user's schema plus a CSV run
  output. Uses the `get_*` civis method names so it also runs on civis v3.
- **Web app.** Runs on `civisanalytics/civis-services-flask:0.1.0` (Python
  3.12.11, Flask 3.1.1, gunicorn 22.0.0, civis 2.7.1) with no installs at start.
  It only runs the scanner job the admin created (`SCANNER_JOB_ID`), using the
  job's saved parameters. It never creates or edits scripts. Shared with Civis
  admins only.
- **Connection.** No direct link between app and scanner. The app starts runs and
  reads status and `PROGRESS` log lines through the Civis API, and reads results
  from the tables. Checklist status is append-only.
- **Data storage.** Five tables in the user's schema: `scan_runs`, `objects`,
  `findings`, `coverage`, `checklist_status`. Every row carries a run ID.
- **Rules.** YAML files in `common/rules/` (`civis_v3.yaml`, `python315.yaml`).
  Findings carry file, line number, and rule ID only.

Constraints to keep in mind when writing code:

- `app/` and `common/` may only call civis methods that exist in civis 2.7.1. A
  unit test enforces this.
- `common/` must run on Python 3.12 and 3.14.
- Never store, log, or output code snippets. Fetched code stays in memory or a
  temp directory deleted at the end of the run.
- Front-end libraries are vendored into `app/static/`. No CDN links.
- Every value in a SQL insert is validated and escaped, including request headers.
- Unit tests use synthetic fixtures only. Never record fixtures from a real Civis
  instance, and never commit real object names, user names, or code.

## Setup commands

_To be filled in._

```
# Example placeholders
# python -m venv .venv
# .venv\Scripts\activate
# pip install -r requirements.txt
# copy .env.example .env
```

## Testing commands

_To be filled in._

```
# Example placeholder
# pytest
```

## Keeping docs current

Update this file, `README.md`, and any other docs in the repo as the project
changes. When architecture, setup, or testing steps change, update the matching
section above in the same commit.
