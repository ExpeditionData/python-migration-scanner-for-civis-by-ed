"""Checks for the local test images. Standard library only; runs inside each image.

    python check_image.py versions <requirements file>
        Fails if the image's Python version, or the version of any package pinned
        in the requirements file, differs from that file. The Python version is
        the "(Python X.Y.Z)" in the file's header comment.

    python check_image.py unchanged <pip freeze before> <pip freeze after>
        Fails if installing the test tools changed or removed any package that
        came with the image, or added anything other than pytest and its
        dependencies.

The Dockerfiles in this folder run both checks while they build, so a build
fails instead of testing against the wrong versions. Local testing only.
"""

from __future__ import annotations

import platform
import re
import sys
from importlib import metadata
from pathlib import Path

# pytest and the packages it pulls in. Nothing else may be added to a test image.
ALLOWED_ADDITIONS = frozenset({"pytest", "pluggy", "iniconfig", "pygments", "packaging"})
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#]+)")
_PYTHON = re.compile(r"\(Python (\d+\.\d+\.\d+)\)")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(path: Path) -> tuple[str | None, dict[str, str]]:
    """The Python version from the header and the exact pins (name==version) in a requirements file."""
    text = path.read_text(encoding="utf-8")
    python = _PYTHON.search(text)
    pins = {}
    for line in text.splitlines():
        match = _PIN.match(line.strip())
        if match:
            pins[normalize(match.group(1))] = match.group(2)
    return (python.group(1) if python else None), pins


def version_problems(path: Path, python_version: str | None = None, installed=None) -> list[str]:
    """Differences between this image and a requirements file. An empty list means they match."""
    python_version = python_version or platform.python_version()
    installed = installed or _installed_version
    expected_python, pins = read_requirements(path)
    problems = []
    if expected_python is None:
        problems.append(f"{path.name} names no Python version in its header")
    elif expected_python != python_version:
        problems.append(f"Python is {python_version}, {path.name} says {expected_python}")
    if not pins:
        problems.append(f"{path.name} has no pinned packages")
    for name, version in sorted(pins.items()):
        found = installed(name)
        if found != version:
            problems.append(f"{name} is {found or 'not installed'}, {path.name} says {version}")
    return problems


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def read_freeze(text: str) -> dict[str, str]:
    """Parse `pip freeze --all` output. Lines like "name @ url" keep the URL as the version."""
    packages = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, _, version = line.partition("==")
        elif " @ " in line:
            name, _, version = line.partition(" @ ")
        else:
            name, version = line, ""
        packages[normalize(name.strip())] = version.strip()
    return packages


def change_problems(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Packages that came with the image must keep their versions. Only pytest and its dependencies may be added."""
    problems = []
    for name, version in sorted(before.items()):
        if name not in after:
            problems.append(f"{name} {version} was removed")
        elif after[name] != version:
            problems.append(f"{name} changed from {version} to {after[name]}")
    for name in sorted(set(after) - set(before)):
        if name not in ALLOWED_ADDITIONS:
            problems.append(f"{name} {after[name]} was added, but only pytest and its dependencies may be")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "versions":
        problems = version_problems(Path(argv[1]))
    elif len(argv) == 3 and argv[0] == "unchanged":
        before = read_freeze(Path(argv[1]).read_text(encoding="utf-8"))
        after = read_freeze(Path(argv[2]).read_text(encoding="utf-8"))
        problems = change_problems(before, after)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for problem in problems:
        print(f"IMAGE CHECK FAILED: {problem}", file=sys.stderr)
    if not problems:
        print(f"Image check passed: {argv[0]}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
