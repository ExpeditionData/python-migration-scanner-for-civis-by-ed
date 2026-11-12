"""Scanner entry point.

Inside Civis (the container script's command):

    cd /app && python -m scanner

Dry run on this machine, against the synthetic instance only:

    python -m scanner --dry-run --out dry_run_output
    python -m scanner --dry-run --preview --out dry_run_output   (seeds the local app preview)

A live scan runs only inside Civis. It refuses to start unless Civis has set
CIVIS_JOB_ID and CIVIS_RUN_ID, so it can never be pointed at a real Civis
instance from a developer's machine (SECURITY.md, "How we build and test it").

Exit codes: 0 succeeded, 3 the tables could not be written (the CSV report was
still saved), 2 configuration problem (including a results schema that holds
other tables or views, when nothing is written at all), 1 any other failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.redact import install_redaction
from scanner import scan_log
from scanner.config import ConfigError, ScanConfig
from scanner.output import SchemaNotDedicated
from scanner.runner import AlreadyFinished, RunResult, ScanFailed, run_scan

EXIT_CODES = {"succeeded": 0, "tables_failed": 3, "failed": 1}
DRY_RUN_SCHEMA = "dry_run_results"


def _configure_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    install_redaction(logging.getLogger())
    scan_log.configure()


def dry_run(out: Path, fixture: Path | None = None, now: datetime | None = None, run_id: str | None = None,
            checkpoint_objects: int = 500, stop_after: int | None = None, data: dict | None = None,
            civis_job_id: int | None = None, civis_run_id: int | None = None,
            clock: Callable[[], datetime] | None = None,
            github_token: str | None = "synthetic-github-token", file_clock: Any = None) -> RunResult:
    """Run the whole pipeline against the synthetic instance. No network calls.

    ``data`` replaces the synthetic instance file (used by the preview seeder),
    ``clock`` replaces the fixed ``now`` so runs can have a duration, and
    ``github_token=None`` runs without a GitHub token, the normal setup, and
    ``file_clock`` (a scanner.synthetic.links.SyntheticClock) is the clock the
    synthetic notebook links are issued and checked on.
    """
    from scanner.output import LocalRunOutputs, SqliteSink
    from scanner.synthetic import FIXTURES, FakeClient, SyntheticSession

    if data is None:
        data = json.loads((fixture or FIXTURES / "synthetic_instance.json").read_text(encoding="utf-8"))
    client = FakeClient(data)
    session = SyntheticSession(data)
    # One made-up clock for the notebook links the client issues and the file host checks
    # (scanner/synthetic/links.py). It moves only when a test advances it.
    session.clock = client.clock = file_clock or client.clock
    out.mkdir(parents=True, exist_ok=True)
    sink = SqliteSink(out / "results.sqlite", DRY_RUN_SCHEMA)
    if stop_after is not None:
        sink = _StopAfter(sink, stop_after)
    config = ScanConfig(
        database="synthetic", schema=DRY_RUN_SCHEMA, github_token=github_token,
        run_id=run_id, request_budget=1000, civis_job_id=civis_job_id, civis_run_id=civis_run_id,
    )
    fixed_now = now or datetime.now(timezone.utc)
    try:
        return run_scan(
            config, client=client, http_session=session, sink=sink, outputs=LocalRunOutputs(out),
            now=clock or (lambda: fixed_now), checkpoint_objects=checkpoint_objects,
        )
    finally:
        (sink.inner if isinstance(sink, _StopAfter) else sink).close()


class SimulatedStop(BaseException):
    """Stands in for a container being stopped partway (used by tests and --stop-after)."""


class _StopAfter:
    """Wraps a sink and stops the run once ``limit`` objects have been written."""

    def __init__(self, inner, limit: int):
        self.inner = inner
        self.limit = limit
        self.written = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def append(self, table, run_id, rows):
        self.inner.append(table, run_id, rows)
        if table.name == "objects":
            self.written += len(rows)
            if self.written >= self.limit:
                raise SimulatedStop()


def live_run() -> RunResult:
    config = ScanConfig.from_env()
    if not (config.civis_job_id and config.civis_run_id):
        raise ConfigError("A live scan runs only inside Civis, as a container script. Use --dry-run on a local machine.")
    import civis  # In the scanner image.

    from scanner.output import CivisRunOutputs, CivisSink
    from scanner.ratelimit import civis_retrying

    # A shorter retries policy than civis-python's default (docs/PLAN.md 17.5): civis holds one lock for
    # each whole request, retries included, so a long retry blocks every other API call in the scan. A
    # request that gets no response still holds that lock with no time limit (civis sets no timeout).
    client = civis.APIClient(retries=civis_retrying())
    sink = CivisSink(client, civis.io, config.database, config.schema, config.credential_id)
    outputs = CivisRunOutputs(client, civis.io, config.civis_job_id, config.civis_run_id)
    return run_scan(config, client=client, http_session=None, sink=sink, outputs=outputs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scanner", description="Python Migration Scanner for Civis Platform: scanner job")
    parser.add_argument("--dry-run", action="store_true", help="scan the synthetic instance and write to a local folder")
    parser.add_argument("--out", type=Path, default=Path("dry_run_output"), help="dry run output folder")
    parser.add_argument("--run-id", help="dry run: resume this run ID")
    parser.add_argument("--stop-after", type=int, help="dry run: stop after this many objects are written (tests resume)")
    parser.add_argument("--preview", action="store_true",
                        help="dry run: seed a local web app preview with several runs of made-up data")
    args = parser.parse_args(argv)
    _configure_logging()
    try:
        if args.dry_run and args.preview:
            from scanner.synthetic.preview import build_preview

            try:
                build_preview(args.out)
            except PermissionError:
                print("The preview data is in use, most likely by a running local preview. Stop it with Ctrl+C, "
                      "then run this command again.", file=sys.stderr)
                return 1
            print(f"Preview data written to {args.out}. Open it with: python -m app.local --results {args.out}")
            return 0
        if args.dry_run:
            result = dry_run(args.out, run_id=args.run_id, stop_after=args.stop_after,
                             checkpoint_objects=5 if args.stop_after else 500)
        else:
            result = live_run()
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except SchemaNotDedicated as exc:
        scan_log.schema_not_dedicated(exc.count)  # One FATAL line; nothing was written.
        return 2
    except AlreadyFinished:
        print("That run ID already finished. Start a new scan without RUN_ID.", file=sys.stderr)
        return 2
    except SimulatedStop:
        print("Stopped on purpose after the requested number of objects. Rerun with --run-id to resume.", file=sys.stderr)
        return 1
    except ScanFailed:
        return 1  # run_scan logged the FATAL line with its step, and recorded the run as failed if it could.
    except Exception as exc:
        scan_log.fatal(exc, "setup")  # Before run_scan started: creating the client or the sink.
        return 1
    return EXIT_CODES.get(result.status, 1)


if __name__ == "__main__":
    sys.exit(main())
