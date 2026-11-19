"""What the README tells admins to set on the scanner job and the Service.

The Setup page (app/setup.py) compares the live settings with these. The
pinned image names and tags are read from the header of each requirements file
(``image: <name>:<tag> (Python X.Y.Z)``), the same lines docker/check_image.py
reads for the Python version, so there is one source of truth. A test checks
that the README shows the same values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_URL = "https://github.com/ExpeditionData/python-migration-scanner-for-civis-by-ed.git"
SCANNER_COMMAND = "cd /app && python -m scanner"
HEALTH_PATH = "/healthz"  # The Service's CIVIS_SERVICE_HEALTH_PATH.


@dataclass(frozen=True)
class ResourceProfile:
    """The scanner job's resources: CPU shares (1000 is one core), memory in MB, and disk in GB."""

    name: str
    cpu: int
    memory_mb: int
    disk_gb: int


# Two profiles, picked by whether the job attaches a GitHub token (docs/PLAN.md 17.14). With a token: the GitHub
# file cache holds at most 1 GB of text (scanner CACHE_MAX_BYTES), each tarball is streamed to the temp directory
# (at most 200 MB, TARBALL_MAX_BYTES, one per thread), and each notebook is held in memory while it is scanned (at
# most 50 MB, NOTEBOOK_MAX_BYTES, one per thread); 1 GB plus the live test's 215 MB peak plus four notebooks fits in
# 4096 MB. Without one: no cache and no tarballs. In three live-test scans CPU peaked at 45 millicores, memory holds
# the 215 MB peak plus one 50 MB notebook per thread, and the disk holds only the clone (about 2 MB) and the CSV.
PROFILE_WITH_TOKEN = ResourceProfile("with_token", cpu=1000, memory_mb=4096, disk_gb=4)
PROFILE_WITHOUT_TOKEN = ResourceProfile("without_token", cpu=500, memory_mb=2048, disk_gb=1)
MEMORY_PER_EXTRA_THREAD_MB = 512  # For each MAX_CONCURRENCY thread above the default of 4, in both profiles.
DEFAULT_THREADS = 4


def resource_profile(github_attached: bool) -> ResourceProfile:
    return PROFILE_WITH_TOKEN if github_attached else PROFILE_WITHOUT_TOKEN


def memory_needed(profile: ResourceProfile, threads: int) -> int:
    """The memory a job needs: the profile's, plus 512 MB for each thread above 4."""
    return profile.memory_mb + MEMORY_PER_EXTRA_THREAD_MB * max(0, threads - DEFAULT_THREADS)


# The scanner job's parameters as the README and the install script set them: name and Civis parameter type.
PARAMETER_TYPES = {
    "RESULTS_DB": "database",
    "RESULTS_SCHEMA": "string",
    "GITHUB": "credential_custom",
    "LOOKBACK_DAYS": "integer",
    "MAX_CONCURRENCY": "integer",
    "REQUEST_BUDGET": "integer",
    "RETENTION_DAYS": "integer",
}
REQUIRED_PARAMETERS = ("RESULTS_DB", "RESULTS_SCHEMA")
RETENTION_DAYS_RANGE = (0, 36500)  # 0 or blank keeps every scan (scanner/config.py).
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

# The tags Civis has saved as its default on objects made with no tag, by image name, oldest first. In the live
# test, objects made on November 5, 2026 showed datascience-python 9.1 and civis-jupyter-python3 5.1, and a
# container script made on November 19, 2026 showed datascience-python 9.2 (Python 3.14.10, civis-python 2.11.0).
# civis-jupyter-python3 5.2.0 was released on November 10, 2026 on datascience-python 9.2.0 and is listed before it
# was seen in the live test. A tag stays on the list after Civis moves on, because objects saved on it were made to
# follow the default. Add the newest tag when Civis changes its default images. No tag is known for
# civisanalytics/civis-services-streamlit, so Services keep the blank or "latest" rule (docs/PLAN.md 17.8 and
# 17.15). The scanner's default-image rule (scanner/inventory/base.py) and the Findings page's "How to read this
# page" read them from here.
CURRENT_DEFAULT_TAGS = {
    "civisanalytics/datascience-python": ("9.1", "9.2"),
    "civisanalytics/civis-jupyter-python3": ("5.1", "5.2"),
}
# A minor tag: two runs of digits joined by one dot (9.2). ASCII digits only.
_MINOR_TAG = re.compile(r"[0-9]+\.[0-9]+")


def major_tag(tag: str) -> str:
    """The major part of a tag: the text before the first dot ("9" for "9.1"). Derived, never stored."""
    return tag.strip().split(".", 1)[0]


def default_tag_forms(image: str | None) -> tuple[str, ...]:
    """The tags, besides blank and "latest", that count as an image's default: every listed tag in
    CURRENT_DEFAULT_TAGS, then each listed tag's major part, with duplicates removed, in that order
    (("9.1", "9.2", "9") for datascience-python). Empty for an image with no listed tags."""
    listed = CURRENT_DEFAULT_TAGS.get(image or "", ())
    return tuple(dict.fromkeys([*listed, *(major_tag(tag) for tag in listed)]))


def is_newer_minor(image: str | None, tag: str | None) -> bool:
    """True when ``tag`` is a minor tag newer than every listed tag in its major: the stripped tag is two runs of
    digits joined by one dot, its major equals the major of a listed tag for ``image``, and its minor, as an
    integer, is greater than the largest listed minor in that major (9.3 and 9.10 with 9.1 and 9.2 listed).
    Civis may have moved its default to it since this release (docs/PLAN.md 17.15). False for an image with no
    listed tags. Derived, never stored."""
    tag = (tag or "").strip()
    if not _MINOR_TAG.fullmatch(tag):
        return False
    major, minor = tag.split(".")
    listed = (t.strip() for t in CURRENT_DEFAULT_TAGS.get(image or "", ()))
    minors = [int(t.split(".")[1]) for t in listed if _MINOR_TAG.fullmatch(t) and t.split(".")[0] == major]
    return bool(minors) and int(minor) > max(minors)


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
