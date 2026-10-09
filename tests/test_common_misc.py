import pytest

from common import urls
from common.disclaimer import DISCLAIMER
from common.models import CivisObject, Finding, FindingSource, Impact, Level, ObjectType
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
    assert "not a complete audit" in DISCLAIMER
    assert "Test every job before February 2027" in DISCLAIMER
    assert chr(0x2014) not in DISCLAIMER  # no em dashes


def test_version_is_semver():
    parts = __version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_rows_serialize_enums_as_plain_strings():
    obj = CivisObject(object_id=1, object_type=ObjectType.PYTHON_SCRIPT, name="example", url="u", workflow_ids=[5, 6])
    row = obj.to_row()
    assert row["object_type"] == "python_script" and type(row["object_type"]) is str
    assert row["workflow_ids"] == "5,6"
    finding = Finding(
        1, ObjectType.PYTHON_SCRIPT, "CV3-ML", "inline source", 3,
        FindingSource.CODE, Level.HIGH, Level.HIGH, Impact.AFFECTED, "fix",
    )
    assert finding.to_row()["impact"] == "affected"
