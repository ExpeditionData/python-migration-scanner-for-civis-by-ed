"""Deployment file checks: start_service.sh, the local test Dockerfiles, and .dockerignore.

No Docker or network calls. The image checks themselves run during the
docker build (see docker/check_image.py).
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "start_service.sh"


def _git_mode(path: Path) -> str | None:
    """The file mode git records for ``path``, or None outside a git checkout (for example in a test image)."""
    if not (ROOT / ".git").exists() or shutil.which("git") is None:
        return None
    out = subprocess.run(
        ["git", "ls-files", "--stage", "--", path.relative_to(ROOT).as_posix()],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return out.split()[0] if out else None


# --- start_service.sh ----------------------------------------------------------------------


def test_start_service_is_executable():
    """The Flask image runs ./start_service.sh directly, so it needs the executable bit."""
    mode = _git_mode(START)
    if mode is not None:
        assert mode == "100755", "run: git update-index --chmod=+x start_service.sh"
    if os.name == "posix":
        assert os.access(START, os.X_OK)
    if mode is None and os.name != "posix":
        pytest.skip("no git checkout and no POSIX file modes to check")


def test_start_service_has_lf_line_endings_and_a_shebang():
    data = START.read_bytes()
    assert b"\r" not in data, "start_service.sh must use LF line endings"
    assert data.startswith(b"#!/bin/bash\n")


def test_start_service_runs_gunicorn_and_installs_nothing():
    lines = [line.strip() for line in START.read_text(encoding="utf-8").splitlines()]
    code = [line for line in lines if line and not line.startswith("#")]
    assert "set -euo pipefail" in code
    assert code[-1] == (
        'exec gunicorn --bind "0.0.0.0:${CIVIS_SERVICE_PORT:-3838}" '
        '--worker-class gthread --workers 2 --threads 4 --timeout 120 "app:create_app()"'
    )
    assert not re.search(r"\b(pip3?|install|uv|apt|apt-get|curl|wget)\b", " ".join(code))


# --- Local test images -----------------------------------------------------------------------

DOCKER = ROOT / "docker"
IMAGES = {
    "scanner": ("civisanalytics/datascience-python:9.1.1", "requirements-scanner.txt", "requirements-dev.txt"),
    "service": ("civisanalytics/civis-services-flask:0.1.0", "requirements-service.txt", "requirements-dev-service.txt"),
}
# Paths that must never reach a build context or an image layer.
NEVER_IN_IMAGE = ["Context", ".env", ".env.*", ".venv", ".venv*", "dry_run_output", "Claude outputs", ".git",
                  "**/__pycache__", ".pytest_cache"]


def _load_check_image():
    spec = importlib.util.spec_from_file_location("check_image", DOCKER / "check_image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_image = _load_check_image()


def _readme_digest(image: str) -> str:
    row = next(line for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines() if f"`{image}`" in line)
    return re.search(r"sha256:[0-9a-f]{64}", row).group(0)


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_dockerfiles_use_the_pinned_digest_and_name_the_tag(name):
    image, requirements, dev = IMAGES[name]
    text = (DOCKER / f"{name}.Dockerfile").read_text(encoding="utf-8")
    repo = image.split(":")[0]
    froms = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert froms == [f"FROM {repo}@{_readme_digest(image)}"]
    assert f"# {image} (linux/amd64)" in text
    assert f"check_image.py versions {requirements}" in text
    assert "check_image.py unchanged before.txt after.txt" in text
    # The only package added is pytest, at the same pin as the local test environment.
    installs = re.findall(r"pip install [^;\n]*", text)
    pytest_pin = re.search(r"^pytest==\S+$", (ROOT / dev).read_text(encoding="utf-8"), re.MULTILINE).group(0)
    assert installs == [f"pip install --no-cache-dir --disable-pip-version-check {pytest_pin}"]


def test_dockerignore_keeps_private_and_local_files_out():
    entries = {line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    missing = [entry for entry in NEVER_IN_IMAGE if entry not in entries]
    assert missing == []


def test_requirements_name_the_image_python_version():
    assert check_image.read_requirements(ROOT / "requirements-scanner.txt")[0] == "3.14.6"
    assert check_image.read_requirements(ROOT / "requirements-service.txt")[0] == "3.12.11"


def test_version_check_reports_every_difference(tmp_path):
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("# image (Python 3.12.11)\n-r other.txt\ncivis==2.7.1\nFlask==3.1.1\n", encoding="utf-8")
    versions = {"civis": "2.7.1", "flask": "3.1.1"}
    assert check_image.version_problems(reqs, "3.12.11", versions.get) == []
    problems = check_image.version_problems(reqs, "3.12.10", {"civis": "2.8.0"}.get)
    assert problems == [
        "Python is 3.12.10, requirements.txt says 3.12.11",
        "civis is 2.8.0, requirements.txt says 2.7.1",
        "flask is not installed, requirements.txt says 3.1.1",
    ]


def test_change_check_allows_only_pytest_and_its_dependencies():
    before = check_image.read_freeze("civis==2.7.1\nPyYAML==6.0.2\npackaging==25.0\npip==25.2\n")
    after = check_image.read_freeze(
        "civis==2.7.1\nPyYAML==6.0.2\npackaging==25.0\npip==25.2\npytest==9.1.1\npluggy==1.6.0\niniconfig==2.1.0\n"
    )
    assert check_image.change_problems(before, after) == []
    upgraded = {**after, "pyyaml": "6.0.3"}
    del upgraded["civis"]
    upgraded["requests"] = "2.34.2"
    assert check_image.change_problems(before, upgraded) == [
        "civis 2.7.1 was removed",
        "pyyaml changed from 6.0.2 to 6.0.3",
        "requests 2.34.2 was added, but only pytest and its dependencies may be",
    ]


# --- Inside the test images only (PMSC_TEST_IMAGE is set by the Dockerfiles) ------------------

IN_IMAGE = os.environ.get("PMSC_TEST_IMAGE")
in_image_only = pytest.mark.skipif(IN_IMAGE not in IMAGES, reason="runs only inside the local test images")


@in_image_only
def test_image_versions_match_the_requirements_file():
    assert check_image.version_problems(ROOT / IMAGES[IN_IMAGE][1]) == []


@in_image_only
def test_image_holds_no_private_or_local_files():
    found = [
        path.relative_to(ROOT).as_posix()
        for pattern in ("Context", ".env", ".env.*", ".venv*", "dry_run_output", "Claude outputs", ".git", ".pytest_cache")
        for path in ROOT.glob(pattern)
    ]
    assert found == []
