"""Errors from civis-python that the tool handles as normal results.

``civis.io.read_civis_sql`` runs the query as an export to a file. When a
query returns no rows, Civis writes no file, and read_civis_sql raises
``civis.base.EmptyResultError`` ("... returned no output.") instead of
returning an empty list. This is the same in civis 2.7.1 (the Service image)
and 2.10 (the scanner image), and was confirmed in the first live test on
November 5, 2026. Every read in the app and the scanner treats it as zero rows.
"""

from __future__ import annotations

EMPTY_RESULT_NAME = "EmptyResultError"


def is_empty_result(exc: BaseException) -> bool:
    """True if ``exc`` is civis.base.EmptyResultError: the query ran and returned no rows.

    Both images have civis. Where it cannot be imported (a test without civis), a class with the same name
    counts, so a made-up stand-in behaves like the real one."""
    try:
        from civis.base import EmptyResultError
    except ImportError:
        return type(exc).__name__ == EMPTY_RESULT_NAME
    return isinstance(exc, EmptyResultError)
