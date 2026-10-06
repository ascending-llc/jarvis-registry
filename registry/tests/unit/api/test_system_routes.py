"""Tests for system routes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.system_routes import router
from registry.auth.dependencies import get_current_user
from registry_pkgs.core.scopes import map_groups_to_scopes

SESSION_SCOPES = ["servers-read", "servers-write"]


def _build_client(groups: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "alice",
        "user_id": "user-1",
        "auth_method": "oauth2",
        "provider": "entra",
        "scopes": SESSION_SCOPES,
        "groups": groups,
    }
    return TestClient(app)


def test_auth_me_returns_token_scopes_from_groups() -> None:
    response = _build_client(["jarvis-registry-power-user"]).get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["tokenScopes"] == map_groups_to_scopes(["jarvis-registry-power-user"])
    assert "mcp-proxy-ops" in body["tokenScopes"]
    assert body["scopes"] == SESSION_SCOPES


def test_auth_me_token_scopes_empty_without_mapped_groups() -> None:
    response = _build_client(["unmapped-group"]).get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["tokenScopes"] == []
    assert response.json()["scopes"] == SESSION_SCOPES
