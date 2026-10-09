"""The tool must survive the change it scans for.

Run the scanner's own rules over this repo's code. Any civis-python v3 or
Python 3.15 finding in scanner/, common/, or app/ fails the test.
"""

from pathlib import Path

from common.models import CivisObject, Impact, ObjectType
from scanner.engine import Engine, kind_for_path

ROOT = Path(__file__).resolve().parent.parent


def test_repo_code_passes_its_own_rules():
    engine = Engine()
    me = CivisObject(0, ObjectType.CONTAINER_SCRIPT, "self", "", uses_default_image=True, impact=Impact.AFFECTED)
    problems = []
    for package in ("scanner", "common", "app"):
        for path in sorted((ROOT / package).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or kind_for_path(path.name) is None:
                continue
            rel = path.relative_to(ROOT).as_posix()
            result = engine.scan_file(rel, path.read_text(encoding="utf-8"))
            if result is None:
                continue
            assert result.fully_parsed, f"{rel} did not parse"
            problems += [f"{f.file}:{f.line_number} {f.rule_id}" for f in engine.to_findings(me, result.matches)]
    for name in ("requirements-scanner.txt", "requirements-service.txt"):
        result = engine.scan_file(name, (ROOT / name).read_text(encoding="utf-8"))
        problems += [
            f"{f.file}:{f.line_number} {f.rule_id}"
            for f in engine.to_findings(me, result.matches)
            if f.rule_id.startswith("CV3-")
        ]
    assert not problems, "\n".join(problems)
