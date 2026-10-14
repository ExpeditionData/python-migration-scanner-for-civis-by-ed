"""Caching around Results, because each read in Civis starts a query run.

- A finished run's objects, findings, and coverage never change, so they are
  kept until the cache is full (a few runs).
- The scan run list, the checklist, and the schema check are kept for a short
  time. Saving checklist changes clears the checklist cache at once.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from typing import Any

SHORT_SECONDS = 20.0
MAX_RUN_TABLES = 12


class CachedResults:
    """``provider`` returns the backend to use for this request (a new Civis client each time)."""

    def __init__(self, provider: Callable[[], Any], clock: Callable[[], float] = time.monotonic):
        self.provider = provider
        self.clock = clock
        self._lock = threading.Lock()
        self._short: dict[str, tuple[float, Any]] = {}
        self._tables: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
        self.reads = 0

    def _short_lived(self, key: str, load: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._short.get(key)
            if hit and self.clock() - hit[0] < SHORT_SECONDS:
                return hit[1]
        value = load()
        self.reads += 1
        with self._lock:
            self._short[key] = (self.clock(), value)
        return value

    def check(self) -> str:
        return self._short_lived("check", lambda: self.provider().check())

    def scan_runs(self) -> list[dict]:
        return self._short_lived("scan_runs", lambda: self.provider().scan_runs())

    def checklist(self) -> list[dict]:
        return self._short_lived("checklist", lambda: self.provider().checklist())

    def rows(self, table: str, run_id: str, finished: bool = True) -> list[dict]:
        key = (table, run_id)
        with self._lock:
            if key in self._tables:
                self._tables.move_to_end(key)
                return self._tables[key]
        value = self.provider().rows(table, run_id)
        self.reads += 1
        if finished:
            with self._lock:
                self._tables[key] = value
                while len(self._tables) > MAX_RUN_TABLES:
                    self._tables.popitem(last=False)
        return value

    def save_checklist(self, run_id: str, entries: list[tuple[int, str, str]], changed_by: str, when: datetime) -> None:
        self.provider().save_checklist(run_id, entries, changed_by, when)
        with self._lock:
            self._short.pop("checklist", None)

    def clear(self) -> None:
        with self._lock:
            self._short.clear()
            self._tables.clear()
