"""Tests for the AS-1545 headline fix in ``proxy_to_mcp_server``:

When the downstream MCP token is missing, handshake methods (``initialize`` / ``tools/list``)
must return HTTP 401 with an RFC 9728 ``resource_metadata`` challenge — NOT a 200 URL-mode
elicitation, which clients cannot process at handshake time. Other methods keep the elicitation.
"""

from unittest.mock import Mock

import pytest
from starlette.requests import Request

from registry.api.proxy_routes import _ProxyRequestMetrics, proxy_to_mcp_server
from registry.constants import ProxyOutcome
from registry.core.config import settings
from registry.core.proxy_call import ProxyCall
from registry_pkgs.core.exceptions import UrlElicitationRequiredException

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


def _metrics() -> _ProxyRequestMetrics:
    """The recorder the handler would pass in; normally created by dynamic_mcp_post_proxy."""
    return _ProxyRequestMetrics(server_name="github", http_method="POST", mcp_method="tools/call")


@pytest.fixture
def recorded(monkeypatch) -> Mock:
    """The proxy's request recorder as a mock, so a test can read the outcome it was given."""
    record = Mock()
    monkeypatch.setattr("registry.api.proxy_routes.record_proxy_request", record)
    monkeypatch.setattr("registry.api.proxy_routes.record_proxy_tool_call", Mock())
    return record


@pytest.fixture
def raise_elicitation(monkeypatch):
    async def _raise(*args, **kwargs):
        raise UrlElicitationRequiredException(
            "needs auth", auth_url="https://github.com/login/oauth/authorize?x=1", server_name="github"
        )

    monkeypatch.setattr("registry.api.proxy_routes.build_authenticated_headers", _raise)


@pytest.mark.parametrize("method", ["initialize", "server/discover", "tools/list"])
async def test_init_methods_return_401_with_discovery_header(raise_elicitation, recorded, method):
    resp = await proxy_to_mcp_server(
        ProxyCall.from_message(1, method, {}, b"{}", None),
        request=_request(),
        target_url="http://backend/mcp",
        auth_context=_AUTH_CONTEXT,
        server=_server(),
        oauth_service=Mock(),
        proxy_client=Mock(),
        metrics=_metrics(),
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
    # Registry's own challenge waits on the user like the elicitation does.
    assert recorded.call_args.args[3] is ProxyOutcome.INPUT_REQUIRED


@pytest.mark.parametrize("method", ["tools/call", "resources/list", "resources/templates/list", "prompts/list"])
async def test_tool_call_still_returns_elicitation(raise_elicitation, recorded, method):
    resp = await proxy_to_mcp_server(
        ProxyCall.from_message(1, method, {}, b"{}", None),
        request=_request(),
        target_url="http://backend/mcp",
        auth_context=_AUTH_CONTEXT,
        server=_server(),
        oauth_service=Mock(),
        proxy_client=Mock(),
        metrics=_metrics(),
    )
    # Non-handshake methods keep the HTTP 200 + JSON-RPC -32042 URL-mode elicitation.
    assert resp.status_code == 200
    import json

    body = json.loads(bytes(resp.body))
    assert body["error"]["code"] == -32042
    assert body["error"]["data"]["elicitations"][0]["mode"] == "url"
    assert recorded.call_args.args[3] is ProxyOutcome.INPUT_REQUIRED
