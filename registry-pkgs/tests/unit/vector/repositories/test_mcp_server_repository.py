import asyncio
from unittest.mock import MagicMock

import pytest

from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


@pytest.mark.asyncio
async def test_resource_schema_prepared_once_per_generation_and_retried_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = MagicMock()
    repo = MCPServerRepository(MagicMock(write_adapter=adapter))
    target = ["MCP_Servers"]
    monkeypatch.setattr(repo, "_search_target", lambda: (adapter, target[0]))
    await asyncio.gather(repo.prepare_resource_visibility(), repo.prepare_resource_visibility())
    adapter.ensure_filterable_properties.assert_called_once_with(
        "MCP_Servers",
        {"resource_kind": "text", "resource_capture_revision": "text"},
    )
    target[0] = "MCP_Servers_g2"
    adapter.ensure_filterable_properties.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await repo.prepare_resource_visibility()
    adapter.ensure_filterable_properties.side_effect = None
    await repo.prepare_resource_visibility()
    assert adapter.ensure_filterable_properties.call_count == 3
    assert adapter.ensure_filterable_properties.call_args.args[0] == "MCP_Servers_g2"


@pytest.mark.asyncio
async def test_logged_schema_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = MagicMock()
    repo = MCPServerRepository(MagicMock(write_adapter=adapter))
    monkeypatch.setattr(repo, "_search_target", lambda: (adapter, "MCP_Servers"))
    adapter.has_property.return_value = False
    with pytest.raises(RuntimeError, match="schema is unavailable"):
        await repo.prepare_resource_visibility()
    adapter.has_property.return_value = True
    await repo.prepare_resource_visibility()
    assert adapter.ensure_filterable_properties.call_count == 2
