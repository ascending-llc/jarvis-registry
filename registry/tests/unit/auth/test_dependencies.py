import pytest

from registry.auth import dependencies as deps


@pytest.mark.unit
def test_effective_scopes_explicit_only(monkeypatch):
    user_context = {"scopes": ["a", "b", "a"], "groups": ["g1"]}
    monkeypatch.setattr(deps, "map_cognito_groups_to_scopes", lambda _groups: ["x", "y"])
    assert deps.effective_scopes_from_context(user_context) == ["a", "b"]


@pytest.mark.unit
def test_effective_scopes_groups_only(monkeypatch):
    user_context = {"scopes": [], "groups": ["g1"]}
    monkeypatch.setattr(deps, "map_cognito_groups_to_scopes", lambda _groups: ["x", "y"])
    assert deps.effective_scopes_from_context(user_context) == ["x", "y"]


@pytest.mark.unit
def test_effective_scopes_no_scopes_no_groups(monkeypatch):
    user_context = {"scopes": [], "groups": []}
    monkeypatch.setattr(deps, "map_cognito_groups_to_scopes", lambda _groups: ["x"])
    assert deps.effective_scopes_from_context(user_context) == []


@pytest.mark.unit
def test_effective_scopes_explicit_takes_precedence(monkeypatch):
    user_context = {"scopes": ["explicit"], "groups": ["g1"]}
    monkeypatch.setattr(deps, "map_cognito_groups_to_scopes", lambda _groups: ["group-scope"])
    assert deps.effective_scopes_from_context(user_context) == ["explicit"]
