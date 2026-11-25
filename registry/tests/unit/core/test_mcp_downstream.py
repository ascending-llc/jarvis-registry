"""Exercise the real SDK wire, cache partitions and error boundaries without external I/O."""

from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from mcp.client.caching import InMemoryResponseCacheStore
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestURLParams,
    GetPromptResult,
    InputRequiredResult,
    ReadResourceResult,
)
from redis.exceptions import RedisError
from tests.mcp_downstream_support import MemoryRedis, sdk_endpoint

from registry.core.mcp_downstream import (
    DownstreamEraCache,
    DownstreamOperation,
    PromptGet,
    ResourceRead,
    ToolCall,
    call_downstream,
)
from registry_pkgs.core.exceptions import DownstreamHttpFailureException, DownstreamUnauthorizedException


async def call(
    transport,
    *,
    redis=None,
    store=None,
    user="u",
    server="s",
    url="http://downstream/mcp",
    operation=ToolCall("search", {}),
    **kwargs,
):
    return await call_downstream(
        url=url,
        transport_type="streamable-http",
        server_id=server,
        user_id=user,
        headers={"Authorization": f"Bearer {user}"},
        operation=operation,
        downstream_transport=transport,
        response_cache=store if store is not None else InMemoryResponseCacheStore(),
        era_cache=DownstreamEraCache(redis if redis is not None else MemoryRedis(), "test"),
        **kwargs,
    )


@pytest.fixture(
    params=[ToolCall("search", {}), ResourceRead("test://doc"), PromptGet("greet", {"name": "Ada"})],
    ids=["tool", "resource", "prompt"],
)
def operation(request) -> DownstreamOperation:
    return request.param


def operation_method(operation: DownstreamOperation) -> str:
    return {ToolCall: "tools/call", ResourceRead: "resources/read", PromptGet: "prompts/get"}[type(operation)]


def result_text(result: CallToolResult | ReadResourceResult | GetPromptResult) -> str:
    if isinstance(result, ReadResourceResult):
        return result.contents[0].text
    if isinstance(result, GetPromptResult):
        return result.messages[0].content.text
    return result.content[0].text


def operation_server(handler):
    """Low-level handlers preserve native results and exceptions for all three operations."""
    from mcp.server import Server
    from mcp.types import ListToolsResult, Tool

    async def listing(ctx, params):
        return ListToolsResult(tools=[Tool(name="search", input_schema={"type": "object"})])

    return Server(
        "operations", on_list_tools=listing, on_call_tool=handler, on_read_resource=handler, on_get_prompt=handler
    )


@pytest.mark.parametrize("era", ["legacy", "2026-07-28"])
async def test_real_sdk_call_caches_era_and_tools_and_keeps_pool(operation, era):
    redis, store = MemoryRedis(), InMemoryResponseCacheStore()
    async with sdk_endpoint(era=era) as transport:
        first = await call(transport, operation=operation, redis=redis, store=store)
        assert result_text(first) == "done"
        probes = transport.count("server/discover")
        assert redis.values["test:mcp_era:s"] == era
        assert redis.ttls["test:mcp_era:s"] == 3600
        await call(transport, operation=operation, redis=redis, store=store)
        assert transport.count("server/discover") == probes
        assert transport.count("tools/list") == (1 if isinstance(operation, ToolCall) else 0)
        assert transport.closed == 0
        await call(transport, operation=operation, redis=redis, store=store, user="other")
        await call(transport, operation=operation, redis=redis, store=store, server="other")
        assert transport.count("tools/list") == (3 if isinstance(operation, ToolCall) else 0)
        assert transport.headers[-1]["authorization"] == "Bearer u"


@pytest.mark.parametrize(("cached", "actual"), [("legacy", "2026-07-28"), ("2026-07-28", "legacy")])
async def test_stale_era_retries_only_before_tool_send(operation, cached, actual):
    redis = MemoryRedis()
    redis.values["test:mcp_era:s"] = cached
    async with sdk_endpoint(era=actual) as transport:
        if cached == "2026-07-28" and not isinstance(operation, ToolCall):
            # Modern cached mode sends the operation without a handshake/listing. Once sent,
            # even a version rejection must not replay it. The next caller negotiates afresh.
            with pytest.raises(DownstreamHttpFailureException):
                await call(transport, operation=operation, redis=redis)
            assert transport.count(operation_method(operation)) == 1
            assert "test:mcp_era:s" not in redis.values
            assert result_text(await call(transport, operation=operation, redis=redis)) == "done"
            assert transport.count(operation_method(operation)) == 2
        else:
            assert result_text(await call(transport, operation=operation, redis=redis)) == "done"
            assert transport.count(operation_method(operation)) == 1
        assert redis.values["test:mcp_era:s"] == actual


@pytest.mark.parametrize("method", ["initialize", "operation"])
async def test_401_after_successful_handshake_preserves_challenge(operation, method):
    method = operation_method(operation) if method == "operation" else method
    async with sdk_endpoint(era="legacy") as transport:
        transport.failure_method = method
        with pytest.raises(DownstreamUnauthorizedException) as error:
            await call(transport, operation=operation)
        assert error.value.www_authenticate == "Bearer test-challenge"
        assert error.value.method == method
        assert error.value.oauth_retry_safe
        assert transport.count(operation_method(operation)) == (1 if method == operation_method(operation) else 0)


async def test_http_500_maps_to_downstream_failure(operation):
    async with sdk_endpoint() as transport:
        transport.failure_method, transport.failure_status = operation_method(operation), 500
        with pytest.raises(DownstreamHttpFailureException):
            await call(transport, operation=operation)
        assert transport.count(operation_method(operation)) == 1


async def test_input_required_is_returned_without_automatic_replay(operation):
    expected = InputRequiredResult(
        input_requests={
            "login": ElicitRequest(
                params=ElicitRequestURLParams(
                    url="https://login.example",
                    message="Sign in",
                )
            )
        }
    )

    async def execute(ctx, params):
        return expected

    async with sdk_endpoint(server_override=operation_server(execute)) as transport:
        result = await call(transport, operation=operation)
        assert isinstance(result, InputRequiredResult)
        assert result.input_requests["login"].params.url == "https://login.example"
        assert transport.count(operation_method(operation)) == 1


async def test_progress_callback_uses_sdk_token():
    callback = AsyncMock()
    async with sdk_endpoint(progress=True) as transport:
        await call(transport, on_progress=callback)
        callback.assert_awaited_once_with(0.5, 1.0, "working")
        message = next(m for m in transport.messages if m["method"] == "tools/call")
        assert message["params"]["_meta"]["progressToken"] is not None


@pytest.mark.parametrize("on_log", [AsyncMock(), None])
async def test_modern_log_opt_in_is_warning_only_when_relaying(on_log):
    async with sdk_endpoint() as transport:
        await call(transport, on_log=on_log)
        message = next(m for m in transport.messages if m["method"] == "tools/call")
        level = message["params"].get("_meta", {}).get("io.modelcontextprotocol/logLevel")
        assert level == ("warning" if on_log else None)


async def test_redis_errors_degrade_without_failing_executed_tool(operation):
    redis = MemoryRedis()
    with patch.object(redis, "get", side_effect=RedisError), patch.object(redis, "set", side_effect=RedisError):
        async with sdk_endpoint() as transport:
            assert isinstance(
                await call(transport, operation=operation, redis=redis),
                (CallToolResult, ReadResourceResult, GetPromptResult),
            )


async def test_transport_failure_maps_without_replay(operation):
    async def fail(request):
        raise httpx2.ConnectError("connection unavailable")

    with pytest.raises(DownstreamHttpFailureException):
        await call(httpx2.MockTransport(fail), operation=operation)


async def test_jsonrpc_version_error_after_tool_is_not_replayed(operation):
    redis = MemoryRedis()
    redis.values["test:mcp_era:s"] = "2026-07-28"
    # Fault injection at the HTTP boundary, after a real SDK listing succeeded.
    async with sdk_endpoint() as transport:
        original = transport.handle_async_request

        async def reject(request):
            import json

            message = json.loads(await request.aread()) if request.method == "POST" else {}
            if message.get("method") == operation_method(operation):
                transport.messages.append(message)
                return httpx2.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {
                            "code": -32022,
                            "message": "version changed",
                            "data": {"supported": ["2025-11-25"]},
                        },
                    },
                )
            return await original(request)

        transport.handle_async_request = reject
        with pytest.raises(MCPError) as error:
            await call(transport, operation=operation, redis=redis)
        assert error.value.code == -32022
        assert transport.count(operation_method(operation)) == 1
        assert "test:mcp_era:s" not in redis.values


async def test_cache_expiry_and_explicit_zero_ttl(monkeypatch):
    from mcp.client.caching import CacheConfig

    from registry.core import mcp_downstream

    now = [1000.0]
    monkeypatch.setattr(mcp_downstream, "CacheConfig", lambda **kwargs: CacheConfig(**kwargs, clock=lambda: now[0]))
    redis, store = MemoryRedis(), InMemoryResponseCacheStore()
    async with sdk_endpoint() as transport:
        await call(transport, redis=redis, store=store)
        now[0] += 301
        await call(transport, redis=redis, store=store)
        assert transport.count("tools/list") == 2
    async with sdk_endpoint(ttl_ms=0) as transport:
        await call(transport, redis=redis, store=store, server="zero")
        await call(transport, redis=redis, store=store, server="zero")
        assert transport.count("tools/list") == 2


async def test_endpoint_change_gets_a_fresh_tool_list_cache_entry():
    redis, store = MemoryRedis(), InMemoryResponseCacheStore()
    async with sdk_endpoint() as transport:
        await call(transport, redis=redis, store=store)
        await call(transport, redis=redis, store=store)
        assert transport.count("tools/list") == 1
        # Same server ID, new URL: the old endpoint's listing must not be reused.
        await call(transport, redis=redis, store=store, url="http://moved/mcp")
        assert transport.count("tools/list") == 2
        await call(transport, redis=redis, store=store, url="http://moved/mcp")
        assert transport.count("tools/list") == 2


def test_tool_list_cache_target_hashes_endpoint_without_leaking_url() -> None:
    from registry.core.mcp_config import MCPTransport
    from registry.core.mcp_downstream import _tool_list_cache_target

    url = "https://downstream/mcp?api_key=secret"
    target = _tool_list_cache_target("s", MCPTransport.STREAMABLE_HTTP, url)
    assert target.startswith("s:")
    assert "secret" not in target
    assert target == _tool_list_cache_target("s", MCPTransport.STREAMABLE_HTTP, url)
    assert target != _tool_list_cache_target("s", MCPTransport.SSE, url)


@pytest.mark.parametrize("raw", [b"legacy", "legacy", b"2026-07-28", "2026-07-28", "bad", b"\xff", None])
async def test_era_cache_validates_values(raw):
    redis = MemoryRedis()
    if raw is not None:
        redis.values["test:mcp_era:s"] = raw
    expected = raw.decode() if isinstance(raw, bytes) and raw != b"\xff" else raw
    expected = expected if expected in ("legacy", "2026-07-28") else None
    assert await DownstreamEraCache(redis, "test").get("s") == expected


async def test_schema_validation_still_runs_on_cache_hit():
    from mcp.server import CacheHint, Server
    from mcp.types import ListToolsResult, TextContent, Tool

    from registry_pkgs.core.exceptions import MisimplementedSpecException

    valid = [True]

    async def listing(ctx, params):
        return ListToolsResult(
            tools=[
                Tool(
                    name="search",
                    input_schema={"type": "object"},
                    output_schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
                )
            ]
        )

    async def execute(ctx, params):
        return CallToolResult(
            content=[TextContent(type="text", text="value")], structured_content={"n": 1 if valid[0] else "bad"}
        )

    server = Server(
        "schema-test", on_list_tools=listing, on_call_tool=execute, cache_hints={"tools/list": CacheHint(ttl_ms=300000)}
    )
    redis, store = MemoryRedis(), InMemoryResponseCacheStore()
    async with sdk_endpoint(server_override=server) as transport:
        await call(transport, redis=redis, store=store)
        valid[0] = False
        with pytest.raises(MisimplementedSpecException):
            await call(transport, redis=redis, store=store)
        assert transport.count("tools/list") == 1
        assert transport.count("tools/call") == 2


async def test_paginated_target_schema_loaded_before_call():
    from mcp.server import Server
    from mcp.types import ListToolsResult, TextContent, Tool

    from registry_pkgs.core.exceptions import MisimplementedSpecException

    async def listing(ctx, params):
        if params and params.cursor:
            return ListToolsResult(
                tools=[Tool(name="search", input_schema={"type": "object"}, output_schema={"type": "integer"})]
            )
        return ListToolsResult(tools=[Tool(name="other", input_schema={"type": "object"})], next_cursor="page2")

    async def execute(ctx, params):
        return CallToolResult(content=[TextContent(type="text", text="bad")], structured_content={"wrong": True})

    async with sdk_endpoint(server_override=Server("pages", on_list_tools=listing, on_call_tool=execute)) as transport:
        with pytest.raises(MisimplementedSpecException):
            await call(transport)
        methods = [m["method"] for m in transport.messages]
        assert methods.index("tools/call") > max(i for i, m in enumerate(methods) if m == "tools/list")
        assert transport.count("tools/list") == 2


async def test_jsonrpc_error_preserves_code_and_data(operation):

    async def execute(ctx, params):
        raise MCPError(-32099, "test error", {"reason": "test"})

    async with sdk_endpoint(server_override=operation_server(execute)) as transport:
        with pytest.raises(MCPError) as error:
            await call(transport, operation=operation)
        assert error.value.code == -32099
        assert error.value.error.data == {"reason": "test"}


async def test_mcp_timeout_is_mapped_without_replay(operation):

    async def execute(ctx, params):
        raise MCPError(-32001, "timeout")

    async with sdk_endpoint(server_override=operation_server(execute)) as transport:
        with pytest.raises(DownstreamHttpFailureException, match="Timed out"):
            await call(transport, operation=operation)
        assert transport.count(operation_method(operation)) == 1


async def test_legacy_sse_real_sdk_stream_closes_and_pool_survives():
    import asyncio

    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from tests.mcp_downstream_support import StreamingASGITransport

    server = MCPServer("sse")

    @server.tool()
    async def search() -> str:
        return "sse done"

    app = server.sse_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    transport = StreamingASGITransport(app)
    redis = MemoryRedis()
    redis.values["test:mcp_era:s"] = "legacy"
    async with asyncio.timeout(5):
        for _ in range(2):
            result = await call_downstream(
                url="http://downstream/sse",
                transport_type="sse",
                server_id="s",
                user_id="u",
                headers={},
                operation=ToolCall("search", {}),
                downstream_transport=transport,
                response_cache=InMemoryResponseCacheStore(),
                era_cache=DownstreamEraCache(redis, "test"),
            )
            assert result.content[0].text == "sse done"
            assert transport.closed == 0
    assert not transport.tasks
    assert redis.values["test:mcp_era:s"] == "legacy"
    await transport.aclose()


async def test_cancellation_during_tool_is_propagated_without_retry(operation):
    import asyncio

    entered = asyncio.Event()

    async def blocking(ctx=None):
        entered.set()
        await asyncio.Event().wait()

    async with sdk_endpoint(action=blocking, resource_action=blocking, prompt_action=blocking) as transport:
        task = asyncio.create_task(call(transport, operation=operation))
        async with asyncio.timeout(5):
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert transport.count(operation_method(operation)) == 1
        assert transport.closed == 0


@pytest.mark.parametrize("era", ["legacy", "2026-07-28"])
async def test_discovery_and_health_negotiate_and_write_era(monkeypatch, era):
    from registry.core.mcp_client import _get_from_streamable_http, perform_health_check
    from registry.core.mcp_transport import SharedTransport

    original_client = httpx2.AsyncClient
    redis = MemoryRedis()
    cache = DownstreamEraCache(redis, "test")
    async with sdk_endpoint(era=era) as transport:

        def client(**kwargs):
            return original_client(transport=SharedTransport(transport), **kwargs)

        monkeypatch.setattr(httpx2, "AsyncClient", client)
        result = await _get_from_streamable_http(
            "http://downstream/mcp",
            include_resources=False,
            include_prompts=False,
            server_id="s",
            era_cache=cache,
        )
        assert result.error_message is None
        assert {tool["name"] for tool in result.tools} == {"search", "tavily_search"}
        assert redis.values["test:mcp_era:s"] == era
        healthy, _, _, snapshot = await perform_health_check(
            "http://downstream/mcp", server_id="health", era_cache=cache
        )
        assert healthy
        assert snapshot.protocol_version == ("2025-11-25" if era == "legacy" else era)
        assert redis.values["test:mcp_era:health"] == era


async def test_connection_route_reports_actual_modern_version(monkeypatch):
    from registry.api.v1.server.server_routes import check_server_connection
    from registry.core.mcp_transport import SharedTransport
    from registry.schemas.server_api_schemas import ServerConnectionTestRequest

    original_client = httpx2.AsyncClient
    async with sdk_endpoint() as transport:
        monkeypatch.setattr(
            httpx2, "AsyncClient", lambda **kwargs: original_client(transport=SharedTransport(transport), **kwargs)
        )
        result = await check_server_connection(ServerConnectionTestRequest(url="http://downstream/mcp"))
        assert result.success
        assert result.protocolVersion == "2026-07-28"


async def test_concurrent_users_close_clients_without_closing_pool(monkeypatch) -> None:
    import asyncio

    clients: list[httpx2.AsyncClient] = []
    original = httpx2.AsyncClient

    def create(**kwargs) -> httpx2.AsyncClient:
        client = original(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx2, "AsyncClient", create)
    async with sdk_endpoint() as transport:
        redis, store = MemoryRedis(), InMemoryResponseCacheStore()
        results = await asyncio.gather(
            call(transport, redis=redis, store=store, user="alice"),
            call(transport, redis=redis, store=store, user="bob"),
        )
        assert all(result.content[0].text == "done" for result in results)
        assert all(client.is_closed for client in clients)
        assert transport.closed == 0
        assert transport.count("tools/list") == 2
        tool_headers = [
            h["authorization"]
            for m, h in zip(transport.messages, transport.headers, strict=True)
            if m["method"] == "tools/call"
        ]
        assert sorted(tool_headers) == ["Bearer alice", "Bearer bob"]


async def test_transport_fault_after_probe_does_not_inherit_probe_401() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        import json

        message = json.loads(await request.aread())
        if message["method"] == "server/discover":
            return httpx2.Response(401, headers={"WWW-Authenticate": "Bearer old-probe"})
        raise httpx2.ConnectError("connection lost after probe")

    with pytest.raises(DownstreamHttpFailureException) as error:
        await call(httpx2.MockTransport(handler))
    assert not isinstance(error.value, DownstreamUnauthorizedException)


async def test_unauthorized_during_close_is_not_recoverable() -> None:
    from registry.core.mcp_downstream import DownstreamCallUnauthorized, _map_error
    from registry.core.mcp_transport import DownstreamPhase, ResponseRecorder

    recorder = ResponseRecorder()
    recorder.phase = DownstreamPhase.CLOSING
    recorder.method = "tools/call"
    recorder.status_code = 401
    mapped = _map_error(RuntimeError("cleanup failed"), recorder)
    assert isinstance(mapped, DownstreamCallUnauthorized)
    assert not mapped.oauth_retry_safe


def test_multiple_failures_are_not_collapsed_into_recoverable_401() -> None:
    from registry.core.mcp_downstream import _map_error
    from registry.core.mcp_transport import ResponseRecorder

    recorder = ResponseRecorder()
    recorder.status_code = 401
    error = ExceptionGroup("independent failures", [RuntimeError("a"), RuntimeError("b")])
    assert _map_error(error, recorder) is error


def test_teardown_errors_do_not_hide_the_real_failure() -> None:
    import anyio

    from registry.core.mcp_downstream import _single_error

    failure = DownstreamHttpFailureException("Timed out fetching the downstream MCP tool list.")
    # The shape the SDK produces when a deadline fires mid-request: the stream task breaks during teardown.
    error = ExceptionGroup(
        "outer", [ExceptionGroup("transport", [anyio.BrokenResourceError(), ExceptionGroup("body", [failure])])]
    )
    assert _single_error(error) is failure


def test_teardown_errors_are_not_used_to_collapse_independent_failures() -> None:
    import anyio

    from registry.core.mcp_downstream import _single_error

    first, second = RuntimeError("a"), RuntimeError("b")
    error = ExceptionGroup("failures", [first, anyio.ClosedResourceError(), second])
    unwrapped = _single_error(error)
    assert isinstance(unwrapped, ExceptionGroup)
    assert list(unwrapped.exceptions) == [first, second]


def test_lone_teardown_error_is_still_reported() -> None:
    import anyio

    from registry.core.mcp_downstream import _single_error

    broken = anyio.BrokenResourceError()
    assert _single_error(ExceptionGroup("g", [ExceptionGroup("h", [broken])])) is broken


@pytest.mark.parametrize(("url", "expected"), [("http://host/mcp", "streamable-http"), ("http://host/sse", "sse")])
async def test_transport_detection_returns_enum(url: str, expected: str) -> None:
    from registry.core.mcp_client import detect_server_transport
    from registry.core.mcp_config import MCPTransport

    result = await detect_server_transport(url)
    assert isinstance(result, MCPTransport)
    assert result.value == expected


async def test_unknown_transport_is_rejected_before_network_io() -> None:
    from registry.core.mcp_config import MCPTransport

    async def reject(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("invalid transport must not initiate network I/O")

    with pytest.raises(ValueError, match="MCPTransport"):
        await call_downstream(
            url="http://downstream/mcp",
            transport_type="invalid",
            server_id="s",
            user_id="u",
            headers={},
            operation=ToolCall("search", {}),
            downstream_transport=httpx2.MockTransport(reject),
            response_cache=InMemoryResponseCacheStore(),
            era_cache=DownstreamEraCache(MemoryRedis(), "test"),
        )
    assert MCPTransport.STREAMABLE_HTTP.value == "streamable-http"


async def test_repeated_pagination_cursor_fails_before_tool_execution() -> None:
    from mcp.server import Server
    from mcp.types import ListToolsResult

    from registry_pkgs.core.exceptions import MisimplementedSpecException

    async def listing(ctx, params):
        return ListToolsResult(tools=[], next_cursor="same-page")

    async with sdk_endpoint(server_override=Server("repeated-cursor", on_list_tools=listing)) as transport:
        with pytest.raises(MisimplementedSpecException, match="repeated a pagination cursor"):
            await call(transport)
        assert transport.count("tools/list") == 2
        assert transport.count("tools/call") == 0


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_stateful_legacy_http_sessions_are_isolated_and_cleaned_up(outcome: str) -> None:
    import asyncio
    import json

    from tests.mcp_downstream_support import StreamingASGITransport

    entered = asyncio.Event()
    should_block = outcome == "cancel"

    async def execute(ctx):
        if should_block:
            entered.set()
            await asyncio.Event().wait()
        return CallToolResult(content=[])

    redis, store = MemoryRedis(), InMemoryResponseCacheStore()
    redis.values["test:mcp_era:s"] = "legacy"
    async with sdk_endpoint(era="legacy", stateless_http=False, action=execute) as transport:
        assert isinstance(transport.inner, StreamingASGITransport)
        if outcome == "failure":
            transport.failure_method = "tools/call"
            transport.failure_status = 500
        async with asyncio.timeout(5):
            if outcome == "cancel":
                task = asyncio.create_task(call(transport, redis=redis, store=store))
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "failure":
                with pytest.raises(DownstreamHttpFailureException):
                    await call(transport, redis=redis, store=store)
            else:
                assert not (await call(transport, redis=redis, store=store)).is_error
            assert len(transport.issued_sessions) == 1
            first_sid = transport.issued_sessions[0]
            first_requests = list(transport.requests)
            assert first_sid
            assert transport.delete_statuses == [200]
            assert transport.closed == 0
            assert not transport.inner.tasks

            # A new call must establish a fresh session after all three exit paths.
            should_block = False
            transport.failure_method = None
            assert not (await call(transport, redis=redis, store=store)).is_error
            second_requests = transport.requests[len(first_requests) :]
            assert len(transport.issued_sessions) == 2
            second_sid = transport.issued_sessions[1]
            assert first_sid != second_sid
            assert transport.delete_statuses == [200, 200]
            assert transport.closed == 0
            assert not transport.inner.tasks
            assert transport.count("initialize") == 2
            assert transport.count("tools/call") == 2
            for requests, expected_sid in [(first_requests, first_sid), (second_requests, second_sid)]:
                tool_requests = []
                for request in requests:
                    if request.method == "POST":
                        method = json.loads(request.content)["method"]
                        if method == "initialize":
                            assert "mcp-session-id" not in request.headers
                        else:
                            assert request.headers["mcp-session-id"] == expected_sid
                        if method == "tools/call":
                            tool_requests.append(request)
                    elif request.method in ("GET", "DELETE"):
                        assert request.headers["mcp-session-id"] == expected_sid
                assert len(tool_requests) == 1
                assert sum(request.method == "DELETE" for request in requests) == 1
    assert transport.closed == 1


def _sse_app_rejecting_posts(status: int):
    """A real SDK SSE server whose POST /messages/ endpoint answers ``status`` (GET still opens the stream).

    The SDK server can't produce this split on its own, so a middleware rejects the POSTs.
    """
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    server = MCPServer("sse")

    @server.tool()
    async def search() -> str:
        return "sse done"

    app = server.sse_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    async def rejecting(scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and "/messages/" in scope["path"]:
            headers = [(b"www-authenticate", b"Bearer sse-challenge")] if status == 401 else []
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return
        await app(scope, receive, send)

    return rejecting


async def test_legacy_sse_post_401_fails_fast_with_challenge():
    import asyncio

    from tests.mcp_downstream_support import StreamingASGITransport

    transport = StreamingASGITransport(_sse_app_rejecting_posts(401))
    try:
        # Well inside the 60 s read timeout the SDK would otherwise wait out.
        async with asyncio.timeout(5):
            with pytest.raises(DownstreamUnauthorizedException) as error:
                await call_downstream(
                    url="http://downstream/sse",
                    transport_type="sse",
                    server_id="s",
                    user_id="u",
                    headers={},
                    operation=ToolCall("search", {}),
                    downstream_transport=transport,
                    response_cache=InMemoryResponseCacheStore(),
                    era_cache=DownstreamEraCache(MemoryRedis(), "test"),
                )
        assert error.value.www_authenticate == "Bearer sse-challenge"
        assert error.value.oauth_retry_safe
    finally:
        await transport.aclose()


def _patch_sse_discovery_client(monkeypatch, transport):
    from registry.core import mcp_transport

    def client(headers=None, timeout=None, auth=None):
        return httpx2.AsyncClient(
            transport=mcp_transport.SharedTransport(transport), headers=headers, timeout=timeout, auth=auth
        )

    monkeypatch.setattr(mcp_transport, "create_mcp_http_client", client)


@pytest.mark.parametrize(
    ("status", "healthy", "message"),
    [
        (401, True, "connected (initialize requires authentication)"),
        (403, True, "connected (initialize requires authentication)"),
        (404, False, "unhealthy: endpoint returned 404 for 'sse' transport"),
        (405, False, "unhealthy: endpoint returned 405 for 'sse' transport"),
    ],
)
async def test_legacy_sse_health_reports_post_rejection_without_waiting(monkeypatch, status, healthy, message):
    import asyncio

    from tests.mcp_downstream_support import StreamingASGITransport

    from registry.core.mcp_client import perform_health_check

    transport = StreamingASGITransport(_sse_app_rejecting_posts(status))
    _patch_sse_discovery_client(monkeypatch, transport)
    try:
        # INIT_TIMEOUT is 30 s; a rejected POST must be reported as soon as it arrives.
        async with asyncio.timeout(5):
            result = await perform_health_check("http://downstream/sse", transport="sse")
        assert result[0] is healthy
        assert result[1].startswith(message)
    finally:
        await transport.aclose()


async def test_legacy_sse_discovery_reports_post_rejection_without_waiting(monkeypatch):
    import asyncio

    from tests.mcp_downstream_support import StreamingASGITransport

    from registry.core.mcp_client import _get_from_sse

    transport = StreamingASGITransport(_sse_app_rejecting_posts(401))
    _patch_sse_discovery_client(monkeypatch, transport)
    try:
        async with asyncio.timeout(5):
            result = await _get_from_sse("http://downstream/sse", include_resources=False, include_prompts=False)
        assert result.tools is None
        assert result.error_message == "Connection failed: SSE server rejected a request with HTTP 401"
    finally:
        await transport.aclose()


async def test_legacy_sse_discovery_still_succeeds_with_fail_fast(monkeypatch):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from tests.mcp_downstream_support import StreamingASGITransport

    from registry.core.mcp_client import _get_from_sse

    server = MCPServer("sse")

    @server.tool()
    async def search() -> str:
        return "sse done"

    transport = StreamingASGITransport(
        server.sse_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    )
    _patch_sse_discovery_client(monkeypatch, transport)
    try:
        result = await _get_from_sse("http://downstream/sse")
        assert result.error_message is None
        assert [tool["name"] for tool in result.tools] == ["search"]
    finally:
        await transport.aclose()


async def test_listing_401_still_preserves_challenge() -> None:
    await test_401_after_successful_handshake_preserves_challenge(ToolCall("search", {}), "tools/list")


async def test_native_results_and_arguments_skip_tool_listing() -> None:
    async with sdk_endpoint(resource_result="document", prompt_result="Hello {name}") as transport:
        resource = await call(transport, operation=ResourceRead("test://doc"))
        assert isinstance(resource, ReadResourceResult)
        assert resource.contents[0].text == "document"
        assert str(resource.contents[0].uri) == "test://doc"
        template = await call(transport, operation=ResourceRead("test://doc/42"))
        assert template.contents[0].text == "42"
        prompt = await call(transport, operation=PromptGet("greet", {"name": "Ada"}))
        assert isinstance(prompt, GetPromptResult)
        assert prompt.messages[0].content.text == "Hello Ada"
        assert transport.count("tools/list") == 0
        message = next(m for m in transport.messages if m["method"] == "prompts/get")
        assert message["params"]["arguments"] == {"name": "Ada"}
        await call(transport)
        assert transport.count("tools/list") == 1


async def test_resource_reads_do_not_cache_contents() -> None:
    action = AsyncMock(side_effect=["first", "second"])
    store = InMemoryResponseCacheStore()
    async with sdk_endpoint(resource_action=action) as transport:
        first = await call(transport, operation=ResourceRead("test://doc"), store=store)
        second = await call(transport, operation=ResourceRead("test://doc"), store=store)
        assert first.contents[0].text == "first"
        assert second.contents[0].text == "second"
        assert transport.count("resources/read") == 2
        assert transport.count("tools/list") == 0


@pytest.mark.parametrize("operation", [ResourceRead("test://doc"), PromptGet("greet", {"name": "Ada"})])
async def test_non_tool_operations_ignore_progress_callback(operation: DownstreamOperation) -> None:
    callback = AsyncMock()
    async with sdk_endpoint() as transport:
        await call(transport, operation=operation, on_progress=callback)
        callback.assert_not_awaited()
        message = next(m for m in transport.messages if m["method"] == operation_method(operation))
        assert "progressToken" not in message["params"].get("_meta", {})
