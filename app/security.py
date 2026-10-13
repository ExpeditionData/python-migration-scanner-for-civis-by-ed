"""Web app protections: response headers, CSRF, safe JSON, safe links.

Civis Platform shows a Service inside a cross-site iframe on
platform.civisanalytics.com, served from a different site. So:

- The frame rule allows 'self' and the Civis Platform origin only. No
  X-Frame-Options header is sent, because it has no allow-list form and would
  block Civis from showing the app.
- The app sets no cookies of its own. A cookie inside a cross-site iframe is a
  third-party cookie and is often blocked. CSRF tokens are therefore stateless:
  an HMAC over the viewer's Civis user ID and a timestamp, sent in a form field
  or the X-CSRF-Token header. Civis's proxy sets and overwrites the
  Civis-User-Id header, so another site cannot forge a token for a viewer.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlsplit

from flask import Flask, abort, request
from flask.json.provider import DefaultJSONProvider
from markupsafe import Markup

DEFAULT_FRAME_ANCESTORS = "https://platform.civisanalytics.com"
CSRF_MAX_AGE_SECONDS = 8 * 60 * 60
CSRF_CLOCK_SKEW_SECONDS = 60
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ORIGIN = re.compile(r"^https://(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d{1,5})?$")


def frame_ancestors(value: str | None = None) -> str:
    """Origins allowed to frame the app. Set CIVIS_FRAME_ANCESTORS to a space-separated
    list of https origins to change it. Anything that is not a plain https origin is
    rejected, so the header cannot be broken or widened by a bad setting."""
    raw = value if value is not None else os.environ.get("CIVIS_FRAME_ANCESTORS", DEFAULT_FRAME_ANCESTORS)
    origins = raw.split()
    for origin in origins:
        if not _ORIGIN.match(origin):
            raise ValueError("CIVIS_FRAME_ANCESTORS must be a space-separated list of https origins")
    return " ".join(["'self'", *origins])


def content_security_policy(ancestors: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            f"frame-ancestors {ancestors}",
        ]
    )


class EscapingJSONProvider(DefaultJSONProvider):
    """JSON responses with <, >, &, and ' escaped as \\u sequences.

    Object names, user names, and file paths come from Civis and are
    user-controlled. Escaping them in JSON means a value can never close a
    script block or be read as HTML, even if front-end code makes a mistake.
    The JSON still decodes to the same values.
    """

    def dumps(self, obj, **kwargs) -> str:
        text = super().dumps(obj, **kwargs)
        return (
            text.replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("'", "\\u0027")
        )


def safe_href(url: object) -> str:
    """Allow only http and https links. Anything else (javascript:, data:) becomes '#'."""
    text = str(url or "")
    scheme = urlsplit(text).scheme.lower()
    return text if scheme in ("http", "https") else "#"


def _viewer_id() -> str:
    user_id = request.headers.get("Civis-User-Id", "")
    return user_id if user_id.isdigit() and len(user_id) <= 20 else "anonymous"


def _csrf_key(app: Flask) -> bytes:
    secret = app.config["SECRET_KEY"]
    secret = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(secret, b"civis-python-version-search/csrf/v1", hashlib.sha256).digest()


def generate_csrf_token(app: Flask, now: float | None = None) -> str:
    issued = str(int(now if now is not None else time.time()))
    mac = hmac.new(_csrf_key(app), f"{_viewer_id()}|{issued}".encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{mac}"


def csrf_token_is_valid(app: Flask, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token or len(token) > 200:
        return False
    issued, _, mac = token.partition(".")
    if not issued.isdigit():
        return False
    age = (now if now is not None else time.time()) - int(issued)
    if age < -CSRF_CLOCK_SKEW_SECONDS or age > CSRF_MAX_AGE_SECONDS:
        return False
    expected = hmac.new(_csrf_key(app), f"{_viewer_id()}|{issued}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac)


def init_app(app: Flask) -> None:
    csp = content_security_policy(frame_ancestors(app.config.get("FRAME_ANCESTORS")))

    @app.before_request
    def _check_csrf():
        if request.method in SAFE_METHODS:
            return None
        origin = request.headers.get("Origin")
        if origin and origin != "null" and urlsplit(origin).netloc != request.host:
            abort(403)
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not csrf_token_is_valid(app, token):
            abort(400)
        return None

    @app.after_request
    def _security_headers(response):
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers.pop("X-Frame-Options", None)
        return response

    app.jinja_env.filters["safe_href"] = safe_href
    app.jinja_env.globals["csrf_token"] = lambda: Markup.escape(generate_csrf_token(app))
