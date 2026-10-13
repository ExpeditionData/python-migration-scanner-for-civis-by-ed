"""The scanner's own log. Structured facts only.

Every log line the scanner writes goes through these functions. They accept
only progress counts, object types, object IDs, rule IDs, and exception type
names, and they validate each one. There is no function that takes free text,
so code, run log text, notebook output, names, or exception messages cannot
reach the scanner's log by accident. A redacting filter is installed as a
second layer.

Line formats (parsed by the web app to show progress):

    PROGRESS phase=code objects_done=420 objects_total=2000
    OBJECT_ERROR object_type=python_script object_id=107 error=CivisAPIError status=403
    PARSE_ERROR object_type=notebook object_id=401 error=JSONDecodeError
    FINDINGS rule_id=CV3-USE-PANDAS count=12
    PHASE name=inventory state=done
"""

from __future__ import annotations

import logging
import re
import sys

from common.models import ObjectType
from common.redact import install_redaction

LOGGER_NAME = "scanner"
PHASES = frozenset({"inventory", "code", "logs", "output", "retention"})
PHASE_STATES = frozenset({"start", "done", "failed"})
_RULE_ID = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)+$")
_ERROR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")

_logger = logging.getLogger(LOGGER_NAME)


def configure(stream=None) -> logging.Logger:
    """Send scanner log lines to stdout (shown in the Civis run log), with redaction."""
    if not _logger.handlers:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
    install_redaction(_logger)
    return _logger


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _object_type(value: object) -> str:
    return str(ObjectType(value))


def _error_name(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _ERROR_NAME.match(name) else "Exception"


def progress(phase: str, done: int, total: int) -> None:
    if phase not in PHASES:
        raise ValueError("unknown phase")
    _logger.info(
        "PROGRESS phase=%s objects_done=%d objects_total=%d",
        phase, _count(done, "done"), _count(total, "total"),
    )


def phase(name: str, state: str) -> None:
    if name not in PHASES or state not in PHASE_STATES:
        raise ValueError("unknown phase or state")
    _logger.info("PHASE name=%s state=%s", name, state)


def object_error(object_type: ObjectType | str, object_id: int, exc: BaseException) -> None:
    """An object could not be read. Logs the error type and HTTP status, never the message."""
    status = getattr(exc, "status_code", None)
    suffix = f" status={status}" if isinstance(status, int) and not isinstance(status, bool) else ""
    _logger.warning(
        "OBJECT_ERROR object_type=%s object_id=%d error=%s%s",
        _object_type(object_type), _count(object_id, "object_id"), _error_name(exc), suffix,
    )


def parse_error(object_type: ObjectType | str, object_id: int, error_type: type[BaseException] | BaseException) -> None:
    """Code or a file failed to parse. Logs the object ID and error type only, never the text."""
    name = error_type.__name__ if isinstance(error_type, type) else _error_name(error_type)
    if not _ERROR_NAME.match(name):
        name = "Exception"
    _logger.warning(
        "PARSE_ERROR object_type=%s object_id=%d error=%s",
        _object_type(object_type), _count(object_id, "object_id"), name,
    )


def findings_count(rule_id: str, count: int) -> None:
    if not isinstance(rule_id, str) or not _RULE_ID.match(rule_id):
        raise ValueError("invalid rule ID")
    _logger.info("FINDINGS rule_id=%s count=%d", rule_id, _count(count, "count"))
