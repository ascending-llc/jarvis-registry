"""Per-call SDK connections over pod-wide pools, with identity-partitioned metadata caches."""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal, overload

import anyio
import httpx2
from mcp import Client
from mcp.client.caching import CacheConfig, InMemoryResponseCacheStore
from mcp.client.session import ClientSession, LoggingFnT
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.dispatcher import ProgressFnT
from mcp.shared.exceptions import MCPError
from mcp.types import (
    UNSUPPORTED_PROTOCOL_VERSION,
    CallToolResult,
    GetPromptResult,
    Implementation,
    InputRequiredResult,
    LoggingLevel,
    ReadResourceResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from redis import Redis
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from registry_pkgs.core.exceptions import (
    DownstreamHttpFailureException,
    DownstreamUnauthorizedException,
    MisimplementedSpecException,
)

from .config import settings
from .mcp_config import MCPClientConfig, MCPTransport
from .mcp_transport import (
    DownstreamPhase,
    ResponseRecorder,
    SharedTransport,
    cancel_on_post_rejection,
    create_sse_client_factory,
)

logger = logging.getLogger(__name__)
_ERA_CACHE_TTL_SECONDS = 3600
_MAX_ERA_NEGOTIATION_ATTEMPTS = 2
_TOOL_LIST_CACHE_TTL_MS = 300_000
_RESPONSE_CACHE_MAX_ENTRIES = 4096
_DOWNSTREAM_FAILURE_MESSAGE = "Error calling downstream MCP server."
# Level requested from 2026-07-28 downstream servers when relaying logs upstream (no effect on legacy servers).
_DOWNSTREAM_LOG_LEVEL: LoggingLevel = "warning"
DownstreamMode = Literal["legacy", "2026-07-28"]


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] | None


@dataclass(frozen=True)
class ResourceRead:
    uri: str


@dataclass(frozen=True)
class PromptGet:
    name: str
    arguments: dict[str, str] | None


type DownstreamOperation = ToolCall | ResourceRead | PromptGet
type DownstreamResult = CallToolResult | ReadResourceResult | GetPromptResult | InputRequiredResult


class DownstreamEraCache:
    def __init__(self, redis_client: Redis, key_prefix: str) -> None:
        self._redis = redis_client
        self._prefix = key_prefix

    async def _run(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(operation, *args, **kwargs)
        except RedisError:
            logger.warning("Downstream era cache unavailable; continuing without cache", exc_info=True)
            return None

    def _key(self, server_id: str) -> str:
        return f"{self._prefix}:mcp_era:{server_id}"

    async def get(self, server_id: str) -> DownstreamMode | None:
        raw = await self._run(self._redis.get, self._key(server_id))
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return raw if raw in ("legacy", "2026-07-28") else None

    async def record(self, server_id: str | None, protocol_version: str) -> None:
        if server_id:
            mode = "2026-07-28" if protocol_version in MODERN_PROTOCOL_VERSIONS else "legacy"
            await self._run(self._redis.set, self._key(server_id), mode, ex=_ERA_CACHE_TTL_SECONDS)

    async def delete(self, server_id: str) -> None:
        await self._run(self._redis.delete, self._key(server_id))


def _tool_list_cache_target(server_id: str, transport_type: MCPTransport, url: str) -> str:
    """Cache target for a server's tool listing, scoped to its current endpoint.

    A server keeps its ID when its URL or transport changes, and the store is per pod, so no single
    invalidation reaches every pod. Folding the endpoint into the key gives a changed endpoint a fresh
    entry everywhere. The endpoint is hashed so credentials embedded in a URL stay out of cache keys.
    """
    endpoint = hashlib.sha256(f"{transport_type}|{url}".encode()).hexdigest()[:16]
    return f"{server_id}:{endpoint}"


class DownstreamCallUnauthorized(DownstreamUnauthorizedException):
    """A 401 annotated with the operation that failed, for bounded OAuth recovery."""

    def __init__(self, recorder: ResponseRecorder) -> None:
        super().__init__(_DOWNSTREAM_FAILURE_MESSAGE, www_authenticate=recorder.www_authenticate)
        self.phase = recorder.phase
        self.method = recorder.method

    @property
    def oauth_retry_safe(self) -> bool:
        return self.phase in (DownstreamPhase.CONNECTING, DownstreamPhase.LISTING, DownstreamPhase.CALLING)


# Raised by SDK stream tasks when the transport closes under them after another failure ended the call.
_TEARDOWN_ERRORS = (anyio.BrokenResourceError, anyio.ClosedResourceError)


def _leaves(group: BaseExceptionGroup) -> list[BaseException]:
    return [
        leaf for exc in group.exceptions for leaf in (_leaves(exc) if isinstance(exc, BaseExceptionGroup) else [exc])
    ]


def _single_error(error: Exception) -> Exception:
    """Unwrap SDK task-group envelopes to the one failure they carry.

    Stream-teardown errors raised while the transport closes after that failure are ignored. A group
    with more than one other failure is returned unchanged, so independent failures are never
    collapsed into one (e.g. a recoverable 401).
    """
    if not isinstance(error, ExceptionGroup):
        return error
    _, rest = error.split(_TEARDOWN_ERRORS)
    leaves = _leaves(rest if rest is not None else error)
    if len(leaves) == 1 and isinstance(leaves[0], Exception):
        return leaves[0]
    return rest if rest is not None else error


def _map_error(error: Exception, recorder: ResponseRecorder) -> Exception:
    if isinstance(error, ExceptionGroup):
        return error
    if recorder.status_code == 401:
        return DownstreamCallUnauthorized(recorder)
    if recorder.status_code is not None and recorder.status_code >= 300:
        return DownstreamHttpFailureException(_DOWNSTREAM_FAILURE_MESSAGE)
    if isinstance(error, MCPError) and error.code == -32001:
        return DownstreamHttpFailureException("Timed out waiting for the downstream MCP server.")
    if isinstance(error, (httpx2.TransportError, OSError)):
        return DownstreamHttpFailureException(_DOWNSTREAM_FAILURE_MESSAGE)
    if isinstance(error, RuntimeError) and (
        str(error).startswith(("Invalid schema for tool ", "Invalid structured content returned by tool "))
        or (
            str(error).startswith("Tool ")
            and str(error).endswith(" has an output schema but did not return structured content")
        )
    ):
        return MisimplementedSpecException(str(error))
    return error


@asynccontextmanager
async def _open_transport(
    url: str,
    transport_type: MCPTransport,
    headers: dict[str, str],
    recorder: ResponseRecorder,
    downstream_transport: httpx2.AsyncBaseTransport,
) -> AsyncIterator[Any]:
    """Yield the SDK transport for one call, on a per-call httpx2 client over the shared pool."""
    if transport_type == MCPTransport.SSE:
        # sse_client builds its own client through the factory.
        yield sse_client(
            url,
            headers=headers,
            httpx_client_factory=create_sse_client_factory(recorder, downstream_transport),
        )
    elif transport_type == MCPTransport.STREAMABLE_HTTP:
        async with httpx2.AsyncClient(
            transport=SharedTransport(downstream_transport),
            headers=headers,
            timeout=httpx2.Timeout(30.0, read=60.0),
            follow_redirects=True,
            event_hooks={"request": [recorder.on_request], "response": [recorder]},
        ) as http_client:
            yield streamable_http_client(url, http_client=http_client)
    else:
        raise ValueError(f"Unsupported downstream transport: {transport_type}")


@contextmanager
def _fail_fast_on_sse(recorder: ResponseRecorder, transport_type: MCPTransport) -> Iterator[None]:
    if transport_type == MCPTransport.SSE:
        with cancel_on_post_rejection(recorder):
            yield
    else:
        yield


@asynccontextmanager
async def _open_client(
    *,
    url: str,
    transport_type: MCPTransport,
    server_id: str,
    user_id: str,
    headers: dict[str, str],
    recorder: ResponseRecorder,
    mode: DownstreamMode | Literal["auto"],
    downstream_transport: httpx2.AsyncBaseTransport,
    response_cache: InMemoryResponseCacheStore,
    on_log: LoggingFnT | None,
) -> AsyncIterator[Client]:
    """Own one SDK connection while borrowing the pod-wide transport pool.

    1. Partition tools/list metadata by user and the server's current endpoint.
    2. Leave business requests to _send_operation, which bypasses the Client content cache.
    """
    async with _open_transport(url, transport_type, headers, recorder, downstream_transport) as transport:
        async with Client(
            transport,
            mode=mode,
            read_timeout_seconds=60,
            client_info=Implementation(**settings.mcp_client_info),
            logging_callback=on_log,
            log_level=_DOWNSTREAM_LOG_LEVEL if on_log else None,
            cache=CacheConfig(
                store=response_cache,
                partition=user_id,
                target_id=_tool_list_cache_target(server_id, transport_type, url),
                default_ttl_ms=_TOOL_LIST_CACHE_TTL_MS,
            ),
        ) as client:
            yield client


async def _load_tool_schema(client: Client, tool_name: str) -> None:
    """Load the target tool's output schema for SDK result validation.

    1. Stop paging when the tool is found or the listing ends; leave missing-tool handling to the SDK.
    2. Run only for ToolCall; resource and prompt requests must not trigger tools/list.
    """
    try:
        # 1. Share one deadline across all pages rather than resetting the timeout per page.
        async with asyncio.timeout(MCPClientConfig.TOOLS_TIMEOUT):
            listing = await client.list_tools()
            cursors: set[str] = set()
            while not any(tool.name == tool_name for tool in listing.tools) and listing.next_cursor:
                cursor = listing.next_cursor
                if cursor in cursors:
                    raise MisimplementedSpecException("Downstream tools/list repeated a pagination cursor.")
                cursors.add(cursor)
                listing = await client.list_tools(cursor=cursor)
    except TimeoutError as exc:
        raise DownstreamHttpFailureException("Timed out fetching the downstream MCP tool list.") from exc


async def _send_operation(
    session: ClientSession,
    operation: DownstreamOperation,
    on_progress: ProgressFnT | None,
) -> DownstreamResult:
    """Dispatch one business request without retry, OAuth recovery or gateway rendering.

    1. Use session methods to bypass content caching and the Client's interactive-input replay.
    2. Return InputRequiredResult to the gateway unchanged via allow_input_required=True.
    3. Forward progress only for tools/call, the only SDK method that supports this callback.
    """
    if isinstance(operation, ToolCall):
        return await session.call_tool(
            operation.tool_name,
            operation.arguments,
            allow_input_required=True,
            progress_callback=on_progress,
        )
    if isinstance(operation, ResourceRead):
        return await session.read_resource(operation.uri, allow_input_required=True)
    return await session.get_prompt(operation.name, operation.arguments, allow_input_required=True)


@overload
async def call_downstream(
    *,
    url: str,
    transport_type: MCPTransport,
    server_id: str,
    user_id: str,
    headers: dict[str, str],
    operation: ToolCall,
    downstream_transport: httpx2.AsyncBaseTransport,
    response_cache: InMemoryResponseCacheStore,
    era_cache: DownstreamEraCache,
    on_progress: ProgressFnT | None = None,
    on_log: LoggingFnT | None = None,
) -> CallToolResult | InputRequiredResult:
    pass


@overload
async def call_downstream(
    *,
    url: str,
    transport_type: MCPTransport,
    server_id: str,
    user_id: str,
    headers: dict[str, str],
    operation: ResourceRead,
    downstream_transport: httpx2.AsyncBaseTransport,
    response_cache: InMemoryResponseCacheStore,
    era_cache: DownstreamEraCache,
    on_progress: ProgressFnT | None = None,
    on_log: LoggingFnT | None = None,
) -> ReadResourceResult | InputRequiredResult:
    pass


@overload
async def call_downstream(
    *,
    url: str,
    transport_type: MCPTransport,
    server_id: str,
    user_id: str,
    headers: dict[str, str],
    operation: PromptGet,
    downstream_transport: httpx2.AsyncBaseTransport,
    response_cache: InMemoryResponseCacheStore,
    era_cache: DownstreamEraCache,
    on_progress: ProgressFnT | None = None,
    on_log: LoggingFnT | None = None,
) -> GetPromptResult | InputRequiredResult:
    pass


async def call_downstream(
    *,
    url: str,
    transport_type: MCPTransport,
    server_id: str,
    user_id: str,
    headers: dict[str, str],
    operation: DownstreamOperation,
    downstream_transport: httpx2.AsyncBaseTransport,
    response_cache: InMemoryResponseCacheStore,
    era_cache: DownstreamEraCache,
    on_progress: ProgressFnT | None = None,
    on_log: LoggingFnT | None = None,
) -> DownstreamResult:
    """Connect, prepare, send and close one downstream operation.

    The era cache remembers the protocol mode; the response cache stores tool-list metadata.
    A rejected cached era allows one automatic negotiation retry, only before business send.
    After send, a version error can invalidate the cache but cannot replay the operation here.
    The caller owns bounded OAuth recovery and rendering of the native SDK result.
    """
    # 1. Normalize the transport and load the server's cached protocol era.
    transport_type = MCPTransport(transport_type)
    cached_mode = await era_cache.get(server_id)
    for attempt in range(_MAX_ERA_NEGOTIATION_ATTEMPTS):
        # 2. Isolate each attempt's HTTP state. Only the first attempt may reuse the cached era;
        #    a retry uses SDK auto negotiation, including fallback to legacy when needed.
        recorder = ResponseRecorder()
        request_sent = False
        connection_mode = cached_mode if attempt == 0 and cached_mode else "auto"
        try:
            # 2a. Guard setup, execution and teardown: an SSE POST rejection may otherwise
            #     only be logged by the SDK while the request waits until its read timeout.
            with _fail_fast_on_sse(recorder, transport_type):
                async with _open_client(
                    url=url,
                    transport_type=transport_type,
                    server_id=server_id,
                    user_id=user_id,
                    headers=headers,
                    recorder=recorder,
                    mode=connection_mode,
                    downstream_transport=downstream_transport,
                    response_cache=response_cache,
                    on_log=on_log,
                ) as client:
                    # 3. Remember the selected era, then preload output schema only for tools.
                    #    Resource and prompt requests skip listing entirely.
                    await era_cache.record(server_id, client.protocol_version)
                    if isinstance(operation, ToolCall):
                        recorder.phase = DownstreamPhase.LISTING
                        await _load_tool_schema(client, operation.tool_name)

                    # 4. Mark before awaiting: the request may reach the downstream even if send fails.
                    #    From this point, protocol-version recovery must never replay the operation.
                    request_sent = True
                    recorder.phase = DownstreamPhase.CALLING
                    result = await _send_operation(client.session, operation, on_progress)
                    # 5. Mark teardown before leaving the context, so a closing 401 cannot
                    #    be mistaken for a rejected business request eligible for OAuth retry.
                    recorder.phase = DownstreamPhase.CLOSING
            # 5a. The SSE guard suppresses its own cancellation after a rejected POST. Check
            #     that outcome before returning: setup or send may not have produced a result.
            if recorder.post_rejected:
                raise DownstreamHttpFailureException(_DOWNSTREAM_FAILURE_MESSAGE)
            return result
        except Exception as exc:
            # 6. Unwrap SDK task-group failures without collapsing independent errors.
            error = _single_error(exc)
            # 7. Separate cache invalidation from retry eligibility. A version rejection after
            #    send still clears stale metadata for the next call, but cannot replay this one.
            version_rejected = isinstance(error, MCPError) and error.code == UNSUPPORTED_PROTOCOL_VERSION
            rejected_before_send = not request_sent and recorder.first_rpc_status_code == 400
            cached_era_rejected = cached_mode is not None and (version_rejected or rejected_before_send)
            if cached_era_rejected:
                await era_cache.delete(server_id)
                can_retry_before_send = attempt == 0 and not request_sent
                if can_retry_before_send:
                    continue

            # 8. Preserve the public error contract when retry is not allowed. In particular,
            #    an HTTP 400 after business send may be invalid input, never a replay signal.
            mapped = _map_error(error, recorder)
            if mapped is exc:
                raise
            raise mapped from exc
    raise AssertionError("unreachable")
