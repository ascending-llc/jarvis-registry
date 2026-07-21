"""Unit tests for the Layer B downstream OAuth endpoints (registry-as-AS):
``/downstream/oauth/authorize`` and ``/downstream/oauth/token``.
"""

import json
import time
from unittest.mock import AsyncMock, Mock

import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from registry.api.v1.mcp.consent_routes import router as consent_router
from registry.api.v1.mcp.oauth_router import router
from registry.auth.dependencies import get_current_user
from registry.constants import DownstreamOAuthConstants
from registry.core.config import settings
from registry.deps import (
    get_consent_store,
    get_mcp_service,
    get_oauth_state_store,
    get_pending_consent_store,
    get_redis_client,
    get_server_service,
    get_session_store,
)
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE, downstream_mcp_code_key
from registry_pkgs.core.jwt_tokens import verify_managed_agent_token

REGISTERED_REDIRECT_URIS = ["http://localhost:33418/cb", "https://app.example.com/cb"]

USER_A = "507f1f77bcf86cd799439011"
USER_B = "507f1f77bcf86cd799439012"
CODE = "upstream-auth-code-xyz"
VERIFIER = "verifier-0123456789-0123456789-0123456789-0123456789"


def _session_user(user_id: str = USER_A) -> dict:
    return {
        "user_id": user_id,
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


@pytest.fixture
def redis_mock() -> Mock:
    return Mock()


@pytest.fixture
def store_mock() -> Mock:
    store = Mock()
    device_codes: dict[str, dict] = {}
    user_codes: dict[str, str] = {}
    refresh_tokens: dict[str, dict] = {}

    def save_device_authorization(device_code: str, user_code: str, data: dict, ttl_seconds: int) -> None:
        device_codes[device_code] = dict(data)
        user_codes[user_code] = device_code

    def update_device_code(device_code: str, data: dict) -> bool:
        if device_code not in device_codes:
            return False
        device_codes[device_code] = dict(data)
        return True

    def consume_device_code_and_save_refresh_token(
        device_code: str,
        refresh_token: str,
        refresh_data: dict,
        *,
        expected_client_id: str,
        expected_user_id: str,
        expected_server_path: str,
    ) -> dict | None:
        data = device_codes.get(device_code)
        if (
            data is None
            or data.get("status") != "approved"
            or data.get("client_id") != expected_client_id
            or data.get("user_id") != expected_user_id
            or data.get("server_path") != expected_server_path
        ):
            return None
        device_codes.pop(device_code)
        refresh_tokens[refresh_token] = dict(refresh_data)
        return data

    # The direct-connect client registered (via DCR against auth-server) these redirect_uris.
    store.get_client = Mock(return_value={"redirect_uris": list(REGISTERED_REDIRECT_URIS)})
    store.validate_client_credentials = Mock(return_value=True)
    store.save_refresh_token = Mock()
    store.get_refresh_token = Mock(return_value=None)
    store.rotate_refresh_token = Mock(return_value=None)
    store.device_codes = device_codes
    store.user_codes = user_codes
    store.refresh_tokens = refresh_tokens
    store.save_device_authorization = Mock(side_effect=save_device_authorization)
    store.get_device_code = Mock(side_effect=lambda device_code: device_codes.get(device_code))
    store.update_device_code = Mock(side_effect=update_device_code)
    store.get_user_code = Mock(side_effect=lambda user_code: user_codes.get(user_code))
    store.consume_device_code = Mock(side_effect=lambda device_code: device_codes.pop(device_code, None))
    store.consume_device_code_and_save_refresh_token = Mock(side_effect=consume_device_code_and_save_refresh_token)
    return store


@pytest.fixture
def consent_store_mock() -> Mock:
    store = Mock()
    store.has_client_consent = Mock(return_value=True)
    store.grant_client_consent = Mock()
    store.has_server_consent = Mock(return_value=True)
    store.grant_server_consent = Mock()
    return store


@pytest.fixture
def pending_consent_store() -> _InMemoryPendingConsentStore:
    return _InMemoryPendingConsentStore()


@pytest.fixture
def session_store_mock() -> Mock:
    store = Mock()
    store.pop = Mock(return_value=None)
    return store


@pytest.fixture
def mcp_service_mock() -> Mock:
    svc = Mock()
    svc.oauth_service.initiate_oauth_flow = AsyncMock(
        return_value=("flow-1", "https://github.com/login/oauth/authorize?x=1", None)
    )
    return svc


@pytest.fixture
def server_service_mock() -> Mock:
    svc = Mock()
    server = Mock()
    server.path = "/github"
    server.serverName = "github"
    svc.get_server_by_path = AsyncMock(return_value=server)
    svc.extract_server_path = AsyncMock(return_value="/github")
    return svc


@pytest.fixture
def client(
    redis_mock,
    mcp_service_mock,
    server_service_mock,
    store_mock,
    consent_store_mock,
    pending_consent_store,
    session_store_mock,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.include_router(consent_router)
    app.dependency_overrides[get_redis_client] = lambda: redis_mock
    app.dependency_overrides[get_mcp_service] = lambda: mcp_service_mock
    app.dependency_overrides[get_server_service] = lambda: server_service_mock
    app.dependency_overrides[get_oauth_state_store] = lambda: store_mock
    app.dependency_overrides[get_consent_store] = lambda: consent_store_mock
    app.dependency_overrides[get_pending_consent_store] = lambda: pending_consent_store
    app.dependency_overrides[get_session_store] = lambda: session_store_mock
    # By default the authorize endpoint sees a session for USER_A (matches the URL user_id).
    app.dependency_overrides[get_current_user] = lambda: _session_user(USER_A)
    return TestClient(app)


def _stored_entry(
    user_id: str = USER_A,
    server_path: str = "github",
    client_id: str = "claude",
    redirect_uri: str = "http://localhost:33418/cb",
) -> str:
    return json.dumps(
        {
            "code_challenge": create_s256_code_challenge(VERIFIER),
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "user_id": user_id,
            "server_path": server_path,
        }
    )


def _device_state(
    *,
    status_value: str = "pending",
    user_id: str = USER_A,
    server_path: str = "github",
    client_id: str = "claude",
) -> dict:
    return {
        "user_id": user_id,
        "server_path": server_path,
        "client_id": client_id,
        "scope": "mcp-proxy-ops",
        "status": status_value,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + 900,
        "nonce": "device-nonce",
    }


def test_authorize_redirects_to_provider(client, redis_mock):
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": "http://localhost:33418/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://github.com/login/oauth/authorize?x=1"


def test_authorize_without_client_consent_redirects_to_frontend(
    client,
    consent_store_mock,
    pending_consent_store,
    mcp_service_mock,
):
    consent_store_mock.has_client_consent.return_value = False

    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": "http://localhost:33418/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )

    assert resp.status_code == 307
    assert resp.headers["location"].startswith(f"{settings.registry_client_url}/consent/downstream?nonce=")
    assert len(pending_consent_store.pending) == 1
    pending = next(iter(pending_consent_store.pending.values()))
    assert pending["user_id"] == USER_A
    assert pending["client_id"] == "claude"
    assert pending["server_path"] == "github"
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_registry_client_skips_consent_gate(
    client,
    consent_store_mock,
    pending_consent_store,
    mcp_service_mock,
):
    consent_store_mock.has_client_consent.return_value = False
    mcp_service_mock.oauth_service.initiate_oauth_flow.reset_mock()

    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={
            "client_id": settings.jwt_token_config.registry_client_id,
            "redirect_uri": "http://localhost:33418/cb",
            "code_challenge": "abc",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 307
    assert resp.headers["location"] == "https://github.com/login/oauth/authorize?x=1"
    assert pending_consent_store.pending == {}
    consent_store_mock.has_client_consent.assert_not_called()
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_called_once()


def test_authorize_invalid_user_id_returns_400(client):
    resp = client.get(
        "/mcp/downstream/oauth/authorize/mcpgw/github",
        params={"client_id": "claude", "redirect_uri": "http://x/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_authorize_unknown_server_returns_404(client, server_service_mock):
    server_service_mock.extract_server_path = AsyncMock(return_value=None)
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/ghost",
        params={"client_id": "claude", "redirect_uri": "https://x/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_authorize_rejects_unsupported_response_type(client, mcp_service_mock):
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={
            "client_id": "claude",
            "redirect_uri": "https://app.example.com/cb",
            "code_challenge": "abc",
            "response_type": "token",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_rejects_non_s256_challenge_method(client, mcp_service_mock):
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={
            "client_id": "claude",
            "redirect_uri": "https://app.example.com/cb",
            "code_challenge": "abc",
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_rejects_dangerous_redirect_scheme(client, mcp_service_mock):
    # Dangerous browser-executing schemes (javascript:, data:, …) must be blocked before
    # they reach the 302 redirect sink.  Native private-use schemes (vscode://, …) are allowed.
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={
            "client_id": "claude",
            "redirect_uri": "javascript:alert(1)",
            "code_challenge": "abc",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_accepts_native_scheme_redirect_uri(client, store_mock, mcp_service_mock):
    # RFC 8252 §7.1 private-use schemes (e.g. vscode://) must be accepted structurally and
    # proceed to the provider when the URI is registered for the client.
    native_uri = "vscode://saoudrizwan.claude-dev/oauth"
    store_mock.get_client.return_value = {"redirect_uris": [native_uri]}
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": native_uri, "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_called_once()


def test_authorize_rejects_unregistered_redirect_uri(client, store_mock, mcp_service_mock):
    store_mock.get_client.return_value = {"redirect_uris": ["https://other.example.com/cb"]}
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": "https://app.example.com/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_rejects_unknown_client(client, store_mock, mcp_service_mock):
    store_mock.get_client.return_value = None
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "ghost", "redirect_uri": "https://app.example.com/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_session_user_mismatch_returns_403(client, mcp_service_mock):
    # AS-1545 review #3: a logged-in user (USER_B) cannot initiate a downstream flow for another
    # user's account (USER_A in the URL) — that would inject a token into the victim's account.
    client.app.dependency_overrides[get_current_user] = lambda: _session_user(USER_B)
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": "http://localhost:33418/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    # The Layer A flow must never be initiated for a mismatched session.
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_authorize_unauthenticated_is_not_public(client, mcp_service_mock):
    # AS-1545 review #3: with no session, the real get_current_user dependency rejects the request
    # (401). In production the UnifiedAuthMiddleware 401s first; either way authorize is not public
    # and the downstream flow is never initiated.
    client.app.dependency_overrides.pop(get_current_user)
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": "http://localhost:33418/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_called()


def test_get_downstream_consent_context_returns_client_metadata(
    client,
    pending_consent_store,
    store_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "response_type": "code",
            "redirect_uri": "http://localhost:33418/cb",
            "code_challenge": "abc",
            "code_challenge_method": "S256",
            "state": "state-1",
        },
    )
    store_mock.get_client.return_value = {
        "client_name": "Claude",
        "client_uri": "https://claude.ai",
        "ip_address": "203.0.113.7",
        "registered_at": 1_700_000_000,
        "redirect_uris": list(REGISTERED_REDIRECT_URIS),
    }

    resp = client.get("/mcp/consent/downstream", params={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {
        "client_name": "Claude",
        "client_uri": "https://claude.ai",
        "ip_address": "203.0.113.7",
        "registered_at": 1_700_000_000,
        "server_path": "github",
    }


def test_get_downstream_consent_context_rejects_other_user(client, pending_consent_store):
    pending_consent_store.save("nonce-1", {"user_id": USER_B, "server_path": "github", "client_id": "claude"})

    resp = client.get("/mcp/consent/downstream", params={"nonce": "nonce-1"})

    assert resp.status_code == 404


def test_approve_downstream_consent_grants_and_returns_redirect(
    client,
    pending_consent_store,
    consent_store_mock,
    mcp_service_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "response_type": "code",
            "redirect_uri": "http://localhost:33418/cb",
            "code_challenge": "abc",
            "code_challenge_method": "S256",
            "state": "state-1",
        },
    )

    resp = client.post("/mcp/consent/downstream", json={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {"redirect_url": "https://github.com/login/oauth/authorize?x=1"}
    consent_store_mock.grant_client_consent.assert_called_once_with(USER_A, "claude")
    assert pending_consent_store.peek("nonce-1") is None
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_called_once()


def test_approve_downstream_consent_rejects_reused_nonce(client):
    resp = client.post("/mcp/consent/downstream", json={"nonce": "missing"})

    assert resp.status_code == 404


def test_approve_downstream_consent_rejects_other_user_without_consuming_nonce(
    client,
    pending_consent_store,
    consent_store_mock,
):
    """A wrong-user approval attempt must not destroy the nonce for its rightful owner."""
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "response_type": "code",
            "redirect_uri": "http://localhost:33418/cb",
            "code_challenge": "abc",
            "code_challenge_method": "S256",
            "state": "state-1",
        },
    )
    client.app.dependency_overrides[get_current_user] = lambda: _session_user(USER_B)

    resp = client.post("/mcp/consent/downstream", json={"nonce": "nonce-1"})

    assert resp.status_code == 404
    assert pending_consent_store.peek("nonce-1") is not None
    consent_store_mock.grant_client_consent.assert_not_called()


def test_deny_downstream_consent_removes_pending_without_granting(
    client,
    pending_consent_store,
    consent_store_mock,
    session_store_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {"user_id": USER_A, "server_path": "github", "client_id": "claude"},
    )

    resp = client.post("/mcp/consent/downstream/deny", json={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "denied", "client_branding": None}
    consent_store_mock.grant_client_consent.assert_not_called()
    assert pending_consent_store.peek("nonce-1") is None
    session_store_mock.pop.assert_not_called()


def test_deny_downstream_consent_rejects_other_user(client, pending_consent_store):
    pending_consent_store.save("nonce-1", {"user_id": USER_B, "server_path": "github", "client_id": "claude"})

    resp = client.post("/mcp/consent/downstream/deny", json={"nonce": "nonce-1"})

    assert resp.status_code == 404
    assert pending_consent_store.peek("nonce-1") is not None


def test_deny_downstream_consent_rejects_reused_nonce(client):
    resp = client.post("/mcp/consent/downstream/deny", json={"nonce": "missing"})

    assert resp.status_code == 404


def test_get_server_consent_context_returns_client_and_server_metadata(
    client,
    pending_consent_store,
    store_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "/github",
            "client_id": "claude",
        },
    )
    store_mock.get_client.return_value = {
        "client_name": "Claude",
        "client_uri": "https://claude.ai",
        "ip_address": "203.0.113.7",
        "registered_at": 1_700_000_000,
    }

    resp = client.get("/mcp/consent/server", params={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {
        "client_name": "Claude",
        "client_uri": "https://claude.ai",
        "ip_address": "203.0.113.7",
        "registered_at": 1_700_000_000,
        "server_path": "/github",
        "server_name": "github",
    }


def test_get_server_consent_context_rejects_other_user(client, pending_consent_store):
    pending_consent_store.save("nonce-1", {"user_id": USER_B, "server_path": "/github", "client_id": "claude"})

    resp = client.get("/mcp/consent/server", params={"nonce": "nonce-1"})

    assert resp.status_code == 404


def test_approve_server_consent_grants_and_consumes_nonce(
    client,
    pending_consent_store,
    consent_store_mock,
    session_store_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "/github",
            "client_id": "claude",
        },
    )

    resp = client.post("/mcp/consent/server", json={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "client_branding": None}
    consent_store_mock.grant_server_consent.assert_called_once_with(USER_A, "claude", "/github")
    assert pending_consent_store.peek("nonce-1") is None
    session_store_mock.pop.assert_not_called()


def test_approve_server_consent_rejects_lost_consume_race(
    client,
    pending_consent_store,
    consent_store_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {"user_id": USER_A, "server_path": "/github", "client_id": "claude"},
    )
    pending_consent_store.consume = Mock(return_value=None)

    response = client.post("/mcp/consent/server", json={"nonce": "nonce-1"})

    assert response.status_code == 404
    consent_store_mock.grant_server_consent.assert_not_called()


def test_approve_server_consent_notifies_mode1_session_and_returns_branding(
    client,
    pending_consent_store,
    consent_store_mock,
    session_store_mock,
):
    """Mode 1 (mcpgw) pending records carry elicitation_id/client_branding; approval should pop
    the paused session, send the elicitation/complete notification, and echo the branding back
    so the frontend can deep-link the user back to their AI app."""
    fake_session = Mock()
    fake_session.send_elicit_complete = AsyncMock()
    session_store_mock.pop.return_value = fake_session
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "/github",
            "client_id": "claude",
            "elicitation_id": "elicit-1",
            "client_branding": "vscode",
            "notify_elicitation_complete": True,
        },
    )

    resp = client.post("/mcp/consent/server", json={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "client_branding": "vscode"}
    session_store_mock.pop.assert_called_once_with("elicit-1")
    fake_session.send_elicit_complete.assert_awaited_once_with("elicit-1")


def test_approve_server_consent_rejects_reused_nonce(client):
    resp = client.post("/mcp/consent/server", json={"nonce": "missing"})

    assert resp.status_code == 404


def test_approve_server_consent_rejects_other_user_without_consuming_nonce(
    client,
    pending_consent_store,
    consent_store_mock,
):
    """A wrong-user approval attempt must not destroy the nonce for its rightful owner."""
    pending_consent_store.save(
        "nonce-1",
        {"user_id": USER_A, "server_path": "/github", "client_id": "claude"},
    )
    client.app.dependency_overrides[get_current_user] = lambda: _session_user(USER_B)

    resp = client.post("/mcp/consent/server", json={"nonce": "nonce-1"})

    assert resp.status_code == 404
    assert pending_consent_store.peek("nonce-1") is not None
    consent_store_mock.grant_server_consent.assert_not_called()


def test_deny_server_consent_removes_pending_without_granting(
    client,
    pending_consent_store,
    consent_store_mock,
    session_store_mock,
):
    pending_consent_store.save(
        "nonce-1",
        {"user_id": USER_A, "server_path": "/github", "client_id": "claude"},
    )

    resp = client.post("/mcp/consent/server/deny", json={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "denied", "client_branding": None}
    consent_store_mock.grant_server_consent.assert_not_called()
    assert pending_consent_store.peek("nonce-1") is None
    session_store_mock.pop.assert_not_called()


def test_deny_server_consent_notifies_mode1_session_and_returns_branding(
    client,
    pending_consent_store,
    consent_store_mock,
    session_store_mock,
):
    """Denying a Mode 1 (mcpgw) elicitation still notifies the paused session so the blocked tool
    call can retry immediately — the notification just means "the human responded," not "access
    was granted"; the retry will hit the gate again and get a fresh elicitation."""
    fake_session = Mock()
    fake_session.send_elicit_complete = AsyncMock()
    session_store_mock.pop.return_value = fake_session
    pending_consent_store.save(
        "nonce-1",
        {
            "user_id": USER_A,
            "server_path": "/github",
            "client_id": "claude",
            "elicitation_id": "elicit-1",
            "client_branding": "claude",
            "notify_elicitation_complete": True,
        },
    )

    resp = client.post("/mcp/consent/server/deny", json={"nonce": "nonce-1"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "denied", "client_branding": "claude"}
    consent_store_mock.grant_server_consent.assert_not_called()
    session_store_mock.pop.assert_called_once_with("elicit-1")
    fake_session.send_elicit_complete.assert_awaited_once_with("elicit-1")


def test_deny_server_consent_rejects_other_user(client, pending_consent_store):
    pending_consent_store.save("nonce-1", {"user_id": USER_B, "server_path": "/github", "client_id": "claude"})

    resp = client.post("/mcp/consent/server/deny", json={"nonce": "nonce-1"})

    assert resp.status_code == 404
    assert pending_consent_store.peek("nonce-1") is not None


def test_deny_server_consent_rejects_reused_nonce(client):
    resp = client.post("/mcp/consent/server/deny", json={"nonce": "missing"})

    assert resp.status_code == 404


def _post_token(
    client,
    *,
    user_id=USER_A,
    server_path="github",
    code=CODE,
    client_id="claude",
    verifier=VERIFIER,
    redirect_uri="http://localhost:33418/cb",
):
    return client.post(
        f"/mcp/downstream/oauth/token/{user_id}/{server_path}",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
    )


def test_token_happy_path_returns_access_and_refresh_token(client, redis_mock, store_mock):
    redis_mock.getdel.return_value = _stored_entry()
    resp = _post_token(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["scope"] == "mcp-proxy-ops"
    claims = verify_managed_agent_token(settings.jwt_token_config, body["access_token"])
    assert claims["user_id"] == USER_A
    assert claims["server_path"] == "github"
    # One-time use: the code is atomically fetched-and-deleted in a single GETDEL call.
    redis_mock.getdel.assert_called_once_with(downstream_mcp_code_key(CODE))
    # The issued refresh token is persisted bound to (user_id, server_path).
    store_mock.save_refresh_token.assert_called_once()
    saved = store_mock.save_refresh_token.call_args.args[1]
    assert saved["user_id"] == USER_A
    assert saved["server_path"] == "github"
    assert saved["client_id"] == "claude"


def _assert_token_error(resp, error: str, description: str, status_code: int = 400) -> None:
    assert resp.status_code == status_code
    body = resp.json()
    assert body["error"] == error
    assert body["error_description"] == description
    assert "detail" not in body


def test_token_unknown_code_returns_400(client, redis_mock):
    redis_mock.getdel.return_value = None
    resp = _post_token(client)
    _assert_token_error(resp, "invalid_grant", "invalid or expired code")
    redis_mock.getdel.assert_called_once_with(downstream_mcp_code_key(CODE))


def test_token_corrupt_entry_returns_400(client, redis_mock):
    redis_mock.getdel.return_value = "not-json"
    resp = _post_token(client)
    _assert_token_error(resp, "invalid_grant", "invalid or expired code")


def test_token_pkce_mismatch_returns_400(client, redis_mock):
    redis_mock.getdel.return_value = _stored_entry()
    resp = _post_token(client, verifier="wrong-verifier")
    _assert_token_error(resp, "invalid_grant", "PKCE verification failed")


def test_token_client_id_mismatch_returns_400(client, redis_mock):
    redis_mock.getdel.return_value = _stored_entry(client_id="claude")
    resp = _post_token(client, client_id="someone-else")
    _assert_token_error(resp, "invalid_client", "client_id mismatch")


def test_token_redirect_uri_mismatch_returns_400(client, redis_mock):
    redis_mock.getdel.return_value = _stored_entry(redirect_uri="http://localhost:33418/cb")
    resp = _post_token(client, redirect_uri="http://evil.localhost/cb")
    _assert_token_error(resp, "invalid_grant", "redirect_uri mismatch")


def test_token_rejects_cross_user_binding(client, redis_mock):
    # Code minted for USER_A, redeemed under USER_B's token endpoint URL → rejected.
    redis_mock.getdel.return_value = _stored_entry(user_id=USER_A)
    resp = _post_token(client, user_id=USER_B)
    _assert_token_error(resp, "invalid_grant", "code does not match this endpoint")


def test_token_rejects_cross_server_binding(client, redis_mock):
    redis_mock.getdel.return_value = _stored_entry(server_path="github")
    resp = _post_token(client, server_path="slack")
    _assert_token_error(resp, "invalid_grant", "code does not match this endpoint")


def test_token_unsupported_grant_type_returns_400(client, redis_mock):
    redis_mock.getdel.return_value = _stored_entry()
    resp = client.post(
        f"/mcp/downstream/oauth/token/{USER_A}/github",
        data={
            "grant_type": "client_credentials",
            "code": CODE,
            "client_id": "claude",
            "code_verifier": VERIFIER,
            "redirect_uri": "http://localhost:33418/cb",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


# ---- refresh_token grant ----


def _refresh_data(user_id=USER_A, server_path="github", client_id="claude"):
    return {
        "client_id": client_id,
        "user_id": user_id,
        "server_path": server_path,
        "scope": "mcp-proxy-ops",
    }


def _post_refresh(client, *, user_id=USER_A, server_path="github", client_id="claude", refresh_token="rt-old"):
    return client.post(
        f"/mcp/downstream/oauth/token/{user_id}/{server_path}",
        data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token},
    )


def test_refresh_happy_path_rotates_token(client, store_mock):
    store_mock.get_refresh_token.return_value = _refresh_data()
    store_mock.rotate_refresh_token.return_value = _refresh_data()
    resp = _post_refresh(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["scope"] == "mcp-proxy-ops"
    claims = verify_managed_agent_token(settings.jwt_token_config, body["access_token"])
    assert claims["user_id"] == USER_A
    assert claims["server_path"] == "github"
    store_mock.rotate_refresh_token.assert_called_once()


def test_refresh_missing_token_returns_400(client, store_mock):
    resp = client.post(
        f"/mcp/downstream/oauth/token/{USER_A}/github",
        data={"grant_type": "refresh_token", "client_id": "claude"},
    )
    _assert_token_error(resp, "invalid_request", "refresh_token is required")


def test_refresh_unknown_token_returns_400(client, store_mock):
    store_mock.get_refresh_token.return_value = None
    _assert_token_error(_post_refresh(client), "invalid_grant", "invalid or expired refresh_token")


def test_refresh_client_id_mismatch_returns_400(client, store_mock):
    store_mock.get_refresh_token.return_value = _refresh_data(client_id="claude")
    _assert_token_error(_post_refresh(client, client_id="someone-else"), "invalid_client", "client_id mismatch")


def test_refresh_rejects_cross_user_binding(client, store_mock):
    store_mock.get_refresh_token.return_value = _refresh_data(user_id=USER_A)
    _assert_token_error(
        _post_refresh(client, user_id=USER_B), "invalid_grant", "refresh_token does not match this endpoint"
    )


def test_refresh_rejects_cross_server_binding(client, store_mock):
    store_mock.get_refresh_token.return_value = _refresh_data(server_path="github")
    _assert_token_error(
        _post_refresh(client, server_path="slack"), "invalid_grant", "refresh_token does not match this endpoint"
    )


def test_refresh_replayed_token_returns_400(client, store_mock):
    # Token passes the read checks but loses the atomic rotation race → rejected.
    store_mock.get_refresh_token.return_value = _refresh_data()
    store_mock.rotate_refresh_token.return_value = None
    resp = _post_refresh(client)
    assert resp.status_code == 400
    # RFC 6749 §5.2: token errors carry a machine-readable `error` code, not `{"detail": ...}`.
    assert resp.json()["error"] == "invalid_grant"


def test_token_errors_use_oauth_error_shape(client, store_mock):
    store_mock.get_refresh_token.return_value = None
    body = _post_refresh(client).json()
    assert body["error"] == "invalid_grant"
    assert "error_description" in body
    assert "detail" not in body


# ---- client_secret validation (M4) ----


def test_token_invalid_client_secret_returns_invalid_client(client, redis_mock, store_mock):
    store_mock.validate_client_credentials.return_value = False
    redis_mock.getdel.return_value = _stored_entry()
    _assert_token_error(_post_token(client), "invalid_client", "invalid client credentials")
    # The code must NOT be consumed when client auth fails.
    redis_mock.getdel.assert_not_called()


def test_refresh_invalid_client_secret_returns_invalid_client(client, store_mock):
    store_mock.validate_client_credentials.return_value = False
    store_mock.get_refresh_token.return_value = _refresh_data()
    _assert_token_error(_post_refresh(client), "invalid_client", "invalid client credentials")
    # The refresh token must NOT be touched when client auth fails.
    store_mock.get_refresh_token.assert_not_called()


# ---- device authorization grant (AS-1727) ----


def test_device_authorization_creates_device_and_pending_consent(
    client,
    store_mock,
    pending_consent_store,
):
    store_mock.get_client.return_value = {
        "redirect_uris": list(REGISTERED_REDIRECT_URIS),
        "grant_types": ["authorization_code", "refresh_token", DEVICE_CODE_GRANT_TYPE],
    }

    response = client.post(
        f"/mcp/downstream/oauth/device/{USER_A}/github",
        data={"client_id": "claude"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verification_uri"] == f"{settings.registry_client_url}/device"
    assert body["verification_uri_complete"].endswith(f"?user_code={body['user_code']}")
    assert body["expires_in"] == 900
    assert body["interval"] == 5
    state = store_mock.device_codes[body["device_code"]]
    assert state["status"] == "pending"
    assert state["client_id"] == "claude"
    assert pending_consent_store.peek(state["nonce"]) == {
        "flow_type": "device",
        "user_id": USER_A,
        "client_id": "claude",
        "server_path": "github",
        "device_code": body["device_code"],
    }
    # Redis retains the device_code past its stated 900s so a late poll hits the expires_at check
    # (expired_token) instead of the key already being gone (invalid_grant) — see constants.py.
    assert store_mock.save_device_authorization.call_args.kwargs["ttl_seconds"] == (
        DownstreamOAuthConstants.DEVICE_CODE_TTL_SECONDS + DownstreamOAuthConstants.DEVICE_CODE_GRACE_PERIOD_SECONDS
    )
    assert state["expires_at"] - int(time.time()) <= DownstreamOAuthConstants.DEVICE_CODE_TTL_SECONDS


def test_device_authorization_rejects_client_without_device_grant(client, store_mock):
    store_mock.get_client.return_value = {
        "redirect_uris": list(REGISTERED_REDIRECT_URIS),
        "grant_types": ["authorization_code"],
    }

    response = client.post(
        f"/mcp/downstream/oauth/device/{USER_A}/github",
        data={"client_id": "claude"},
    )

    _assert_token_error(
        response,
        "unauthorized_client",
        "client is not registered for the device_code grant type",
    )


def test_device_authorization_rejects_unknown_client(client, store_mock):
    store_mock.get_client.return_value = None

    response = client.post(
        f"/mcp/downstream/oauth/device/{USER_A}/github",
        data={"client_id": "unknown"},
    )

    _assert_token_error(response, "invalid_client", "unknown client_id")


def test_device_authorization_rejects_invalid_user_and_unknown_server(client, store_mock, server_service_mock):
    invalid_user = client.post(
        "/mcp/downstream/oauth/device/not-an-object-id/github",
        data={"client_id": "claude"},
    )
    _assert_token_error(invalid_user, "invalid_request", "invalid user_id: not-an-object-id")

    server_service_mock.extract_server_path.return_value = None
    unknown_server = client.post(
        f"/mcp/downstream/oauth/device/{USER_A}/unknown",
        data={"client_id": "claude"},
    )
    _assert_token_error(
        unknown_server,
        "invalid_request",
        "server not found for path 'unknown'",
        status_code=404,
    )


def test_resolve_device_code_normalizes_manual_entry(client, store_mock):
    store_mock.user_codes["ABCD-EFGH"] = "device-1"
    store_mock.device_codes["device-1"] = _device_state()

    response = client.get("/mcp/consent/device/resolve", params={"user_code": " abcd efgh "})

    assert response.status_code == 200
    assert response.json() == {"nonce": "device-nonce"}


def test_resolve_device_code_rejects_unknown_or_non_pending_code(client, store_mock):
    missing = client.get("/mcp/consent/device/resolve", params={"user_code": "ABCD-EFGH"})
    assert missing.status_code == 404

    store_mock.user_codes["ABCD-EFGH"] = "device-1"
    store_mock.device_codes["device-1"] = _device_state(status_value="approved")
    approved = client.get("/mcp/consent/device/resolve", params={"user_code": "ABCD-EFGH"})
    assert approved.status_code == 404


def test_approve_device_consent_reuses_client_consent_and_starts_layer_a(
    client,
    pending_consent_store,
    consent_store_mock,
    mcp_service_mock,
):
    pending_consent_store.save(
        "device-nonce",
        {
            "flow_type": "device",
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "device_code": "device-1",
        },
    )

    response = client.post("/mcp/consent/downstream", json={"nonce": "device-nonce"})

    assert response.status_code == 200
    assert response.json() == {"redirect_url": "https://github.com/login/oauth/authorize?x=1"}
    consent_store_mock.grant_client_consent.assert_called_once_with(USER_A, "claude")
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_awaited_once()
    call_kwargs = mcp_service_mock.oauth_service.initiate_oauth_flow.await_args.kwargs
    assert call_kwargs["device_code"] == "device-1"
    assert "mcp_client_context" not in call_kwargs


def test_approve_device_consent_rejects_lost_consume_race(
    client,
    pending_consent_store,
    consent_store_mock,
    mcp_service_mock,
):
    pending_consent_store.save(
        "device-nonce",
        {
            "flow_type": "device",
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "device_code": "device-1",
        },
    )
    pending_consent_store.consume = Mock(return_value=None)

    response = client.post("/mcp/consent/downstream", json={"nonce": "device-nonce"})

    assert response.status_code == 404
    consent_store_mock.grant_client_consent.assert_not_called()
    mcp_service_mock.oauth_service.initiate_oauth_flow.assert_not_awaited()


def test_approve_device_consent_marks_failed_when_layer_a_cannot_start(
    client,
    store_mock,
    pending_consent_store,
    consent_store_mock,
    mcp_service_mock,
):
    store_mock.device_codes["device-1"] = _device_state()
    pending_consent_store.save(
        "device-nonce",
        {
            "flow_type": "device",
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "device_code": "device-1",
        },
    )
    mcp_service_mock.oauth_service.initiate_oauth_flow.return_value = (None, None, "provider unavailable")

    response = client.post("/mcp/consent/downstream", json={"nonce": "device-nonce"})

    assert response.status_code == 400
    assert store_mock.device_codes["device-1"]["status"] == "failed"
    assert pending_consent_store.peek("device-nonce") is None
    consent_store_mock.grant_client_consent.assert_not_called()


def test_device_consent_rejects_other_user_without_consuming_nonce(
    client,
    pending_consent_store,
    consent_store_mock,
):
    pending_consent_store.save(
        "device-nonce",
        {
            "flow_type": "device",
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "device_code": "device-1",
        },
    )
    client.app.dependency_overrides[get_current_user] = lambda: _session_user(USER_B)

    response = client.post("/mcp/consent/downstream", json={"nonce": "device-nonce"})

    assert response.status_code == 404
    assert pending_consent_store.peek("device-nonce") is not None
    consent_store_mock.grant_client_consent.assert_not_called()


def test_deny_device_consent_marks_polling_state_denied(
    client,
    store_mock,
    pending_consent_store,
):
    store_mock.device_codes["device-1"] = _device_state()
    pending_consent_store.save(
        "device-nonce",
        {
            "flow_type": "device",
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "device_code": "device-1",
        },
    )

    response = client.post("/mcp/consent/downstream/deny", json={"nonce": "device-nonce"})

    assert response.status_code == 200
    assert store_mock.device_codes["device-1"]["status"] == "denied"


def test_deny_device_consent_rejects_lost_consume_race(
    client,
    store_mock,
    pending_consent_store,
):
    store_mock.device_codes["device-1"] = _device_state()
    pending_consent_store.save(
        "device-nonce",
        {
            "flow_type": "device",
            "user_id": USER_A,
            "server_path": "github",
            "client_id": "claude",
            "device_code": "device-1",
        },
    )
    pending_consent_store.consume = Mock(return_value=None)

    response = client.post("/mcp/consent/downstream/deny", json={"nonce": "device-nonce"})

    assert response.status_code == 404
    assert store_mock.device_codes["device-1"]["status"] == "pending"


def _post_device_token(client: TestClient, *, device_code: str = "device-1") -> Response:
    return client.post(
        f"/mcp/downstream/oauth/token/{USER_A}/github",
        data={
            "grant_type": DEVICE_CODE_GRANT_TYPE,
            "client_id": "claude",
            "device_code": device_code,
        },
    )


def test_device_token_invalid_client_secret_returns_invalid_client(client, store_mock):
    store_mock.device_codes["device-1"] = _device_state()
    store_mock.validate_client_credentials.return_value = False

    _assert_token_error(_post_device_token(client), "invalid_client", "invalid client credentials")
    # The device_code must NOT be touched when client auth fails, matching the authorization_code
    # and refresh_token grants' own client_secret validation (M4).
    store_mock.get_device_code.assert_not_called()


def test_device_token_polling_statuses(client, store_mock):
    store_mock.device_codes["device-1"] = _device_state()
    _assert_token_error(
        _post_device_token(client),
        "authorization_pending",
        "user has not yet authorized this request",
    )

    store_mock.device_codes["device-1"] = _device_state(status_value="denied")
    _assert_token_error(_post_device_token(client), "access_denied", "user denied authorization")

    store_mock.device_codes["device-1"] = _device_state(status_value="failed")
    _assert_token_error(
        _post_device_token(client),
        "server_error",
        "downstream authorization failed",
        status_code=500,
    )


def test_device_token_rejects_expired_and_cross_endpoint_codes(client, store_mock):
    expired_state = _device_state(status_value="approved")
    expired_state["expires_at"] = int(time.time()) - 1
    store_mock.device_codes["device-1"] = expired_state
    _assert_token_error(_post_device_token(client), "expired_token", "device_code has expired")

    store_mock.device_codes["device-1"] = _device_state(status_value="approved", user_id=USER_B)
    _assert_token_error(
        _post_device_token(client),
        "invalid_grant",
        "device_code does not match this endpoint",
    )

    store_mock.device_codes["device-1"] = _device_state(status_value="approved", server_path="slack")
    _assert_token_error(
        _post_device_token(client),
        "invalid_grant",
        "device_code does not match this endpoint",
    )


def test_device_token_approved_is_single_use(client, store_mock):
    store_mock.device_codes["device-1"] = _device_state(status_value="approved")

    response = _post_device_token(client)

    assert response.status_code == 200
    body = response.json()
    claims = verify_managed_agent_token(settings.jwt_token_config, body["access_token"])
    assert claims["user_id"] == USER_A
    assert claims["server_path"] == "github"
    assert body["refresh_token"]
    assert "device-1" not in store_mock.device_codes
    assert store_mock.refresh_tokens[body["refresh_token"]] == {
        "client_id": "claude",
        "user_id": USER_A,
        "server_path": "github",
        "scope": "mcp-proxy-ops",
    }

    _assert_token_error(_post_device_token(client), "invalid_grant", "device_code not found")


def test_device_token_storage_failure_leaves_approved_code_retryable(client, store_mock):
    store_mock.device_codes["device-1"] = _device_state(status_value="approved")
    store_mock.consume_device_code_and_save_refresh_token.side_effect = RuntimeError("redis unavailable")

    response = _post_device_token(client)

    _assert_token_error(response, "server_error", "internal server error", status_code=500)
    assert store_mock.device_codes["device-1"]["status"] == "approved"
