"""Real TCP pool regression: long-lived GET streams must never starve POSTs and cleanup."""

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx2
import pytest
import uvicorn
from mcp.client.caching import InMemoryResponseCacheStore
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult

from registry.core.mcp_config import MCPTransport
from registry.core.mcp_downstream import DownstreamEraCache, ToolCall, call_downstream
from registry.core.mcp_transport import MethodRoutedTransport
from tests.mcp_downstream_support import MemoryRedis

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _tcp_server(
    server: MCPServer, messages: list[dict], http_methods: list[str], *, json_response: bool
) -> AsyncIterator[str]:
    app = server.streamable_http_app(
        stateless_http=False,
        json_response=json_response,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def capture(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        http_index = len(http_methods)
        http_methods.append(scope["method"])
        body = bytearray()

        async def record_body():
            message = await receive()
            if message["type"] == "http.request" and scope["method"] == "POST":
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    parsed = json.loads(body)
                    parsed["_http_index"] = http_index
                    messages.append(parsed)
            return message

        await app(scope, record_body, send)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server_task = uvicorn.Server(
            uvicorn.Config(
                capture,
                log_config=None,
                log_level="error",
                lifespan="on",
                timeout_graceful_shutdown=1,
            )
        )
        task = asyncio.create_task(server_task.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server_task.started:
                    if task.done():
                        await task
                        raise RuntimeError("TCP test server exited before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
        finally:
            server_task.should_exit = True
            try:
                async with asyncio.timeout(5):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("json_response", [False, True])
async def test_open_get_streams_never_starve_requests_or_cleanup(json_response: bool) -> None:
    """A full stream pool must not block another call's POSTs or its closing DELETE.

    Call A holds the only stream connection (its session GET) and one request connection (a tools/call
    that blocks). Call B must still initialize, call and terminate on the request pool while A is open.
    In a single pool of the same total size, B would wait for a connection that A never releases.
    """
    entered, release = asyncio.Event(), asyncio.Event()
    executions = 0
    sdk_server = MCPServer("pool-regression")

    @sdk_server.tool()
    async def search() -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            entered.set()
            await release.wait()
        return "done"

    messages, http_methods = [], []
    redis = MemoryRedis()
    redis.values["test:mcp_era:s"] = "legacy"
    pools = MethodRoutedTransport(
        request_pool=httpx2.AsyncHTTPTransport(limits=httpx2.Limits(max_connections=2, max_keepalive_connections=2)),
        stream_pool=httpx2.AsyncHTTPTransport(limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1)),
    )
    store = InMemoryResponseCacheStore()
    real_client = httpx2.AsyncClient

    def client(**kwargs) -> httpx2.AsyncClient:
        # B's own session GET finds the stream pool full; it must fail fast in the background, not block B.
        kwargs["timeout"] = httpx2.Timeout(2, pool=0.05)
        return real_client(**kwargs)

    first: asyncio.Task | None = None
    try:
        async with _tcp_server(sdk_server, messages, http_methods, json_response=json_response) as url:

            async def invoke() -> CallToolResult:
                return await call_downstream(
                    url=url,
                    transport_type=MCPTransport.STREAMABLE_HTTP,
                    server_id="s",
                    user_id="u",
                    headers={},
                    operation=ToolCall("search", {}),
                    downstream_transport=pools,
                    response_cache=store,
                    era_cache=DownstreamEraCache(redis, "test"),
                )

            with patch.object(httpx2, "AsyncClient", client):
                async with asyncio.timeout(5):
                    first = asyncio.create_task(invoke())
                    await entered.wait()
                    while "GET" not in http_methods:  # A's session stream is open on the stream pool
                        await asyncio.sleep(0.01)

                    second = await invoke()

                    assert not second.is_error
                    assert not first.done()
                    assert http_methods.count("DELETE") == 1
                    release.set()
                    assert not (await first).is_error
                    assert sum(m["method"] == "tools/call" for m in messages) == 2
                    assert http_methods.count("DELETE") == 2
    finally:
        release.set()
        if first is not None and not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        await pools.aclose()
