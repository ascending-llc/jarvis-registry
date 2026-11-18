"""Real MCP client/server protocol tests on loopback; no Jarvis/IdP deployment."""

import argparse
import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from synthetic_mcp_server import build_server, valid_port


@pytest.mark.parametrize("port", ["1024", "8879", "65535"])
def test_valid_port_accepts_unprivileged_range(port: str) -> None:
    assert valid_port(port) == int(port)


@pytest.mark.parametrize("port", ["0", "1023", "65536"])
def test_valid_port_rejects_out_of_range(port: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        valid_port(port)


def test_valid_port_rejects_noninteger() -> None:
    with pytest.raises(ValueError):
        valid_port("not-a-port")


def test_server_builds_with_the_single_synthetic_tool() -> None:
    # v2 moves transport settings (host/port) onto run()/streamable_http_app(); the constructor only
    # carries name/instructions, so there is no app.settings host/port to assert.
    app = build_server()
    assert isinstance(app, MCPServer)


@asynccontextmanager
async def real_mcp_client() -> AsyncIterator[Client]:
    # Retain the bound socket so another process cannot take the selected port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = build_server()
    streamable_app = app.streamable_http_app(stateless_http=True, json_response=True)
    server = uvicorn.Server(uvicorn.Config(streamable_app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Fixture exited before startup")
                await asyncio.sleep(0.01)
        async with httpx2.AsyncClient(trust_env=False, timeout=5) as http_client:
            async with Client(
                streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http_client), mode="legacy"
            ) as client:
                yield client
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            listener.close()


@pytest.mark.asyncio
async def test_real_http_discovery_and_input_schema() -> None:
    async with real_mcp_client() as client:
        response = await client.list_tools()
        assert [tool.name for tool in response.tools] == ["lookup_synthetic_ticket"]
        tool = response.tools[0]
        assert tool.input_schema["required"] == ["ticket_id"]
        assert tool.input_schema["properties"]["ticket_id"]["type"] == "string"
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


@pytest.mark.asyncio
async def test_real_http_success_returns_only_fabricated_record() -> None:
    async with real_mcp_client() as client:
        result = await client.call_tool("lookup_synthetic_ticket", {"ticket_id": "SYNTHETIC-1"})
        assert result.is_error is False
        assert result.structured_content == {"ticket_id": "SYNTHETIC-1", "status": "open", "synthetic": True}


@pytest.mark.asyncio
async def test_real_http_unknown_id_returns_tool_error() -> None:
    async with real_mcp_client() as client:
        result = await client.call_tool("lookup_synthetic_ticket", {"ticket_id": "OTHER-1"})
        assert result.is_error is True
        assert "Only SYNTHETIC-1 is supported" in result.content[0].text


@pytest.mark.asyncio
async def test_real_http_missing_required_argument_returns_tool_error() -> None:
    async with real_mcp_client() as client:
        result = await client.call_tool("lookup_synthetic_ticket", {})
        assert result.is_error is True
        assert "ticket_id" in result.content[0].text
