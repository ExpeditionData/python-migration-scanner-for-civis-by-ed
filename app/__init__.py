"""Flask web app, deployed as a Civis Service.

Started by gunicorn: ``gunicorn "app:create_app()"``. This module sets up the
security settings every page relies on (see SECURITY.md and app/security.py)
and connects the pages to their data: Civis inside the Service, or the
dry-run preview files in local preview mode (app/local.py).
"""

from __future__ import annotations

import hashlib
import hmac
import os

from flask import Flask

from app import pages, security, views

MAX_REQUEST_BYTES = 1024 * 1024


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _secret_key() -> str | None:
    """FLASK_SECRET_KEY if set, otherwise a key derived from the Service's CIVIS_API_KEY.

    Deriving the key means no extra secret has to be stored, every gunicorn
    worker gets the same key, and the key changes when Civis rotates the API key.
    The derivation is one-way; the API key itself is never stored or logged.
    """
    explicit = os.environ.get("FLASK_SECRET_KEY", "")
    if len(explicit) >= 32:
        return explicit
    api_key = os.environ.get("CIVIS_API_KEY", "")
    if api_key:
        return hmac.new(api_key.encode(), b"civis-python-version-search/flask-secret/v1", hashlib.sha256).hexdigest()
    return None


def create_app(test_config: dict | None = None) -> Flask:
    if _truthy(os.environ.get("FLASK_DEBUG")):
        raise RuntimeError("Flask debug mode is not allowed for this app. Unset FLASK_DEBUG.")

    app = Flask(__name__)
    app.json = security.EscapingJSONProvider(app)
    app.config.update(
        SECRET_KEY=_secret_key(),
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PROPAGATE_EXCEPTIONS=False,
    )
    if test_config:
        app.config.update(test_config)
    # Debug mode is always off, whatever the configuration says.
    app.config["DEBUG"] = False
    app.debug = False
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("No secret key: set FLASK_SECRET_KEY (32+ characters) or run inside Civis with CIVIS_API_KEY.")

    services = app.config.pop("SERVICES", None)
    if services is None:
        from app.services import civis_services
        from app.settings import Settings

        services = civis_services(Settings.from_env())
    app.extensions["cpvs"] = services

    security.init_app(app)
    app.register_blueprint(pages.bp)
    app.jinja_env.filters["number"] = views.number
    return app
