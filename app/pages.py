"""Pages. Phase 5 adds the dashboard, inventory, findings, checklist, and history.

The "/" route must answer quickly or Civis treats the Service as down, so it
makes no Civis API or database calls.
"""

from __future__ import annotations

from flask import Blueprint, Response, render_template

from common.disclaimer import DISCLAIMER

bp = Blueprint("pages", __name__)


@bp.get("/")
def landing():
    return render_template("landing.html", disclaimer=DISCLAIMER)


@bp.get("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")
