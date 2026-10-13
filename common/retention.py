"""Deleting old scan runs from all five result tables.

The scanner job has a RETENTION_DAYS parameter. Blank or 0 keeps every run
(the default). A positive number deletes runs that started more than that many
days ago, but never the most recent run.

Checklist history is handled with care: rows written while an old run was in
view are deleted only when a newer row exists for the same object and rule, so
the current status a person set is never lost.

All names are validated as SQL identifiers and every run ID is validated and
escaped (see common/sql.py). Nothing here grants or changes permissions.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from common import sql
from common.schema import CHECKLIST_STATUS, COVERAGE, FINDINGS, OBJECTS, SCAN_RUNS

MAX_RUN_IDS_PER_STATEMENT = 500


def expired_run_ids(
    runs: list[tuple[str, datetime]],
    retention_days: int | None,
    now: datetime,
) -> list[str]:
    """Run IDs to delete. ``runs`` holds (run_id, started_at) pairs."""
    if not retention_days:
        return []
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 0:
        raise ValueError("retention_days must be a non-negative integer")
    if not runs:
        return []
    newest = max(runs, key=lambda r: r[1])[0]
    cutoff = now - timedelta(days=retention_days)
    return sorted(run_id for run_id, started in runs if started < cutoff and run_id != newest)


def delete_runs_sql(schema: str, run_ids: list[str]) -> list[str]:
    """DELETE statements that remove the given runs from all five tables.

    Run them in the order returned. ``scan_runs`` is last, so a partial failure
    leaves the run listed and the deletion can simply be retried.
    """
    if not run_ids:
        return []
    if len(run_ids) > MAX_RUN_IDS_PER_STATEMENT:
        raise sql.UnsafeValueError("Too many run IDs in one deletion. Delete in batches.")
    id_list = ", ".join(sql.run_id(r) for r in run_ids)
    statements = []
    for table in (FINDINGS, COVERAGE, OBJECTS):
        statements.append(f"DELETE FROM {sql.qualified_table(schema, table.name)} WHERE run_id IN ({id_list})")

    checklist = sql.qualified_table(schema, CHECKLIST_STATUS.name)
    statements.append(
        f"DELETE FROM {checklist} USING {checklist} AS newer "
        f"WHERE {checklist}.run_id IN ({id_list}) "
        f"AND newer.object_id = {checklist}.object_id "
        f"AND newer.rule_id = {checklist}.rule_id "
        f"AND newer.changed_at > {checklist}.changed_at"
    )
    statements.append(f"DELETE FROM {sql.qualified_table(schema, SCAN_RUNS.name)} WHERE run_id IN ({id_list})")
    return statements
