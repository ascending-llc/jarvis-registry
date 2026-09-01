"""Tests for the registry header-building adapters (registry.utils.mcp_headers).

The shared logic is tested in registry-pkgs; these tests guard the *registry wiring*:
that the wrappers actually inject HeaderBuildConfig (and, for authenticated headers, the
group-mapped effective scopes) into the shared chain. This is the regression class that the
proxy/tool tests hide by mocking build_authenticated_headers wholesale — a wrapper that
forgot to pass `cfg` / `effective_scopes` would raise TypeError only at runtime.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId

from registry.core.config import settings
from registry.utils import mcp_headers
from registry.utils.mcp_headers import (
    build_authenticated_headers,
    build_complete_headers_for_server,
    get_header_build_config,
)
from registry_pkgs.oauth.headers import HeaderBuildConfig


def _server():
    from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName="github",
        path="/github",
        config={"url": "https://github.example.com/mcp"},
        author=PydanticObjectId(),
    )


def _auth_context():
    return {"user_id": "666666666666666666666666", "username": "alice", "groups": [], "scopes": ["servers-read"]}


class TestGetHeaderBuildConfig:
    def test_snapshots_registry_settings(self):
        cfg = get_header_build_config()
        assert isinstance(cfg, HeaderBuildConfig)
        assert cfg.registry_app_name == settings.registry_app_name
        assert cfg.redis_key_prefix == settings.redis_key_prefix
        assert cfg.encryption_key == settings.encryption_key

    def test_is_cached(self):
        # lru_cache: repeated calls return the same instance (built once per process).
        assert get_header_build_config() is get_header_build_config()


class TestBuildAuthenticatedHeadersWrapper:
    @pytest.mark.asyncio
    async def test_injects_cfg_and_effective_scopes(self):
        delegate = AsyncMock(return_value={"Authorization": "Bearer x"})
        with (
            patch.object(mcp_headers, "_build_authenticated_headers", delegate),
            patch.object(mcp_headers, "effective_scopes_from_context", return_value=["mapped-scope"]) as resolver,
        ):
            result = await build_authenticated_headers(SimpleNamespace(), _server(), _auth_context())

        assert result == {"Authorization": "Bearer x"}
        resolver.assert_called_once()
        kwargs = delegate.await_args.kwargs
        # The regression guard: the registry wrapper MUST inject both, or the shared
        # function raises TypeError (both are required keyword-only args there).
        assert isinstance(kwargs["cfg"], HeaderBuildConfig)
        assert kwargs["effective_scopes"] == ["mapped-scope"]
        # registry runs interactively — it relies on the shared default rather than forcing it.
        assert kwargs.get("interactive", True) is True


class TestBuildCompleteHeadersWrapper:
    @pytest.mark.asyncio
    async def test_injects_cfg(self):
        delegate = AsyncMock(return_value={"Authorization": "Bearer y"})
        with patch.object(mcp_headers, "_build_complete_headers_for_server", delegate):
            result = await build_complete_headers_for_server(SimpleNamespace(), _server(), "user-1")

        assert result == {"Authorization": "Bearer y"}
        kwargs = delegate.await_args.kwargs
        assert isinstance(kwargs["cfg"], HeaderBuildConfig)
