# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project summary

Civis Python Version Search is a Flask web app plus a scanner job, both of which
run inside Civis Platform. The scanner job collects information about the Python
versions used across Civis Platform objects, and the web app lets users search
and review those results. The code is licensed under Apache 2.0 by Expedition
Data LLC and is written so the repository could be made public at any time.

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

_To be filled in as the app is built._

- Flask web app: TBD
- Scanner job: TBD
- Data storage: TBD
- Deployment in Civis Platform: TBD

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
