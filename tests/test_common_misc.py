import json

import pytest

from common import urls
from common.disclaimer import DISCLAIMER, NON_AFFILIATION
from common.models import CivisObject, Finding, FindingSource, Impact, Level, ObjectType, WorkflowRef, workflows_json
from common.version import __version__


def test_object_urls(monkeypatch):
    monkeypatch.delenv("CIVIS_PLATFORM_URL", raising=False)
    assert (
        urls.object_url(ObjectType.CONTAINER_SCRIPT, 42)
        == "https://platform.civisanalytics.com/spa/#/scripts/containers/42"
    )
    assert urls.object_url(ObjectType.NOTEBOOK, 7, base="https://example.test/") == "https://example.test/spa/#/notebooks/7"
    for object_type in ObjectType:
        assert urls.object_url(object_type, 1).endswith("/1")


def test_object_url_uses_environment_base(monkeypatch):
    monkeypatch.setenv("CIVIS_PLATFORM_URL", "https://civis.example.test")
    assert urls.object_url(ObjectType.SERVICE, 3) == "https://civis.example.test/spa/#/services/3"


def test_object_url_requires_integer_id():
    with pytest.raises(ValueError):
        urls.object_url(ObjectType.SERVICE, "3")


def test_disclaimer_says_the_essentials():
    assert DISCLAIMER.startswith('This tool is provided free and "as is," without warranty of any kind')
    assert "It is not a complete audit." in DISCLAIMER
    assert "Results depend on the permissions of the Civis user the scan runs as" in DISCLAIMER
    assert "Test every job before February 2027" in DISCLAIMER
    assert "has no obligation to provide support or updates" in DISCLAIMER
    assert DISCLAIMER.endswith("This is not professional advice.")
    assert chr(0x2014) not in DISCLAIMER  # no em dashes
    assert not set(DISCLAIMER) & set("“”‘’")  # straight quotes only


def test_non_affiliation_statement_is_the_fixed_text():
    assert NON_AFFILIATION == (
        "This tool is independent and is not affiliated with, endorsed by, sponsored by, or supported by "
        'Civis Analytics. "Civis" and "Civis Platform" are trademarks of Civis Analytics, used here only to '
        "identify the platform this tool works with."
    )


def test_readme_opens_with_the_non_affiliation_statement():
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    paragraphs = [" ".join(p.split()) for p in readme.split("\n\n")]
    assert paragraphs[0].startswith("# Python Migration Scanner for Civis Platform by Expedition Data")
    assert paragraphs[1] == NON_AFFILIATION  # Right under the title ...
    flat = " ".join(readme.split())
    assert flat.index(NON_AFFILIATION) < flat.index("**Disclaimer.**")  # ... and before the disclaimer.


def test_disclaimer_fits_its_scan_runs_column():
    from common.schema import SCAN_RUNS

    column = next(c for c in SCAN_RUNS.columns if c.name == "disclaimer")
    assert len(DISCLAIMER.encode("utf-8")) <= int(column.sql_type.split("(")[1].rstrip(")"))


def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_rows_serialize_enums_as_plain_strings():
    refs = [WorkflowRef(6, "Second"), WorkflowRef(5, "First")]
    obj = CivisObject(object_id=1, object_type=ObjectType.PYTHON_SCRIPT, name="example", url="u", workflows=refs)
    row = obj.to_row()
    assert row["object_type"] == "python_script" and type(row["object_type"]) is str
    assert json.loads(row["workflows"]) == [{"id": 5, "name": "First"}, {"id": 6, "name": "Second"}]
    assert obj.workflow_ids == [5, 6]
    finding = Finding(
        1, ObjectType.PYTHON_SCRIPT, "CV3-ML", "inline source", 3,
        FindingSource.CODE, Level.HIGH, Level.HIGH, Impact.AFFECTED, "fix",
    )
    assert finding.to_row()["impact"] == "affected"


def test_workflows_json_is_capped_for_the_column():
    refs = [WorkflowRef(i, "w" * 300) for i in range(100)]
    text = workflows_json(refs)
    assert len(text) < 4096
    entries = json.loads(text)
    assert entries[-1]["more"] == 100 - (len(entries) - 1)
    assert all(len(e.get("name", "")) <= 200 for e in entries)
    assert workflows_json([]) is None


def test_readme_carries_the_same_disclaimer():
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    quoted = " ".join(line[2:].strip() for line in readme.splitlines() if line.startswith("> "))
    assert DISCLAIMER in quoted.replace("**Disclaimer.** ", "")
