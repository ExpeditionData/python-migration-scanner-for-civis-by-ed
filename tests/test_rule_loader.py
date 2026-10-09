import re

import pytest

from common.models import Level
from common.rule_loader import RuleValidationError, load_rules, parse_rule_document

BASE_RULE = {
    "id": "TST-EXAMPLE",
    "title": "Example",
    "severity": "high",
    "confidence": "high",
    "ast": [{"match": "call", "names": ["foo"]}],
    "fix": "Do something else.",
    "source": "https://example.test",
}


def doc(**rule_overrides):
    rule = {**BASE_RULE, **rule_overrides}
    rule = {k: v for k, v in rule.items() if v is not None}
    return {"family": "test", "id_prefix": "TST", "rules": [rule]}


def test_shipped_rule_files_load():
    rules = load_rules()
    assert len(rules.family("civis_v3")) == 33
    assert len(rules.family("python315")) == 25
    assert len(set(rules.ids())) == len(rules)


def test_rename_table_expands_to_the_full_changelog_list():
    rules = load_rules()
    renames = [r for r in rules if r.id.startswith("CV3-RENAME-")]
    assert len(renames) == 19
    me = rules.get("CV3-RENAME-USERS-LIST-ME")
    assert "client.users.get_me" in me.fix
    assert me.ast == ({"match": "call", "suffixes": ["users.list_me"]},)
    assert me.log_patterns[0].search("The method name ``<client>.users.list_me`` is deprecated and will be removed")
    assert rules.get("CV3-RENAME-ENDPOINTS-LIST").confidence is Level.MEDIUM
    assert rules.get("CV3-RENAME-NOTIFICATIONS-LIST").confidence is Level.MEDIUM


def test_pending_share_renames_are_not_included():
    ids = load_rules().ids()
    assert not [i for i in ids if "SHARES" in i or "SEARCH" in i]


def test_every_shipped_rule_has_fix_and_source():
    for rule in load_rules():
        assert rule.fix and rule.source.startswith("https://")
        assert "—" not in rule.fix + rule.title  # no em dashes


def test_log_confidence_by_family():
    rules = load_rules()
    assert all(r.log_confidence is Level.HIGH for r in rules.family("civis_v3"))
    assert all(r.log_confidence is Level.MEDIUM for r in rules.family("python315"))


def test_valid_document_parses():
    (rule,) = parse_rule_document(doc())
    assert rule.id == "TST-EXAMPLE" and rule.impact == "auto"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"id": "tst-lower"}, "ID must be"),
        ({"id": "OTHER-PREFIX"}, "start with TST-"),
        ({"severity": "critical"}, "severity"),
        ({"confidence": None}, "confidence"),
        ({"impact": "maybe"}, "impact"),
        ({"fix": None}, "'fix' is required"),
        ({"ast": [{"match": "no_such_kind"}]}, "unknown matcher kind"),
        ({"ast": [{"match": "call", "nmes": ["x"]}]}, "unknown parameters"),
        ({"ast": [{"names": ["x"]}]}, "needs a 'match' kind"),
        ({"python_regex": "("}, "invalid regex"),
        ({"ast": None}, "no detection method"),
        ({"log": {"patterns": []}}, "patterns is empty"),
        ({"typo_key": 1}, "unknown keys"),
    ],
)
def test_invalid_rules_fail_at_load(overrides, message):
    with pytest.raises(RuleValidationError, match=re.escape(message)):
        parse_rule_document(doc(**overrides))


def test_duplicate_rename_rows_fail():
    data = {
        "family": "test",
        "id_prefix": "TST",
        "renames": {
            "severity": "high",
            "confidence": "high",
            "fix": "Rename {old} to {new}.",
            "source": "https://example.test",
            "items": [["users", "list_me", "get_me"], ["users", "list_me", "get_me"]],
        },
    }
    with pytest.raises(RuleValidationError, match="listed twice"):
        parse_rule_document(data)
