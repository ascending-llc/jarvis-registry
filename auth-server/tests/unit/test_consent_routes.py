"""Unit tests for auth-server consent routes."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from auth_server.deps import (
    get_auth_provider,
    get_consent_store,
    get_oauth2_config,
    get_oauth_state_store,
    get_pending_consent_store,
    get_signer,
    get_user_service,
)
from auth_server.routes.oauth_flow import CONSENT_NONCE_COOKIE, router
from tests.support.oauth_state_store import InMemoryOAuthStateStore


class _InMemoryConsentStore:
    def __init__(self) -> None:
        self.client_consents: set[tuple[str, str]] = set()

    def has_client_consent(self, user_id: str, client_id: str) -> bool:
        return (user_id, client_id) in self.client_consents

    def grant_client_consent(self, user_id: str, client_id: str) -> None:
        self.client_consents.add((user_id, client_id))


class _InMemoryPendingConsentStore:
    def __init__(self) -> None:
        self.pending: dict[str, dict[str, Any]] = {}

    def save(self, nonce: str, data: dict[str, Any], ttl_seconds: int = 600) -> None:
        self.pending[nonce] = dict(data)

    def peek(self, nonce: str) -> dict[str, Any] | None:
        return self.pending.get(nonce)

    def consume(self, nonce: str) -> dict[str, Any] | None:
        return self.pending.pop(nonce, None)


def _pending_payload() -> dict[str, Any]:
    return {
        "token_data": {"access_token": "provider-token"},
        "mapped_user": {
            "user_id": "507f1f77bcf86cd799439011",
            "username": "alice",
            "groups": [],
        },
        "session_data": {
            "client_id": "external-client",
            "client_redirect_uri": "https://client.example.com/callback",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
            "client_state": "client-state",
        },
        "resolved_scopes": ["servers-read"],
    }


def _client() -> tuple[TestClient, InMemoryOAuthStateStore, _InMemoryConsentStore, _InMemoryPendingConsentStore]:
    oauth_store = InMemoryOAuthStateStore()
    oauth_store.save_client(
        "external-client",
        {
            "client_id": "external-client",
            "client_name": "External App",
            "client_uri": "https://client.example.com",
            "registered_at": 1_700_000_000,
            "ip_address": "127.0.0.1",
        },
    )
    consent_store = _InMemoryConsentStore()
    pending_store = _InMemoryPendingConsentStore()

    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_oauth_state_store] = lambda: oauth_store
    app.dependency_overrides[get_consent_store] = lambda: consent_store
    app.dependency_overrides[get_pending_consent_store] = lambda: pending_store
    return TestClient(app), oauth_store, consent_store, pending_store


def _oauth2_config() -> dict[str, Any]:
    return {
        "providers": {
            "keycloak": {
                "enabled": True,
                "client_id": "provider-client",
                "client_secret": "provider-secret",
                "token_url": "https://idp.example.com/token",
                "user_info_url": "https://idp.example.com/userinfo",
                "grant_type": "authorization_code",
                "response_type": "code",
                "scopes": ["openid", "profile"],
            }
        }
    }


def _auth_provider() -> MagicMock:
    provider = MagicMock()
    provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "test-kid"}]})
    provider.client_id = "provider-client"
    provider.m2m_client_id = "provider-client"
    provider.realm = "test-realm"
    provider.realm_url = "https://idp.example.com/realms/test-realm"
    provider.external_realm_url = "https://idp.example.com/realms/test-realm"
    return provider


def _user_service() -> MagicMock:
    service = MagicMock()
    service.resolve_user_id = AsyncMock(return_value="507f1f77bcf86cd799439011")
    return service


def test_consent_page_requires_query_and_cookie_nonce_match() -> None:
    client, _, _, pending_store = _client()
    pending_store.save("nonce-1", _pending_payload())

    response = client.get(
        "/auth/oauth2/consent",
        params={"nonce": "nonce-1"},
        cookies={CONSENT_NONCE_COOKIE: "different"},
    )

    assert response.status_code == 400
    assert "This link has expired" in response.text


def test_consent_page_renders_client_metadata_and_post_forms() -> None:
    client, _, _, pending_store = _client()
    pending_store.save("nonce-1", _pending_payload())

    response = client.get(
        "/auth/oauth2/consent",
        params={"nonce": "nonce-1"},
        cookies={CONSENT_NONCE_COOKIE: "nonce-1"},
    )

    assert response.status_code == 200
    assert "External App" in response.text
    assert 'action="/auth/oauth2/consent/approve"' in response.text
    assert 'action="/auth/oauth2/consent/deny"' in response.text


def test_approve_consent_rejects_form_cookie_nonce_mismatch() -> None:
    client, _, _, pending_store = _client()
    pending_store.save("nonce-1", _pending_payload())

    response = client.post(
        "/auth/oauth2/consent/approve",
        data={"nonce": "nonce-1"},
        cookies={CONSENT_NONCE_COOKIE: "different"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired consent request"
    assert pending_store.peek("nonce-1") is not None


def test_approve_consent_grants_and_finishes_oauth_callback() -> None:
    client, oauth_store, consent_store, pending_store = _client()
    pending_store.save("nonce-1", _pending_payload())

    response = client.post(
        "/auth/oauth2/consent/approve",
        data={"nonce": "nonce-1"},
        cookies={CONSENT_NONCE_COOKIE: "nonce-1"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://client.example.com/callback")
    query = parse_qs(urlparse(location).query)
    assert query["state"] == ["client-state"]
    auth_code = query["code"][0]
    assert oauth_store.get_authcode(auth_code) is not None
    assert consent_store.has_client_consent("507f1f77bcf86cd799439011", "external-client") is True
    assert pending_store.peek("nonce-1") is None


def test_deny_consent_is_post_and_consumes_pending_nonce() -> None:
    client, _, _, pending_store = _client()
    pending_store.save("nonce-1", _pending_payload())

    response = client.post("/auth/oauth2/consent/deny", data={"nonce": "nonce-1"}, follow_redirects=False)

    assert response.status_code == 302
    assert "error=access_denied" in response.headers["location"]
    assert pending_store.peek("nonce-1") is None


@patch("auth_server.routes.oauth_flow.exchange_code_for_token")
@patch("auth_server.routes.oauth_flow.get_token_kid")
@patch("auth_server.routes.oauth_flow.decode_jwt_with_jwk")
def test_oauth_callback_without_client_consent_redirects_to_consent(
    mock_decode_jwt,
    mock_get_token_kid,
    mock_exchange_code_for_token,
) -> None:
    client, oauth_store, _, pending_store = _client()
    signer = URLSafeTimedSerializer("test-secret-key")
    mock_exchange_code_for_token.return_value = {"access_token": "provider-token", "id_token": "provider-id-token"}
    mock_get_token_kid.return_value = "test-kid"
    mock_decode_jwt.return_value = {
        "sub": "provider-user",
        "preferred_username": "alice",
        "email": "alice@example.com",
        "name": "Alice",
        "groups": [],
    }

    client.app.dependency_overrides[get_oauth2_config] = _oauth2_config
    client.app.dependency_overrides[get_signer] = lambda: signer
    client.app.dependency_overrides[get_auth_provider] = _auth_provider
    client.app.dependency_overrides[get_user_service] = _user_service

    session_data = {
        "state": "internal-state",
        "client_state": "client-state",
        "provider": "keycloak",
        "redirect_uri": "https://client.example.com/callback",
        "client_id": "external-client",
        "client_redirect_uri": "https://client.example.com/callback",
        "code_challenge": "challenge",
        "code_challenge_method": "S256",
    }

    response = client.get(
        "/auth/oauth2/callback/keycloak",
        params={"code": "idp-code", "state": "internal-state"},
        cookies={"oauth2_temp_session": signer.dumps(session_data)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://localhost:8888/auth/oauth2/consent?nonce=")
    nonce = parse_qs(urlparse(location).query)["nonce"][0]
    assert pending_store.peek(nonce) is not None
    assert oauth_store.authorization_codes_storage == {}
