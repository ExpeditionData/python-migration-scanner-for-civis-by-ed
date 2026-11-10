"""Writing results: the five tables, the CSV report, and the run output.

Two sinks share one interface:

- ``CivisSink`` writes to the client's schema through civis-python (inside Civis).
- ``SqliteSink`` writes to a local SQLite file, for dry runs on synthetic data.

Rules (SECURITY.md and docs/PLAN.md):

- Before anything is written, the schema must hold nothing but this tool's five
  tables (``other_objects``), or the run stops (scanner/runner.py).
- Tables are created if missing (CREATE TABLE IF NOT EXISTS). The schema itself
  is never created, nothing here grants permissions or drops tables, and DELETE
  runs only in ``delete_runs``, the documented retention cleanup.
- Every schema and table name is validated as an SQL identifier. Every value
  in SQL text is validated and escaped; row data is sent as data, not SQL.
- Rows are append-only by run ID. Text values are cut to fit their column.
- The CSV report guards against spreadsheet formula injection, because object
  names and file paths are user-controlled.
"""

from __future__ import annotations

import csv
import io
import numbers
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from common import retention, sql
from common.civis_errors import is_empty_result
from common.disclaimer import DISCLAIMER, NON_AFFILIATION
from common.rule_loader import RuleSet
from common.schema import (
    CHECKLIST_STATUS, COVERAGE, FINDINGS, OBJECTS, SCAN_RUNS, TABLES, Table, other_object_count, schema_objects_sql,
)

_VARCHAR = re.compile(r"VARCHAR\((\d+)\)")


class SchemaNotDedicated(Exception):
    """The results schema holds tables or views that are not this tool's. ``count`` is how many; no names."""

    def __init__(self, count: int):
        super().__init__("The results schema holds other tables or views.")
        self.count = count


class TableWriteError(Exception):
    """A table could not be written. ``table`` names it; the cause is chained."""

    def __init__(self, table: str):
        super().__init__(f"Writing the {table} table failed.")
        self.table = table


def fit(value: Any, sql_type: str) -> Any:
    """Cut text to the column's VARCHAR length in UTF-8 bytes (Redshift counts bytes)."""
    match = _VARCHAR.fullmatch(sql_type)
    if not match or not isinstance(value, str):
        return value
    limit = int(match.group(1))
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def table_rows(table: Table, run_id: str, rows: list[dict]) -> list[tuple]:
    out = []
    for row in rows:
        values = {**row, "run_id": run_id}
        out.append(tuple(fit(values.get(c.name), c.sql_type) for c in table.columns))
    return out


WHOLE_NUMBER_TYPES = frozenset({"INTEGER", "BIGINT"})
_DIGITS = re.compile(r"-?\d+")


class ColumnValueError(ValueError):
    """A value does not fit its column's type. Names the table, column, and type, never the value."""

    def __init__(self, table: str, column: str, sql_type: str):
        super().__init__(f"A value in {table}.{column} is not a valid {sql_type}.")
        self.table = table
        self.column = column


def _is_bool(value: Any) -> bool:
    """A Python or numpy boolean."""
    return isinstance(value, bool) or type(value).__name__ in ("bool", "bool_")


def whole_number(value: Any) -> int | None:
    """An INTEGER or BIGINT cell: None, or a whole number as int. Raises ValueError for a bool (a bool is an int
    in Python, but never a valid ID or count), a number that is not whole, NaN, or anything else."""
    if value is None:
        return None
    if _is_bool(value):
        raise ValueError("bool")
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError("not whole")
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        return int(value.strip())
    raise ValueError("not a whole number")


def boolean_text(value: Any) -> str | None:
    """A BOOLEAN cell as the text Redshift's COPY reads: "true", "false", or None (an empty cell, NULL)."""
    if value is None:
        return None
    if _is_bool(value):
        return "true" if value else "false"
    raise ValueError("not a bool")


def table_frame(table: Table, run_id: str, rows: list[dict]) -> Any:
    """The pandas DataFrame CivisSink writes for ``rows``, typed from the table's columns (docs/PLAN.md 17.7).

    civis.io.dataframe_to_civis writes the frame with ``df.to_csv(index=False)`` and loads that CSV into the
    table. Built naively, a whole-number column that mixes numbers and None becomes float64, which to_csv writes
    as 12345.0, and the database load rejects it. So:

    - INTEGER and BIGINT columns use pandas' nullable Int64 dtype (whole numbers as digits, None as an empty cell).
    - BOOLEAN columns hold the text "true" or "false" (None as an empty cell), not Python's True and False.
    - TIMESTAMP and VARCHAR columns are built as before.

    A value that does not fit raises ColumnValueError before anything is written.
    """
    import pandas  # In the scanner image. Imported here so the module loads without it.

    values = table_rows(table, run_id, rows)
    data: dict[str, Any] = {}
    for index, column in enumerate(table.columns):
        cells = [row[index] for row in values]
        try:
            if column.sql_type in WHOLE_NUMBER_TYPES:
                data[column.name] = pandas.array([whole_number(c) for c in cells], dtype="Int64")
            elif column.sql_type == "BOOLEAN":
                data[column.name] = pandas.Series([boolean_text(c) for c in cells], dtype=object)
            else:
                data[column.name] = cells
        except (ValueError, TypeError, OverflowError) as exc:
            raise ColumnValueError(table.name, column.name, column.sql_type) from exc
    return pandas.DataFrame(data, columns=table.column_names)


class ResultSink(Protocol):
    def other_objects(self) -> int: ...  # Tables and views in the schema that are not this tool's.
    def ensure_tables(self) -> None: ...
    def append(self, table: Table, run_id: str, rows: list[dict]) -> None: ...
    def finished_object_ids(self, run_id: str) -> set[int]: ...
    def run_rows(self, run_id: str) -> dict[str, list[dict]]: ...
    def latest_status(self, run_id: str) -> str | None: ...
    def checklist_pairs(self) -> set[tuple[int, str]]: ...
    def run_starts(self) -> list[tuple[str, datetime]]: ...
    def delete_runs(self, run_ids: list[str]) -> None: ...


# ---------------------------------------------------------------------------
# SQLite, for dry runs
# ---------------------------------------------------------------------------


class SqliteSink:
    """Local SQLite file attached under the schema name, so table names match Civis."""

    def __init__(self, path: Path, schema: str):
        self.schema = sql.identifier(schema)
        self.conn = sqlite3.connect(":memory:", detect_types=0)
        self.conn.execute(f"ATTACH DATABASE ? AS {self.schema}", (str(path),))
        self.statements: list[str] = []

    def _execute(self, statement: str, params: tuple = ()) -> sqlite3.Cursor:
        self.statements.append(statement)
        return self.conn.execute(statement, params)

    def other_objects(self) -> int:
        rows = self._select(f"SELECT type AS kind, name FROM {self.schema}.sqlite_master WHERE type IN ('table', 'view')")
        return other_object_count(rows)

    def ensure_tables(self) -> None:
        for table in TABLES:
            self._execute(table.create_sql(self.schema))
        self.conn.commit()

    def append(self, table: Table, run_id: str, rows: list[dict]) -> None:
        if not rows:
            return
        columns = ", ".join(table.column_names)
        marks = ", ".join("?" for _ in table.columns)
        statement = f"INSERT INTO {sql.qualified_table(self.schema, table.name)} ({columns}) VALUES ({marks})"
        self.statements.append(statement)
        self.conn.executemany(statement, [_sqlite_values(r) for r in table_rows(table, run_id, rows)])
        self.conn.commit()

    def _select(self, statement: str, params: tuple = ()) -> list[dict]:
        cursor = self._execute(statement, params)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def finished_object_ids(self, run_id: str) -> set[int]:
        rows = self._select(f"SELECT DISTINCT object_id FROM {sql.qualified_table(self.schema, OBJECTS.name)} WHERE run_id = ?", (run_id,))
        return {r["object_id"] for r in rows}

    def run_rows(self, run_id: str) -> dict[str, list[dict]]:
        result = {}
        for table in (OBJECTS, FINDINGS, COVERAGE):
            result[table.name] = self._select(
                f"SELECT * FROM {sql.qualified_table(self.schema, table.name)} WHERE run_id = ?", (run_id,)
            )
        return result

    def latest_status(self, run_id: str) -> str | None:
        rows = self._select(
            f"SELECT status, recorded_at FROM {sql.qualified_table(self.schema, SCAN_RUNS.name)} WHERE run_id = ?",
            (run_id,),
        )
        return newest_status(rows)

    def checklist_pairs(self) -> set[tuple[int, str]]:
        rows = self._select(f"SELECT DISTINCT object_id, rule_id FROM {sql.qualified_table(self.schema, CHECKLIST_STATUS.name)}")
        return {(r["object_id"], r["rule_id"]) for r in rows}

    def run_starts(self) -> list[tuple[str, datetime]]:
        rows = self._select(
            f"SELECT run_id, MIN(started_at) AS started_at FROM {sql.qualified_table(self.schema, SCAN_RUNS.name)} GROUP BY run_id"
        )
        return [(r["run_id"], datetime.fromisoformat(r["started_at"])) for r in rows if r["started_at"]]

    def delete_runs(self, run_ids: list[str]) -> None:
        """The retention cleanup. SQLite form of common.retention.delete_runs_sql."""
        if not run_ids:
            return
        for run in run_ids:
            sql.run_id(run)
        marks = ", ".join("?" for _ in run_ids)
        params = tuple(run_ids)
        for table in (FINDINGS, COVERAGE, OBJECTS):
            self._execute(f"DELETE FROM {sql.qualified_table(self.schema, table.name)} WHERE run_id IN ({marks})", params)
        checklist = sql.qualified_table(self.schema, CHECKLIST_STATUS.name)
        self._execute(
            f"DELETE FROM {checklist} WHERE run_id IN ({marks}) AND EXISTS ("
            f"SELECT 1 FROM {checklist} AS newer WHERE newer.object_id = {CHECKLIST_STATUS.name}.object_id "
            f"AND newer.rule_id = {CHECKLIST_STATUS.name}.rule_id AND newer.changed_at > {CHECKLIST_STATUS.name}.changed_at)",
            params,
        )
        self._execute(f"DELETE FROM {sql.qualified_table(self.schema, SCAN_RUNS.name)} WHERE run_id IN ({marks})", params)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def _sqlite_values(row: tuple) -> tuple:
    return tuple(v.isoformat(sep=" ") if isinstance(v, datetime) else v for v in row)


# ---------------------------------------------------------------------------
# Civis, for real scans
# ---------------------------------------------------------------------------


class CivisSink:
    """Writes through civis-python inside Civis. ``io`` is civis.io (injected for tests)."""

    def __init__(self, client: Any, io_module: Any, database: str | int, schema: str, credential_id: int | None):
        self.client = client
        self.io = io_module
        self.database = database
        self.schema = sql.identifier(schema)
        self.credential_id = credential_id
        self.statements: list[str] = []

    def _query(self, statement: str) -> None:
        self.statements.append(statement)
        self.io.query_civis(
            statement, self.database, client=self.client, credential_id=self.credential_id, hidden=True
        ).result()

    def _read(self, statement: str) -> list[dict]:
        """Rows as dicts. read_civis_sql raises EmptyResultError when a query returns no rows (Civis writes no
        file for an empty result), so that error means zero rows. Every other error is raised as is."""
        self.statements.append(statement)
        try:
            rows = self.io.read_civis_sql(
                statement, self.database, return_as="list", client=self.client,
                credential_id=self.credential_id, hidden=True,
            )
        except Exception as exc:
            if is_empty_result(exc):
                return []
            raise
        if not rows:
            return []
        header, body = rows[0], rows[1:]
        return [dict(zip(header, r)) for r in body]

    def other_objects(self) -> int:
        return other_object_count(self._read(schema_objects_sql(self.schema)))

    def ensure_tables(self) -> None:
        for table in TABLES:
            try:
                self._query(table.create_sql(self.schema))
            except Exception as exc:
                raise TableWriteError(table.name) from exc

    def append(self, table: Table, run_id: str, rows: list[dict]) -> None:
        if not rows:
            return
        try:
            frame = table_frame(table, run_id, rows)
        except ColumnValueError as exc:  # Nothing was sent; the scan ends tables_failed like a failed load.
            raise TableWriteError(table.name) from exc
        try:
            self.io.dataframe_to_civis(
                frame, self.database, sql.qualified_table(self.schema, table.name), client=self.client,
                existing_table_rows="append", credential_id=self.credential_id, hidden=True,
            ).result()
        except Exception as exc:
            raise TableWriteError(table.name) from exc

    def finished_object_ids(self, run_id: str) -> set[int]:
        rows = self._read(
            f"SELECT DISTINCT object_id FROM {sql.qualified_table(self.schema, OBJECTS.name)} WHERE run_id = {sql.run_id(run_id)}"
        )
        return {int(r["object_id"]) for r in rows}

    def run_rows(self, run_id: str) -> dict[str, list[dict]]:
        result = {}
        for table in (OBJECTS, FINDINGS, COVERAGE):
            rows = self._read(f"SELECT * FROM {sql.qualified_table(self.schema, table.name)} WHERE run_id = {sql.run_id(run_id)}")
            result[table.name] = [_ints(row) for row in rows]
        return result

    def latest_status(self, run_id: str) -> str | None:
        rows = self._read(
            f"SELECT status, recorded_at FROM {sql.qualified_table(self.schema, SCAN_RUNS.name)} WHERE run_id = {sql.run_id(run_id)}"
        )
        return newest_status(rows)

    def checklist_pairs(self) -> set[tuple[int, str]]:
        rows = self._read(f"SELECT DISTINCT object_id, rule_id FROM {sql.qualified_table(self.schema, CHECKLIST_STATUS.name)}")
        return {(int(r["object_id"]), r["rule_id"]) for r in rows}

    def run_starts(self) -> list[tuple[str, datetime]]:
        rows = self._read(
            f"SELECT run_id, MIN(started_at) AS started_at FROM {sql.qualified_table(self.schema, SCAN_RUNS.name)} GROUP BY run_id"
        )
        return [(r["run_id"], _parse_timestamp(r["started_at"])) for r in rows if r.get("started_at")]

    def delete_runs(self, run_ids: list[str]) -> None:
        """The retention cleanup: the only place DELETE runs."""
        for statement in retention.delete_runs_sql(self.schema, run_ids):
            self._query(statement)


def newest_status(rows: list[dict]) -> str | None:
    """The current status of a run: the newest row, and a final status over "running" on a tie."""
    if not rows:
        return None
    best = max(rows, key=lambda r: (str(r.get("recorded_at") or ""), r.get("status") != "running"))
    return best["status"]


_INT_COLUMNS = ("object_id", "line_number")


def _ints(row: dict) -> dict:
    """read_civis_sql returns text; IDs and line numbers are compared as integers."""
    for name in _INT_COLUMNS:
        value = row.get(name)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            row[name] = int(value)
        elif value == "":
            row[name] = None
    return row


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# CSV report and run outputs
# ---------------------------------------------------------------------------

REPORT_NOTICE_LINES = 2  # The disclaimer and the non-affiliation statement come before the column names.
REPORT_COLUMNS = [
    "row_type", "object_id", "object_type", "object_name", "object_url", "owner_name", "owner_active",
    "impact", "uses_default_image", "docker_image_name", "docker_image_tag", "scan_status", "scan_note",
    "rule_id", "rule_title", "severity", "confidence", "source", "file", "line_number", "fix_guidance", "workflows",
]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """Stop spreadsheets from running a cell as a formula (CSV injection)."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return "" if value is None else value


def report_csv(objects: list[dict], findings: list[dict], rules: RuleSet) -> bytes:
    """One row per finding, plus one row for every object with no findings.

    Every inventoried object appears at least once, with its scan status. The
    file starts with REPORT_NOTICE_LINES notice lines (the disclaimer, then the
    non-affiliation statement), each one quoted cell, written as is: the
    formula guard applies to data cells only. The column names are on line 3.
    """
    by_object: dict[int, list[dict]] = {}
    for finding in findings:
        by_object.setdefault(int(finding["object_id"]), []).append(finding)
    titles = {r.id: r.title for r in rules}

    buffer = io.StringIO()
    notices = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    notices.writerow([DISCLAIMER])
    notices.writerow([NON_AFFILIATION])
    writer = csv.DictWriter(buffer, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for obj in sorted(objects, key=lambda o: (str(o["object_type"]), int(o["object_id"]))):
        base = {
            "object_id": obj["object_id"], "object_type": obj["object_type"], "object_name": obj.get("name"),
            "object_url": obj.get("url"), "owner_name": obj.get("owner_name"), "owner_active": obj.get("owner_active"),
            "impact": obj.get("impact"), "uses_default_image": obj.get("uses_default_image"),
            "docker_image_name": obj.get("docker_image_name"), "docker_image_tag": obj.get("docker_image_tag"),
            "scan_status": obj.get("scan_status"), "scan_note": obj.get("scan_note"), "workflows": obj.get("workflows"),
        }
        rows = by_object.get(int(obj["object_id"]))
        if not rows:
            writer.writerow({k: _cell(v) for k, v in {**base, "row_type": "object"}.items()})
            continue
        for finding in sorted(rows, key=lambda f: (str(f["rule_id"]), str(f["file"]), f["line_number"] or 0)):
            row = {
                **base, "row_type": "finding", "rule_id": finding["rule_id"],
                "rule_title": titles.get(finding["rule_id"], ""), "severity": finding["severity"],
                "confidence": finding["confidence"], "source": finding["source"], "file": finding["file"],
                "line_number": finding["line_number"], "fix_guidance": finding["fix_guidance"],
                "impact": finding["impact"],
            }
            writer.writerow({k: _cell(v) for k, v in row.items()})
    return buffer.getvalue().encode("utf-8-sig")


class LocalRunOutputs:
    """Dry runs save the report to a local folder."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.saved: list[Path] = []

    def save_csv(self, name: str, data: bytes) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / name
        path.write_bytes(data)
        self.saved.append(path)


class CivisRunOutputs:
    """Real scans attach the report to the scanner job's run, with no expiration."""

    def __init__(self, client: Any, io_module: Any, job_id: int, run_id: int):
        self.client = client
        self.io = io_module
        self.job_id = job_id
        self.run_id = run_id

    def save_csv(self, name: str, data: bytes) -> None:
        file_id = self.io.file_to_civis(io.BytesIO(data), name=name, expires_at=None, client=self.client)
        self.client.scripts.post_containers_runs_outputs(self.job_id, self.run_id, "File", file_id)

