from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

import registry_pkgs.workflows.mcp_headers_provider as provider_module
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.oauth.headers import HeaderBuildConfig
from registry_pkgs.workflows.mcp_headers_provider import McpHeadersProvider


def _cfg() -> HeaderBuildConfig:
    return HeaderBuildConfig(
        registry_app_name="jarvis-registry",
        redis_key_prefix="jarvis-registry",
        jwt_signing_config=SimpleNamespace(),
        encryption_key=None,
    )


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
        "scopes": ["servers-read"],
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
        cfg=_cfg(),
        scope_resolver=lambda ctx: list(ctx.get("scopes") or []),
    )

    headers = await provider(_server(), _auth_context())

    assert headers == {"Authorization": "Bearer downstream"}
    build_headers.assert_awaited_once()
    # scope_resolver output is forwarded as effective_scopes
    assert build_headers.await_args.kwargs["effective_scopes"] == ["servers-read"]
    assert build_headers.await_args.kwargs["interactive"] is True


@pytest.mark.asyncio
async def test_provider_requires_auth_context():
    provider = McpHeadersProvider(
        oauth_service=SimpleNamespace(),
        cfg=_cfg(),
        scope_resolver=lambda ctx: [],
    )
    with pytest.raises(ValueError, match="auth_context is required"):
        await provider(_server(), None)


@pytest.mark.asyncio
async def test_provider_non_interactive_propagates_flag(monkeypatch: pytest.MonkeyPatch):
    build_headers = AsyncMock(return_value={})
    monkeypatch.setattr(provider_module, "build_authenticated_headers", build_headers)
    provider = McpHeadersProvider(
        oauth_service=SimpleNamespace(),
        cfg=_cfg(),
        scope_resolver=lambda ctx: [],
        interactive=False,
    )

    await provider(_server(), _auth_context())

    assert build_headers.await_args.kwargs["interactive"] is False
