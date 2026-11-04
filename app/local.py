"""Local preview of the web app, on made-up data only.

    python -m scanner --dry-run --preview --out dry_run_output
    python -m app.local --results dry_run_output

Then open http://127.0.0.1:8050 in a browser.

Safety rules (SECURITY.md, "How we build and test it"):

- It reads only the dry-run preview files. It never contacts Civis.
- It refuses to start if CIVIS_API_KEY is set, so it cannot be pointed at a
  real Civis instance by accident.
- It listens on 127.0.0.1 only, so other machines cannot reach it.
- Flask debug mode stays off.

``--state`` shows the app's empty and error states with the same data:
normal, no-scan, first-scan-running, failed, tables-failed, schema-missing,
no-job-id, job-unreadable, database-unrecognized, schema-refused,
tables-missing, setup-failures, not-scanner-job, and schema-shared.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

HOST = "127.0.0.1"


def build(results: Path, state: str = "normal"):
    from app import create_app
    from app.services import local_services

    if os.environ.get("CIVIS_API_KEY"):
        raise SystemExit("Local preview refuses to start while CIVIS_API_KEY is set. Unset it and try again.")
    if not (results / "results.sqlite").exists():
        raise SystemExit(f"No preview data in {results}. Run: python -m scanner --dry-run --preview --out {results}")
    return create_app({"SECRET_KEY": secrets.token_hex(32), "SERVICES": local_services(results, state)})


def main(argv: list[str] | None = None) -> int:
    from app.backends.local_backend import STATES

    parser = argparse.ArgumentParser(prog="python -m app.local", description="Local preview on made-up data")
    parser.add_argument("--results", type=Path, default=Path("dry_run_output"), help="folder written by the dry run")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--state", choices=STATES, default="normal", help="show an empty or error state")
    args = parser.parse_args(argv)
    app = build(args.results, args.state)
    print(f"Local preview (made-up data): http://{HOST}:{args.port}  Press Ctrl+C to stop.")
    app.run(host=HOST, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
