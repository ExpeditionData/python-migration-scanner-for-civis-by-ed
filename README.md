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

This project is in development. Step-by-step setup instructions, with
screenshots, will be added before the first release.

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

## Pinned Docker images

The tool runs only on these exact images and installs nothing at run time. To
confirm an image has not changed, compare its digest with the one below, for
example with `docker pull <image>@<digest>`.

| Part | Image and tag | Digest (linux/amd64) | Pushed |
|---|---|---|---|
| Scanner job | `civisanalytics/datascience-python:9.1.1` | `sha256:d6903fdcf6b13801417621ad7522c09a16e1d2c01d11eb398f75ad0f73d974ef` | September 17, 2026 |
| Web app Service | `civisanalytics/civis-services-flask:0.1.0` | `sha256:e8d7a631ab46598caa6735e2dd657763c95eb5c76af7a57510f3d2370f02a20f` | August 6, 2025 |

The digests were read from Docker Hub on October 13, 2026.

## For developers

- **Setup and test commands:** see [CLAUDE.md](CLAUDE.md).
- **Design and decisions:** see [docs/PLAN.md](docs/PLAN.md).
- **Unit tests** use synthetic data only and make no Civis or network calls.
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
