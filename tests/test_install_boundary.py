"""The install script is the one exception to the read-only rule, and the scanner and the app never use it.

install/civis_install.py is run by hand by an admin as a Civis Python script (docs/PLAN.md 17.14). The scanner,
the app, and the shared package must never import it, so nothing they do can reach its write calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("scanner", "app", "common")


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names |= {f"{module}.{alias.name}" if module else alias.name for alias in node.names}
    return names


def test_scanner_app_and_common_never_import_the_install_script():
    offenders = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            names = _imported_modules(ast.parse(text))
            if any(name == "install" or name.startswith("install.") or "civis_install" in name for name in names):
                offenders.append(path.relative_to(ROOT).as_posix())
            elif "civis_install" in text or "importlib" in text and "install/" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_the_install_script_is_not_a_package_the_tool_can_import():
    """No __init__.py: the script stands alone and is loaded by path in tests only."""
    assert not (ROOT / "install" / "__init__.py").exists()
