"""Unit tests for the Layer B downstream OAuth endpoints (registry-as-AS):
``/downstream/oauth/authorize`` and ``/downstream/oauth/token``.
"""

import json
from unittest.mock import AsyncMock, Mock

import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.v1.mcp.oauth_router import router
from registry.auth.dependencies import get_current_user
from registry.core.config import settings
from registry.deps import get_mcp_service, get_oauth_state_store, get_redis_client, get_server_service
from registry_pkgs.core.downstream_oauth import downstream_mcp_code_key
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
    }


@pytest.fixture
def redis_mock() -> Mock:
    return Mock()


@pytest.fixture
def store_mock() -> Mock:
    store = Mock()
    # The direct-connect client registered (via DCR against auth-server) these redirect_uris.
    store.get_client = Mock(return_value={"redirect_uris": list(REGISTERED_REDIRECT_URIS)})
    store.validate_client_credentials = Mock(return_value=True)
    store.save_refresh_token = Mock()
    store.get_refresh_token = Mock(return_value=None)
    store.rotate_refresh_token = Mock(return_value=None)
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
def client(redis_mock, mcp_service_mock, server_service_mock, store_mock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_redis_client] = lambda: redis_mock
    app.dependency_overrides[get_mcp_service] = lambda: mcp_service_mock
    app.dependency_overrides[get_server_service] = lambda: server_service_mock
    app.dependency_overrides[get_oauth_state_store] = lambda: store_mock
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


def test_authorize_redirects_to_provider(client, redis_mock):
    resp = client.get(
        f"/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"client_id": "claude", "redirect_uri": "http://localhost:33418/cb", "code_challenge": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://github.com/login/oauth/authorize?x=1"


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


def test_authorize_rejects_non_http_redirect_scheme(client, mcp_service_mock):
    # A non-http(s) scheme must never reach the redirect sink (open-redirect / exfil via javascript:).
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


def _assert_token_error(resp, error: str, description: str) -> None:
    assert resp.status_code == 400
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
