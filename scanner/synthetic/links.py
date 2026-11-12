"""Made-up time-limited notebook file links, like the ones Civis returns, and a fake clock.

In the live test (docs/PLAN.md 17.9), each ``notebooks.get`` returned a new
S3 link, SigV4 signed with ``X-Amz-Expires=300`` and the signed headers
``host`` only, issued at the moment of the call. S3 refused it with HTTP 403
(``AccessDenied``, "Request has expired") once it was more than 300 seconds
old. The synthetic client signs a new link on every call with ``sign_link``,
and the synthetic file host checks it with ``check_link``, both on a
``SyntheticClock`` that moves only when a test calls ``advance``. No real
waits, and nothing here is a real signature or key.
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

LINK_EXPIRES_SECONDS = 300
CLOCK_START = datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc)
_DATE_FORMAT = "%Y%m%dT%H%M%SZ"
_KEY = "synthetic-signing-key-not-a-secret"  # pragma: allowlist secret  gitleaks:allow
_REQUIRED = ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires", "X-Amz-SignedHeaders",
             "X-Amz-Security-Token", "X-Amz-Signature")

EXPIRED_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
    b"<Message>Request has expired</Message><X-Amz-Expires>300</X-Amz-Expires></Error>"
)
BAD_SIGNATURE_BODY = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>SignatureDoesNotMatch</Code>'
    b"<Message>The request signature we calculated does not match the signature you provided.</Message></Error>"
)


class SyntheticClock:
    """A made-up UTC clock. It starts at CLOCK_START and moves only when ``advance`` is called."""

    def __init__(self, start: datetime = CLOCK_START):
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += timedelta(seconds=seconds)


_serials = itertools.count(1)
_serial_lock = threading.Lock()


def _signature(base: str, date: str, expires: str, token: str) -> str:
    return hashlib.sha256(f"{_KEY}|{base}|{date}|{expires}|{token}".encode("utf-8")).hexdigest()


def base_of(url: str) -> str:
    """The link without its query string, which is how the synthetic file host keys its files."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def sign_link(url: str, issued: datetime) -> str:
    """A new link to ``url``'s file, issued at ``issued``. Every call gives a different link."""
    with _serial_lock:
        serial = next(_serials)
    base = base_of(url)
    date = issued.astimezone(timezone.utc).strftime(_DATE_FORMAT)
    expires = str(LINK_EXPIRES_SECONDS)
    token = f"synthetic-session-token-{serial}"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"SYNTHETICKEYID/{date[:8]}/us-east-1/s3/aws4_request",
        "X-Amz-Date": date,
        "X-Amz-Expires": expires,
        "X-Amz-SignedHeaders": "host",
        "X-Amz-Security-Token": token,
        "X-Amz-Signature": _signature(base, date, expires, token),
    }
    return f"{base}?{urlencode(query)}"


def link_issued_at(url: str) -> datetime | None:
    """When a synthetic link was issued, or None if it is not one."""
    values = parse_qs(urlsplit(url).query)
    try:
        return datetime.strptime(values["X-Amz-Date"][0], _DATE_FORMAT).replace(tzinfo=timezone.utc)
    except (KeyError, IndexError, ValueError):
        return None


def check_link(url: str, now: datetime) -> tuple[int, bytes]:
    """(status, body) the synthetic file host answers for a signed link: 200 with an empty body when the link
    is valid and fresh, 403 SignatureDoesNotMatch for a link it did not sign (or one without a query string),
    and 403 AccessDenied "Request has expired" once more than X-Amz-Expires seconds have passed."""
    values = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    if any(name not in values for name in _REQUIRED):
        return 403, BAD_SIGNATURE_BODY
    expected = _signature(base_of(url), values["X-Amz-Date"], values["X-Amz-Expires"], values["X-Amz-Security-Token"])
    issued = link_issued_at(url)
    if values["X-Amz-Signature"] != expected or issued is None or not values["X-Amz-Expires"].isdigit():
        return 403, BAD_SIGNATURE_BODY
    if (now - issued).total_seconds() > int(values["X-Amz-Expires"]):
        return 403, EXPIRED_BODY
    return 200, b""
