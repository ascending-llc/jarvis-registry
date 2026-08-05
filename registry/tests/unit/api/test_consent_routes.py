from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.v1.mcp.consent_routes import router
from registry.auth.dependencies import get_current_user
from registry.core.config import settings
from registry.deps import get_oauth_state_store, get_pending_consent_store

USER_ID = "507f1f77bcf86cd799439011"


def _session_user() -> dict:
    return {
        "user_id": USER_ID,
        "username": "alice",
        "groups": [],
        "scopes": [],
        "auth_method": "traditional",
        "provider": "local",
        "auth_source": "jwt_session_auth",
        "client_id": settings.registry_app_name,
    }


class _InMemoryPendingConsentStore:
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}

    def save(self, nonce: str, data: dict, ttl_seconds: int = 600) -> None:
        self.pending[nonce] = dict(data)

    def peek(self, nonce: str) -> dict | None:
        return self.pending.get(nonce)

    def consume(self, nonce: str) -> dict | None:
        return self.pending.pop(nonce, None)


def _build_client(pending_store: _InMemoryPendingConsentStore, client_metadata: dict | None = None) -> TestClient:
    store_mock = Mock()
    store_mock.get_client = Mock(
        return_value=client_metadata
        or {
            "client_id": "ext-client",
            "client_name": "External App",
            "client_uri": "https://client.example.com",
            "registered_at": 1_700_000_000,
            "ip_address": "127.0.0.1",
        }
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_oauth_state_store] = lambda: store_mock
    app.dependency_overrides[get_pending_consent_store] = lambda: pending_store
    app.dependency_overrides[get_current_user] = _session_user
    return TestClient(app)


def test_downstream_consent_context_includes_redirect_uri() -> None:
    pending_store = _InMemoryPendingConsentStore()
    pending_store.save(
        "nonce-redirect",
        {
            "user_id": USER_ID,
            "server_path": "github",
            "response_type": "code",
            "client_id": "ext-client",
            "redirect_uri": "https://client.example.com/callback",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
            "state": "some-state",
        },
    )

    client = _build_client(pending_store)
    response = client.get("/mcp/consent/downstream", params={"nonce": "nonce-redirect"})

    assert response.status_code == 200
    data = response.json()
    assert data["redirect_uri"] == "https://client.example.com/callback"
    assert data["client_name"] == "External App"


def test_downstream_consent_context_returns_null_redirect_uri_for_device_flow() -> None:
    pending_store = _InMemoryPendingConsentStore()
    pending_store.save(
        "nonce-device",
        {
            "flow_type": "device",
            "user_id": USER_ID,
            "client_id": "ext-client",
            "server_path": "github",
            "device_code": "dev-code-1",
        },
    )

    client = _build_client(pending_store)
    response = client.get("/mcp/consent/downstream", params={"nonce": "nonce-device"})

    assert response.status_code == 200
    data = response.json()
    assert data["redirect_uri"] is None
    assert data["client_name"] == "External App"
