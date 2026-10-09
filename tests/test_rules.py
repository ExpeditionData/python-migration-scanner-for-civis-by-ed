"""At least one positive and one negative fixture for every rule.

All fixture code is synthetic. None of it comes from a Civis instance.
"""

from __future__ import annotations

import pytest

from common.models import FindingSource, Level
from common.rule_loader import load_rules
from scanner.engine import Engine

RULES = load_rules()
ENGINE = Engine(RULES)

# rule_id -> list of (kind, positive text, negative text)
# kind is one of: python, shell, requirements, pyproject, dockerfile, notebook
CASES: dict[str, list[tuple[str, str, str]]] = {
    "CV3-USE-PANDAS": [
        (
            "python",
            "import civis\ndf = civis.io.read_civis_sql(sql, 'analytics', use_pandas=True)\n",
            "import civis\ndf = civis.io.read_civis_sql(sql, 'analytics', return_as='pandas')\n",
        ),
        (
            "python",
            "from civis.io import read_civis\ndf = read_civis('schema.tbl', 'analytics', use_pandas=False)\n",
            "from civis.io import read_civis\ndf = read_civis('schema.tbl', 'analytics')\n",
        ),
    ],
    "CV3-DEFAULT-CREDENTIAL": [
        (
            "python",
            "import civis\nclient = civis.APIClient()\ncred = client.default_credential\n",
            "import civis\nclient = civis.APIClient()\ncred = client.default_database_credential_id\n",
        ),
    ],
    "CV3-GET-DATABASE-CREDENTIAL-ID": [
        (
            "python",
            "cred = client.get_database_credential_id('svc_user', 'analytics')\n",
            "db_id = client.get_database_id('analytics')\n",
        ),
    ],
    "CV3-FEATURE-FLAGS": [
        (
            "python",
            "flags = client.feature_flags\n",
            "flags = client.users.get_me()['feature_flags']\nmore = client.users.get_me().feature_flags\n",
        ),
    ],
    "CV3-PARALLEL": [
        (
            "python",
            "from civis.parallel import make_backend_factory\n",
            "from civis.futures import CivisFuture\n",
        ),
        (
            "python",
            "import civis\nfactory = civis.parallel.make_backend_factory()\n",
            "import civis\nnote = 'civis.parallel is mentioned only in a string'\n",
        ),
    ],
    "CV3-ML": [
        (
            "python",
            "from civis.ml import ModelPipeline\n",
            "import civis\n# civis.ml appears only in this comment\nclient = civis.APIClient()\n",
        ),
    ],
    "CV3-CONTAINERFUTURE": [
        (
            "python",
            "from civis.futures import ContainerFuture\nfut = ContainerFuture(10, 20)\n",
            "from civis.futures import CivisFuture\nfut = CivisFuture(poller, (10, 20))\n",
        ),
    ],
    "CV3-SERVICECLIENT": [
        (
            "python",
            "from civis.service_client import ServiceClient\nsc = ServiceClient(55)\n",
            "from vendor.sdk import ServiceClient\nsc = ServiceClient()\n",
        ),
    ],
    "CV3-POLLABLERESULT": [
        (
            "python",
            "from civis.polling import PollableResult\n",
            "from civis.futures import CivisFuture\n",
        ),
    ],
    "CV3-CIVISASYNCRESULTBASE": [
        (
            "python",
            "from civis.base import CivisAsyncResultBase\nclass Mine(CivisAsyncResultBase):\n    pass\n",
            "from civis.base import CivisAPIError\n",
        ),
    ],
    "CV3-RUN-TEMPLATE-JSONVALUE": [
        (
            "python",
            "import civis\nout = civis.utils.run_template(12, {'a': 1}, JSONValue=True)\n",
            "import civis\nout = civis.utils.run_template(12, {'a': 1}, return_as='JSONValue')\n",
        ),
        (
            "python",
            "from civis.utils import run_template\nout = run_template(12, {'a': 1}, True)\n",
            "from civis.utils import run_template\nout = run_template(12, {'a': 1}, client=client, return_as='files')\n",
        ),
    ],
    "CV3-RUN-TEMPLATE-DEFAULT": [
        (
            "python",
            "from civis.utils import run_template\nfiles = run_template(12, {'a': 1})\n",
            "from civis.utils import run_template\nfiles = run_template(12, {'a': 1}, return_as='files')\n",
        ),
        (
            "python",
            "import civis\nfiles = civis.utils.run_template(12, arguments={'a': 1}, client=client)\n",
            "from my_helpers import run_template\nfiles = run_template(12, {'a': 1})\n",
        ),
    ],
    "CV3-UNPINNED-INSTALL": [
        ("shell", "pip install civis pandas==2.2.0 && python run.py", "pip install 'civis>=2.8,<3' && python run.py"),
        ("shell", "python -m pip install -U civis>=2.4", "python -m pip install -r requirements.txt"),
        ("requirements", "pandas\ncivis>=2.8  # client\n", "pandas\ncivis~=2.10\n"),
        (
            "pyproject",
            '[project]\nname = "demo"\ndependencies = ["civis>=2.0", "pandas"]\n',
            '[project]\nname = "demo"\ndependencies = ["civis>=2.0,<3", "pandas"]\n',
        ),
        (
            "pyproject",
            '[tool.poetry.dependencies]\npython = "^3.12"\ncivis = "*"\n',
            '[tool.poetry.dependencies]\npython = "^3.12"\ncivis = "^2.10"\n',
        ),
        (
            "dockerfile",
            "FROM python:3.14-slim\nRUN pip install --no-cache-dir \\\n    civis \\\n    requests\n",
            "FROM python:3.14-slim\nRUN pip install --no-cache-dir civis==2.10.0\n",
        ),
        (
            "notebook",
            '{"cells": [{"cell_type": "code", "source": ["!pip install civis\\n", "import civis\\n"], "outputs": []}]}',
            '{"cells": [{"cell_type": "code", "source": ["%pip install civis==2.10.0\\n"], "outputs": []}]}',
        ),
    ],
    "PY315-STRPTIME-NO-YEAR": [
        (
            "python",
            "from datetime import datetime\nd = datetime.strptime(line[:15], '%b %d %H:%M:%S')\n",
            "from datetime import datetime\nd = datetime.strptime(text, '%Y-%m-%d')\n",
        ),
        (
            "python",
            "import time\nFMT = '%m/%d'\nt = time.strptime(value, FMT)\n",
            "import time\nt = time.strptime(value, '%m/%d/%y')\nliteral = '100%% done on %d'\n",
        ),
    ],
    "PY315-LOAD-MODULE": [
        (
            "python",
            "from importlib.machinery import SourceFileLoader\ncfg = SourceFileLoader('cfg', path).load_module()\n",
            "import importlib.util\nspec = importlib.util.spec_from_file_location('cfg', path)\n"
            "cfg = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(cfg)\n",
        ),
        (
            "python",
            "import importlib.abc\nclass OldLoader(importlib.abc.Loader):\n    def load_module(self, name):\n        return None\n",
            "import importlib.abc\nclass NewLoader(importlib.abc.Loader):\n    def exec_module(self, module):\n        return None\n",
        ),
    ],
    "PY315-ZIPIMPORT-LOAD-MODULE": [
        (
            "python",
            "import zipimport\nmod = zipimport.zipimporter('bundle.zip').load_module('tool')\n",
            "import zipimport\nimporter = zipimport.zipimporter('bundle.zip')\nspec = importer.find_spec('tool')\n",
        ),
    ],
    "PY315-SQLITE-CONNECT-POSITIONAL": [
        (
            "python",
            "import sqlite3\nconn = sqlite3.connect('local.db', 10)\n",
            "import sqlite3\nconn = sqlite3.connect('local.db', timeout=10)\n",
        ),
    ],
    "PY315-SQLITE-METHOD-KEYWORDS": [
        (
            "python",
            "conn.create_function(name='double', narg=1, func=double)\n",
            "conn.create_function('double', 1, double, deterministic=True)\n",
        ),
    ],
    "PY315-ARGPARSE-DEST": [
        (
            "python",
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('-f', '-foo')\n",
            "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('-f', '-foo', dest='f')\n"
            "parser.add_argument('-g', '--goo')\n",
        ),
    ],
    "PY315-SRE-MODULES": [
        ("python", "import sre_parse\n", "import re\n"),
        ("python", "from sre_constants import MAXREPEAT\n", "from re import MULTILINE\n"),
    ],
    "PY315-GLOB-GLOB0-GLOB1": [
        (
            "python",
            "import glob\nfiles = glob.glob1('/data', '*.csv')\n",
            "import glob\nfiles = glob.glob('*.csv', root_dir='/data')\n",
        ),
    ],
    "PY315-CGI-HANDLER": [
        (
            "python",
            "from http.server import CGIHTTPRequestHandler\n",
            "from http.server import SimpleHTTPRequestHandler\n",
        ),
        ("shell", "python -m http.server 8000 --cgi", "python -m http.server 8000"),
    ],
    "PY315-RESOURCES-FILES-PACKAGE": [
        (
            "python",
            "from importlib.resources import files\nroot = files(package='mypkg')\n",
            "from importlib.resources import files\nroot = files('mypkg')\n",
        ),
    ],
    "PY315-PATH-IS-RESERVED": [
        (
            "python",
            "from pathlib import PureWindowsPath\nbad = PureWindowsPath('nul').is_reserved()\n",
            "import ipaddress\nflag = ipaddress.ip_address('10.0.0.1').is_reserved\n",
        ),
    ],
    "PY315-PLATFORM-JAVA-VER": [
        ("python", "import platform\nv = platform.java_ver()\n", "import platform\nv = platform.python_version()\n"),
    ],
    "PY315-CTYPES-SETPOINTERTYPE": [
        (
            "python",
            "import ctypes\nctypes.SetPointerType(node_ptr, Node)\n",
            "import ctypes\nnode_ptr = ctypes.POINTER(Node)\n",
        ),
    ],
    "PY315-SYSCONFIG-CHECK-HOME": [
        (
            "python",
            "import sysconfig\nflag = sysconfig.is_python_build(check_home=True)\n",
            "import sysconfig\nflag = sysconfig.is_python_build()\n",
        ),
    ],
    "PY315-RLOCK-ARGS": [
        ("python", "import threading\nlock = threading.RLock(1)\n", "import threading\nlock = threading.RLock()\n"),
    ],
    "PY315-CO-LNOTAB": [
        ("python", "table = func.__code__.co_lnotab\n", "lines = list(func.__code__.co_lines())\n"),
    ],
    "PY315-MODULE-CACHED": [
        ("python", "import json\npath = json.__cached__\n", "import json\npath = json.__spec__.cached\n"),
        ("python", "print(__cached__)\n", "print(__file__)\n"),
    ],
    "PY315-TYPING-NAMEDTUPLE-KWARGS": [
        (
            "python",
            "from typing import NamedTuple\nPoint = NamedTuple('Point', x=int, y=int)\n",
            "from typing import NamedTuple\nPoint = NamedTuple('Point', [('x', int), ('y', int)])\n",
        ),
    ],
    "PY315-TYPING-TYPEDDICT-NO-FIELDS": [
        (
            "python",
            "from typing import TypedDict\nEmpty = TypedDict('Empty')\n",
            "from typing import TypedDict\nEmpty = TypedDict('Empty', {})\n",
        ),
        (
            "python",
            "import typing\nEmpty = typing.TypedDict('Empty', None)\n",
            "import typing\nRow = typing.TypedDict('Row', {'id': int})\n",
        ),
    ],
    "PY315-TYPING-NO-TYPE-CHECK-DECORATOR": [
        (
            "python",
            "import typing\n@typing.no_type_check_decorator\ndef deco(f):\n    return f\n",
            "import typing\n@typing.no_type_check\ndef f():\n    pass\n",
        ),
    ],
    "PY315-BYTESTRING-STAR-IMPORT": [
        (
            "python",
            "from typing import *\ndef f(b: ByteString):\n    pass\n",
            "from collections.abc import ByteString\ndef f(b: ByteString):\n    pass\n",
        ),
    ],
    "PY315-WAVE-MARKERS": [
        (
            "python",
            "import wave\nw = wave.open('a.wav')\nmarks = w.getmarkers()\n",
            "marks = timeline.getmarkers()\n",
        ),
    ],
    "PY315-RLIMIT-NEGATIVE": [
        (
            "python",
            "import resource\nresource.setrlimit(resource.RLIMIT_NOFILE, (-1, -1))\n",
            "import resource\nresource.setrlimit(resource.RLIMIT_NOFILE, (resource.RLIM_INFINITY, 4096))\n",
        ),
        (
            "python",
            "import resource\nunlimited = resource.RLIM_INFINITY == -1\n",
            "import resource\nunlimited = resource.RLIM_INFINITY > 0\n",
        ),
    ],
    "PY315-PINNED-PACKAGE-INSTALL": [
        ("requirements", "pandas==1.5.3\n", "pandas>=2.0\n"),
        ("shell", "pip install numpy<2 && python job.py", "pip install numpy && python job.py"),
        (
            "pyproject",
            '[tool.poetry.dependencies]\npython = "^3.12"\nscipy = "1.11.4"\n',
            '[tool.poetry.dependencies]\npython = "^3.12"\nscipy = ">=1.11"\n',
        ),
    ],
}

# Sample log lines. Civis lines use the exact civis-python warning text.
# Python lines use the warning text printed by Python 3.13 and 3.14.
LOG_SAMPLES: dict[str, str] = {
    "CV3-USE-PANDAS": "/app/job.py:12: FutureWarning: To support multiple dataframe libraries, the keyword argument 'use_pandas' is deprecated since civis-python v2.6.0 and will be removed in civis-python v3.0.0 (scheduled for February 2027).",
    "CV3-DEFAULT-CREDENTIAL": "/app/job.py:5: FutureWarning: The property `default_credential` is deprecated and will be removed at civis-python v3.0.0 (scheduled for February 2027). Please use `default_database_credential_id` instead.",
    "CV3-GET-DATABASE-CREDENTIAL-ID": "/app/job.py:9: FutureWarning: The method `get_database_credential_id` is deprecated and will be removed at civis-python v3.0.0 (scheduled for February 2027).",
    "CV3-FEATURE-FLAGS": "/app/job.py:3: FutureWarning: The property `feature_flags` is deprecated and will be removed at civis-python v3.0.0 (scheduled for February 2027). Please use client.users.get_me()['feature_flags'] instead.",
    "CV3-PARALLEL": "FutureWarning: `civis.parallel` is deprecated since civis-python v2.10.0 and will be removed at civis-python v3.0.0 (scheduled for release in February 2027).",
    "CV3-ML": "FutureWarning: `civis.ml` is deprecated since civis-python v2.10.0 and will be removed at civis-python v3.0.0 (scheduled for release in February 2027).",
    "CV3-CONTAINERFUTURE": "FutureWarning: The ContainerFuture class is deprecated since civis-python v2.10.0. In civis-python v3.0.0 (scheduled for February 2027), it will be removed.",
    "CV3-SERVICECLIENT": "FutureWarning: The ServiceClient class is deprecated since civis-python v2.10.0 and will be removed in civis-python v3.0.0 (scheduled for February 2027). No replacement is planned for it.",
    "CV3-POLLABLERESULT": "FutureWarning: The PollableResult class is deprecated since civis-python v2.10.0. In civis-python v3.0.0 (scheduled for February 2027), it will be renamed as _PollableResult intended for private use only within civis-python.",
    "CV3-CIVISASYNCRESULTBASE": "FutureWarning: The CivisAsyncResultBase class is deprecated since civis-python v2.10.0. In civis-python v3.0.0 (scheduled for February 2027), it will be renamed as _CivisAsyncResultBase intended for private use only within civis-python.",
    "CV3-RUN-TEMPLATE-JSONVALUE": "FutureWarning: As of civis-python v2.8.0, civis.utils.run_template can return three types of values, so 'JSONValue' is deprecated and will be removed in civis-python v3.0.0 (scheduled for February 2027).",
    "CV3-LOG-FUTUREWARNING": "/app/job.py:40: FutureWarning: Some other feature is deprecated and will be removed at civis-python v3.0.0 (scheduled for February 2027).",
    "PY315-STRPTIME-NO-YEAR": "/app/job.py:7: DeprecationWarning: Parsing dates involving a day of month without a year specified is ambiguous and fails to parse leap day. The default behavior will change in Python 3.15.",
    "PY315-LOAD-MODULE": "<frozen importlib._bootstrap>:1: DeprecationWarning: the load_module() method is deprecated and slated for removal in Python 3.15; use exec_module() instead",
    "PY315-ZIPIMPORT-LOAD-MODULE": "DeprecationWarning: 'zipimport.zipimporter.load_module' is deprecated and slated for removal in Python 3.15; use zipimport.zipimporter.exec_module() instead",
    "PY315-SQLITE-CONNECT-POSITIONAL": "DeprecationWarning: Passing more than 1 positional argument to sqlite3.connect() is deprecated. Parameters 'timeout' and 'detect_types' will become keyword-only parameters in Python 3.15.",
    "PY315-SQLITE-METHOD-KEYWORDS": "DeprecationWarning: Passing keyword arguments 'name', 'narg' and 'func' to _sqlite3.Connection.create_function() is deprecated. Parameters 'name', 'narg' and 'func' will become positional-only in Python 3.15.",
    "PY315-SRE-MODULES": "DeprecationWarning: module 'sre_parse' is deprecated",
    "PY315-GLOB-GLOB0-GLOB1": "DeprecationWarning: glob.glob1 is deprecated and will be removed in Python 3.15. Use glob.glob and pass a directory to its root_dir argument instead.",
    "PY315-CGI-HANDLER": "DeprecationWarning: 'http.server.CGIHTTPRequestHandler' is deprecated and slated for removal in Python 3.15",
    "PY315-RESOURCES-FILES-PACKAGE": "DeprecationWarning: First parameter to files is renamed to 'anchor'",
    "PY315-PATH-IS-RESERVED": "DeprecationWarning: pathlib.PurePath.is_reserved() is deprecated and scheduled for removal in Python 3.15. Use os.path.isreserved() to detect reserved paths on Windows.",
    "PY315-PLATFORM-JAVA-VER": "DeprecationWarning: 'java_ver' is deprecated and slated for removal in Python 3.15",
    "PY315-CTYPES-SETPOINTERTYPE": "DeprecationWarning: 'ctypes.SetPointerType' is deprecated and slated for removal in Python 3.15",
    "PY315-SYSCONFIG-CHECK-HOME": "DeprecationWarning: The check_home argument of sysconfig.is_python_build is deprecated and its value is ignored. It will be removed in Python 3.15.",
    "PY315-RLOCK-ARGS": "DeprecationWarning: Passing arguments to RLock is deprecated and will be removed in 3.15",
    "PY315-CO-LNOTAB": "DeprecationWarning: co_lnotab is deprecated, use co_lines instead.",
    "PY315-TYPING-NAMEDTUPLE-KWARGS": "DeprecationWarning: Creating NamedTuple classes using keyword arguments is deprecated and will be disallowed in Python 3.15. Use the class-based or functional syntax instead.",
    "PY315-TYPING-TYPEDDICT-NO-FIELDS": "DeprecationWarning: Failing to pass a value for the 'fields' parameter is deprecated and will be disallowed in Python 3.15.",
    "PY315-TYPING-NO-TYPE-CHECK-DECORATOR": "DeprecationWarning: 'typing.no_type_check_decorator' is deprecated and slated for removal in Python 3.15",
    "PY315-WAVE-MARKERS": "DeprecationWarning: 'Wave_read.getmarkers' is deprecated and slated for removal in Python 3.15",
    "PY315-LOG-DEPRECATION": "DeprecationWarning: 'somemodule.old_helper' is deprecated and slated for removal in Python 3.15",
}

NEGATIVE_LOG_LINES = [
    "INFO Loaded 1200 rows into staging.daily_orders",
    "/app/job.py:3: FutureWarning: Warning: The tables/:id/refresh endpoint is deprecated. Please use tables/scan from now on.",
    "DeprecationWarning: 'locale.getdefaultlocale' is deprecated and slated for removal in Python 3.15",
    "FutureWarning: pandas will change the default of observed in a future version",
]


def _rename_cases() -> None:
    for rule in RULES:
        if not rule.id.startswith("CV3-RENAME-"):
            continue
        endpoint, old = rule.ast[0]["suffixes"][0].split(".")
        new = rule.title.split(" is renamed to ")[1].split(" ")[0].split(".")[-1]
        CASES[rule.id] = [
            (
                "python",
                f"import civis\nclient = civis.APIClient()\nresult = client.{endpoint}.{old}()\n",
                f"import civis\nclient = civis.APIClient()\nresult = client.{endpoint}.{new}()\n",
            ),
            (
                "python",
                f"import civis\nresult = civis.APIClient().{endpoint}.{old}(limit=5)\n",
                f"import civis\nname = '{endpoint}.{old}'\n",
            ),
        ]
        LOG_SAMPLES[rule.id] = (
            f"/app/job.py:8: FutureWarning: The method name ``<client>.{endpoint}.{old}`` is deprecated and "
            "will be removed at civis-python v3.0.0 (scheduled for February 2027). "
            f"Please switch to ``<client>.{endpoint}.{new}`` for the same method."
        )


_rename_cases()


def run(kind: str, text: str) -> set[str]:
    label = "fixture"
    if kind == "python":
        result = ENGINE.scan_python(text, label)
        assert result.fully_parsed, "fixture should be valid Python"
    elif kind == "shell":
        result = ENGINE.scan_shell(text, label)
    elif kind == "requirements":
        result = ENGINE.scan_requirements(text, label)
    elif kind == "pyproject":
        result = ENGINE.scan_pyproject(text, label)
    elif kind == "dockerfile":
        result = ENGINE.scan_dockerfile(text, label)
    elif kind == "notebook":
        result = ENGINE.scan_notebook(text, label)
    else:
        raise AssertionError(kind)
    return {m.rule_id for m in result.matches}


def _code_params():
    for rule_id, cases in sorted(CASES.items()):
        for index, (kind, positive, negative) in enumerate(cases):
            yield pytest.param(rule_id, kind, positive, negative, id=f"{rule_id}-{kind}-{index}")


def test_every_rule_has_a_fixture():
    rule_ids = set(RULES.ids())
    code_rules = {r.id for r in RULES if r.checks_python or r.checks_shell}
    log_rules = {r.id for r in RULES if r.checks_logs}
    assert code_rules <= set(CASES), sorted(code_rules - set(CASES))
    assert log_rules <= set(LOG_SAMPLES), sorted(log_rules - set(LOG_SAMPLES))
    assert set(CASES) | set(LOG_SAMPLES) <= rule_ids


@pytest.mark.parametrize("rule_id, kind, positive, negative", list(_code_params()))
def test_rule_code_fixtures(rule_id, kind, positive, negative):
    assert rule_id in run(kind, positive), f"{rule_id} should match the positive fixture"
    assert rule_id not in run(kind, negative), f"{rule_id} should not match the negative fixture"


@pytest.mark.parametrize("rule_id", sorted(LOG_SAMPLES))
def test_rule_log_samples(rule_id):
    matches = ENGINE.scan_log(LOG_SAMPLES[rule_id], "run log")
    found = {m.rule_id for m in matches}
    assert rule_id in found
    rule = RULES.get(rule_id)
    if not rule.log_fallback:
        # A line that a specific rule explains does not also raise the catch-all rule.
        assert not any(RULES.get(r).log_fallback and RULES.get(r).family == rule.family for r in found)
    expected = Level.HIGH if rule.family == "civis_v3" else Level.MEDIUM
    assert all(m.confidence is expected and m.source is FindingSource.LOG for m in matches)


@pytest.mark.parametrize("line", NEGATIVE_LOG_LINES)
def test_unrelated_log_lines_do_not_match(line):
    assert ENGINE.scan_log(line, "run log") == []


def test_zipimporter_load_module_is_not_double_reported():
    found = run("python", CASES["PY315-ZIPIMPORT-LOAD-MODULE"][0][1])
    assert "PY315-LOAD-MODULE" not in found


def test_run_template_rules_do_not_overlap():
    found = run("python", "import civis\nout = civis.utils.run_template(12, {'a': 1}, JSONValue=True)\n")
    assert found == {"CV3-RUN-TEMPLATE-JSONVALUE"}
