"""Links to objects in the Civis Platform web interface.

The path patterns below follow the Civis single-page app routes. They are not
documented by Civis and must be confirmed in a live Civis instance.
The base URL can be changed with the CIVIS_PLATFORM_URL environment variable.
"""

from __future__ import annotations

import os

from common.models import ObjectType

DEFAULT_BASE_URL = "https://platform.civisanalytics.com"

_PATHS = {
    ObjectType.PYTHON_SCRIPT: "/spa/#/scripts/python3/{id}",
    ObjectType.CONTAINER_SCRIPT: "/spa/#/scripts/containers/{id}",
    ObjectType.CUSTOM_SCRIPT: "/spa/#/scripts/custom/{id}",
    ObjectType.NOTEBOOK: "/spa/#/notebooks/{id}",
    ObjectType.SERVICE: "/spa/#/services/{id}",
    ObjectType.WORKFLOW: "/spa/#/workflows/{id}",
}


def base_url() -> str:
    return os.environ.get("CIVIS_PLATFORM_URL", DEFAULT_BASE_URL).rstrip("/")


def object_url(object_type: ObjectType, object_id: int, base: str | None = None) -> str:
    if isinstance(object_id, bool) or not isinstance(object_id, int):
        raise ValueError("object_id must be an integer")
    root = (base or base_url()).rstrip("/")
    return root + _PATHS[object_type].format(id=object_id)
