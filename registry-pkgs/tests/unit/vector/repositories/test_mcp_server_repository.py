import asyncio
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

_FIELDS = {"resource_kind": "text", "resource_capture_revision": "text"}


def _repo(monkeypatch: pytest.MonkeyPatch, target: list[str]) -> tuple[MCPServerRepository, MagicMock, MagicMock]:
    reader, writer = MagicMock(), MagicMock()
    repo = MCPServerRepository(MagicMock(write_adapter=writer))
    monkeypatch.setattr(repo, "_search_target", lambda: (reader, target[0]))
    return repo, reader, writer


@pytest.mark.asyncio
async def test_resource_schema_prepared_once_per_generation_and_retried_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = ["MCP_Servers"]
    repo, reader, writer = _repo(monkeypatch, target)
    reader.has_property.return_value = False
    writer.has_property.return_value = True
    assert await asyncio.gather(repo.prepare_resource_visibility(), repo.prepare_resource_visibility()) == [True, True]
    writer.ensure_filterable_properties.assert_called_once_with("MCP_Servers", _FIELDS)
    target[0] = "MCP_Servers_g2"
    writer.ensure_filterable_properties.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await repo.prepare_resource_visibility()
    writer.ensure_filterable_properties.side_effect = None
    assert await repo.prepare_resource_visibility() is True
    assert writer.ensure_filterable_properties.call_count == 3
    assert writer.ensure_filterable_properties.call_args.args[0] == "MCP_Servers_g2"


@pytest.mark.asyncio
async def test_logged_schema_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, reader, writer = _repo(monkeypatch, ["MCP_Servers"])
    reader.has_property.return_value = False
    writer.has_property.return_value = False
    with pytest.raises(RuntimeError, match="schema is unavailable"):
        await repo.prepare_resource_visibility()
    writer.has_property.return_value = True
    assert await repo.prepare_resource_visibility() is True
    assert writer.ensure_filterable_properties.call_count == 2


@pytest.mark.asyncio
async def test_existing_columns_never_take_the_write_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, reader, _writer = _repo(monkeypatch, ["MCP_Servers"])
    reader.has_property.return_value = True
    gated = PropertyMock(side_effect=EmbeddingReindexInProgressException("reindex"))
    with patch.object(MCPServerRepository, "_write_adapter", gated):
        assert await repo.prepare_resource_visibility() is True
    gated.assert_not_called()


@pytest.mark.asyncio
async def test_missing_columns_during_reindex_fail_closed_without_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, reader, writer = _repo(monkeypatch, ["MCP_Servers"])
    reader.has_property.return_value = False
    gated = PropertyMock(side_effect=EmbeddingReindexInProgressException("reindex"))
    with patch.object(MCPServerRepository, "_write_adapter", gated):
        assert await repo.prepare_resource_visibility() is False
    writer.has_property.return_value = True
    assert await repo.prepare_resource_visibility() is True
    writer.ensure_filterable_properties.assert_called_once_with("MCP_Servers", _FIELDS)
