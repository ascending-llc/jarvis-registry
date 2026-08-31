"""Focused tests for federation provider handler fan-out."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry.services.federation.federation_handlers import AwsAgentCoreSyncHandler


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enrichment_failure_does_not_block_sibling_entities() -> None:
    first_server = SimpleNamespace(name="first")
    second_server = SimpleNamespace(name="second")
    agent = SimpleNamespace(
        federationMetadata=SimpleNamespace(model_dump=MagicMock(return_value={"runtimeArn": "arn:agent"}))
    )
    runtime_invoker = MagicMock()

    async def _enrich_server(*, server, **_kwargs) -> None:
        if server is first_server:
            raise RuntimeError("unexpected enrichment failure")

    runtime_invoker.enrich_mcp_server = AsyncMock(side_effect=_enrich_server)
    runtime_invoker.enrich_a2a_agent = AsyncMock()
    handler = AwsAgentCoreSyncHandler(
        discovery_client=MagicMock(),
        runtime_invoker=runtime_invoker,
    )

    await handler._enrich_discovered_entities(
        federation=SimpleNamespace(),
        discovered={"mcp_servers": [first_server, second_server], "a2a_agents": [agent]},
        region="us-east-1",
        assume_role_arn=None,
    )

    assert runtime_invoker.enrich_mcp_server.await_count == 2
    runtime_invoker.enrich_a2a_agent.assert_awaited_once()
