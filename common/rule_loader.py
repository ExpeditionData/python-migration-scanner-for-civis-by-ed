"""Load and validate the YAML rule files in common/rules/.

Each file holds one rule family. A rule says what to look for (AST matchers for
Python code, regex for text, install-requirement checks, log patterns), how
severe and how certain a match is, and how to fix it. The civis-python v3 file
also has a ``renames`` table that expands into one rule per renamed method.

The AST and install matcher kinds are implemented in scanner/engine.py. This
module only checks that each rule names a known kind with known parameters, so
a typo in a rule file fails at load time instead of silently matching nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from common.models import Level

RULES_DIR = Path(__file__).parent / "rules"
RULE_FILES = ("civis_v3.yaml", "python315.yaml")

IMPACT_MODES = {"auto", "advisory", "affected"}

# Matcher kind -> allowed parameters. Kept in sync with scanner/engine.py by a test.
AST_MATCHERS: dict[str, set[str]] = {
    "call": {
        "names",
        "qualnames",
        "suffixes",
        "keywords_any",
        "keywords_absent",
        "min_positional",
        "max_positional",
        "min_args",
        "or_min_positional",
        "requires_import",
        "receiver_call_qualnames",
        "exclude_receiver_call_qualnames",
    },
    "attribute": {"names", "include_bare_names", "exclude_receiver_calls", "requires_import"},
    "reference": {"qualnames", "bare_names"},
    "strptime_no_year": set(),
    "argparse_dest": set(),
    "namedtuple_kwargs": set(),
    "typeddict_no_fields": set(),
    "star_import_name": {"modules", "names"},
    "rlimit_negative": set(),
    "loader_subclass_load_module": set(),
}

INSTALL_MATCHERS: dict[str, set[str]] = {
    "unbounded_major": {"package", "below_major"},
    "pinned_or_upper_bound": set(),
}

_ID_PATTERN = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)+$")


class RuleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    family: str
    title: str
    severity: Level
    confidence: Level
    impact: str
    fix: str
    source: str
    default_image_only: bool = False
    ast: tuple[dict, ...] = ()
    python_regex: re.Pattern | None = None
    shell_regex: re.Pattern | None = None
    install: tuple[dict, ...] = ()
    log_patterns: tuple[re.Pattern, ...] = ()
    log_exclude: tuple[re.Pattern, ...] = ()
    log_confidence: Level = Level.MEDIUM
    log_fallback: bool = False  # True for the catch-all log rule of a family.

    @property
    def checks_python(self) -> bool:
        return bool(self.ast or self.python_regex)

    @property
    def checks_shell(self) -> bool:
        return bool(self.shell_regex or self.install)

    @property
    def checks_requirements(self) -> bool:
        return bool(self.install)

    @property
    def checks_logs(self) -> bool:
        return bool(self.log_patterns)


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id = {r.id: r for r in self.rules}

    def __iter__(self):
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    def get(self, rule_id: str) -> Rule:
        return self._by_id[rule_id]

    def ids(self) -> list[str]:
        return [r.id for r in self.rules]

    def family(self, name: str) -> list[Rule]:
        return [r for r in self.rules if r.family == name]


def load_rules(directory: Path = RULES_DIR, files: tuple[str, ...] = RULE_FILES) -> RuleSet:
    rules: list[Rule] = []
    for name in files:
        rules.extend(load_rule_file(directory / name))
    ids = [r.id for r in rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise RuleValidationError(f"Duplicate rule IDs: {', '.join(duplicates)}")
    return RuleSet(rules)


def load_rule_file(path: Path) -> list[Rule]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_rule_document(data, where=path.name)


def parse_rule_document(data: dict, where: str = "<rules>") -> list[Rule]:
    if not isinstance(data, dict):
        raise RuleValidationError(f"{where}: top level must be a mapping")
    _only_keys(data, {"family", "id_prefix", "log_confidence", "rules", "renames"}, where)
    family = _required_str(data, "family", where)
    prefix = _required_str(data, "id_prefix", where)
    log_conf = _level(data.get("log_confidence", "medium"), f"{where}: log_confidence")

    rules = []
    for index, raw in enumerate(data.get("rules") or []):
        rules.append(_parse_rule(raw, family, prefix, log_conf, f"{where}: rules[{index}]"))
    if "renames" in data:
        rules.extend(_expand_renames(data["renames"], family, prefix, log_conf, f"{where}: renames"))
    if not rules:
        raise RuleValidationError(f"{where}: no rules defined")
    return rules


_RULE_KEYS = {
    "id",
    "title",
    "severity",
    "confidence",
    "impact",
    "default_image_only",
    "ast",
    "python_regex",
    "shell_regex",
    "install",
    "log",
    "fix",
    "source",
}


def _parse_rule(raw: dict, family: str, prefix: str, log_conf: Level, where: str) -> Rule:
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where}: each rule must be a mapping")
    _only_keys(raw, _RULE_KEYS, where)
    rule_id = _required_str(raw, "id", where)
    where = f"{where} ({rule_id})"
    if not _ID_PATTERN.match(rule_id) or not rule_id.startswith(prefix + "-"):
        raise RuleValidationError(f"{where}: ID must be upper case, hyphenated, and start with {prefix}-")

    impact = raw.get("impact", "auto")
    if impact not in IMPACT_MODES:
        raise RuleValidationError(f"{where}: impact must be one of {sorted(IMPACT_MODES)}")

    ast_matchers = tuple(_validate_matchers(raw.get("ast") or [], AST_MATCHERS, f"{where}: ast"))
    install = tuple(_validate_matchers(raw.get("install") or [], INSTALL_MATCHERS, f"{where}: install"))
    log_patterns, log_exclude, log_fallback = _parse_log(raw.get("log"), f"{where}: log")

    rule = Rule(
        id=rule_id,
        family=family,
        title=_required_str(raw, "title", where),
        severity=_level(raw.get("severity"), f"{where}: severity"),
        confidence=_level(raw.get("confidence"), f"{where}: confidence"),
        impact=impact,
        fix=_required_str(raw, "fix", where),
        source=_required_str(raw, "source", where),
        default_image_only=bool(raw.get("default_image_only", False)),
        ast=ast_matchers,
        python_regex=_compile(raw.get("python_regex"), f"{where}: python_regex"),
        shell_regex=_compile(raw.get("shell_regex"), f"{where}: shell_regex"),
        install=install,
        log_patterns=log_patterns,
        log_exclude=log_exclude,
        log_confidence=log_conf,
        log_fallback=log_fallback,
    )
    if not (rule.checks_python or rule.checks_shell or rule.checks_logs):
        raise RuleValidationError(f"{where}: rule has no detection method")
    return rule


def _expand_renames(raw: dict, family: str, prefix: str, log_conf: Level, where: str) -> list[Rule]:
    _only_keys(raw, {"severity", "confidence", "medium_confidence", "fix", "source", "items"}, where)
    severity = _level(raw.get("severity"), f"{where}: severity")
    confidence = _level(raw.get("confidence"), f"{where}: confidence")
    medium = set(raw.get("medium_confidence") or [])
    fix_template = _required_str(raw, "fix", where)
    source = _required_str(raw, "source", where)
    items = raw.get("items") or []
    if not items:
        raise RuleValidationError(f"{where}: items is empty")

    rules = []
    seen = set()
    for index, item in enumerate(items):
        if not (isinstance(item, list) and len(item) == 3 and all(isinstance(x, str) for x in item)):
            raise RuleValidationError(f"{where}: items[{index}] must be [endpoint, old_name, new_name]")
        endpoint, old, new = item
        for part in item:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", part):
                raise RuleValidationError(f"{where}: items[{index}] has an invalid name")
        key = f"{endpoint}.{old}"
        if key in seen:
            raise RuleValidationError(f"{where}: {key} is listed twice")
        seen.add(key)
        rule_id = f"{prefix}-RENAME-{endpoint}-{old}".upper().replace("_", "-")
        old_full = f"client.{endpoint}.{old}"
        new_full = f"client.{endpoint}.{new}"
        warning = re.escape(f"The method name ``<client>.{endpoint}.{old}`` is deprecated")
        rules.append(
            Rule(
                id=rule_id,
                family=family,
                title=f"{old_full} is renamed to {new_full} in civis-python v3",
                severity=severity,
                confidence=Level.MEDIUM if key in medium else confidence,
                impact="auto",
                fix=fix_template.format(old=old_full, new=new_full),
                source=source,
                ast=({"match": "call", "suffixes": [key]},),
                python_regex=re.compile(rf"\.{endpoint}\.{old}\s*\("),
                log_patterns=(re.compile(warning),),
                log_confidence=log_conf,
            )
        )
    unknown_medium = medium - seen
    if unknown_medium:
        raise RuleValidationError(f"{where}: medium_confidence lists unknown names {sorted(unknown_medium)}")
    return rules


def _validate_matchers(raw: list, kinds: dict[str, set[str]], where: str) -> list[dict]:
    if not isinstance(raw, list):
        raise RuleValidationError(f"{where}: must be a list")
    result = []
    for index, matcher in enumerate(raw):
        if not isinstance(matcher, dict) or "match" not in matcher:
            raise RuleValidationError(f"{where}[{index}]: each matcher needs a 'match' kind")
        kind = matcher["match"]
        if kind not in kinds:
            raise RuleValidationError(f"{where}[{index}]: unknown matcher kind '{kind}'")
        unknown = set(matcher) - {"match"} - kinds[kind]
        if unknown:
            raise RuleValidationError(f"{where}[{index}]: unknown parameters {sorted(unknown)} for '{kind}'")
        result.append(dict(matcher))
    return result


def _parse_log(raw, where: str) -> tuple[tuple[re.Pattern, ...], tuple[re.Pattern, ...], bool]:
    if raw is None:
        return (), (), False
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where}: must be a mapping")
    _only_keys(raw, {"patterns", "exclude", "fallback"}, where)
    patterns = tuple(_compile(p, f"{where}: patterns") for p in raw.get("patterns") or [])
    exclude = tuple(_compile(p, f"{where}: exclude") for p in raw.get("exclude") or [])
    if not patterns:
        raise RuleValidationError(f"{where}: patterns is empty")
    return patterns, exclude, bool(raw.get("fallback", False))


def _compile(pattern, where: str) -> re.Pattern | None:
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern:
        raise RuleValidationError(f"{where}: must be a non-empty string")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise RuleValidationError(f"{where}: invalid regex ({exc})") from None


def _level(value, where: str) -> Level:
    try:
        return Level(value)
    except ValueError:
        raise RuleValidationError(f"{where}: must be one of high, medium, low") from None


def _required_str(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuleValidationError(f"{where}: '{key}' is required")
    return value.strip()


def _only_keys(raw: dict, allowed: set[str], where: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise RuleValidationError(f"{where}: unknown keys {sorted(unknown)}")
