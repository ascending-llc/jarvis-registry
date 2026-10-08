import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from registry_pkgs.oauth import discovery as discovery_module
from registry_pkgs.oauth.discovery import DiscoveryResult, discover_mcp_oauth
from registry_pkgs.oauth.errors import OAuthDiscoveryError

FIXTURES = Path(__file__).parent / "fixtures"

ATLASSIAN_URL = "https://mcp.atlassian.com/v2/mcp"
ATLASSIAN_ISSUER = "https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3"
ATLASSIAN_PRM_URL = "https://mcp.atlassian.com/.well-known/oauth-protected-resource/v2/mcp"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class _Recorder:
    """httpx.MockTransport handler serving fixed responses by (method, url) and recording requests."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]):
        self.routes = routes
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = (request.method, str(request.url))
        self.requests.append(key)
        return self.routes.get(key, httpx.Response(404))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    @property
    def urls(self) -> list[str]:
        return [url for _, url in self.requests]


def _atlassian_routes() -> dict[tuple[str, str], httpx.Response]:
    challenge = _load("atlassian_v2_challenge.json")
    as_metadata = _load("atlassian_v2_as_metadata.json")
    return {
        ("POST", ATLASSIAN_URL): httpx.Response(401, headers={"WWW-Authenticate": challenge["www-authenticate"]}),
        ("GET", ATLASSIAN_PRM_URL): httpx.Response(200, json=_load("atlassian_v2_prm.json")),
        (
            "GET",
            "https://auth.atlassian.com/.well-known/oauth-authorization-server/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3",
        ): httpx.Response(200, json=as_metadata),
    }


def test_sdk_helpers_used_by_discovery_are_importable() -> None:
    """Contract: every SDK symbol discovery.py relies on still exists under the same name."""
    from mcp.client.auth.utils import (  # noqa: F401
        build_oauth_authorization_server_metadata_discovery_urls,
        build_protected_resource_metadata_discovery_urls,
        extract_field_from_www_auth,
        get_client_metadata_scopes,
    )
    from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata  # noqa: F401


@pytest.mark.asyncio
async def test_atlassian_v2_discovery() -> None:
    recorder = _Recorder(_atlassian_routes())
    async with recorder.client() as client:
        result = await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)

    prm_scopes = _load("atlassian_v2_prm.json")["scopes_supported"]
    assert isinstance(result, DiscoveryResult)
    assert result.server_url == ATLASSIAN_URL
    assert result.issuer == ATLASSIAN_ISSUER
    assert result.resource == ATLASSIAN_URL
    assert result.authorization_server_metadata["authorization_endpoint"] == "https://auth.atlassian.com/authorize"
    assert result.authorization_server_metadata["registration_endpoint"].endswith("/dcr/register")
    assert result.scope is not None
    assert set(result.scope.split()) == set(prm_scopes)
    assert result.protected_resource_metadata == _load("atlassian_v2_prm.json")
    # The probe found the header URL, so it is the first (and only) PRM candidate fetched.
    assert recorder.urls[:2] == [ATLASSIAN_URL, ATLASSIAN_PRM_URL]


@pytest.mark.asyncio
async def test_supplied_www_authenticate_skips_probe_and_uses_its_scope() -> None:
    recorder = _Recorder(_atlassian_routes())
    header = f'Bearer resource_metadata="{ATLASSIAN_PRM_URL}", scope="read:me offline_access"'
    async with recorder.client() as client:
        result = await discover_mcp_oauth(ATLASSIAN_URL, http_client=client, www_authenticate=header)

    assert ("POST", ATLASSIAN_URL) not in recorder.requests
    assert result.scope == "read:me offline_access"


@pytest.mark.asyncio
async def test_prm_candidates_tried_header_then_path_then_root() -> None:
    header_url = "https://mcp.example.com/custom-prm"
    routes = {
        ("POST", "https://mcp.example.com/a/mcp"): httpx.Response(
            401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{header_url}"'}
        ),
        ("GET", "https://mcp.example.com/.well-known/oauth-protected-resource"): httpx.Response(
            200,
            json={"resource": "https://mcp.example.com/a/mcp", "authorization_servers": ["https://as.example.com"]},
        ),
        ("GET", "https://as.example.com/.well-known/oauth-authorization-server"): httpx.Response(
            200,
            json={
                "issuer": "https://as.example.com",
                "authorization_endpoint": "https://as.example.com/authorize",
                "token_endpoint": "https://as.example.com/token",
            },
        ),
    }
    recorder = _Recorder(routes)
    async with recorder.client() as client:
        result = await discover_mcp_oauth("https://mcp.example.com/a/mcp", http_client=client)

    assert recorder.urls[:4] == [
        "https://mcp.example.com/a/mcp",
        header_url,
        "https://mcp.example.com/.well-known/oauth-protected-resource/a/mcp",
        "https://mcp.example.com/.well-known/oauth-protected-resource",
    ]
    assert result.issuer == "https://as.example.com"


@pytest.mark.asyncio
async def test_as_candidates_follow_sdk_order() -> None:
    as_url = "https://as.example.com/tenant"
    routes = {
        ("GET", "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"): httpx.Response(
            200, json={"resource": "https://mcp.example.com/mcp", "authorization_servers": [as_url]}
        ),
        ("GET", "https://as.example.com/tenant/.well-known/openid-configuration"): httpx.Response(
            200,
            json={
                "issuer": as_url,
                "authorization_endpoint": "https://as.example.com/authorize",
                "token_endpoint": "https://as.example.com/token",
            },
        ),
    }
    recorder = _Recorder(routes)
    async with recorder.client() as client:
        result = await discover_mcp_oauth("https://mcp.example.com/mcp", http_client=client)

    assert recorder.urls[-3:] == [
        "https://as.example.com/.well-known/oauth-authorization-server/tenant",
        "https://as.example.com/.well-known/openid-configuration/tenant",
        "https://as.example.com/tenant/.well-known/openid-configuration",
    ]
    assert result.issuer == as_url


@pytest.mark.asyncio
async def test_legacy_branch_without_prm() -> None:
    routes = {
        ("POST", "https://mcp.notion.com/mcp"): httpx.Response(401),
        ("GET", "https://mcp.notion.com/.well-known/oauth-authorization-server"): httpx.Response(
            200, json=_load("notion_as_metadata.json")
        ),
    }
    recorder = _Recorder(routes)
    async with recorder.client() as client:
        result = await discover_mcp_oauth("https://mcp.notion.com/mcp", http_client=client)

    assert result.resource is None
    assert result.protected_resource_metadata is None
    assert result.issuer == "https://mcp.notion.com"
    assert result.scope == "default"  # from AS scopes_supported


@pytest.mark.asyncio
async def test_legacy_branch_issuer_mismatch_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    as_metadata = {**_load("notion_as_metadata.json"), "issuer": "https://other.notion.com"}
    routes = {
        ("GET", "https://mcp.notion.com/.well-known/oauth-authorization-server"): httpx.Response(200, json=as_metadata),
    }
    async with _Recorder(routes).client() as client:
        result = await discover_mcp_oauth("https://mcp.notion.com/mcp", http_client=client)

    assert result.issuer == "https://other.notion.com"
    assert "issuer" in caplog.text


@pytest.mark.asyncio
async def test_hubspot_root_resource_accepted() -> None:
    routes = {
        ("POST", "https://mcp.hubspot.com"): httpx.Response(
            401,
            headers={
                "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.hubspot.com/.well-known/oauth-protected-resource"'
            },
        ),
        ("GET", "https://mcp.hubspot.com/.well-known/oauth-protected-resource"): httpx.Response(
            200, json={**_load("hubspot_prm.json"), "resource": "https://mcp.hubspot.com/"}
        ),
        ("GET", "https://mcp.hubspot.com/.well-known/oauth-authorization-server"): httpx.Response(
            200, json=_load("hubspot_as_metadata.json")
        ),
    }
    async with _Recorder(routes).client() as client:
        result = await discover_mcp_oauth("https://mcp.hubspot.com", http_client=client)

    assert result.resource == "https://mcp.hubspot.com/"
    assert result.issuer == "https://mcp.hubspot.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource",
    ["https://evil.example.com/v2/mcp", "https://mcp.atlassian.com/v1/mcp", "https://mcp.atlassian.com/v2/mcp-other"],
)
async def test_prm_resource_for_another_resource_rejected(resource: str) -> None:
    routes = _atlassian_routes()
    routes[("GET", ATLASSIAN_PRM_URL)] = httpx.Response(
        200, json={**_load("atlassian_v2_prm.json"), "resource": resource}
    )
    async with _Recorder(routes).client() as client:
        with pytest.raises(OAuthDiscoveryError):
            await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)


@pytest.mark.asyncio
async def test_prm_resource_path_prefix_accepted() -> None:
    routes = _atlassian_routes()
    routes[("GET", ATLASSIAN_PRM_URL)] = httpx.Response(
        200, json={**_load("atlassian_v2_prm.json"), "resource": "https://mcp.atlassian.com/v2/"}
    )
    async with _Recorder(routes).client() as client:
        result = await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)
    assert result.resource == "https://mcp.atlassian.com/v2/"


@pytest.mark.asyncio
async def test_prm_issuer_mismatch_rejected() -> None:
    routes = _atlassian_routes()
    key = (
        "GET",
        "https://auth.atlassian.com/.well-known/oauth-authorization-server/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3",
    )
    routes[key] = httpx.Response(
        200, json={**_load("atlassian_v2_as_metadata.json"), "issuer": "https://auth.atlassian.com"}
    )
    async with _Recorder(routes).client() as client:
        with pytest.raises(OAuthDiscoveryError, match="issuer"):
            await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)


@pytest.mark.asyncio
async def test_no_as_metadata_raises() -> None:
    async with _Recorder({}).client() as client:
        with pytest.raises(OAuthDiscoveryError):
            await discover_mcp_oauth("https://mcp.example.com/mcp", http_client=client)


@pytest.mark.asyncio
async def test_invalid_documents_are_skipped() -> None:
    routes = _atlassian_routes()
    routes[("GET", ATLASSIAN_PRM_URL)] = httpx.Response(200, content=b"<html>not json</html>")
    routes[("GET", "https://mcp.atlassian.com/.well-known/oauth-protected-resource")] = httpx.Response(
        200, json={"unexpected": True}
    )
    # Neither PRM parses, so the legacy branch looks for AS metadata at the origin and finds none.
    async with _Recorder(routes).client() as client:
        with pytest.raises(OAuthDiscoveryError, match="No authorization server metadata"):
            await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)


@pytest.mark.asyncio
async def test_transport_error_raises_discovery_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthDiscoveryError) as exc_info:
            await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
async def test_total_timeout_bounds_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery_module, "DISCOVERY_TOTAL_TIMEOUT_SECONDS", 0.2)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.15)  # below the per-request limit, but several of them exceed the total
        return httpx.Response(404)

    loop = asyncio.get_running_loop()
    started = loop.time()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthDiscoveryError, match="exceeded"):
            await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)
    assert loop.time() - started < 1.0
    assert calls < 5


@pytest.mark.asyncio
async def test_each_request_carries_the_per_request_timeout() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=60.0) as client:
        with pytest.raises(OAuthDiscoveryError):
            await discover_mcp_oauth(ATLASSIAN_URL, http_client=client)
    assert seen
    assert all(t["read"] == discovery_module.DISCOVERY_TIMEOUT_SECONDS for t in seen)
