"""Checkpoints: write finished objects to the tables every 500 objects or 10 minutes.

Each checkpoint writes findings, then coverage rows, then object rows. An
object row is the "done" marker: a rerun with the same run ID skips every
object that already has a row in ``objects`` for that run.

If a run stops between writing an object's findings and its object row, the
rerun scans that object again and its findings are written twice. That is
safer than losing them; readers drop exact duplicate finding rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from common.schema import COVERAGE, FINDINGS, OBJECTS
from scanner import scan_log
from scanner.output import ResultSink
from scanner.process import ObjectResult

EVERY_OBJECTS = 500
EVERY_SECONDS = 600.0


def coverage_object_rows(result: ObjectResult) -> list[dict]:
    return [
        {"row_kind": "object", "object_type": str(c.object_type), "object_id": c.object_id, "reason": c.reason}
        for c in result.coverage
    ]


class Checkpointer:
    def __init__(
        self,
        sink: ResultSink | None,
        run_id: str,
        every_objects: int = EVERY_OBJECTS,
        every_seconds: float = EVERY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.sink = sink
        self.run_id = run_id
        self.every_objects = every_objects
        self.every_seconds = every_seconds
        self.clock = clock
        self.pending: list[ObjectResult] = []
        self.last_flush = clock()
        self.written = 0
        self.flushes = 0

    def add(self, result: ObjectResult) -> None:
        self.pending.append(result)
        if len(self.pending) >= self.every_objects or self.clock() - self.last_flush >= self.every_seconds:
            self.flush()

    def flush(self) -> None:
        """Raises TableWriteError if a table cannot be written; the caller decides what to do."""
        batch, self.pending = self.pending, []
        self.last_flush = self.clock()
        if not batch or self.sink is None:
            return
        self.sink.append(FINDINGS, self.run_id, [f.to_row() for r in batch for f in r.findings])
        self.sink.append(COVERAGE, self.run_id, [row for r in batch for row in coverage_object_rows(r)])
        self.sink.append(OBJECTS, self.run_id, [r.obj.to_row() for r in batch])
        self.written += len(batch)
        self.flushes += 1
        scan_log.checkpoint(self.written)
