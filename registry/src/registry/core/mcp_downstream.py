"""Per-call SDK connections over pod-wide pools, with identity-partitioned metadata caches."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Literal

import anyio
import httpx2
from mcp import Client
from mcp.client.caching import CacheConfig, InMemoryResponseCacheStore
from mcp.client.session import LoggingFnT
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.dispatcher import ProgressFnT
from mcp.shared.exceptions import MCPError
from mcp.types import (
    UNSUPPORTED_PROTOCOL_VERSION,
    CallToolResult,
    Implementation,
    InputRequiredResult,
    LoggingLevel,
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
_TOOL_LIST_CACHE_TTL_MS = 300_000
_RESPONSE_CACHE_MAX_ENTRIES = 4096
_DOWNSTREAM_FAILURE_MESSAGE = "Error calling downstream MCP server."
# Level requested from 2026-07-28 downstream servers when relaying logs upstream (no effect on legacy servers).
_DOWNSTREAM_LOG_LEVEL: LoggingLevel = "warning"
DownstreamMode = Literal["legacy", "2026-07-28"]


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


async def call_downstream_tool(
    *,
    url: str,
    transport_type: MCPTransport,
    server_id: str,
    user_id: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any] | None,
    downstream_transport: httpx2.AsyncBaseTransport,
    response_cache: InMemoryResponseCacheStore,
    era_cache: DownstreamEraCache,
    on_progress: ProgressFnT | None = None,
    on_log: LoggingFnT | None = None,
) -> CallToolResult | InputRequiredResult:
    transport_type = MCPTransport(transport_type)
    cached_mode = await era_cache.get(server_id)
    for attempt in range(2):
        recorder = ResponseRecorder()
        tool_sent = False
        try:
            # An SSE POST rejection cancels the block instead of waiting out the read timeout.
            with _fail_fast_on_sse(recorder, transport_type):
                async with _open_transport(url, transport_type, headers, recorder, downstream_transport) as transport:
                    async with Client(
                        transport,
                        mode=cached_mode if attempt == 0 and cached_mode else "auto",
                        read_timeout_seconds=60,
                        client_info=Implementation(**settings.mcp_client_info),
                        logging_callback=on_log,
                        log_level=_DOWNSTREAM_LOG_LEVEL if on_log else None,
                        cache=CacheConfig(
                            store=response_cache,
                            partition=user_id,
                            target_id=server_id,
                            default_ttl_ms=_TOOL_LIST_CACHE_TTL_MS,
                        ),
                    ) as client:
                        await era_cache.record(server_id, client.protocol_version)
                        recorder.phase = DownstreamPhase.LISTING
                        try:
                            # One deadline for the entire listing, not a fresh timeout for every page.
                            async with asyncio.timeout(MCPClientConfig.TOOLS_TIMEOUT):
                                listing = await client.list_tools()
                                cursors: set[str] = set()
                                while not any(tool.name == tool_name for tool in listing.tools) and listing.next_cursor:
                                    cursor = listing.next_cursor
                                    if cursor in cursors:
                                        raise MisimplementedSpecException(
                                            "Downstream tools/list repeated a pagination cursor."
                                        )
                                    cursors.add(cursor)
                                    listing = await client.list_tools(cursor=cursor)
                        except TimeoutError as exc:
                            raise DownstreamHttpFailureException(
                                "Timed out fetching the downstream MCP tool list."
                            ) from exc
                        tool_sent = True
                        recorder.phase = DownstreamPhase.CALLING
                        result = await client.session.call_tool(
                            tool_name,
                            arguments,
                            allow_input_required=True,
                            progress_callback=on_progress,
                        )
                        recorder.phase = DownstreamPhase.CLOSING
                    return result
            raise DownstreamHttpFailureException(_DOWNSTREAM_FAILURE_MESSAGE)  # mapped below from the status
        except Exception as exc:
            error = _single_error(exc)
            version_error = isinstance(error, MCPError) and error.code == UNSUPPORTED_PROTOCOL_VERSION
            # HTTP 400 after sending a tool may be invalid input, never an invitation to replay.
            stale_mode = version_error or (not tool_sent and recorder.first_rpc_status_code == 400)
            if cached_mode and stale_mode:
                await era_cache.delete(server_id)
                if attempt == 0 and not tool_sent:
                    continue
            mapped = _map_error(error, recorder)
            if mapped is exc:
                raise
            raise mapped from exc
    raise AssertionError("unreachable")
