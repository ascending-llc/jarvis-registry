"""Tests for the AS-1545 headline fix in ``proxy_to_mcp_server``:

When the downstream MCP token is missing, handshake methods (``initialize`` / ``tools/list``)
must return HTTP 401 with an RFC 9728 ``resource_metadata`` challenge — NOT a 200 URL-mode
elicitation, which clients cannot process at handshake time. Other methods keep the elicitation.
"""

from unittest.mock import Mock

import pytest
from starlette.requests import Request

from registry.api.proxy_routes import proxy_to_mcp_server
from registry.core.config import settings
from registry.core.exceptions import UrlElicitationRequiredException

USER_ID = "507f1f77bcf86cd799439011"


def _request(user_id: str = USER_ID, server_path: str = "github") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": f"/proxy/server/{user_id}/{server_path}",
        "query_string": b"",
        "headers": [(b"accept", b"text/event-stream")],
        "path_params": {"user_id": user_id, "server_path": server_path},
    }
    return Request(scope)


def _server() -> Mock:
    s = Mock()
    s.serverName = "github"
    return s


_AUTH_CONTEXT = {"auth_method": "downstream_mcp_token", "user_id": USER_ID, "username": USER_ID}


@pytest.fixture
def raise_elicitation(monkeypatch):
    async def _raise(*args, **kwargs):
        raise UrlElicitationRequiredException(
            "needs auth", auth_url="https://github.com/login/oauth/authorize?x=1", server_name="github"
        )

    monkeypatch.setattr("registry.api.proxy_routes.build_authenticated_headers", _raise)


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
async def test_init_methods_return_401_with_discovery_header(raise_elicitation, method):
    resp = await proxy_to_mcp_server(
        1,
        request=_request(),
        target_url="http://backend/mcp",
        auth_context=_AUTH_CONTEXT,
        server=_server(),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        mcp_method=method,
    )
    assert resp.status_code == 401
    www = resp.headers["WWW-Authenticate"]
    assert www.startswith("Bearer")
    expected = (
        f"{settings.jwt_issuer}/.well-known/oauth-protected-resource"
        f"{settings.service_base_path}/proxy/server/{USER_ID}/github"
    )
    assert f'resource_metadata="{expected}"' in www
    # RFC 6750 §3.1: no `error` attribute when no relevant token was presented.
    assert "error=" not in www


async def test_tool_call_still_returns_elicitation(raise_elicitation):
    resp = await proxy_to_mcp_server(
        1,
        request=_request(),
        target_url="http://backend/mcp",
        auth_context=_AUTH_CONTEXT,
        server=_server(),
        oauth_service=Mock(),
        proxy_client=Mock(),
        redis_client=Mock(),
        mcp_method="tools/call",
    )
    # Non-handshake methods keep the HTTP 200 + JSON-RPC -32042 URL-mode elicitation.
    assert resp.status_code == 200
    import json

    body = json.loads(bytes(resp.body))
    assert body["error"]["code"] == -32042
    assert body["error"]["data"]["elicitations"][0]["mode"] == "url"
