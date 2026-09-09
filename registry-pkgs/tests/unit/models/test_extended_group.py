import pytest
from pydantic import ValidationError

from registry_pkgs.models import ExtendedGroup
from registry_pkgs.models.extended_group import ExtendedGroupSource


@pytest.fixture(autouse=True)
def _no_collection(monkeypatch):
    monkeypatch.setattr(ExtendedGroup, "get_pymongo_collection", classmethod(lambda cls: None))


@pytest.mark.parametrize("value", ["local", "entra", "google"])
def test_source_accepts_supported_values(value):
    group = ExtendedGroup.model_validate({"name": "g", "source": value})
    assert group.source == ExtendedGroupSource(value)


def test_source_defaults_to_local():
    assert ExtendedGroup.model_validate({"name": "g"}).source == ExtendedGroupSource.LOCAL


def test_source_rejects_unknown_value():
    with pytest.raises(ValidationError):
        ExtendedGroup.model_validate({"name": "g", "source": "okta"})


def test_inherits_groups_collection():
    assert ExtendedGroup.Settings.name == "groups"
