"""Exercise both MCP entry points and the direct-connect refresh grant against the same consent."""

from typing import Any
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fakeredis import FakeRedis
from fastapi.testclient import TestClient
from httpx2 import Response

from registry.constants import UrlElicitationInputKey
from registry.container import RegistryContainer
from registry.core.config import settings
from registry.utils.csrf import compute_csrf_token
from registry_pkgs.core.jwt_tokens import mint_crud_session_token
from tests.integration.test_mcpgw_stateless import (
    CLIENT_ID,
    SERVER_ID,
    USER_ID,
    _make_pod,
    _modern_execute_tool,
    _modern_headers,
    _modern_meta,
    _modern_post,
    _server_doc,
)

APPROVE_URL = "/api/v1/mcp/consent/server"
TOKEN_URL = f"/api/v1/mcp/downstream/oauth/token/{USER_ID}/github"
DIRECT_URL = f"/proxy/server/{USER_ID}/github"


@pytest.fixture
def mock_rbac_for_tests() -> None:
    """Exercise production scope checks for both the browser session and the MCP client."""


@pytest.fixture
def container() -> RegistryContainer:
    container = RegistryContainer(settings, db_client=Mock(), redis_client=FakeRedis(decode_responses=True))
    server = _server_doc()
    container.server_service = AsyncMock()
    container.server_service.get_server_by_id.return_value = server
    container.server_service.get_server_by_path.return_value = server
    container.server_service.extract_server_path.return_value = server.path
    container.acl_service = AsyncMock()
    container.acl_service.get_accessible_resource_ids.return_value = {SERVER_ID}
    container.search_service = Mock()
    container.a2a_agent_service = Mock()
    container.a2a_client_registry = Mock()
    container.mcp_proxy_client = AsyncMock()
    container.mcpgw_downstream_transport = AsyncMock()
    return container


def _consent_nonce(message: dict[str, Any]) -> str:
    result = message["result"]
    assert result["resultType"] == "input_required"
    prompt = result["inputRequests"][UrlElicitationInputKey.CONSENT]["params"]
    assert prompt["mode"] == "url"
    url = urlsplit(prompt["url"])
    assert url.path.endswith("/consent/server")
    (nonce,) = parse_qs(url.query)["nonce"]
    return nonce


def _refresh(client: TestClient, token: str) -> Response:
    return client.post(TOKEN_URL, data={"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": token})


def _assert_revoked_refresh(client: TestClient, token: str) -> None:
    response = _refresh(client, token)
    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_grant",
        "error_description": "User consent is required. Restart the authorization flow.",
    }


def test_revoked_client_requires_consent_on_both_mcp_paths(
    container: RegistryContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mcpgw_modern_protocol_enabled", True)
    monkeypatch.setattr(settings, "mcpgw_allowed_hosts", "localhost")
    registration = {"client_name": "Consent integration client", "token_endpoint_auth_method": "none"}
    container.oauth_client_store.save_client(CLIENT_ID, registration)
    old_grant = container.consent_store.grant_client_consent(USER_ID, CLIENT_ID)
    container.oauth_state_store.save_refresh_token(
        "initial-refresh",
        {"client_id": CLIENT_ID, "user_id": USER_ID, "server_path": "github", "consent_grant_id": old_grant},
    )
    session = mint_crud_session_token(
        settings.jwt_token_config,
        subject=USER_ID,
        token_type="access_token",
        expires_in_seconds=300,
        extra_claims={"scope": "user-read"},
    )
    browser = {
        "Cookie": f"{settings.session_cookie_name}={session}",
        settings.csrf_header_name: compute_csrf_token(session),
    }
    app = _make_pod(container)
    app.state.container = container
    with TestClient(app, base_url="http://localhost") as client:
        # Obtain a real access/refresh pair before revocation, proving the grant and registration work.
        issued = _refresh(client, "initial-refresh")
        assert issued.status_code == 200, issued.text
        refresh_token = issued.json()["refresh_token"]
        headers = _modern_headers(
            {
                "Authorization": f"Bearer {issued.json()['access_token']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
        )
        gateway_call = _modern_execute_tool(url_elicitation=True)
        direct_call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {}, "_meta": _modern_meta(url_elicitation=True)},
        }
        direct_headers = {**headers, "MCP-Name": "search"}
        pending = client.post(DIRECT_URL, headers=direct_headers, json=direct_call)
        assert pending.status_code == 200
        stale_nonce = _consent_nonce(pending.json())
        approve_nonce = _consent_nonce(_modern_post(client, gateway_call, headers))
        approved = client.post(APPROVE_URL, headers=browser, json={"nonce": approve_nonce})
        assert approved.status_code == 200, approved.text
        assert container.consent_store.has_server_consent(USER_ID, CLIENT_ID, "/github")

        revoked = client.delete(f"/api/v1/consents/clients/{CLIENT_ID}", headers=browser)
        assert revoked.status_code == 204, revoked.text
        assert container.oauth_client_store.get_client(CLIENT_ID) == registration
        direct = client.post(DIRECT_URL, headers=direct_headers, json=direct_call)
        assert direct.status_code == 200
        for message in (_modern_post(client, gateway_call, headers), direct.json()):
            pending_state = container.pending_consent_store.peek(_consent_nonce(message))
            assert pending_state is not None
            assert pending_state["client_id"] == CLIENT_ID
            assert pending_state["server_path"] == "/github"
        container.mcpgw_downstream_transport.handle_async_request.assert_not_awaited()
        container.mcp_proxy_client.stream.assert_not_called()
        _assert_revoked_refresh(client, refresh_token)

        stale = client.post(APPROVE_URL, headers=browser, json={"nonce": stale_nonce})
        assert stale.status_code == 409
        assert stale.json() == {
            "detail": "This app's access was revoked. Reconnect it from your AI app, then try again."
        }
        assert container.pending_consent_store.peek(stale_nonce) is None
        assert not container.consent_store.has_server_consent(USER_ID, CLIENT_ID, "/github")
        new_grant = container.consent_store.grant_client_consent(USER_ID, CLIENT_ID)
        assert new_grant != old_grant
        _assert_revoked_refresh(client, refresh_token)
