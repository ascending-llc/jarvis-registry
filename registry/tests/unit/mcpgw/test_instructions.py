"""Exercise instruction delivery through real MCP messages without external services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import anyio
import httpx
import pytest
from mcp import ClientSession, types
from mcp.shared.memory import create_client_server_memory_streams

from registry.mcpgw import server as gateway

if TYPE_CHECKING:
    from registry.container import RegistryContainer


@asynccontextmanager
async def _gateway_session(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[ClientSession, types.InitializeResult]]:
    # Metadata requests must not call any service. Plain objects fail if a service
    # method is accidentally used, and the HTTP transport rejects external I/O.
    dependencies = (
        "a2a_client_registry",
        "server_service",
        "mcp_server_repo",
        "a2a_agent_repo",
        "search_service",
        "mcp_client_service",
        "oauth_service",
        "session_store",
        "redis_client",
        "acl_service",
        "consent_store",
        "pending_consent_store",
    )
    container = cast("RegistryContainer", SimpleNamespace(**{name: object() for name in dependencies}))

    def reject_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected outgoing request: {request.method} {request.url}")

    monkeypatch.setattr(
        gateway, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(reject_request))
    )
    app = gateway.create_gateway_mcp_app(container_provider=lambda: container)
    server = app._mcp_server

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(
                partial(
                    server.run,
                    *server_streams,
                    server.create_initialization_options(),
                    raise_exceptions=True,
                )
            )
            try:
                async with ClientSession(*client_streams, read_timeout_seconds=timedelta(seconds=5)) as client:
                    initialized = await client.initialize()
                    yield client, initialized
            finally:
                tasks.cancel_scope.cancel()


def test_instruction_budget_preserves_the_discovery_rule_in_the_first_line() -> None:
    instructions = gateway._SYSTEM_INSTRUCTIONS
    first_line = instructions.splitlines()[0]

    assert instructions == instructions.lstrip()
    assert len(instructions.encode("utf-8")) <= 1000
    assert len(first_line) <= 250
    assert "Before claiming" in first_line
    assert "try Registry discovery when permitted" in first_line


@pytest.mark.asyncio
async def test_initialize_and_prompt_deliver_the_same_instructions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _gateway_session(monkeypatch) as (client, initialized):
        assert initialized.instructions == gateway._SYSTEM_INSTRUCTIONS
        assert initialized.instructions

        prompts = await client.list_prompts()
        assert "gateway_capabilities" in {prompt.name for prompt in prompts.prompts}
        prompt = await client.get_prompt("gateway_capabilities")
        assert len(prompt.messages) == 1
        content = prompt.messages[0].content
        assert isinstance(content, types.TextContent)
        assert content.text == initialized.instructions


@pytest.mark.asyncio
async def test_tools_keep_their_schemas_and_expose_discovery_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _gateway_session(monkeypatch) as (client, _):
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        assert set(tools) == {
            "discover_servers",
            "discover_agents",
            "execute_agent",
            "execute_tool",
            "read_resource",
            "execute_prompt",
        }

        for name in ("discover_servers", "discover_agents"):
            tool = tools[name]
            assert "Before claiming" in (tool.description or "")[:250]
            schema = tool.inputSchema
            assert set(schema["properties"]) == {"query", "top_n", "type_list"}
            assert not schema.get("required")
            query = schema["properties"]["query"]
            assert (query["type"], query["default"], query["minLength"], query["maxLength"]) == ("string", "", 0, 512)
            top_n = schema["properties"]["top_n"]
            assert (top_n["type"], top_n["default"], top_n["minimum"], top_n["maximum"]) == ("integer", 3, 1, 50)
            assert schema["properties"]["type_list"]["items"]["type"] == "string"

        assert set(tools["execute_tool"].inputSchema["required"]) == {"tool_name", "arguments", "server_id"}
        assert set(tools["execute_agent"].inputSchema["required"]) == {"agent_id", "message"}
