"""The product and repository names.

The tool is "Python Migration Scanner for Civis Platform by Expedition Data".
The old product name may appear only in docs/PLAN.md, where the rename is
recorded. The old repository name and its short prefix appear nowhere.

The old names are built from parts here, so this file does not match itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_PRODUCT = re.compile(r"\s+".join(["Civis", "Python", "Version", "Search"]), re.IGNORECASE)
OLD_SLUG = re.compile("-".join(["civis", "python", "version", "search"]), re.IGNORECASE)
OLD_PREFIX = re.compile("c" + "pvs", re.IGNORECASE)
SKIPPED_DIRS = {".git", ".venv", ".venv312", "Context", "dry_run_output", "Claude outputs", "__pycache__", ".pytest_cache"}


def _repo_files() -> list[Path]:
    """Tracked files in a git checkout. In the test images (no .git), every file under /app,
    which .dockerignore has already limited to the repository's own files."""
    if (ROOT / ".git").exists() and shutil.which("git"):
        names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / name for name in names.splitlines()]
    return [
        path for path in ROOT.rglob("*")
        if path.is_file() and not SKIPPED_DIRS.intersection(path.relative_to(ROOT).parts)
    ]


def _text_files():
    for path in _repo_files():
        try:
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # Fonts and other binary files.


def test_old_product_name_appears_only_where_the_rename_is_recorded():
    found = [name for name, text in _text_files() if OLD_PRODUCT.search(text) and name != "docs/PLAN.md"]
    assert found == []


def test_old_repository_name_and_prefix_appear_nowhere():
    found = [name for name, text in _text_files() if OLD_SLUG.search(text) or OLD_PREFIX.search(text)]
    assert found == []


def test_the_new_name_leads_in_the_readme_notice_and_security_page():
    full = "Python Migration Scanner for Civis Platform by Expedition Data"
    assert (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0] == f"# {full}"
    assert (ROOT / "NOTICE").read_text(encoding="utf-8").splitlines()[0] == full
    security = re.sub(r"\s+", " ", (ROOT / "SECURITY.md").read_text(encoding="utf-8"))
    assert full in security
    assert security.index("Python Migration Scanner") == security.index(full)  # The first mention is the full name.


TRADEMARKS = (
    "The Expedition Data name and logo are trademarks of Expedition Data LLC and are not licensed under the "
    "Apache License 2.0. Civis and Civis Platform are trademarks of Civis Analytics; Expedition Data LLC is not "
    "affiliated with Civis Analytics."
)


def test_notice_is_the_reviewed_text_and_keeps_the_fonts_paragraph():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert notice.startswith(
        "Python Migration Scanner for Civis Platform by Expedition Data\n"
        "Copyright 2026 Expedition Data LLC.\n\n"
        "This product includes software developed by Expedition Data LLC.\n\n"
        "Licensed under the Apache License, Version 2.0. See the LICENSE file for the full license text.\n\n"
        + TRADEMARKS + "\n\n"
        "This product includes fonts licensed under the SIL Open Font License,\n"
    )


def test_readme_license_section_has_the_trademark_sentences():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    license_section = " ".join(readme[readme.index("## License"):].split())
    assert TRADEMARKS in license_section
