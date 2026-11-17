"""Real SDK HTTP server fixture shared by gateway transport tests."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp.server import CacheHint
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, InputRequiredResult, TextContent


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.ttls[key] = ex

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class SDKTransport(httpx2.AsyncBaseTransport):
    def __init__(self, app: Any, era: str, *, streaming: bool = False) -> None:
        self.inner = StreamingASGITransport(app) if streaming else httpx2.ASGITransport(app=app)
        self.era = era
        self.requests: list[httpx2.Request] = []
        self.issued_sessions: list[str] = []
        self.delete_statuses: list[int] = []
        self.messages: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []
        self.failure_method: str | None = None
        self.failure_status = 401
        self.fail_once = False
        self.challenge = "Bearer test-challenge"
        self.closed = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        method = None
        if request.method == "POST":
            message = json.loads(await request.aread())
            self.messages.append(message)
            self.headers.append(request.headers)
            method = message["method"]
            if self.failure_method == method:
                if self.fail_once:
                    self.failure_method = None
                return httpx2.Response(self.failure_status, headers={"WWW-Authenticate": self.challenge})
            modern = request.headers.get("mcp-protocol-version") == "2026-07-28"
            if (self.era == "legacy" and modern) or (self.era == "2026-07-28" and not modern):
                return httpx2.Response(
                    400,
                    json={
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {
                            "code": -32022,
                            "message": "Unsupported protocol version",
                            "data": {"supported": [self.era if self.era != "legacy" else "2025-11-25"]},
                        },
                    },
                )
        response = await self.inner.handle_async_request(request)
        if method == "initialize" and (session_id := response.headers.get("mcp-session-id")):
            self.issued_sessions.append(session_id)
        if request.method == "DELETE":
            self.delete_statuses.append(response.status_code)
        return response

    async def aclose(self) -> None:
        self.closed += 1
        await self.inner.aclose()

    def count(self, method: str) -> int:
        return sum(m["method"] == method for m in self.messages)


@asynccontextmanager
async def sdk_endpoint(
    *,
    era: str = "2026-07-28",
    result: CallToolResult | InputRequiredResult | None = None,
    progress: bool = False,
    ttl_ms: int = 300000,
    server_override: Any = None,
    stateless_http: bool = True,
    action: Callable[[Context], Awaitable[CallToolResult | InputRequiredResult]] | None = None,
) -> AsyncIterator[SDKTransport]:
    server = MCPServer("test-downstream", subscriptions=False, cache_hints={"tools/list": CacheHint(ttl_ms=ttl_ms)})

    @server.tool(name="tavily_search")
    @server.tool(name="search")
    async def search(ctx: Context) -> CallToolResult | InputRequiredResult:
        if progress:
            await ctx.report_progress(0.5, 1.0, "working")
        if action:
            return await action(ctx)
        return result if result is not None else CallToolResult(content=[TextContent(type="text", text="done")])

    if server_override is not None:
        server = server_override
    app = server.streamable_http_app(
        stateless_http=stateless_http,
        json_response=not progress,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    async with app.router.lifespan_context(app):
        transport = SDKTransport(app, era, streaming=not stateless_http)
        try:
            yield transport
        finally:
            await transport.aclose()


class StreamingASGITransport(httpx2.AsyncBaseTransport):
    """Expose response chunks immediately so an SSE GET can stay open during POSTs."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.tasks: set[Any] = set()
        self.closed = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        import asyncio

        transport = self
        started, disconnected = asyncio.Event(), asyncio.Event()
        chunks = asyncio.Queue()
        status, headers = [500], []
        body = await request.aread()
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                headers.extend(message["headers"])
                started.set()
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await chunks.put(message["body"])
                if not message.get("more_body", False):
                    await chunks.put(None)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "root_path": "",
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
            "server": (request.url.host, request.url.port or 80),
            "client": ("127.0.0.1", 1234),
        }

        async def run():
            try:
                await self.app(scope, receive, send)
            finally:
                started.set()
                await chunks.put(None)

        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        await started.wait()

        class Stream(httpx2.AsyncByteStream):
            async def __aiter__(self):
                while (chunk := await chunks.get()) is not None:
                    yield chunk
                if task.done() and not task.cancelled():
                    task.result()

            async def aclose(self):
                disconnected.set()
                if request.method == "GET":
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                transport.tasks.discard(task)

        return httpx2.Response(status[0], headers=headers, stream=Stream())

    async def aclose(self) -> None:
        import asyncio

        self.closed += 1
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
