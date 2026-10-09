"""Engine behavior beyond single rules: fallback, notebooks, impact, and no code leakage."""

from __future__ import annotations

import json
from dataclasses import astuple

import pytest

from common.models import CivisObject, FindingSource, Impact, Level, ObjectType
from common.rule_loader import AST_MATCHERS, INSTALL_MATCHERS
from scanner.engine import AST_MATCHER_IMPLS, INSTALL_MATCHER_IMPLS, Engine, kind_for_path, parse_requirement

ENGINE = Engine()


def ids(result):
    return {m.rule_id for m in result.matches}


def make_obj(uses_default_image, impact):
    return CivisObject(
        object_id=101,
        object_type=ObjectType.CONTAINER_SCRIPT,
        name="example job",
        url="https://example.test/101",
        uses_default_image=uses_default_image,
        impact=impact,
    )


def test_matcher_registries_match_the_loader():
    assert set(AST_MATCHER_IMPLS) == set(AST_MATCHERS)
    assert set(INSTALL_MATCHER_IMPLS) == set(INSTALL_MATCHERS)


def test_unparseable_python_falls_back_to_regex_with_lower_confidence():
    code = "print 'python 2 style'\ndf = civis.io.read_civis_sql(sql, 'db', use_pandas=True)\n"
    result = ENGINE.scan_python(code, "legacy.py")
    assert not result.fully_parsed
    assert result.notes == ["legacy.py: could not be parsed"]
    (match,) = [m for m in result.matches if m.rule_id == "CV3-USE-PANDAS"]
    assert match.line_number == 2
    assert match.confidence is Level.MEDIUM


def test_line_numbers_point_at_the_call():
    code = "import civis\n\n\nclient = civis.APIClient()\nme = client.users.list_me()\n"
    (match,) = ENGINE.scan_python(code, "job.py").matches
    assert (match.rule_id, match.file, match.line_number) == ("CV3-RENAME-USERS-LIST-ME", "job.py", 5)


def test_findings_never_contain_code_text():
    secret = "hunter2-not-a-real-password"
    code = (
        "import civis\n"
        f"DB_PASSWORD = '{secret}'\n"
        f"df = civis.io.read_civis_sql('select * from t where p = \\'{secret}\\'', 'db', use_pandas=True)\n"
    )
    result = ENGINE.scan_python(code, "job.py")
    findings = ENGINE.to_findings(make_obj(True, Impact.AFFECTED), result.matches)
    assert findings
    for finding in findings:
        assert secret not in repr(astuple(finding))
        assert "select" not in repr(astuple(finding))
    for match in result.matches:
        assert secret not in repr(match)


def test_notebook_cells_magics_and_cross_cell_imports():
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["# civis.parallel in markdown is ignored\n"]},
            {"cell_type": "code", "source": ["from civis.utils import run_template\n", "%matplotlib inline\n"], "outputs": []},
            {"cell_type": "code", "source": ["!pip install civis\n", "files = run_template(5, {})\n"], "outputs": []},
            {"cell_type": "code", "source": "%%bash\npip install civis\n", "outputs": []},
            {"cell_type": "code", "source": ["%%time\n", "x = client.users.list_me()\n"], "outputs": []},
        ]
    }
    result = ENGINE.scan_notebook(json.dumps(notebook), "report.ipynb")
    found = {(m.rule_id, m.file, m.line_number) for m in result.matches}
    assert ("CV3-RUN-TEMPLATE-DEFAULT", "report.ipynb [cell 3]", 2) in found
    assert ("CV3-UNPINNED-INSTALL", "report.ipynb [cell 3]", 1) in found
    assert ("CV3-UNPINNED-INSTALL", "report.ipynb [cell 4]", 2) in found
    assert ("CV3-RENAME-USERS-LIST-ME", "report.ipynb [cell 5]", 2) in found
    assert not any("cell 1" in f for _, f, _ in found)
    assert result.fully_parsed


def test_notebook_outputs_are_scanned_as_log_text():
    warning = (
        "/tmp/ipykernel_1/123.py:2: FutureWarning: The method name ``<client>.users.list_me`` is deprecated "
        "and will be removed at civis-python v3.0.0 (scheduled for February 2027)."
    )
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["me = client.users.get_me()\n"],
                "outputs": [{"output_type": "stream", "name": "stderr", "text": ["first line\n", warning + "\n"]}],
            }
        ]
    }
    result = ENGINE.scan_notebook(json.dumps(notebook), "nb.ipynb")
    (match,) = result.matches
    assert match.rule_id == "CV3-RENAME-USERS-LIST-ME"
    assert match.source is FindingSource.NOTEBOOK_OUTPUT
    assert (match.file, match.line_number) == ("nb.ipynb [cell 1 output]", 2)
    assert match.confidence is Level.HIGH


def test_bad_notebook_file_is_reported_not_raised():
    result = ENGINE.scan_notebook("not json", "broken.ipynb")
    assert result.matches == [] and not result.fully_parsed


def test_log_scan_reports_each_rule_once_at_first_line():
    line = "FutureWarning: The property `feature_flags` is deprecated and will be removed at civis-python v3.0.0"
    matches = ENGINE.scan_log(["start", line, "middle", line], "run log")
    assert [(m.rule_id, m.line_number) for m in matches] == [("CV3-FEATURE-FLAGS", 2)]


def test_impact_follows_the_object_image():
    code = "import civis\nflags = client.feature_flags\n"
    matches = ENGINE.scan_python(code, "job.py").matches
    (affected,) = ENGINE.to_findings(make_obj(True, Impact.AFFECTED), matches)
    (advisory,) = ENGINE.to_findings(make_obj(False, Impact.ADVISORY), matches)
    assert affected.impact is Impact.AFFECTED and advisory.impact is Impact.ADVISORY
    assert affected.severity is Level.HIGH and affected.fix_guidance.startswith("Use client.users.get_me()")


def test_unpinned_civis_install_is_affected_even_on_a_custom_image():
    matches = ENGINE.scan_shell("pip install civis && python run.py", "docker command").matches
    findings = ENGINE.to_findings(make_obj(False, Impact.ADVISORY), matches)
    assert {(f.rule_id, f.impact) for f in findings} == {("CV3-UNPINNED-INSTALL", Impact.AFFECTED)}


@pytest.mark.parametrize(
    "uses_default, expected",
    [(True, {"PY315-PINNED-PACKAGE-INSTALL"}), (False, set()), (None, set())],
)
def test_pinned_package_rule_only_applies_on_default_images(uses_default, expected):
    matches = ENGINE.scan_requirements("pandas==1.5.3\n", "requirements.txt").matches
    impact = Impact.AFFECTED if uses_default else Impact.ADVISORY
    findings = ENGINE.to_findings(make_obj(uses_default, impact), matches)
    assert {f.rule_id for f in findings} == expected
    assert all(f.impact is Impact.ADVISORY for f in findings)


@pytest.mark.parametrize(
    "path, kind",
    [
        ("jobs/etl.py", "python"),
        ("analysis.ipynb", "notebook"),
        ("requirements.txt", "requirements"),
        ("deploy/requirements-prod.txt", "requirements"),
        ("pyproject.toml", "pyproject"),
        ("Dockerfile", "dockerfile"),
        ("docker/scanner.Dockerfile", "dockerfile"),
        ("README.md", None),
        ("setup.cfg", None),
        ("run.sh", None),
    ],
)
def test_kind_for_path(path, kind):
    assert kind_for_path(path) == kind


def test_scan_file_dispatches_and_skips_unknown_files():
    assert ids(ENGINE.scan_file("requirements.txt", "civis\n")) == {"CV3-UNPINNED-INSTALL"}
    assert ENGINE.scan_file("notes.md", "client.users.list_me()") is None


@pytest.mark.parametrize(
    "text, name, specs",
    [
        ("civis", "civis", ()),
        ("Civis[extra]>=2.8,<3", "civis", ((">=", "2.8"), ("<", "3"))),
        ("civis==2.10.0 ; python_version >= '3.11'", "civis", (("==", "2.10.0"),)),
        ("python_dateutil~=2.9", "python-dateutil", (("~=", "2.9"),)),
    ],
)
def test_parse_requirement(text, name, specs):
    req = parse_requirement(text)
    assert (req.name, req.specs) == (name, specs)


@pytest.mark.parametrize(
    "text",
    ["-r requirements.txt", "./local_pkg", "git+https://github.com/example/repo.git", "pkg @ https://example.test/x.whl", "# comment", "$PKG"],
)
def test_parse_requirement_skips_non_requirements(text):
    assert parse_requirement(text) is None


@pytest.mark.parametrize(
    "command, flagged",
    [
        ("pip install civis", True),
        ("pip install civis==3.0.0", False),
        ("pip install 'civis>=3'", False),
        ("pip install civis<3.0", False),
        ("pip install civis<3.1", True),
        ("pip install civis<=2.10.1", False),
        ("pip install civis==2.*", False),
        ("pip install civis!=2.9.0", True),
        ("pip install --index-url https://pypi.example.test/simple civis-tools", False),
        ("uv pip install civis", True),
        ("pip3 install -U pandas civis", True),
    ],
)
def test_unbounded_civis_detection(command, flagged):
    assert ("CV3-UNPINNED-INSTALL" in ids(ENGINE.scan_shell(command, "cmd"))) is flagged


def test_large_python_files_use_regex_only(monkeypatch):
    import scanner.engine as engine_module

    monkeypatch.setattr(engine_module, "MAX_PYTHON_CHARS", 10)
    result = ENGINE.scan_python("x = client.users.list_me()\n", "big.py")
    assert not result.fully_parsed
    assert ids(result) == {"CV3-RENAME-USERS-LIST-ME"}
