from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import workflow_mcp_headers_provider as provider_module
from registry.services.workflow_mcp_headers_provider import McpHeadersProvider
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows.types import McpConsentRequiredError


def _server() -> ExtendedMCPServer:
    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName="github",
        path="/github",
        config={"url": "https://github.example.com/mcp"},
        author=PydanticObjectId(),
    )


def _auth_context() -> dict[str, object]:
    return {
        "user_id": "666666666666666666666666",
        "client_id": "workflow-client",
        "username": "alice",
        "groups": [],
        "scopes": [],
        "auth_method": "service",
        "provider": "workflow",
        "auth_source": "workflow_resume",
    }


@pytest.mark.asyncio
async def test_provider_builds_headers_when_server_consent_exists(monkeypatch: pytest.MonkeyPatch):
    consent_store = MagicMock()
    consent_store.has_server_consent.return_value = True
    build_headers = AsyncMock(return_value={"Authorization": "Bearer downstream"})
    monkeypatch.setattr(provider_module, "build_authenticated_headers", build_headers)
    provider = McpHeadersProvider(
        oauth_service=SimpleNamespace(),
        consent_store=consent_store,
        pending_consent_store=MagicMock(),
        registry_client_url="https://registry.example.com/",
    )

    headers = await provider(_server(), _auth_context())

    assert headers == {"Authorization": "Bearer downstream"}
    consent_store.has_server_consent.assert_called_once_with("666666666666666666666666", "workflow-client", "/github")
    build_headers.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_creates_pending_consent_and_blocks_downstream(monkeypatch: pytest.MonkeyPatch):
    consent_store = MagicMock()
    consent_store.has_server_consent.return_value = False
    pending_store = MagicMock()
    build_headers = AsyncMock()
    monkeypatch.setattr(provider_module, "build_authenticated_headers", build_headers)
    monkeypatch.setattr(provider_module.secrets, "token_urlsafe", lambda size: "consent-nonce")
    provider = McpHeadersProvider(
        oauth_service=SimpleNamespace(),
        consent_store=consent_store,
        pending_consent_store=pending_store,
        registry_client_url="https://registry.example.com/",
    )

    with pytest.raises(McpConsentRequiredError) as exc_info:
        await provider(_server(), _auth_context())

    assert exc_info.value.auth_url == "https://registry.example.com/consent/server?nonce=consent-nonce"
    pending_store.save.assert_called_once()
    assert pending_store.save.call_args.args[1]["server_path"] == "/github"
    build_headers.assert_not_awaited()
