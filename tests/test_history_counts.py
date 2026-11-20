"""Scan history's counts from two aggregate reads (docs/PLAN.md 17.17). Made-up data only. No Civis calls.

rc13 read every listed scan's whole objects and findings tables and counted in Python. The two statements in
common/schema.py must give the same affected, advisory, and fixed counts. A reference copy of rc13's logic is kept
here, with objects identified by object type and object ID since v0.1.1 (docs/PLAN.md 17.19), and the statements
run on SQLite (``schema=None``) against made-up findings.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from common import sql
from common.schema import FINDINGS, history_fixed_sql, history_impact_sql
from tests.civis_fakes import FakeAPIError, RecordingIO
from tests.test_sql_scope import scope_problems

COLUMNS = FINDINGS.column_names
RULES = ("CV3-ML", "CV3-IO", "PY315-A", "PY315-B")
IMPACTS = ("affected", "advisory", "unknown", "", None)
# Random cases mostly use one type, so objects often share an ID with one of another type.
OBJECT_TYPES = ("python_script", "python_script", "python_script", "notebook", "service", "workflow")


# ---------------------------------------------------------------------------
# rc13's logic (app/views._object_impacts and views.finding_pairs), copied as the reference, with the object
# type added to the key (docs/PLAN.md 17.19)
# ---------------------------------------------------------------------------


def _reference_object_impacts(findings: list[dict]) -> dict:
    impact: dict = {}
    for f in findings:
        key = (f["object_type"], f["object_id"])
        if f.get("impact") == "affected":
            impact[key] = "affected"
        else:
            impact.setdefault(key, "advisory")
    return impact


def _reference_counts(rows: list[dict], run_ids: list[str], pairs: list[tuple[str, str]]) -> dict[str, dict]:
    by_run = {r: [f for f in rows if f["run_id"] == r] for r in run_ids}
    counts = {}
    for run_id, findings in by_run.items():
        impacts = _reference_object_impacts(findings)
        counts[run_id] = {"affected": sum(1 for v in impacts.values() if v == "affected"),
                          "advisory": sum(1 for v in impacts.values() if v == "advisory")}
    for older, newer in pairs:
        old = {(f["object_type"], f["object_id"], f["rule_id"]) for f in by_run[older]}
        new = {(f["object_type"], f["object_id"], f["rule_id"]) for f in by_run[newer]}
        counts[newer]["fixed"] = len(old - new)
    return counts


# ---------------------------------------------------------------------------
# The two statements on SQLite
# ---------------------------------------------------------------------------


def _finding(run_id: str, object_id, rule_id: str, impact, object_type: str = "python_script") -> dict:
    return {"run_id": run_id, "object_id": object_id, "object_type": object_type, "rule_id": rule_id,
            "file": "inline source", "line_number": 1, "impact": impact, "source": "code"}


def _sql_counts(rows: list[dict], run_ids: list[str], pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """The statements' answer, put together the way history_counts_from_rows does (without importing the app)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"CREATE TABLE findings ({', '.join(COLUMNS)})")
        conn.executemany(f"INSERT INTO findings VALUES ({', '.join('?' * len(COLUMNS))})",
                         [[r.get(c) for c in COLUMNS] for r in rows])
        counts = {r: {"affected": 0, "advisory": 0} for r in run_ids}
        for run_id, affected, advisory in conn.execute(history_impact_sql(None, run_ids)).fetchall():
            counts[run_id] = {"affected": int(affected), "advisory": int(advisory)}
        if pairs:
            for run_id, fixed in conn.execute(history_fixed_sql(None, pairs)).fetchall():
                counts[run_id]["fixed"] = int(fixed)
        return counts
    finally:
        conn.close()


def _pairs(run_ids: list[str]) -> list[tuple[str, str]]:
    return list(zip(run_ids, run_ids[1:]))


def _check(rows: list[dict], run_ids: list[str]) -> dict[str, dict]:
    pairs = _pairs(run_ids)
    expected = _reference_counts(rows, run_ids, pairs)
    assert _sql_counts(rows, run_ids, pairs) == expected
    return expected


def test_a_scan_with_no_findings_counts_zero_and_fixes_everything_before_it():
    rows = [_finding("run-a", 1, "CV3-ML", "affected"), _finding("run-a", 2, "CV3-IO", "advisory")]
    counts = _check(rows, ["run-a", "run-b"])
    assert counts["run-b"] == {"affected": 0, "advisory": 0, "fixed": 2}


def test_an_object_with_an_affected_and_an_advisory_finding_is_affected():
    rows = [_finding("run-a", 1, "CV3-ML", "advisory"), _finding("run-a", 1, "CV3-IO", "affected"),
            _finding("run-a", 2, "CV3-IO", "advisory")]
    assert _check(rows, ["run-a"])["run-a"] == {"affected": 1, "advisory": 1}


@pytest.mark.parametrize("impact", ["unknown", "", None])
def test_an_impact_other_than_affected_is_advisory(impact):
    rows = [_finding("run-a", 1, "CV3-ML", impact)]
    assert _check(rows, ["run-a"])["run-a"] == {"affected": 0, "advisory": 1}


def test_duplicate_rows_count_once():
    """A resumed scan can write the same finding twice."""
    rows = [_finding("run-a", 1, "CV3-ML", "affected")] * 3 + [_finding("run-b", 1, "CV3-ML", "affected")] * 2
    counts = _check(rows, ["run-a", "run-b"])
    assert counts["run-a"] == {"affected": 1, "advisory": 0} and counts["run-b"]["fixed"] == 0


def test_an_empty_object_id_is_one_object():
    rows = [_finding("run-a", None, "CV3-ML", "affected"), _finding("run-a", None, "CV3-IO", "advisory"),
            _finding("run-b", None, "CV3-IO", "advisory")]
    counts = _check(rows, ["run-a", "run-b"])
    assert counts["run-a"] == {"affected": 1, "advisory": 0} and counts["run-b"]["fixed"] == 1


def test_a_fixed_finding_and_a_rule_moved_to_another_object():
    rows = [_finding("run-a", 1, "CV3-ML", "affected"), _finding("run-a", 2, "CV3-IO", "advisory"),
            # run-b: object 1's finding is fixed, and CV3-IO moved from object 2 to object 3.
            _finding("run-b", 3, "CV3-IO", "advisory")]
    counts = _check(rows, ["run-a", "run-b"])
    assert counts["run-b"] == {"affected": 0, "advisory": 1, "fixed": 2}


def test_a_script_and_a_notebook_with_the_same_id_are_two_objects():
    """Civis numbers jobs apart from notebooks, Services, and workflows (docs/PLAN.md 17.19)."""
    rows = [_finding("run-a", 7, "CV3-ML", "affected"), _finding("run-a", 7, "CV3-ML", "advisory", "notebook")]
    assert _check(rows, ["run-a"])["run-a"] == {"affected": 1, "advisory": 1}


def test_a_service_and_a_workflow_with_a_jobs_id_are_three_objects():
    rows = [_finding("run-a", 7, "CV3-IO", "advisory"), _finding("run-a", 7, "CV3-IO", "advisory", "service"),
            _finding("run-a", 7, "PY315-A", "affected", "workflow")]
    assert _check(rows, ["run-a"])["run-a"] == {"affected": 1, "advisory": 2}


def test_a_fix_on_one_of_two_objects_with_the_same_id_counts_once():
    rows = [_finding("run-a", 7, "CV3-ML", "affected"), _finding("run-a", 7, "CV3-ML", "affected", "notebook"),
            # run-b: the script's finding is fixed; the notebook's is not.
            _finding("run-b", 7, "CV3-ML", "affected", "notebook")]
    counts = _check(rows, ["run-a", "run-b"])
    assert counts["run-b"] == {"affected": 1, "advisory": 0, "fixed": 1}


def test_a_finding_that_moves_to_another_type_with_the_same_id_is_fixed():
    rows = [_finding("run-a", 7, "CV3-ML", "affected"), _finding("run-b", 7, "CV3-ML", "affected", "service")]
    counts = _check(rows, ["run-a", "run-b"])
    assert counts["run-b"] == {"affected": 1, "advisory": 0, "fixed": 1}


def test_run_ids_not_asked_for_are_left_out():
    rows = [_finding("run-a", 1, "CV3-ML", "affected"), _finding("run-z", 1, "CV3-ML", "affected")]
    assert set(_check(rows, ["run-a"])) == {"run-a"}


def test_random_cases_match_the_reference():
    rng = random.Random(20261120)
    for _ in range(200):
        run_ids = [f"run-{n}" for n in range(rng.randint(1, 10))]
        rows = []
        for run_id in run_ids:
            for _ in range(rng.choice((0, 0, 1, 3, 8, 15))):
                rows.append(_finding(run_id, rng.choice((None, 1, 2, 3, 4, 5)), rng.choice(RULES), rng.choice(IMPACTS),
                                     rng.choice(OBJECT_TYPES)))
        if rows and rng.random() < 0.3:
            rows += rng.sample(rows, k=min(len(rows), 3))  # Duplicate rows.
        rows.append(_finding("run-other", 1, "CV3-ML", "affected"))  # A scan not asked for.
        _check(rows, run_ids)


# ---------------------------------------------------------------------------
# The SQL text
# ---------------------------------------------------------------------------

TEN = [f"run-20261{n:03d}T120000Z-abc{n:03d}" for n in range(1, 11)]


def test_both_statements_stay_in_the_results_schema():
    assert scope_problems(history_impact_sql("results", TEN), "results") == []
    assert scope_problems(history_fixed_sql("results", _pairs(TEN)), "results") == []
    assert history_fixed_sql("results", _pairs(TEN)).count(" UNION ALL ") == 8


def test_the_statements_as_sent():
    assert history_impact_sql("results", ["run-1", "run-2"]) == (
        "SELECT run_id, SUM(affected) AS affected_objects, SUM(1 - affected) AS advisory_objects FROM "
        "(SELECT run_id, object_type, object_id, MAX(CASE WHEN impact = 'affected' THEN 1 ELSE 0 END) AS affected "
        "FROM results.findings WHERE run_id IN ('run-1', 'run-2') GROUP BY run_id, object_type, object_id) "
        "AS per_object GROUP BY run_id"
    )
    assert history_fixed_sql(None, [("run-1", "run-2"), ("run-2", "run-3")]) == (
        "SELECT 'run-2' AS run_id, COUNT(1) AS fixed_pairs FROM (SELECT object_type, object_id, rule_id FROM findings "
        "WHERE run_id = 'run-1' EXCEPT SELECT object_type, object_id, rule_id FROM findings WHERE run_id = 'run-2') "
        "AS fixed_1"
        " UNION ALL "
        "SELECT 'run-3' AS run_id, COUNT(1) AS fixed_pairs FROM (SELECT object_type, object_id, rule_id FROM findings "
        "WHERE run_id = 'run-2' EXCEPT SELECT object_type, object_id, rule_id FROM findings WHERE run_id = 'run-3') "
        "AS fixed_2"
    )
    for statement in (history_impact_sql("results", TEN), history_fixed_sql("results", _pairs(TEN))):
        assert "COUNT(*)" not in statement


@pytest.mark.parametrize("build", [
    lambda: history_impact_sql("results", ["run-1", "run'; DROP TABLE x; --"]),
    lambda: history_fixed_sql("results", [("run-1", "run 2")]),
    lambda: history_impact_sql("results; DROP", ["run-1"]),
    lambda: history_fixed_sql("bad schema", [("run-1", "run-2")]),
    lambda: history_impact_sql("results", []),
    lambda: history_fixed_sql("results", []),
])
def test_unsafe_values_and_empty_lists_are_refused(build):
    with pytest.raises(sql.UnsafeValueError):
        build()


# ---------------------------------------------------------------------------
# The app's three implementations (Flask needed: the scanner image skips these)
# ---------------------------------------------------------------------------


def _app_module(name: str):
    pytest.importorskip("flask", reason="Flask is not in this image (the scanner image)")
    import importlib

    return importlib.import_module(name)


def test_history_counts_from_rows():
    from_rows = _app_module("app.backends.base").history_counts_from_rows
    text = from_rows(["run-a", "run-b"], [{"run_id": "run-a", "affected_objects": "3", "advisory_objects": "4"}],
                     [{"run_id": "run-b", "fixed_pairs": "2"}])
    assert text == {"run-a": {"affected": 3, "advisory": 4}, "run-b": {"affected": 0, "advisory": 0, "fixed": 2}}
    numbers = from_rows(["run-a"], [{"run_id": "run-a", "affected_objects": 1, "advisory_objects": 0}], [])
    assert numbers == {"run-a": {"affected": 1, "advisory": 0}}
    unknown = from_rows(["run-a"], [{"run_id": "run-x", "affected_objects": "9", "advisory_objects": "9"}],
                        [{"run_id": "run-x", "fixed_pairs": "9"}])
    assert unknown == {"run-a": {"affected": 0, "advisory": 0}}
    with pytest.raises(KeyError):
        from_rows(["run-a"], [{"run_id": "run-a", "affected_objects": "1"}], [])


def test_civis_results_reads():
    backend = _app_module("app.backends.civis_backend")
    base = _app_module("app.backends.base")
    io = RecordingIO()
    store = backend.CivisResults(object(), io, 31, "results", 41)
    assert store.history_counts([], []) == {} and io.reads == []
    assert store.history_counts(["run-a"], []) == {"run-a": {"affected": 0, "advisory": 0}}
    assert len(io.small_reads) == 1 and not io.full_reads and "affected_objects" in io.small_reads[0]
    failing = RecordingIO(fail={"affected_objects": FakeAPIError(500)})
    with pytest.raises(base.ResultsUnavailable) as raised:
        backend.CivisResults(object(), failing, 31, "results", 41).history_counts(["run-a"], [])
    assert raised.value.kind == "error"


class _Store:
    def __init__(self, gate: threading.Event | None = None, started: threading.Event | None = None):
        self.calls = 0
        self.gate, self.started = gate, started

    def history_counts(self, run_ids, pairs):
        self.calls += 1
        if self.started:
            self.started.set()
        if self.gate:
            self.gate.wait(5)
        return {r: {"affected": 1, "advisory": 0} for r in run_ids}

    def save_checklist(self, *args):
        pass


def test_cached_history_counts_concurrent_first_loads_start_one_read():
    cache_module = _app_module("app.backends.cache")
    started, gate = threading.Event(), threading.Event()
    store = _Store(gate, started)
    cache = cache_module.CachedResults(lambda: store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cache.history_counts, ["run-a", "run-b"], [("run-a", "run-b")]) for _ in range(4)]
        assert started.wait(5)
        gate.set()
        results = [f.result(5) for f in futures]
    assert store.calls == 1 and cache.reads == 1
    assert all(r is results[0] for r in results)


def test_cached_history_counts_keep_drop_and_clear():
    cache_module = _app_module("app.backends.cache")
    store = _Store()
    clock = {"now": 0.0}
    cache = cache_module.CachedResults(lambda: store, clock=lambda: clock["now"])
    cache.history_counts(["run-a"], [])
    clock["now"] = cache_module.SHORT_SECONDS * 100  # No expiry.
    cache.history_counts(["run-a"], [])
    assert store.calls == 1
    cache.history_counts(["run-b", "run-a"], [("run-a", "run-b")])  # Another list of scans: read again.
    assert store.calls == 2
    cache.save_checklist("run-a", [], "x", datetime(2026, 11, 20))  # A checklist save keeps it.
    cache.history_counts(["run-a"], [])
    assert store.calls == 2
    assert cache_module.MAX_HISTORY_COUNTS == 4
    for n in range(4):  # Four more keys push out the least recently used ("run-b", "run-a" and "run-a").
        cache.history_counts([f"run-{n}"], [])
    assert store.calls == 6
    cache.history_counts(["run-a"], [])
    assert store.calls == 7
    cache.history_counts(["run-3"], [])  # Still kept.
    assert store.calls == 7
    cache.clear()
    cache.history_counts(["run-3"], [])
    assert store.calls == 8
