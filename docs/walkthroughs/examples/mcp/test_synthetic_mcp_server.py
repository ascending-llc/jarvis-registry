"""Real MCP client/server protocol tests on loopback; no Jarvis/IdP deployment."""

import argparse
import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from synthetic_mcp_server import HOST, HTTP_OPTIONS, build_http_app, build_server, valid_port


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


def test_server_is_loopback_only_and_stateless() -> None:
    assert HOST == "127.0.0.1"
    assert HTTP_OPTIONS == {"json_response": True, "stateless_http": True}
    assert build_http_app(build_server()) is not None


@asynccontextmanager
async def real_mcp_client() -> AsyncIterator[ClientSession]:
    # Retain the bound socket so another process cannot take the selected port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_http_app(build_server()), host=HOST, port=port, log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Fixture exited before startup")
                await asyncio.sleep(0.01)
        async with httpx2.AsyncClient(trust_env=False, timeout=5) as http_client:
            async with streamable_http_client(f"http://{HOST}:{port}/mcp", http_client=http_client) as (read, write):
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
