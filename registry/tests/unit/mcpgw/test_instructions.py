"""Exercise instruction delivery through real MCP messages without external services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from mcp import Client

from registry.mcpgw import server as gateway

if TYPE_CHECKING:
    from registry.container import RegistryContainer

_TOOL_NAMES = (
    "discover_servers",
    "discover_agents",
    "execute_agent",
    "execute_tool",
    "read_resource",
    "execute_prompt",
)
_MAX_DESCRIPTION_CHARACTERS = 2048


@asynccontextmanager
async def _gateway_session() -> AsyncIterator[Client]:
    # Metadata requests must not call any service. Plain objects fail if a service
    # method is accidentally used, and the mock transport rejects external I/O.
    dependencies = (
        "a2a_client_registry",
        "server_service",
        "mcp_server_repo",
        "a2a_agent_repo",
        "search_service",
        "mcp_client_service",
        "oauth_service",
        "client_info_store",
        "redis_client",
        "acl_service",
        "consent_store",
        "pending_consent_store",
    )

    def reject_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected outgoing request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_request)) as proxy_client:
        container = cast(
            "RegistryContainer",
            SimpleNamespace(mcpgw_proxy_client=proxy_client, **{name: object() for name in dependencies}),
        )
        app = gateway.create_gateway_mcp_app(container_provider=lambda: container)
        # v2: drive the in-process server through mcp.Client in legacy mode (negotiates 2025-11-25).
        async with Client(app, mode="legacy") as client:
            yield client


def test_instruction_budget_preserves_the_discovery_rule_in_the_first_line() -> None:
    instructions = gateway._SYSTEM_INSTRUCTIONS
    first_line = instructions.splitlines()[0]

    assert instructions == instructions.lstrip()
    assert len(instructions.encode("utf-8")) <= 1000
    assert len(first_line) <= 250
    assert "Before claiming" in first_line
    assert "try Registry discovery when permitted" in first_line


def test_instructions_tell_the_model_to_show_consent_urls() -> None:
    instructions = gateway._SYSTEM_INSTRUCTIONS

    assert "show the user its URL verbatim" in instructions
    assert "Surface auth errors" not in instructions


@pytest.mark.asyncio
async def test_initialize_delivers_instructions_without_prompts() -> None:
    async with _gateway_session() as client:
        # v2 exposes the negotiated instructions on the client (not title).
        assert client.instructions == gateway._SYSTEM_INSTRUCTIONS
        assert client.instructions

        prompts = await client.list_prompts()
        assert prompts.prompts == []


@pytest.mark.asyncio
async def test_tools_keep_their_schemas_and_expose_discovery_guidance() -> None:
    async with _gateway_session() as client:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        assert set(tools) == set(_TOOL_NAMES)

        for name in ("discover_servers", "discover_agents"):
            tool = tools[name]
            assert "Before claiming" in (tool.description or "")[:250]
            schema = tool.input_schema
            assert set(schema["properties"]) == {"query", "top_n", "type_list"}
            assert not schema.get("required")
            query = schema["properties"]["query"]
            assert (query["type"], query["default"], query["minLength"], query["maxLength"]) == ("string", "", 0, 512)
            assert "not the user's full sentence" in query["description"]
            top_n = schema["properties"]["top_n"]
            assert (top_n["type"], top_n["default"], top_n["minimum"], top_n["maximum"]) == ("integer", 3, 1, 50)
            assert schema["properties"]["type_list"]["items"]["type"] == "string"

            # Protocol schema captured before adding contextual empty-result hints.
            assert tool.output_schema == {
                "properties": {
                    "result": {
                        "items": {"additionalProperties": True, "type": "object"},
                        "title": "Result",
                        "type": "array",
                    }
                },
                "required": ["result"],
                "title": f"{name}Output",
                "type": "object",
            }

        assert "different providers" in tools["discover_servers"].description
        assert set(tools["execute_tool"].input_schema["required"]) == {"tool_name", "arguments", "server_id"}
        assert set(tools["execute_agent"].input_schema["required"]) == {"agent_id", "message"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
async def test_tool_descriptions_fit_client_budget_without_indentation(tool_name: str) -> None:
    async with _gateway_session() as client:
        listed = await client.list_tools()
        tool = next(tool for tool in listed.tools if tool.name == tool_name)

        description = tool.description
        assert description
        assert len(description) <= _MAX_DESCRIPTION_CHARACTERS
        assert all(line == line.lstrip() for line in description.splitlines())
        if tool_name in ("discover_servers", "discover_agents"):
            assert "Before claiming" in description[:250]
