from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services import workflow_mcp_headers_provider as provider_module
from registry.services.workflow_mcp_headers_provider import McpHeadersProvider
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer


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
async def test_provider_builds_headers(monkeypatch: pytest.MonkeyPatch):
    build_headers = AsyncMock(return_value={"Authorization": "Bearer downstream"})
    monkeypatch.setattr(provider_module, "build_authenticated_headers", build_headers)
    provider = McpHeadersProvider(
        oauth_service=SimpleNamespace(),
    )

    headers = await provider(_server(), _auth_context())

    assert headers == {"Authorization": "Bearer downstream"}
    build_headers.assert_awaited_once()
