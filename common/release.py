"""What the README tells admins to set on the scanner job and the Service.

The Setup page (app/setup.py) compares the live settings with these. The
pinned image names and tags are read from the header of each requirements file
(``image: <name>:<tag> (Python X.Y.Z)``), the same lines docker/check_image.py
reads for the Python version, so there is one source of truth. A test checks
that the README shows the same values.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_URL = "https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git"
SCANNER_COMMAND = "cd /app && python -m scanner"
SCANNER_CPU = 1000  # CPU shares (one core)
SCANNER_MEMORY_MB = 4096
SCANNER_DISK_GB = 4
MEMORY_PER_EXTRA_THREAD_MB = 512  # For each MAX_CONCURRENCY thread above the default of 4.
DEFAULT_THREADS = 4
# A release tag: v, then numbers and dots, with an optional -rcN suffix (for example v0.1.0-rc3).
RELEASE_TAG = re.compile(r"^v\d+(\.\d+)*(-rc\d+)?$")
_IMAGE = re.compile(r"image:\s*([a-z0-9][a-z0-9._/-]*):([A-Za-z0-9._-]+)\s*\(Python")
REQUIREMENTS = {"scanner": "requirements-scanner.txt", "service": "requirements-service.txt"}


@lru_cache(maxsize=None)
def pinned_image(part: str) -> tuple[str, str] | None:
    """(image name, tag) for "scanner" or "service", from its requirements file. None if unreadable."""
    try:
        text = (ROOT / REQUIREMENTS[part]).read_text(encoding="utf-8")
    except (KeyError, OSError):
        return None
    match = _IMAGE.search(" ".join(text.split()))
    return (match.group(1), match.group(2)) if match else None


def normalize_repo_url(url: object) -> str:
    """A repository URL without scheme, "www.", trailing slash, ".git", or case, for comparison."""
    text = str(url or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


def is_this_repository(url: object) -> bool:
    return bool(url) and normalize_repo_url(url) == normalize_repo_url(REPOSITORY_URL)


def is_release_tag(ref: object) -> bool:
    return isinstance(ref, str) and bool(RELEASE_TAG.match(ref.strip()))
