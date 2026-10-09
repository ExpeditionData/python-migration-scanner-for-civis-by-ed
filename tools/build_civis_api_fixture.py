"""Build a JSON list of the public civis-python API for one civis version.

The output is used by tests/test_civis_271_compat.py to check that app/ and
common/ only call civis methods and keyword arguments that exist in the civis
version shipped in the Service image.

The script reads files from an extracted civis wheel. It never imports or runs
civis code.

Usage:
    pip download civis==2.7.1 --no-deps --only-binary=:all: -d <tmpdir>
    (unzip the wheel into <tmpdir>/x)
    python tools/build_civis_api_fixture.py <tmpdir>/x/civis tests/fixtures/civis_2_7_1_api.json
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def _params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    args = func.args
    positional = [a.arg for a in args.posonlyargs + args.args if a.arg not in ("self", "cls")]
    keyword_only = [a.arg for a in args.kwonlyargs]
    return {
        "positional": positional,
        "keyword": positional + keyword_only,
        "var_keyword": args.kwarg is not None,
        "var_positional": args.vararg is not None,
    }


def endpoint_methods(client_stub: Path) -> dict:
    """Return {endpoint: {method: params}} from civis/client.pyi."""
    tree = ast.parse(client_stub.read_text(encoding="utf-8"))
    endpoints = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.startswith("_") or node.name.startswith("_Response"):
            continue
        if node.bases:
            continue
        name = node.name[1:].lower()
        methods = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods[item.name] = _params(item)
        endpoints[name] = methods
    return endpoints


def module_functions(paths: list[Path], exported: set[str] | None) -> dict:
    funcs = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                if exported is not None and node.name not in exported:
                    continue
                funcs[node.name] = _params(node)
    return funcs


def dunder_all(init_file: Path) -> set[str]:
    tree = ast.parse(init_file.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return {elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)}
    return set()


def api_client_members(client_py: Path) -> dict:
    tree = ast.parse(client_py.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "APIClient":
            members = {}
            init = None
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == "__init__":
                        init = _params(item)
                    elif not item.name.startswith("_"):
                        members[item.name] = _params(item)
            return {"init": init, "members": sorted(members)}
    raise SystemExit("APIClient class not found")


def main() -> None:
    civis_dir = Path(sys.argv[1])
    out = Path(sys.argv[2])
    io_dir = civis_dir / "io"
    utils_dir = civis_dir / "utils"
    data = {
        "civis_version": (civis_dir / "__init__.py").exists() and _version(civis_dir),
        "endpoints": endpoint_methods(civis_dir / "client.pyi"),
        "io": module_functions(sorted(io_dir.glob("*.py")), dunder_all(io_dir / "__init__.py")),
        "utils": module_functions(
            sorted(utils_dir.glob("*.py")), dunder_all(utils_dir / "__init__.py")
        ),
        "api_client": api_client_members(civis_dir / "client.py"),
    }
    out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out} with {sum(len(m) for m in data['endpoints'].values())} endpoint methods")


def _version(civis_dir: Path) -> str:
    dist = next(civis_dir.parent.glob("civis-*.dist-info"), None)
    if dist is None:
        return "unknown"
    return dist.name.removeprefix("civis-").removesuffix(".dist-info")


if __name__ == "__main__":
    main()
