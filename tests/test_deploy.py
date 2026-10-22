"""Deployment file checks: start_service.sh, the local test Dockerfiles, and .dockerignore.

No Docker or network calls. The image checks themselves run during the
docker build (see docker/check_image.py).
"""

from __future__ import annotations

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
    assert code[-1] == 'exec gunicorn --bind "0.0.0.0:${CIVIS_SERVICE_PORT:-3838}" --workers 2 "app:create_app()"'
    assert not re.search(r"\b(pip3?|install|uv|apt|apt-get|curl|wget)\b", " ".join(code))
