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
# The scanner job's LOOKBACK_DAYS parameter: which scripts are inventoried (those that ran or changed in this many
# days) and whose most recent run log is read. 400 days, not 365, keeps jobs that run once a year even when a yearly
# run happens a few weeks late (docs/PLAN.md 17.6). The scanner (scanner/config.py) and the app share these.
LOOKBACK_PARAMETER = "LOOKBACK_DAYS"
DEFAULT_LOOKBACK_DAYS = 400
LOOKBACK_DAYS_RANGE = (1, 3650)

# The current tag of each Civis default image, by image name. Civis saves the then-current default tag on an
# object made with no tag: in the live test (November 5, 2026), every test Python, container, and custom script
# created through the API with no tag showed datascience-python tag 9.1, and the test notebook showed
# civis-jupyter-python3 tag 5.1. Update these when Civis changes its default images. No current tag is known for
# civisanalytics/civis-services-streamlit, so Services keep the blank or "latest" rule (docs/PLAN.md 17.8).
# The scanner's default-image rule (scanner/inventory/base.py) and the Findings page's "How to read this page"
# read them from here.
CURRENT_DEFAULT_TAGS = {
    "civisanalytics/datascience-python": "9.1",
    "civisanalytics/civis-jupyter-python3": "5.1",
}


def major_tag(tag: str) -> str:
    """The major part of a tag: the text before the first dot ("9" for "9.1"). Derived, never stored."""
    return tag.strip().split(".", 1)[0]


def default_tag_forms(image: str | None) -> tuple[str, ...]:
    """The tags, besides blank and "latest", that count as an image's default: its current default tag and that
    tag's major part. Empty for an image with no known current tag."""
    current = CURRENT_DEFAULT_TAGS.get(image or "")
    return (current, major_tag(current)) if current else ()


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


def scanner_job_mismatches(job: object) -> tuple[str, ...]:
    """Which identity fields of a container script are not this tool's scanner job: "repository", "command",
    or "image". Empty means it is the scanner job.

    The one identity check, used by the start path (app/pages.py), the job settings (app/job_settings.py),
    and Setup lines 1 and 2 (app/setup.py), so they cannot disagree. The repository is compared like the
    Setup page always did (no scheme, ".git", trailing slash, or case); the command must be exactly the README
    command apart from leading and trailing spaces; the image name must be the pinned scanner image's name.
    The image tag, resources, and parameters are Setup page checks only."""
    if not isinstance(job, dict):
        return ("repository", "command", "image")
    mismatches = []
    if not is_this_repository(job.get("repo_http_uri")):
        mismatches.append("repository")
    if str(job.get("docker_command") or "").strip() != SCANNER_COMMAND:
        mismatches.append("command")
    pinned = pinned_image("scanner")
    if pinned is None or str(job.get("docker_image_name") or "").strip() != pinned[0]:
        mismatches.append("image")
    return tuple(mismatches)


def is_scanner_job(job: object) -> bool:
    return not scanner_job_mismatches(job)


def is_release_tag(ref: object) -> bool:
    return isinstance(ref, str) and bool(RELEASE_TAG.match(ref.strip()))
