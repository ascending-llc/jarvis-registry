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
from registry.deps import get_mcp_service, get_redis_client, get_server_service
from registry_pkgs.core.downstream_oauth import downstream_mcp_code_key

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
def client(redis_mock, mcp_service_mock, server_service_mock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_redis_client] = lambda: redis_mock
    app.dependency_overrides[get_mcp_service] = lambda: mcp_service_mock
    app.dependency_overrides[get_server_service] = lambda: server_service_mock
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


def test_token_happy_path_returns_confirmation_token(client, redis_mock):
    redis_mock.getdel.return_value = _stored_entry()
    resp = _post_token(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    assert body["access_token"]
    # One-time use: the code is atomically fetched-and-deleted in a single GETDEL call.
    redis_mock.getdel.assert_called_once_with(downstream_mcp_code_key(CODE))


def test_token_unknown_code_returns_400(client, redis_mock):
    redis_mock.get.return_value = None
    assert _post_token(client).status_code == 400


def test_token_corrupt_entry_returns_400(client, redis_mock):
    redis_mock.get.return_value = "not-json"
    assert _post_token(client).status_code == 400


def test_token_pkce_mismatch_returns_400(client, redis_mock):
    redis_mock.get.return_value = _stored_entry()
    assert _post_token(client, verifier="wrong-verifier").status_code == 400


def test_token_client_id_mismatch_returns_400(client, redis_mock):
    redis_mock.get.return_value = _stored_entry(client_id="claude")
    assert _post_token(client, client_id="someone-else").status_code == 400


def test_token_redirect_uri_mismatch_returns_400(client, redis_mock):
    redis_mock.get.return_value = _stored_entry(redirect_uri="http://localhost:33418/cb")
    assert _post_token(client, redirect_uri="http://evil.localhost/cb").status_code == 400


def test_token_rejects_cross_user_binding(client, redis_mock):
    # Code minted for USER_A, redeemed under USER_B's token endpoint URL → rejected.
    redis_mock.get.return_value = _stored_entry(user_id=USER_A)
    assert _post_token(client, user_id=USER_B).status_code == 400


def test_token_rejects_cross_server_binding(client, redis_mock):
    redis_mock.get.return_value = _stored_entry(server_path="github")
    assert _post_token(client, server_path="slack").status_code == 400


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
