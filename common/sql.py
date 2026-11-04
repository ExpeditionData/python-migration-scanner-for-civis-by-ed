"""Safe SQL text building.

``civis.io.query_civis`` sends plain SQL text and has no bound parameters, and
Civis SQL script parameters are text templates, not database bind parameters.
So every value that goes into SQL is validated against a strict pattern first,
then written as an escaped literal. Identifiers (schema and table names) are
validated and never escaped, because a name that needs escaping is rejected.
"""

from __future__ import annotations

import re
from datetime import datetime

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,126}$")
_RULE_ID = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Control characters are never valid in names, user names, or labels.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class UnsafeValueError(ValueError):
    """Raised when a value fails validation. The message never echoes the value."""


def identifier(name: str) -> str:
    """Return a schema, table, or column name after validating it."""
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise UnsafeValueError("Invalid SQL identifier.")
    return name


def quoted_identifier(name: object) -> str | None:
    """A name to show in SQL for a person to run, or None if it cannot be written safely.

    A plain identifier is returned as is. Anything else is double-quoted with
    inner double quotes doubled, unless it is empty, too long, or holds control
    characters or a backslash. Used only for text shown to an admin, never run.
    """
    if not isinstance(name, str) or not name:
        return None
    if _IDENTIFIER.match(name):
        return name
    if len(name.encode("utf-8")) > 127 or _CONTROL.search(name) or "\\" in name:
        return None
    return '"' + name.replace('"', '""') + '"'


def qualified_table(schema: str, table: str) -> str:
    return f"{identifier(schema)}.{identifier(table)}"


def integer(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnsafeValueError("Expected an integer.")
    return str(value)


def rule_id(value: str) -> str:
    if not isinstance(value, str) or not _RULE_ID.match(value):
        raise UnsafeValueError("Invalid rule ID.")
    return string(value)


def run_id(value: str) -> str:
    if not isinstance(value, str) or not _RUN_ID.match(value):
        raise UnsafeValueError("Invalid run ID.")
    return string(value)


def choice(value: str, allowed: set[str] | frozenset[str]) -> str:
    if value not in allowed:
        raise UnsafeValueError("Value is not one of the allowed choices.")
    return string(value)


def timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise UnsafeValueError("Expected a datetime.")
    return string(value.strftime("%Y-%m-%d %H:%M:%S"))


def string(value: str, max_length: int = 1024) -> str:
    """Return ``value`` as a single-quoted SQL string literal.

    Doubles single quotes. Rejects backslashes, because Redshift treats a
    backslash in a literal as an escape character and PostgreSQL does not, so
    no single escaping works on both. Also rejects control characters and
    values longer than ``max_length``.
    """
    if not isinstance(value, str):
        raise UnsafeValueError("Expected a string.")
    if len(value) > max_length:
        raise UnsafeValueError("Value is too long.")
    if _CONTROL.search(value):
        raise UnsafeValueError("Value contains control characters.")
    if "\\" in value:
        raise UnsafeValueError("Value contains a backslash.")
    return "'" + value.replace("'", "''") + "'"


def nullable(value, render) -> str:
    return "NULL" if value is None else render(value)
