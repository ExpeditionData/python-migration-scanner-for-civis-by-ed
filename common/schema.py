"""Definitions of the five result tables in the schema the user names.

Every row carries a run ID so rescans can be compared. Column types use the
subset of SQL that Redshift and PostgreSQL both accept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from common import sql
from common.models import ChecklistStatus


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    description: str


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def create_sql(self, schema: str) -> str:
        cols = ",\n  ".join(f"{sql.identifier(c.name)} {c.sql_type}" for c in self.columns)
        return f"CREATE TABLE IF NOT EXISTS {sql.qualified_table(schema, self.name)} (\n  {cols}\n)"


SCAN_RUNS = Table(
    "scan_runs",
    "Status rows for each scan run: one when it starts and one when it ends.",
    (
        Column("run_id", "VARCHAR(64)", "Unique ID for the scan run."),
        Column("started_at", "TIMESTAMP", "When the scan started (UTC)."),
        Column("finished_at", "TIMESTAMP", "When the scan finished (UTC). Empty while running."),
        Column("status", "VARCHAR(32)", "running, succeeded, tables_failed (CSV report only), or failed."),
        Column("scanning_user_id", "BIGINT", "Civis user ID the scan ran as."),
        Column("scanning_user_name", "VARCHAR(256)", "Civis user name the scan ran as."),
        Column("tool_version", "VARCHAR(32)", "Version of this tool."),
        Column("parameters", "VARCHAR(4096)", "Scan parameters as JSON. Never includes secrets."),
        Column("civis_job_id", "BIGINT", "Civis job ID of the scanner container script."),
        Column("civis_run_id", "BIGINT", "Civis run ID of the scanner run."),
        Column("disclaimer", "VARCHAR(2048)", "Disclaimer text shown with the results."),
        Column("recorded_at", "TIMESTAMP", "When this row was written. Rows are append-only; the newest row per run is current."),
    ),
)

OBJECTS = Table(
    "objects",
    "Full inventory of Python-based objects found in each run.",
    (
        Column("run_id", "VARCHAR(64)", "Scan run ID."),
        Column("object_id", "BIGINT", "Civis object ID."),
        Column("object_type", "VARCHAR(32)", "python_script, container_script, custom_script, dbt_script, notebook, service, or workflow."),
        Column("name", "VARCHAR(1024)", "Object name in Civis."),
        Column("url", "VARCHAR(512)", "Link to the object in Civis."),
        Column("owner_id", "BIGINT", "Civis user ID of the owner."),
        Column("owner_name", "VARCHAR(256)", "Owner name."),
        Column("owner_active", "BOOLEAN", "False if the owner is deactivated. Empty if unknown."),
        Column("archived", "BOOLEAN", "True only for archived jobs used by an active workflow."),
        Column("hidden", "BOOLEAN", "True if Civis marks the object hidden."),
        Column("scheduled", "BOOLEAN", "True if the object runs on a schedule."),
        Column("schedule", "VARCHAR(512)", "Short description of the schedule."),
        Column("last_run_id", "BIGINT", "ID of the most recent run."),
        Column("last_run_at", "VARCHAR(64)", "When the most recent run finished, as Civis reports it."),
        Column("last_run_state", "VARCHAR(32)", "State of the most recent run."),
        Column("docker_image_name", "VARCHAR(512)", "Docker image name."),
        Column("docker_image_tag", "VARCHAR(256)", "Docker image tag. Empty means latest."),
        Column("uses_default_image", "BOOLEAN", "True if the object runs on a Civis default image."),
        Column("impact", "VARCHAR(16)", "affected, advisory, or unknown."),
        Column("git_repo", "VARCHAR(1024)", "Git repository URL, if git-backed."),
        Column("git_ref", "VARCHAR(256)", "Git branch, tag, or commit."),
        Column("git_path", "VARCHAR(1024)", "Path inside the repository."),
        Column("template_id", "BIGINT", "Template ID, for custom scripts."),
        Column("backing_script_id", "BIGINT", "Script behind the template, for custom scripts."),
        Column("workflows", "VARCHAR(4096)", "JSON list of active workflows that run this object: [{\"id\": 1, \"name\": \"...\"}]."),
        Column("scan_status", "VARCHAR(16)", "scanned, partial, not_scanned, or pending."),
        Column("scan_note", "VARCHAR(1024)", "Why the object was not fully scanned."),
    ),
)

FINDINGS = Table(
    "findings",
    "One row per rule match. File, line number, and rule ID only. Never code.",
    (
        Column("run_id", "VARCHAR(64)", "Scan run ID."),
        Column("object_id", "BIGINT", "Civis object ID."),
        Column("object_type", "VARCHAR(32)", "Object type."),
        Column("rule_id", "VARCHAR(128)", "Rule ID from the rules files."),
        Column("file", "VARCHAR(1024)", "File or code location label."),
        Column("line_number", "INTEGER", "Line number inside that file."),
        Column("source", "VARCHAR(32)", "code, log, or notebook_output."),
        Column("severity", "VARCHAR(16)", "high, medium, or low."),
        Column("confidence", "VARCHAR(16)", "high, medium, or low."),
        Column("impact", "VARCHAR(16)", "affected, advisory, or unknown."),
        Column("fix_guidance", "VARCHAR(2048)", "How to fix it."),
        Column("workflows", "VARCHAR(4096)", "JSON list of active workflows that run this object, so an admin sees what else breaks."),
    ),
)

COVERAGE = Table(
    "coverage",
    "Counts by object type, plus every object not fully scanned and why.",
    (
        Column("run_id", "VARCHAR(64)", "Scan run ID."),
        Column("row_kind", "VARCHAR(16)",
               "summary, object, not_python, outside_window (scripts that did not run or change within "
               "LOOKBACK_DAYS), template_copies (custom scripts also listed under another type), or users "
               "(owner active status not fully known)."),
        Column("object_type", "VARCHAR(32)", "Object type. Empty on users rows."),
        Column("object_id", "BIGINT", "Object ID, on object rows only."),
        Column("language", "VARCHAR(32)", "Language of objects left out, on not_python rows only (for example r or sql)."),
        Column("found_count", "INTEGER",
               "Objects found on summary rows; objects left out on not_python, outside_window, and "
               "template_copies rows."),
        Column("scanned_count", "INTEGER", "Objects fully scanned, on summary rows only."),
        Column("hidden_skipped_count", "INTEGER",
               "Hidden objects skipped, on summary rows only. Empty from v0.1.0-rc6: hidden objects are not counted."),
        Column("archived_included_count", "INTEGER", "Archived jobs included because an active workflow runs them."),
        Column("reason", "VARCHAR(1024)", "Why the object was not fully scanned, on object rows; a fixed note on users rows."),
    ),
)

CHECKLIST_STATUS = Table(
    "checklist_status",
    "Append-only log of checklist changes. The newest row per object and rule is current.",
    (
        Column("run_id", "VARCHAR(64)", "Scan run that was in view when the row was written."),
        Column("object_id", "BIGINT", "Civis object ID."),
        Column("rule_id", "VARCHAR(128)", "Rule ID."),
        Column("status", "VARCHAR(16)", "open, fixed, or ignored."),
        Column("changed_by", "VARCHAR(256)", "Civis user name, or the scanner for new open rows."),
        Column("changed_at", "TIMESTAMP", "When the change was made (UTC)."),
    ),
)

TABLES: tuple[Table, ...] = (SCAN_RUNS, OBJECTS, FINDINGS, COVERAGE, CHECKLIST_STATUS)

CHECKLIST_STATUSES = frozenset(s.value for s in ChecklistStatus)
TABLE_NAMES = frozenset(t.name for t in TABLES)


def schema_objects_sql(schema: str) -> str:
    """Every table and view in the results schema, from pg_catalog (which lists them whatever the user's
    privileges). Every name in the tool's SQL is an unquoted identifier, stored in lower case, so the catalog
    is searched for the lower-case name."""
    name = sql.string(sql.identifier(schema).lower())
    return (
        f"SELECT 'table' AS kind, tablename AS name FROM pg_catalog.pg_tables WHERE schemaname = {name} "
        f"UNION ALL SELECT 'view' AS kind, viewname AS name FROM pg_catalog.pg_views WHERE schemaname = {name}"
    )


def other_object_count(rows) -> int:
    """How many tables and views in the schema are not this tool's five tables. Zero means the schema is
    dedicated to the tool (or does not exist). A view with one of our table names counts as another object.
    Only the count leaves this function; the names are never kept, shown, or logged."""
    return sum(1 for row in rows if not (row.get("kind") == "table" and str(row.get("name")) in TABLE_NAMES))
DATABASE_USER_PLACEHOLDER = "<database user>"


def create_schema_sql(schema: str) -> str:
    """The web app's "Set up results storage" button runs this, then each table's create_sql."""
    return f"CREATE SCHEMA IF NOT EXISTS {sql.identifier(schema)}"


def admin_schema_sql(schema: str, database_user: str | None) -> str:
    """The statement shown to a database admin when the app's database user cannot create schemas.
    Shown only, never run. Unless it can be written safely, the user name becomes a placeholder."""
    user = sql.quoted_identifier(database_user) or DATABASE_USER_PLACEHOLDER
    return f"CREATE SCHEMA {sql.identifier(schema)} AUTHORIZATION {user};"


def admin_owner_sql(schema: str, database_user: str | None) -> str:
    """The statement shown to a database admin when the schema already existed but the app's database user
    could not create tables in it. It makes that user the schema's owner (the README's ownership model).
    Shown only, never run. The user name is written the same way as in admin_schema_sql."""
    user = sql.quoted_identifier(database_user) or DATABASE_USER_PLACEHOLDER
    return f"ALTER SCHEMA {sql.identifier(schema)} OWNER TO {user};"


def checklist_insert_sql(
    schema: str,
    run_id: str,
    entries: list[tuple[int, str, str]],
    changed_by: str,
    changed_at: datetime,
) -> str:
    """Build one INSERT for several checklist changes.

    ``entries`` holds (object_id, rule_id, status) tuples. Every value is
    validated, including ``changed_by``, which comes from a Civis request header.
    """
    if not entries:
        raise sql.UnsafeValueError("No checklist entries to save.")
    if len(entries) > 500:
        raise sql.UnsafeValueError("Too many checklist entries in one save.")
    who = sql.string(changed_by, max_length=256)
    when = sql.timestamp(changed_at)
    rid = sql.run_id(run_id)
    rows = []
    for object_id, rule, status in entries:
        rows.append(
            f"({rid}, {sql.integer(object_id)}, {sql.rule_id(rule)}, "
            f"{sql.choice(status, CHECKLIST_STATUSES)}, {who}, {when})"
        )
    columns = ", ".join(CHECKLIST_STATUS.column_names)
    table = sql.qualified_table(schema, CHECKLIST_STATUS.name)
    return f"INSERT INTO {table} ({columns}) VALUES\n" + ",\n".join(rows)
