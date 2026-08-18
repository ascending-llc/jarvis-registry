from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
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


def _downstream_error_payload(user_id: str = USER_ID) -> dict:
    return {
        "flow_type": "downstream_error",
        "user_id": user_id,
        "server_path": "github",
        "redirect_uri": "http://localhost:33418/callback?existing=1",
        "error": "invalid_client",
        "error_description": "unknown client_id",
        "client_state": "state-1",
    }


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
    assert data["scopes"] == [
        {
            "name": "mcp-proxy-ops",
            "description": "Act on your behalf to connect to and call tools on your registered MCP servers.",
        }
    ]


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


def test_downstream_error_consent_context_and_approval_are_one_shot() -> None:
    pending_store = _InMemoryPendingConsentStore()
    pending_store.save("error-nonce", _downstream_error_payload())
    client = _build_client(pending_store)

    context_response = client.get("/mcp/consent/downstream-error", params={"nonce": "error-nonce"})
    approve_response = client.post("/mcp/consent/downstream-error", json={"nonce": "error-nonce"})
    replay_response = client.post("/mcp/consent/downstream-error", json={"nonce": "error-nonce"})

    assert context_response.status_code == 200
    assert context_response.json()["redirect_uri"] == "http://localhost:33418/callback?existing=1"
    assert approve_response.status_code == 200
    query = parse_qs(urlsplit(approve_response.json()["redirect_url"]).query)
    assert query == {
        "existing": ["1"],
        "error": ["invalid_client"],
        "error_description": ["unknown client_id"],
        "state": ["state-1"],
    }
    assert replay_response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/mcp/consent/downstream-error"),
        ("POST", "/mcp/consent/downstream-error"),
        ("POST", "/mcp/consent/downstream-error/deny"),
    ],
)
def test_downstream_error_consent_wrong_user_does_not_consume_nonce(method: str, path: str) -> None:
    pending_store = _InMemoryPendingConsentStore()
    pending_store.save("owned-nonce", _downstream_error_payload(user_id="other-user"))
    client = _build_client(pending_store)

    if method == "GET":
        response = client.get(path, params={"nonce": "owned-nonce"})
    else:
        response = client.post(path, json={"nonce": "owned-nonce"})

    assert response.status_code == 404
    assert pending_store.peek("owned-nonce") is not None


def test_downstream_error_consent_deny_never_returns_redirect_uri() -> None:
    pending_store = _InMemoryPendingConsentStore()
    pending_store.save("deny-nonce", _downstream_error_payload())
    client = _build_client(pending_store)

    response = client.post("/mcp/consent/downstream-error/deny", json={"nonce": "deny-nonce"})
    replay_response = client.post("/mcp/consent/downstream-error/deny", json={"nonce": "deny-nonce"})

    assert response.status_code == 200
    assert response.json() == {"status": "denied"}
    assert "redirect" not in response.text
    assert pending_store.peek("deny-nonce") is None
    assert replay_response.status_code == 404


def test_regular_and_error_consent_routes_reject_other_payload_type_without_consuming() -> None:
    pending_store = _InMemoryPendingConsentStore()
    pending_store.save("error-nonce", _downstream_error_payload())
    pending_store.save(
        "regular-nonce",
        {
            "user_id": USER_ID,
            "server_path": "github",
            "client_id": "ext-client",
            "redirect_uri": "http://localhost:33418/callback",
        },
    )
    client = _build_client(pending_store)

    regular_response = client.get("/mcp/consent/downstream", params={"nonce": "error-nonce"})
    error_response = client.get("/mcp/consent/downstream-error", params={"nonce": "regular-nonce"})

    assert regular_response.status_code == 404
    assert error_response.status_code == 404
    assert pending_store.peek("error-nonce") is not None
    assert pending_store.peek("regular-nonce") is not None
