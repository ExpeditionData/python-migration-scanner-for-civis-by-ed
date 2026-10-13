"""Keep credentials and Civis content out of logs and error messages.

Two layers:

1. Callers log only safe, structured facts (counts, object IDs, rule IDs, error
   type names). ``safe_error`` turns an exception into its type name and HTTP
   status, never its message, because messages can echo code, log text, or
   request details.
2. As a backstop, ``RedactingFilter`` scrubs every log record before it is
   written: known secret values from the environment (CIVIS_API_KEY, GitHub
   tokens, attached credential passwords) and common secret patterns.

Runs on Python 3.12 and 3.14 and uses only the standard library.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable, Mapping

REDACTED = "[REDACTED]"

# Environment variables that always hold secrets.
SECRET_ENV_NAMES = frozenset(
    {
        "CIVIS_API_KEY",
        "CIVIS_SERVICE_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "REPO_HTTP_TOKEN",
        "FLASK_SECRET_KEY",
    }
)
# Civis exposes attached credentials as <NAME>_PASSWORD, <NAME>_SECRET_ACCESS_KEY,
# and similar. Database parameters add <NAME>_CREDENTIAL_PASSWORD.
SECRET_ENV_SUFFIXES = ("_PASSWORD", "_TOKEN", "_SECRET", "_API_KEY", "_SECRET_ACCESS_KEY", "_PRIVATE_KEY")
_MIN_SECRET_LENGTH = 6

_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # GitHub tokens (classic and fine-grained).
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    # Authorization headers and bearer tokens.
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic|token)\s+\S+"), r"\1" + REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + REDACTED),
    # key=value and key: value pairs with secret-looking names.
    (
        re.compile(
            r"(?i)\b((?:[a-z0-9_]*?)(?:api[_-]?key|token|password|passwd|pwd|secret)[a-z0-9_]*['\"]?\s*[=:]\s*)"
            r"(['\"]?)[^\s'\",;&]+\2"
        ),
        r"\1" + REDACTED,
    ),
    # Credentials inside URLs: https://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + REDACTED + "@"),
    # Signed URL query parameters (Civis file links, S3 presigned URLs).
    (
        re.compile(
            r"(?i)([?&](?:x-amz-signature|x-amz-credential|x-amz-security-token|signature|sig|"
            r"access_token|api_key|token)=)[^&\s]+"
        ),
        r"\1" + REDACTED,
    ),
)


def secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    """Values of environment variables that hold secrets, longest first."""
    env = os.environ if env is None else env
    values = set()
    for name, value in env.items():
        upper = name.upper()
        if upper in SECRET_ENV_NAMES or upper.endswith(SECRET_ENV_SUFFIXES):
            if value and len(value) >= _MIN_SECRET_LENGTH:
                values.add(value)
    return sorted(values, key=len, reverse=True)


def redact(text: object, extra_secrets: Iterable[str] = (), env: Mapping[str, str] | None = None) -> str:
    """Return ``text`` with known secret values and secret-looking patterns removed."""
    result = str(text)
    for value in [*extra_secrets, *secret_values(env)]:
        if value and len(value) >= _MIN_SECRET_LENGTH:
            result = result.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def safe_error(exc: BaseException) -> str:
    """Describe an exception by type and HTTP status only. Never its message."""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    return f"{name} (HTTP {status})" if isinstance(status, int) else name


class RedactingFilter(logging.Filter):
    """Scrub every record, including formatted tracebacks, before a handler writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # A bad format string must not leak raw arguments.
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = redact(formatter.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Add the redacting filter to every handler on ``logger`` (the root logger by default)."""
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
