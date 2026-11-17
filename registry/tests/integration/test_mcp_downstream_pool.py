"""Real TCP pool regression: long-lived GET streams must leave room for POST and cleanup."""

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
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult

from registry.core.mcp_config import MCPTransport
from registry.core.mcp_downstream import DownstreamCallLimiter, DownstreamEraCache, call_downstream_tool
from registry_pkgs.core.exceptions import DownstreamHttpFailureException
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


@pytest.mark.parametrize(
    ("outcome", "json_response"),
    [(outcome, False) for outcome in ("success", "failure", "cancel", "queue_timeout", "queue_cancel")]
    + [(outcome, True) for outcome in ("success", "failure", "queue_timeout", "queue_cancel")],
)
async def test_small_tcp_pool_admits_complete_calls_and_releases_capacity(outcome: str, json_response: bool) -> None:
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
            if outcome == "failure":
                raise MCPError(-32099, "intentional downstream failure")
        return "done"

    messages, http_methods = [], []
    # Three connections reserve GET + active POST + cleanup, admitting one complete call.
    limiter = DownstreamCallLimiter(3, queue_timeout=0.05 if outcome == "queue_timeout" else 2)
    redis = MemoryRedis()
    redis.values["test:mcp_era:s"] = "legacy"
    pool = httpx2.AsyncHTTPTransport(limits=httpx2.Limits(max_connections=3, max_keepalive_connections=3))
    store = InMemoryResponseCacheStore()
    real_client = httpx2.AsyncClient

    def client(**kwargs) -> httpx2.AsyncClient:
        # Queueing longer than this pool timeout must still succeed with call-level admission.
        kwargs["timeout"] = httpx2.Timeout(2, pool=0.05)
        return real_client(**kwargs)

    tasks = []
    try:
        async with _tcp_server(sdk_server, messages, http_methods, json_response=json_response) as url:

            async def invoke() -> CallToolResult:
                return await call_downstream_tool(
                    url=url,
                    transport_type=MCPTransport.STREAMABLE_HTTP,
                    server_id="s",
                    user_id="u",
                    headers={},
                    tool_name="search",
                    arguments={},
                    downstream_transport=pool,
                    call_limiter=limiter,
                    response_cache=store,
                    era_cache=DownstreamEraCache(redis, "test"),
                )

            with patch.object(httpx2, "AsyncClient", client):
                async with asyncio.timeout(5):
                    first = asyncio.create_task(invoke())
                    tasks.append(first)
                    await entered.wait()
                    assert limiter.capacity == 1
                    count_before_queue = len(http_methods)
                    queued = asyncio.create_task(invoke())
                    tasks.append(queued)
                    if outcome == "queue_timeout":
                        with pytest.raises(
                            DownstreamHttpFailureException, match="waiting for downstream MCP call capacity"
                        ):
                            await queued
                        assert len(http_methods) == count_before_queue
                    elif outcome == "queue_cancel":
                        await asyncio.sleep(0.01)
                        queued.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await queued
                        assert len(http_methods) == count_before_queue
                    else:
                        await asyncio.sleep(0.1)
                        assert not queued.done()
                        assert len(http_methods) == count_before_queue
                    if outcome == "cancel":
                        first.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await first
                    else:
                        release.set()
                        if outcome == "failure":
                            with pytest.raises(MCPError) as error:
                                await first
                            assert error.value.code == -32099
                        else:
                            assert isinstance(await first, CallToolResult)
                    if outcome in ("queue_timeout", "queue_cancel"):
                        queued = asyncio.create_task(invoke())
                        tasks.append(queued)
                    second_result = await queued
                    assert not second_result.is_error
                    assert executions == 2
                    assert sum(m["method"] == "tools/call" for m in messages) == 2
                    assert sum(m["method"] == "initialize" for m in messages) == 2
                    assert http_methods.count("DELETE") == 2
                    initializations = [m for m in messages if m["method"] == "initialize"]
                    assert initializations[1]["_http_index"] > http_methods.index("DELETE")
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.aclose()
