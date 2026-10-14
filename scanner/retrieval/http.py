"""The scanner's only HTTP client outside the civis API client.

It enforces SECURITY.md requirement 18: outbound calls go only to GitHub (for
the client's own repos) and to Civis file storage (for notebook files, through
links the Civis API returns). Every request and every redirect hop is checked
against an allow list of hosts, over https only. Bodies are size-capped.

Errors carry a short reason and the HTTP status only. They never include the
URL, because file links carry signatures and GitHub links can carry tokens.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

GITHUB_HOSTS = ("api.github.com", "codeload.github.com")
# Amazon S3 hostnames only: s3.amazonaws.com, s3.<region>.amazonaws.com, and the
# virtual-host forms <bucket>.s3.amazonaws.com and <bucket>.s3.<region>.amazonaws.com.
# Other amazonaws.com hosts (EC2, API Gateway, and so on) are refused.
_S3_REGION = r"[a-z]{2}(?:-gov)?-[a-z]+-\d"
_S3_BUCKET = r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]"
_S3_HOST = re.compile(rf"^(?:{_S3_BUCKET}\.)?s3(?:\.{_S3_REGION})?\.amazonaws\.com$")


def is_s3_host(host: str) -> bool:
    return bool(_S3_HOST.match((host or "").lower()))


# Civis returns notebook links on its own domains or on Amazon S3. The exact host
# is confirmed in Phase 7; a link on any other host is refused and reported.
CIVIS_FILE_HOSTS: tuple = ("*.civisanalytics.com", "*.civis.io", is_s3_host)

MAX_REDIRECTS = 5
CHUNK_BYTES = 64 * 1024


class FetchError(Exception):
    """A request failed. ``reason`` is safe to log and store; it never holds a URL."""

    def __init__(self, reason: str, status: int | None = None, headers: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


def host_allowed(host: str, allowed: Iterable[str | Callable[[str], bool]]) -> bool:
    """Each entry is an exact host, a "*.domain" wildcard, or a function that checks the host."""
    host = (host or "").lower()
    for pattern in allowed:
        if callable(pattern):
            if pattern(host):
                return True
            continue
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def _default_session() -> Any:
    import requests  # In the scanner image. Imported lazily so tests need no network stack.

    return requests.Session()


class SafeHttp:
    def __init__(self, session: Any = None, timeout: float = 60.0):
        self._session = session
        self._local = threading.local()
        self.timeout = timeout

    @property
    def session(self) -> Any:
        if self._session is not None:
            return self._session
        if not hasattr(self._local, "session"):
            self._local.session = _default_session()  # One session per worker thread.
        return self._local.session

    def get(
        self,
        url: str,
        *,
        allowed_hosts: Iterable[str],
        max_bytes: int,
        headers: dict | None = None,
        to_path: Path | None = None,
    ) -> bytes | None:
        """GET ``url``. Returns the body, or writes it to ``to_path`` and returns None."""
        allowed = tuple(allowed_hosts)
        headers = dict(headers or {})
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme != "https" or not host_allowed(parts.hostname or "", allowed):
                raise FetchError("The link points to a host the scanner is not allowed to contact.")
            try:
                response = self.session.get(
                    current, headers=headers, stream=True, allow_redirects=False, timeout=self.timeout
                )
            except Exception as exc:  # Network errors: report the type only.
                raise FetchError(f"The request failed ({type(exc).__name__}).") from None
            try:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError("A redirect had no location.", status)
                    next_url = urljoin(current, location)
                    if urlsplit(next_url).hostname != parts.hostname:
                        headers.pop("Authorization", None)  # Never send a token to another host.
                    current = next_url
                    continue
                if status < 200 or status >= 300:
                    raise FetchError(f"The request was refused (HTTP {status}).", status, dict(response.headers))
                return _read_body(response, max_bytes, to_path)
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        raise FetchError("Too many redirects.")


def _read_body(response: Any, max_bytes: int, to_path: Path | None) -> bytes | None:
    length = response.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise FetchError("The file is larger than the scanner's size limit.")
    total = 0
    chunks: list[bytes] = []
    sink = open(to_path, "wb") if to_path else None
    try:
        for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise FetchError("The file is larger than the scanner's size limit.")
            if sink:
                sink.write(chunk)
            else:
                chunks.append(chunk)
    finally:
        if sink:
            sink.close()
    return None if sink else b"".join(chunks)
