"""Real MCP client/server protocol tests on loopback; no Jarvis/IdP deployment."""

import argparse
import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp.server import Settings
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


def test_server_is_loopback_only_and_restores_sdk_settings() -> None:
    previous = Settings.model_config.get("env_file")
    app = build_server(8879)
    assert app.settings.host == "127.0.0.1"
    assert app.settings.port == 8879
    assert Settings.model_config.get("env_file") == previous


@asynccontextmanager
async def real_mcp_client() -> AsyncIterator[ClientSession]:
    # Retain the bound socket so another process cannot take the selected port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    app = build_server(port)
    server = uvicorn.Server(uvicorn.Config(app.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Fixture exited before startup")
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(trust_env=False, timeout=5) as http_client:
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as client:
                    await client.initialize()
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
        assert tool.inputSchema["required"] == ["ticket_id"]
        assert tool.inputSchema["properties"]["ticket_id"]["type"] == "string"
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_real_http_success_returns_only_fabricated_record() -> None:
    async with real_mcp_client() as client:
        result = await client.call_tool("lookup_synthetic_ticket", {"ticket_id": "SYNTHETIC-1"})
        assert result.isError is False
        assert result.structuredContent == {"ticket_id": "SYNTHETIC-1", "status": "open", "synthetic": True}


@pytest.mark.asyncio
async def test_real_http_unknown_id_returns_tool_error() -> None:
    async with real_mcp_client() as client:
        result = await client.call_tool("lookup_synthetic_ticket", {"ticket_id": "OTHER-1"})
        assert result.isError is True
        assert "Only SYNTHETIC-1 is supported" in result.content[0].text


@pytest.mark.asyncio
async def test_real_http_missing_required_argument_returns_tool_error() -> None:
    async with real_mcp_client() as client:
        result = await client.call_tool("lookup_synthetic_ticket", {})
        assert result.isError is True
        assert "ticket_id" in result.content[0].text
