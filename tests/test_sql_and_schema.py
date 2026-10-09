from datetime import datetime

import pytest

from common import schema, sql


def test_identifier_accepts_simple_names():
    assert sql.identifier("scan_results") == "scan_results"
    assert sql.qualified_table("migration_scan", "findings") == "migration_scan.findings"


@pytest.mark.parametrize("bad", ["", "1abc", "a-b", "a b", "a;drop", 'a"b', "x" * 200, None, 5])
def test_identifier_rejects_unsafe_names(bad):
    with pytest.raises(sql.UnsafeValueError):
        sql.identifier(bad)


def test_string_doubles_single_quotes():
    assert sql.string("o'brien") == "'o''brien'"


@pytest.mark.parametrize("bad", ["a\\b", "line\nbreak", "nul\x00", "tab\t", "x" * 2000, 5, None])
def test_string_rejects_unsafe_values(bad):
    with pytest.raises(sql.UnsafeValueError):
        sql.string(bad)


def test_error_messages_never_echo_the_value():
    secret = "a'; DROP TABLE x; --\\"
    with pytest.raises(sql.UnsafeValueError) as excinfo:
        sql.string(secret)
    assert secret not in str(excinfo.value)


@pytest.mark.parametrize("bad", [True, "5", 5.0, None])
def test_integer_rejects_non_integers(bad):
    with pytest.raises(sql.UnsafeValueError):
        sql.integer(bad)


def test_rule_id_and_choice_validation():
    assert sql.rule_id("CV3-USE-PANDAS") == "'CV3-USE-PANDAS'"
    with pytest.raises(sql.UnsafeValueError):
        sql.rule_id("cv3-use-pandas'; --")
    with pytest.raises(sql.UnsafeValueError):
        sql.choice("done", {"open", "fixed"})


def test_every_table_has_run_id_and_valid_create_sql():
    for table in schema.TABLES:
        assert table.column_names[0] == "run_id"
        ddl = table.create_sql("results_schema")
        assert ddl.startswith(f"CREATE TABLE IF NOT EXISTS results_schema.{table.name} (")
        for name in table.column_names:
            sql.identifier(name)


def test_create_sql_rejects_unsafe_schema():
    with pytest.raises(sql.UnsafeValueError):
        schema.FINDINGS.create_sql("bad schema")


def test_findings_table_has_no_code_column():
    names = set(schema.FINDINGS.column_names)
    assert not names & {"code", "snippet", "source_code", "text", "line_text"}


def test_checklist_insert_escapes_header_value():
    stmt = schema.checklist_insert_sql(
        "results_schema",
        "run-20261009-a1",
        [(101, "CV3-USE-PANDAS", "fixed"), (102, "PY315-STRPTIME-NO-YEAR", "ignored")],
        changed_by="o'neil",
        changed_at=datetime(2026, 10, 9, 12, 30, 0),
    )
    assert stmt.startswith(
        "INSERT INTO results_schema.checklist_status "
        "(run_id, object_id, rule_id, status, changed_by, changed_at) VALUES"
    )
    assert "'o''neil'" in stmt
    assert "('run-20261009-a1', 101, 'CV3-USE-PANDAS', 'fixed'" in stmt
    assert stmt.count("\n") == 2


@pytest.mark.parametrize(
    "entries, changed_by",
    [
        ([(101, "CV3-USE-PANDAS", "done")], "user"),
        ([("101", "CV3-USE-PANDAS", "open")], "user"),
        ([(101, "x'); DROP TABLE y; --", "open")], "user"),
        ([(101, "CV3-USE-PANDAS", "open")], "user\\'); DROP TABLE y; --"),
        ([(101, "CV3-USE-PANDAS", "open")], "line\nbreak"),
        ([], "user"),
    ],
)
def test_checklist_insert_rejects_bad_values(entries, changed_by):
    with pytest.raises(sql.UnsafeValueError):
        schema.checklist_insert_sql("results_schema", "run1", entries, changed_by, datetime(2026, 10, 9))
