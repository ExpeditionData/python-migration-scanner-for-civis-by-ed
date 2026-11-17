"""Python passed inline to a Python interpreter in shell text (docs/PLAN.md 17.12, Part A).

Docker commands, template and workflow task docker commands, notebook shell lines and shell cell magics, and
Dockerfile RUN lines can run Python with ``python -c "..."`` or a heredoc on the interpreter's stdin. That code
is checked with the Python rules as well as the shell rules. All code below is made up.
"""

from __future__ import annotations

import copy
import io
import json
import sqlite3

import pytest

from common.models import Level, ScanStatus
from scanner import scan_log
from scanner.engine import Engine, python_in_shell
from scanner.process import PARSE_NOTE

ENGINE = Engine()
LIST_ME = "import civis; civis.APIClient().users.list_me()"


def found(result):
    return [(m.rule_id, m.file, m.line_number) for m in result.matches]


def list_me_lines(result):
    return [m.line_number for m in result.matches if m.rule_id == "CV3-RENAME-USERS-LIST-ME"]


# --- -c forms ----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        f'python -c "{LIST_ME}"',
        f'python3 -c "{LIST_ME}"',
        f'python3.12 -c "{LIST_ME}"',
        f'python3.15 -c "{LIST_ME}"',
        f'/usr/bin/python3 -c "{LIST_ME}"',
        f'/usr/local/bin/python -c "{LIST_ME}"',
        f'/usr/bin/env python3 -c "{LIST_ME}"',
        f'env python3 -c "{LIST_ME}"',
        f'env PYTHONUNBUFFERED=1 python3 -c "{LIST_ME}"',
        f'PYTHONPATH=/app python -c "{LIST_ME}"',
        f'python -u -c "{LIST_ME}"',
        f'python3 -u -B -c "{LIST_ME}"',
        f'python3 -W ignore -c "{LIST_ME}"',
        f'python3 -Wignore -X utf8 -c "{LIST_ME}"',
        f'python3 --check-hash-based-pycs never -c "{LIST_ME}"',
        f'python3 -uc "{LIST_ME}"',
        f'python3 -c"{LIST_ME}"',
        f"python -c '{LIST_ME}'",
        f'exec python3 -c "{LIST_ME}"',
        f'if true; then python3 -c "{LIST_ME}"; fi',
    ],
)
def test_c_forms_are_scanned_as_python(command):
    result = ENGINE.scan_shell(command, "docker command")
    assert found(result) == [("CV3-RENAME-USERS-LIST-ME", "docker command", 1)]
    assert result.fully_parsed and result.parse_errors == []


def test_c_code_in_the_second_of_two_commands():
    result = ENGINE.scan_shell(f'pip install civis==2.10.0 && python -c "{LIST_ME}"', "docker command")
    assert ("CV3-RENAME-USERS-LIST-ME", "docker command", 1) in found(result)
    for joiner in ("&&", ";", "||", "|", "&"):
        assert list_me_lines(ENGINE.scan_shell(f'echo start {joiner} python3 -c "{LIST_ME}"', "cmd")) == [1]
    assert list_me_lines(ENGINE.scan_shell(f'(cd /app && python3 -c "{LIST_ME}")', "cmd")) == [1]


def test_quoted_multi_line_c_counts_from_the_line_where_the_quote_opens():
    command = (
        "echo starting\n"
        'python3 -u -c "import civis\n'
        "client = civis.APIClient()\n"
        'me = client.users.list_me()"\n'
        "echo done\n"
    )
    assert list_me_lines(ENGINE.scan_shell(command, "docker command")) == [4]
    # The quote opens on a line after a backslash continuation.
    command = 'python3 \\\n  -c "import civis\nclient = civis.APIClient()\nclient.users.list_me()"\n'
    assert list_me_lines(ENGINE.scan_shell(command, "docker command")) == [4]
    # The code starts on its own line inside the quotes.
    command = "python3 -c '\nimport civis\ncivis.APIClient().users.list_me()\n'\n"
    assert list_me_lines(ENGINE.scan_shell(command, "docker command")) == [3]
    # A first_line other than 1 moves every line the same way.
    command = 'echo hi\npython -c "import civis\ncivis.APIClient().users.list_me()"'
    assert list_me_lines(ENGINE.scan_shell(command, "cell", first_line=10)) == [12]


# --- heredocs ----------------------------------------------------------------


@pytest.mark.parametrize(
    "opening, closing, indent",
    [
        ("python - <<EOF", "EOF", ""),
        ("python3 <<'PY'", "PY", ""),
        ('python3 <<"PY"', "PY", ""),
        ("python3 << PY", "PY", ""),
        ("python3 -u - <<-END", "END", "\t"),
        ("python3 -u - <<- END", "END", "\t"),
        ("/usr/bin/env python3 - <<EOF", "EOF", ""),
        ("cd /app && python3 - <<EOF", "EOF", ""),
        ("python3 - <<EOF > /tmp/out.log 2>&1", "EOF", ""),
        ("python3 2>&1 <<EOF", "EOF", ""),
    ],
)
def test_heredoc_forms_are_scanned_as_python(opening, closing, indent):
    command = "\n".join(
        [
            "echo starting",
            opening,
            f"{indent}import civis",
            f"{indent}if True:",
            f"{indent}    civis.APIClient().users.list_me()",
            f"{indent}{closing}",
            "echo done",
        ]
    )
    result = ENGINE.scan_shell(command, "docker command")
    assert found(result) == [("CV3-RENAME-USERS-LIST-ME", "docker command", 5)]
    assert result.fully_parsed


def test_heredoc_body_is_not_also_checked_as_shell():
    command = "python3 - <<EOF\n# pip install civis\nimport civis\nEOF\npip install civis\n"
    result = ENGINE.scan_shell(command, "docker command")
    assert found(result) == [("CV3-UNPINNED-INSTALL", "docker command", 5)]  # Line 2 is a Python comment.
    _units, python_lines = python_in_shell(command)
    assert python_lines == {1, 2, 3}  # The body and the line that ends it, from 0.


def test_other_heredocs_stay_shell():
    command = "cat > setup.sh <<EOF\npip install civis\nit's a note\nEOF\npython3 -c \"" + LIST_ME + '"\n'
    result = ENGINE.scan_shell(command, "docker command")
    assert ("CV3-UNPINNED-INSTALL", "docker command", 2) in found(result)
    assert list_me_lines(result) == [5]  # The apostrophe in the cat heredoc does not hide the next command.


def test_heredoc_with_no_end_runs_to_the_end_of_the_text():
    result = ENGINE.scan_shell("python3 - <<EOF\nimport civis\ncivis.APIClient().users.list_me()\n", "cmd")
    assert list_me_lines(result) == [3]


def test_heredoc_on_a_script_or_another_command_is_not_python():
    for opening in ("python3 run.py <<EOF", "python3 -m etl <<EOF", "cat <<EOF", "bash <<EOF"):
        result = ENGINE.scan_shell(f"{opening}\nclient.users.list_me()\nEOF\n", "cmd")
        assert list_me_lines(result) == [], opening


# --- lines shlex cannot split, code that does not parse, and the dedupe --------


def test_a_line_shlex_cannot_split_gets_the_shell_rules_only():
    command = f'pip install civis && python -c "{LIST_ME}\n'  # The quote never closes.
    result = ENGINE.scan_shell(command, "docker command")
    assert found(result) == [("CV3-UNPINNED-INSTALL", "docker command", 1)]
    assert result.fully_parsed
    # The lines after it are still read.
    command = f'echo "unclosed\npip install civis\npython3 -c "{LIST_ME}"\n'
    result = ENGINE.scan_shell(command, "docker command")
    assert ("CV3-UNPINNED-INSTALL", "docker command", 2) in found(result)


def test_code_that_does_not_parse_uses_the_regex_fallback():
    result = ENGINE.scan_shell('python3 -c "import civis; x = $COUNT; civis.APIClient().users.list_me()"', "cmd")
    (match,) = result.matches
    assert (match.rule_id, match.line_number, match.confidence) == ("CV3-RENAME-USERS-LIST-ME", 1, Level.MEDIUM)
    assert not result.fully_parsed and result.parse_errors == ["SyntaxError"]
    result = ENGINE.scan_shell("python3 - <<EOF\nprint 'old'\nclient.users.list_me()\nEOF\n", "cmd")
    assert list_me_lines(result) == [3] and result.parse_errors == ["SyntaxError"]


def test_a_rule_matching_as_python_and_as_shell_is_reported_once():
    command = 'python3 -c "from http.server import CGIHTTPRequestHandler" && python3 -m http.server --cgi 8000'
    result = ENGINE.scan_shell(command, "docker command")
    assert found(result) == [("PY315-CGI-HANDLER", "docker command", 1)]


def test_unparseable_code_makes_the_object_partial_with_the_parse_note(tmp_path):
    from scanner.__main__ import dry_run
    from scanner.synthetic import FakeClient as SyntheticClient

    stream = io.StringIO()
    logger = scan_log.configure(stream)
    previous = logger.handlers[0].stream
    logger.handlers[0].setStream(stream)
    try:
        data = copy.deepcopy(SyntheticClient.from_fixture("synthetic_instance.json").data)
        entry = next(c for c in data["containers"] if c["id"] == 212)
        entry["docker_command"] = 'python3 -c "import civis; n = $COUNT; civis.APIClient().users.list_me()"'
        dry_run(tmp_path, data=data)
    finally:
        logger.handlers[0].setStream(previous)
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    try:
        status, note = conn.execute("SELECT scan_status, scan_note FROM objects WHERE object_id = 212").fetchone()
        rows = conn.execute("SELECT rule_id, file, line_number FROM findings WHERE object_id = 212").fetchall()
    finally:
        conn.close()
    assert status == ScanStatus.PARTIAL.value and PARSE_NOTE in note
    assert rows == [("CV3-RENAME-USERS-LIST-ME", "docker command", 1)]
    assert "PARSE_ERROR object_type=container_script object_id=212 error=SyntaxError" in stream.getvalue()


# --- not covered ---------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python run.py",
        "python3 /app/run.py --date 2026-10-01",
        "python -m civis_job",
        'echo "python -c"',
        f'echo "python -c \\"{LIST_ME}\\""',
        f'echo python -c "{LIST_ME}"',
        f'mytool -c "{LIST_ME}"',
        f'bash -c "python -c \'{LIST_ME}\'"',
        f'python2 -c "{LIST_ME}"',
        f'pythonista -c "{LIST_ME}"',
        "cat job.py | python -",
        "pip install pandas",
        f'python3 run.py -c "{LIST_ME}"',
        f'python3 -m etl -c "{LIST_ME}"',
        f'# python3 -c "{LIST_ME}"',
    ],
)
def test_negatives(command):
    result = ENGINE.scan_shell(command, "docker command")
    assert list_me_lines(result) == []
    assert python_in_shell(command)[0] == []


# --- every place shell text is scanned --------------------------------------------


def test_dockerfile_run_lines():
    dockerfile = "\n".join(
        [
            "FROM civisanalytics/datascience-python:9.1.1",
            f'RUN python3 -c "{LIST_ME}"',
            "RUN python3 -c \"import civis; \\",
            '    civis.APIClient().users.list_me()"',
            "RUN python3 <<EOF",
            "import civis",
            "civis.APIClient().users.list_me()",
            "EOF",
            "RUN pip install civis",
            'CMD ["python", "run.py"]',
        ]
    )
    result = ENGINE.scan_dockerfile(dockerfile, "Dockerfile")
    assert list_me_lines(result) == [2, 3, 7]
    assert ("CV3-UNPINNED-INSTALL", "Dockerfile", 9) in found(result)
    assert ENGINE.scan_file("Dockerfile", dockerfile).matches == result.matches


def test_notebook_shell_lines_and_shell_cell_magics():
    notebook = {
        "cells": [
            {"cell_type": "code", "source": ["import os\n", f'!python3 -c "{LIST_ME}"\n']},
            {
                "cell_type": "code",
                "source": ["%%bash\n", "python3 - <<EOF\n", "import civis\n", "civis.APIClient().users.list_me()\n", "EOF\n"],
            },
            {"cell_type": "code", "source": ['!python3 -c "x = $COUNT"\n']},
        ]
    }
    result = ENGINE.scan_notebook(json.dumps(notebook), "notebook file")
    assert found(result) == [
        ("CV3-RENAME-USERS-LIST-ME", "notebook file [cell 1]", 2),
        ("CV3-RENAME-USERS-LIST-ME", "notebook file [cell 2]", 4),
    ]
    assert result.parse_errors == ["SyntaxError"]  # The third cell's code does not parse.


def test_labels_are_kept_for_each_unit():
    for label in ("docker command", "template docker command", "workflow task load docker command"):
        assert found(ENGINE.scan_shell(f'python -c "{LIST_ME}"', label)) == [("CV3-RENAME-USERS-LIST-ME", label, 1)]


def test_synthetic_container_and_workflow_task_show_the_findings(tmp_path):
    from scanner.__main__ import dry_run

    dry_run(tmp_path)
    conn = sqlite3.connect(tmp_path / "results.sqlite")
    try:
        rows = set(conn.execute("SELECT object_id, rule_id, file, line_number FROM findings WHERE object_id IN (212, 601)"))
    finally:
        conn.close()
    assert {
        (212, "CV3-RENAME-USERS-LIST-ME", "docker command", 1),
        (212, "CV3-PARALLEL", "docker command", 2),
        (601, "CV3-RENAME-USERS-LIST-ME", "workflow task inline_container_step docker command", 2),
        (601, "CV3-DEFAULT-CREDENTIAL", "workflow task inline_container_step docker command", 5),
    } <= rows
