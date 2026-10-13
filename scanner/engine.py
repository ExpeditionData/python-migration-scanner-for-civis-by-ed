"""Rule engine: runs the YAML rules over code text and log text.

Inputs are code or log text held in memory. Outputs are matches that carry a
rule ID, a file label, and a line number. Nothing here keeps, returns, or logs
the text it scans, so code snippets can never reach a table, a run output, or
a log through the engine.

Python code is parsed with the ast module. If a file does not parse (for
example Python 2 code), the rule's regex fallback runs instead and confidence
drops one level. Shell text (container commands, notebook ! and % lines,
Dockerfile RUN lines), requirements files, and pyproject.toml are checked with
regex and install-requirement rules. Log text is checked with log patterns.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterable

from common.models import CivisObject, Finding, FindingSource, Impact, Level
from common.rule_loader import Rule, RuleSet, load_rules

# Files larger than this are checked with regex only, to keep memory bounded.
MAX_PYTHON_CHARS = 2_000_000


@dataclass(frozen=True)
class Match:
    rule_id: str
    file: str
    line_number: int | None
    confidence: Level
    source: FindingSource = FindingSource.CODE


@dataclass
class ScanResult:
    matches: list[Match] = field(default_factory=list)
    # True if every Python unit parsed. False means regex fallback was used for
    # at least one unit, so the object's coverage is "partial".
    fully_parsed: bool = True
    notes: list[str] = field(default_factory=list)
    # Exception type names for parse failures, for the scanner log. Never messages.
    parse_errors: list[str] = field(default_factory=list)

    def extend(self, other: "ScanResult") -> None:
        self.matches.extend(other.matches)
        self.fully_parsed = self.fully_parsed and other.fully_parsed
        self.notes.extend(other.notes)
        self.parse_errors.extend(other.parse_errors)


def kind_for_path(path: str) -> str | None:
    """Return how to scan a repo file, or None to skip it."""
    name = PurePosixPath(path).name.lower()
    if name.endswith(".py"):
        return "python"
    if name.endswith(".ipynb"):
        return "notebook"
    if name.startswith("requirements") and name.endswith(".txt"):
        return "requirements"
    if name == "pyproject.toml":
        return "pyproject"
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    return None


def _downgrade(level: Level) -> Level:
    return {Level.HIGH: Level.MEDIUM, Level.MEDIUM: Level.LOW, Level.LOW: Level.LOW}[level]


# ---------------------------------------------------------------------------
# Python AST support
# ---------------------------------------------------------------------------


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _attr_tail(node: ast.AST) -> str | None:
    """Attribute names on a chain, ignoring what the chain starts from.

    client.users.list_me -> "users.list_me"; APIClient().users.list_me -> "users.list_me".
    """
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join(reversed(parts)) if parts else None


def _last_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _qual_match(name: str | None, targets: Iterable[str]) -> bool:
    if not name:
        return False
    return any(name == t or name.startswith(t + ".") for t in targets)


class _Context:
    """Import aliases and module-level string constants for one Python unit."""

    def __init__(self, tree: ast.AST, extra_aliases: dict[str, str] | None = None):
        self.aliases: dict[str, str] = dict(extra_aliases or {})
        self.imported: set[str] = set()
        self.star_modules: set[str] = set()
        self.str_constants: dict[str, str] = {}
        collect_imports(tree, self.aliases, self.imported, self.star_modules)
        self._collect_constants(tree)

    def _collect_constants(self, tree: ast.AST) -> None:
        counts: dict[str, int] = {}
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        counts[target.id] = counts.get(target.id, 0) + 1
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            self.str_constants[target.id] = node.value.value
        for name, count in counts.items():
            if count > 1:
                self.str_constants.pop(name, None)

    def resolve(self, dotted: str | None) -> str | None:
        if not dotted:
            return None
        head, _, rest = dotted.partition(".")
        head = self.aliases.get(head, head)
        return f"{head}.{rest}" if rest else head

    def imports_any(self, modules: Iterable[str]) -> bool:
        return any(_qual_match(m, modules) for m in self.imported)

    def string_value(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.str_constants.get(node.id)
        return None


def collect_imports(
    tree: ast.AST,
    aliases: dict[str, str],
    imported: set[str] | None = None,
    star_modules: set[str] | None = None,
) -> None:
    imported = imported if imported is not None else set()
    star_modules = star_modules if star_modules is not None else set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    aliases.setdefault(top, top)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
            for alias in node.names:
                if alias.name == "*":
                    star_modules.add(node.module)
                    continue
                full = f"{node.module}.{alias.name}"
                imported.add(full)
                aliases[alias.asname or alias.name] = full


# Each AST matcher: (node types it looks at, function(node, ctx, params) -> bool)


def _m_call(node: ast.Call, ctx: _Context, p: dict) -> bool:
    func = node.func
    last = _last_name(func)
    dotted = _dotted(func)
    resolved = ctx.resolve(dotted)
    hit = False
    if p.get("names") and last in p["names"]:
        hit = True
    if p.get("qualnames") and resolved in p["qualnames"]:
        hit = True
    if p.get("suffixes"):
        tail = _attr_tail(func)
        if tail and any(tail == s or tail.endswith("." + s) for s in p["suffixes"]):
            hit = True
    if not hit:
        return False

    if p.get("requires_import") and not ctx.imports_any(p["requires_import"]):
        return False
    receiver = func.value if isinstance(func, ast.Attribute) else None
    receiver_call = ctx.resolve(_dotted(receiver.func)) if isinstance(receiver, ast.Call) else None
    if p.get("receiver_call_qualnames") and receiver_call not in p["receiver_call_qualnames"]:
        return False
    if p.get("exclude_receiver_call_qualnames") and receiver_call in p["exclude_receiver_call_qualnames"]:
        return False

    positional = [a for a in node.args if not isinstance(a, ast.Starred)]
    has_star_args = len(positional) != len(node.args)
    keywords = {k.arg for k in node.keywords if k.arg}
    has_star_kwargs = any(k.arg is None for k in node.keywords)

    if p.get("keywords_absent"):
        # With **kwargs we cannot tell what is passed, so do not guess.
        if has_star_kwargs or keywords & set(p["keywords_absent"]):
            return False
    if "max_positional" in p and (has_star_args or len(positional) > p["max_positional"]):
        return False

    triggers = [k for k in ("keywords_any", "or_min_positional", "min_positional", "min_args") if k in p]
    if not triggers:
        return True
    if p.get("keywords_any") and keywords & set(p["keywords_any"]):
        return True
    for key in ("or_min_positional", "min_positional"):
        if key in p and len(positional) >= p[key]:
            return True
    if "min_args" in p and len(node.args) + len(node.keywords) >= p["min_args"]:
        return True
    return False


def _m_attribute(node: ast.AST, ctx: _Context, p: dict) -> bool:
    names = p.get("names") or []
    if p.get("requires_import") and not ctx.imports_any(p["requires_import"]):
        return False
    if isinstance(node, ast.Attribute):
        if node.attr not in names:
            return False
        excluded = p.get("exclude_receiver_calls")
        if excluded and isinstance(node.value, ast.Call) and _last_name(node.value.func) in excluded:
            return False
        return True
    if isinstance(node, ast.Name):
        return bool(p.get("include_bare_names")) and node.id in names
    return False


def _m_reference(node: ast.AST, ctx: _Context, p: dict) -> bool:
    targets = p.get("qualnames") or []
    if isinstance(node, ast.Import):
        return any(_qual_match(a.name, targets) for a in node.names)
    if isinstance(node, ast.ImportFrom):
        if not node.module or node.level:
            return False
        if _qual_match(node.module, targets):
            return True
        return any(_qual_match(f"{node.module}.{a.name}", targets) for a in node.names if a.name != "*")
    if isinstance(node, ast.Name):
        if node.id in ctx.aliases:
            return _qual_match(ctx.aliases[node.id], targets)
        return node.id in (p.get("bare_names") or [])
    if isinstance(node, ast.Attribute):
        return _qual_match(ctx.resolve(_dotted(node)), targets)
    return False


def _m_strptime_no_year(node: ast.Call, ctx: _Context, p: dict) -> bool:
    if _last_name(node.func) != "strptime":
        return False
    fmt_node = node.args[1] if len(node.args) >= 2 else None
    for kw in node.keywords:
        if kw.arg == "format":
            fmt_node = kw.value
    fmt = ctx.string_value(fmt_node)
    if fmt is None:
        return False
    fmt = fmt.replace("%%", "")
    return "%d" in fmt and not any(d in fmt for d in ("%Y", "%y", "%G"))


_SHORT_OPT = re.compile(r"^-[^-]$")
_SINGLE_DASH_LONG = re.compile(r"^-[^-].+$")


def _m_argparse_dest(node: ast.Call, ctx: _Context, p: dict) -> bool:
    if _last_name(node.func) != "add_argument":
        return False
    if any(k.arg == "dest" for k in node.keywords):
        return False
    options = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    if not options or len(options) != len(node.args):
        return False
    return (
        any(_SHORT_OPT.match(o) for o in options)
        and any(_SINGLE_DASH_LONG.match(o) for o in options)
        and not any(o.startswith("--") for o in options)
    )


def _is_typing_name(node: ast.AST, ctx: _Context, name: str) -> bool:
    dotted = _dotted(node)
    if dotted == name and name not in ctx.aliases:
        return "typing" in ctx.star_modules
    return ctx.resolve(dotted) == f"typing.{name}"


def _m_namedtuple_kwargs(node: ast.Call, ctx: _Context, p: dict) -> bool:
    if not _is_typing_name(node.func, ctx, "NamedTuple"):
        return False
    return len(node.args) == 1 and any(k.arg for k in node.keywords)


def _m_typeddict_no_fields(node: ast.Call, ctx: _Context, p: dict) -> bool:
    if not _is_typing_name(node.func, ctx, "TypedDict"):
        return False
    fields_node = node.args[1] if len(node.args) >= 2 else None
    for kw in node.keywords:
        if kw.arg == "fields":
            fields_node = kw.value
    if len(node.args) < 1:
        return False
    if fields_node is None:
        return True
    return isinstance(fields_node, ast.Constant) and fields_node.value is None


def _m_star_import_name(node: ast.Name, ctx: _Context, p: dict) -> bool:
    if node.id not in (p.get("names") or []) or node.id in ctx.aliases:
        return False
    return bool(ctx.star_modules & set(p.get("modules") or []))


def _is_negative_number(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and node.operand.value > 0
    )


def _m_rlimit_negative(node: ast.AST, ctx: _Context, p: dict) -> bool:
    if isinstance(node, ast.Call):
        if ctx.resolve(_dotted(node.func)) not in ("resource.setrlimit", "resource.prlimit"):
            return False
        return any(_is_negative_number(n) for arg in node.args for n in ast.walk(arg))
    if isinstance(node, ast.Compare):
        sides = [node.left, *node.comparators]
        has_infinity = any(ctx.resolve(_dotted(s)) == "resource.RLIM_INFINITY" for s in sides)
        return has_infinity and any(_is_negative_number(s) for s in sides)
    return False


def _m_loader_subclass_load_module(node: ast.ClassDef, ctx: _Context, p: dict) -> bool:
    bases = [ctx.resolve(_dotted(b)) or "" for b in node.bases]
    if not any(b.split(".")[-1].endswith("Loader") for b in bases):
        return False
    methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return "load_module" in methods and "exec_module" not in methods


AST_MATCHER_IMPLS: dict[str, tuple[tuple[type, ...], Callable]] = {
    "call": ((ast.Call,), _m_call),
    "attribute": ((ast.Attribute, ast.Name), _m_attribute),
    "reference": ((ast.Import, ast.ImportFrom, ast.Name, ast.Attribute), _m_reference),
    "strptime_no_year": ((ast.Call,), _m_strptime_no_year),
    "argparse_dest": ((ast.Call,), _m_argparse_dest),
    "namedtuple_kwargs": ((ast.Call,), _m_namedtuple_kwargs),
    "typeddict_no_fields": ((ast.Call,), _m_typeddict_no_fields),
    "star_import_name": ((ast.Name,), _m_star_import_name),
    "rlimit_negative": ((ast.Call, ast.Compare), _m_rlimit_negative),
    "loader_subclass_load_module": ((ast.ClassDef,), _m_loader_subclass_load_module),
}


# ---------------------------------------------------------------------------
# Install requirement support
# ---------------------------------------------------------------------------

_SPEC = re.compile(r"(===|==|~=|!=|<=|>=|<|>)\s*([^,;\s]+)")
_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?P<spec>[^;]*)")
_PIP_INSTALL = re.compile(
    r"(?:^|[\s;&|(`])(?:(?:python[0-9.]*|py)\s+-m\s+)?(?:uv\s+)?pip[0-9.]*\s+install\b(?P<args>[^;&|\n`)]*)"
)
# pip options that take a value, so the next token is not a requirement.
_PIP_VALUE_OPTIONS = {
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root",
    "--trusted-host", "--platform", "--python-version", "--implementation", "--abi",
    "--only-binary", "--no-binary", "--cache-dir", "--src", "--upgrade-strategy",
    "--progress-bar", "--log", "--timeout", "--retries", "--proxy", "--cert", "--client-cert",
    "--report", "--python",
}


@dataclass(frozen=True)
class Requirement:
    name: str
    specs: tuple[tuple[str, str], ...]


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(text: str) -> Requirement | None:
    text = text.strip().strip("'\"")
    if not text or text.startswith(("-", "#", ".", "/", "$")) or "://" in text or " @ " in text:
        return None
    if text.endswith((".whl", ".tar.gz", ".zip", ".txt")):
        return None
    m = _REQUIREMENT.match(text)
    if not m:
        return None
    specs = tuple(_SPEC.findall(m.group("spec")))
    rest = _SPEC.sub("", m.group("spec")).replace(",", "").strip()
    if rest:  # Something we do not understand, such as a path.
        return None
    return Requirement(_normalize(m.group("name")), specs)


def _version_tuple(version: str) -> tuple[int, ...] | None:
    parts = []
    for piece in version.split("."):
        if piece == "*":
            break
        digits = re.match(r"\d+", piece)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts) or None


def _below_major(op: str, version: str, major: int) -> bool | None:
    """True if this spec keeps the package below ``major``; None if it targets ``major`` or later."""
    v = _version_tuple(version)
    if v is None:
        return False
    if op == "<":
        return v[0] < major or (v[0] == major and all(x == 0 for x in v[1:]))
    if op == "<=":
        return v[0] < major
    if op in ("==", "===", "~="):
        return True if v[0] < major else None
    if op in (">=", ">") and v[0] >= major:
        return None
    return False


def _install_unbounded_major(req: Requirement, p: dict) -> bool:
    if req.name != _normalize(p["package"]):
        return False
    major = int(p["below_major"])
    bounded = False
    for op, version in req.specs:
        result = _below_major(op, version, major)
        if result is None:
            return False  # Explicitly asks for the new major version.
        bounded = bounded or result
    return not bounded


def _install_pinned_or_upper_bound(req: Requirement, p: dict) -> bool:
    return any(op in ("==", "===", "<", "<=", "~=") for op, _ in req.specs)


INSTALL_MATCHER_IMPLS: dict[str, Callable[[Requirement, dict], bool]] = {
    "unbounded_major": _install_unbounded_major,
    "pinned_or_upper_bound": _install_pinned_or_upper_bound,
}


def pip_install_requirements(command: str) -> list[Requirement]:
    """Requirements named on pip install command lines in shell text."""
    found = []
    for m in _PIP_INSTALL.finditer(command):
        args = m.group("args")
        try:
            tokens = shlex.split(args, comments=True)
        except ValueError:
            tokens = args.split()
        skip_next = False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            if token.startswith("-"):
                if token in _PIP_VALUE_OPTIONS:
                    skip_next = True
                continue
            req = parse_requirement(token)
            if req:
                found.append(req)
    return found


def _poetry_requirement(name: str, value) -> Requirement | None:
    if isinstance(value, dict):
        value = value.get("version")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value in ("", "*"):
        return Requirement(_normalize(name), ())
    if value[0] in "^~" and _version_tuple(value[1:]):
        return Requirement(_normalize(name), (("~=", value[1:]),))
    if _version_tuple(value) and not _SPEC.search(value):
        return Requirement(_normalize(name), (("==", value),))
    return parse_requirement(f"{name}{value}")


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class Engine:
    def __init__(self, rules: RuleSet | None = None):
        self.rules = rules if rules is not None else load_rules()
        self._python_rules = [r for r in self.rules if r.checks_python]
        self._shell_rules = [r for r in self.rules if r.checks_shell]
        self._install_rules = [r for r in self.rules if r.install]
        self._log_rules = [r for r in self.rules if r.checks_logs and not r.log_fallback]
        self._log_fallbacks = [r for r in self.rules if r.checks_logs and r.log_fallback]
        self._ast_index: dict[type, list[tuple[Rule, dict, Callable]]] = {}
        for rule in self._python_rules:
            for matcher in rule.ast:
                node_types, func = AST_MATCHER_IMPLS[matcher["match"]]
                for node_type in node_types:
                    self._ast_index.setdefault(node_type, []).append((rule, matcher, func))

    # -- Python -------------------------------------------------------------

    def scan_python(self, text: str, label: str, extra_aliases: dict[str, str] | None = None) -> ScanResult:
        if len(text) > MAX_PYTHON_CHARS:
            return ScanResult(self._python_regex(text, label), False, [f"{label}: too large to parse"], ["FileTooLarge"])
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            return ScanResult(
                self._python_regex(text, label), False, [f"{label}: could not be parsed"], [type(exc).__name__]
            )
        try:
            matches = self._python_ast(tree, label, extra_aliases)
        except RecursionError:
            return ScanResult(self._python_regex(text, label), False, [f"{label}: too deeply nested"], ["RecursionError"])
        return ScanResult(matches, True)

    def _python_ast(self, tree: ast.AST, label: str, extra_aliases: dict[str, str] | None) -> list[Match]:
        ctx = _Context(tree, extra_aliases)
        seen: set[tuple[str, int]] = set()
        matches = []
        for node in ast.walk(tree):
            for rule, matcher, func in self._ast_index.get(type(node), ()):
                line = getattr(node, "lineno", None)
                key = (rule.id, line)
                if key in seen:
                    continue
                if func(node, ctx, matcher):
                    seen.add(key)
                    matches.append(Match(rule.id, label, line, rule.confidence))
        return sorted(matches, key=lambda m: (m.line_number or 0, m.rule_id))

    def _python_regex(self, text: str, label: str) -> list[Match]:
        matches = []
        for number, line in enumerate(text.splitlines(), start=1):
            for rule in self._python_rules:
                if rule.python_regex and rule.python_regex.search(line):
                    matches.append(Match(rule.id, label, number, _downgrade(rule.confidence)))
        return matches

    # -- Shell, requirements, pyproject, Dockerfile --------------------------

    def scan_shell(self, text: str, label: str, first_line: int = 1) -> ScanResult:
        matches = []
        for number, line in _logical_lines(text, first_line):
            matches.extend(self._shell_line(line, label, number))
        return ScanResult(matches)

    def _shell_line(self, line: str, label: str, number: int) -> list[Match]:
        matches = []
        for rule in self._shell_rules:
            if rule.shell_regex and rule.shell_regex.search(line):
                matches.append(Match(rule.id, label, number, rule.confidence))
        for req in pip_install_requirements(line):
            matches.extend(self._requirement_matches(req, label, number))
        return _dedupe(matches)

    def _requirement_matches(self, req: Requirement, label: str, number: int) -> list[Match]:
        matches = []
        for rule in self._install_rules:
            for matcher in rule.install:
                if INSTALL_MATCHER_IMPLS[matcher["match"]](req, matcher):
                    matches.append(Match(rule.id, label, number, rule.confidence))
                    break
        return matches

    def scan_requirements(self, text: str, label: str) -> ScanResult:
        matches = []
        for number, line in _logical_lines(text, 1):
            line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
            req = parse_requirement(line)
            if req:
                matches.extend(self._requirement_matches(req, label, number))
        return ScanResult(_dedupe(matches))

    def scan_pyproject(self, text: str, label: str) -> ScanResult:
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError):
            return ScanResult([], True, [f"{label}: could not be parsed"])
        lines = text.splitlines()
        matches = []
        project = data.get("project") or {}
        dep_strings = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            dep_strings.extend(group or [])
        for dep in dep_strings:
            if isinstance(dep, str):
                req = parse_requirement(dep)
                if req:
                    matches.extend(self._requirement_matches(req, label, _find_line(lines, dep)))
        poetry = ((data.get("tool") or {}).get("poetry") or {})
        poetry_groups = [poetry.get("dependencies") or {}]
        poetry_groups += [g.get("dependencies") or {} for g in (poetry.get("group") or {}).values() if isinstance(g, dict)]
        for group in poetry_groups:
            for name, value in group.items():
                if name.lower() == "python":
                    continue
                req = _poetry_requirement(name, value)
                if req:
                    matches.extend(self._requirement_matches(req, label, _find_line(lines, name, key=True)))
        return ScanResult(_dedupe(matches))

    def scan_dockerfile(self, text: str, label: str) -> ScanResult:
        matches = []
        for number, line in _logical_lines(text, 1):
            stripped = line.lstrip()
            if stripped[:4].upper() == "RUN " or stripped[:4].upper() == "RUN\t":
                matches.extend(self._shell_line(stripped[4:], label, number))
        return ScanResult(matches)

    # -- Notebooks ------------------------------------------------------------

    def scan_notebook(self, text: str, label: str) -> ScanResult:
        """Scan code cells as Python, ! and % lines as shell, and outputs as log text."""
        try:
            notebook = json.loads(text)
            cells = notebook.get("cells") or []
        except (ValueError, AttributeError) as exc:
            return ScanResult([], False, [f"{label}: notebook file could not be read"], [type(exc).__name__])

        prepared = []
        aliases: dict[str, str] = {}
        for index, cell in enumerate(cells, start=1):
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source = _cell_text(cell.get("source"))
            python_text, shell_lines, is_shell_cell = _split_cell(source)
            prepared.append((index, cell, source, python_text, shell_lines, is_shell_cell))
            if python_text and not is_shell_cell:
                try:
                    collect_imports(ast.parse(python_text), aliases)
                except (SyntaxError, ValueError, RecursionError, MemoryError):
                    pass

        result = ScanResult()
        for index, cell, source, python_text, shell_lines, is_shell_cell in prepared:
            cell_label = f"{label} [cell {index}]"
            if is_shell_cell:
                result.extend(self.scan_shell(source, cell_label))
            else:
                if python_text.strip():
                    result.extend(self.scan_python(python_text, cell_label, extra_aliases=aliases))
                for number, line in shell_lines:
                    result.matches.extend(self._shell_line(line, cell_label, number))
            output_text = _cell_output_text(cell.get("outputs"))
            if output_text:
                result.matches.extend(
                    self.scan_log(output_text, f"{label} [cell {index} output]", FindingSource.NOTEBOOK_OUTPUT)
                )
        return result

    # -- Logs -----------------------------------------------------------------

    def scan_log(self, log: str | Iterable[str], label: str, source: FindingSource = FindingSource.LOG) -> list[Match]:
        """One match per rule per log, at the first line where it appears."""
        lines = log.splitlines() if isinstance(log, str) else log
        found: dict[str, Match] = {}
        for number, line in enumerate(lines, start=1):
            if not isinstance(line, str):
                continue
            matched_families = set()
            for rule in self._log_rules:
                if _log_hit(rule, line):
                    matched_families.add(rule.family)
                    found.setdefault(rule.id, Match(rule.id, label, number, rule.log_confidence, source))
            for rule in self._log_fallbacks:
                if rule.family not in matched_families and _log_hit(rule, line):
                    found.setdefault(rule.id, Match(rule.id, label, number, rule.log_confidence, source))
        return sorted(found.values(), key=lambda m: (m.line_number or 0, m.rule_id))

    # -- Repo files -------------------------------------------------------------

    def scan_file(self, path: str, text: str) -> ScanResult | None:
        kind = kind_for_path(path)
        if kind == "python":
            return self.scan_python(text, path)
        if kind == "notebook":
            return self.scan_notebook(text, path)
        if kind == "requirements":
            return self.scan_requirements(text, path)
        if kind == "pyproject":
            return self.scan_pyproject(text, path)
        if kind == "dockerfile":
            return self.scan_dockerfile(text, path)
        return None

    # -- Findings ---------------------------------------------------------------

    def to_findings(self, obj: CivisObject, matches: Iterable[Match]) -> list[Finding]:
        findings = []
        seen = set()
        for match in matches:
            rule = self.rules.get(match.rule_id)
            if rule.default_image_only and obj.uses_default_image is not True:
                continue
            if rule.impact == "advisory":
                impact = Impact.ADVISORY
            elif rule.impact == "affected":
                impact = Impact.AFFECTED
            else:
                impact = obj.impact
            key = (match.rule_id, match.file, match.line_number, match.source)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    object_id=obj.object_id,
                    object_type=obj.object_type,
                    rule_id=rule.id,
                    file=match.file,
                    line_number=match.line_number,
                    source=match.source,
                    severity=rule.severity,
                    confidence=match.confidence,
                    impact=impact,
                    fix_guidance=rule.fix,
                    workflows=tuple(obj.workflows),
                )
            )
        return findings


def _log_hit(rule: Rule, line: str) -> bool:
    return any(p.search(line) for p in rule.log_patterns) and not any(e.search(line) for e in rule.log_exclude)


def _dedupe(matches: list[Match]) -> list[Match]:
    seen = set()
    result = []
    for m in matches:
        key = (m.rule_id, m.file, m.line_number)
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result


def _logical_lines(text: str, first_line: int) -> list[tuple[int, str]]:
    """Join backslash-continued lines. Returns (starting line number, joined text)."""
    result = []
    buffer: list[str] = []
    start = first_line
    for offset, raw in enumerate(text.splitlines()):
        if not buffer:
            start = first_line + offset
        if raw.rstrip().endswith("\\"):
            buffer.append(raw.rstrip()[:-1])
            continue
        buffer.append(raw)
        result.append((start, " ".join(buffer)))
        buffer = []
    if buffer:
        result.append((start, " ".join(buffer)))
    return result


def _find_line(lines: list[str], needle: str, key: bool = False) -> int | None:
    for number, line in enumerate(lines, start=1):
        if key:
            if re.match(rf"^\s*[\"']?{re.escape(needle)}[\"']?\s*=", line):
                return number
        elif needle in line:
            return number
    return None


def _cell_text(source) -> str:
    if isinstance(source, list):
        return "".join(s for s in source if isinstance(s, str))
    return source if isinstance(source, str) else ""


_SHELL_CELL_MAGICS = ("%%bash", "%%sh", "%%script", "%%system")


def _split_cell(source: str) -> tuple[str, list[tuple[int, str]], bool]:
    """Split a notebook cell into Python text and shell lines, keeping line numbers.

    IPython lines that start with ! or % are blanked in the Python text and
    returned as shell lines. Cells that start with a shell cell magic are shell.
    """
    lines = source.splitlines()
    first = lines[0].strip() if lines else ""
    if first.startswith(_SHELL_CELL_MAGICS):
        return "", [], True
    python_lines = []
    shell_lines = []
    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if number == 1 and stripped.startswith("%%"):
            python_lines.append("")  # Other cell magics, such as %%time. The rest is Python.
        elif stripped.startswith(("!", "%")):
            shell_lines.append((number, stripped.lstrip("!%")))
            python_lines.append("")
        else:
            python_lines.append(line)
    return "\n".join(python_lines), shell_lines, False


def _cell_output_text(outputs) -> str:
    parts = []
    for output in outputs or []:
        if not isinstance(output, dict):
            continue
        if output.get("output_type") == "stream":
            parts.append(_cell_text(output.get("text")))
        elif output.get("output_type") == "error":
            parts.extend(t for t in output.get("traceback") or [] if isinstance(t, str))
    return "\n".join(parts)
