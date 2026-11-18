"""HTTP transport primitives shared by MCP discovery and tool execution."""

import json
import logging
import re
from enum import StrEnum

import httpx2
from mcp.shared._httpx_utils import McpHttpClientFactory, create_mcp_http_client

from .mcp_config import MCPClientConfig

logger = logging.getLogger(__name__)


class DownstreamPhase(StrEnum):
    CONNECTING = "connecting"
    LISTING = "listing"
    CALLING = "calling"
    RESULT_VALIDATION = "result_validation"
    CLOSING = "closing"


class SharedTransport(httpx2.AsyncBaseTransport):
    """A borrowed transport: per-call clients close themselves, the container owns the pool."""

    def __init__(self, transport: httpx2.AsyncBaseTransport) -> None:
        self.transport = transport

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        pass


class MethodRoutedTransport(httpx2.AsyncBaseTransport):
    """Two downstream pools: one for GET streams, one for request and cleanup traffic.

    The SDK keeps a GET open for the whole call (a stateful streamable-HTTP session's server-message
    stream, a legacy SSE event stream, a resumed response stream) while the same call still needs
    POSTs and a closing DELETE. In one shared pool, a burst of open streams can take every connection
    and leave no call able to send its POST. Routing GETs to their own pool means requests and cleanup
    only ever wait for other requests, which finish on their own.

    The owner (the container) closes both pools; per-call clients borrow this through SharedTransport.
    """

    def __init__(self, *, request_pool: httpx2.AsyncBaseTransport, stream_pool: httpx2.AsyncBaseTransport) -> None:
        self.request_pool = request_pool
        self.stream_pool = stream_pool

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        pool = self.stream_pool if request.method == "GET" else self.request_pool
        return await pool.handle_async_request(request)

    async def aclose(self) -> None:
        try:
            await self.request_pool.aclose()
        finally:
            await self.stream_pool.aclose()


class ResponseRecorder:
    """Record the latest RPC response, ignoring background GET/DELETE and notifications.

    Auto negotiation may legitimately reject discover before initialize succeeds. A later
    successful RPC supersedes that failure; an error during tools/call supersedes the handshake.
    """

    def __init__(self) -> None:
        self.status_code: int | None = None
        self.first_rpc_status_code: int | None = None
        self.session_id: str | None = None
        self.www_authenticate: str | None = None
        self._rpc_started = False
        self.phase = DownstreamPhase.CONNECTING
        self.method: str | None = None

    async def on_request(self, request: httpx2.Request) -> None:
        if request.method == "POST":
            try:
                message = json.loads(await request.aread())
            except ValueError:
                return
            if isinstance(message, dict) and "id" in message:
                self.method = message.get("method")
                if self.phase == DownstreamPhase.CALLING and self.method != "tools/call":
                    self.phase = DownstreamPhase.RESULT_VALIDATION
                self._rpc_started = True
                self.status_code = None
                self.www_authenticate = None

    async def __call__(self, response: httpx2.Response) -> None:
        # v2 transports follow same-origin redirects themselves, one ``client.send`` per hop, so the hook
        # also fires on each 3xx. Skip those: the status that matters is the redirect target's.
        if response.is_redirect:
            return
        request = response.request
        if sid := response.headers.get("mcp-session-id"):
            self.session_id = sid
        if request.method == "POST":
            try:
                message = json.loads(request.content)
            except (ValueError, httpx2.RequestNotRead):
                return
            if "id" not in message:
                return
            if self.first_rpc_status_code is None:
                self.first_rpc_status_code = response.status_code
        elif request.method != "GET" or self._rpc_started or self.status_code is not None:
            return
        self.status_code = response.status_code
        self.www_authenticate = response.headers.get("www-authenticate")


def normalize_sse_endpoint_url_for_request(url_str: str) -> str:
    """
    Normalize URLs in HTTP requests by removing mount paths.
    Example: http://localhost:8000/currenttime/messages/... -> http://localhost:8000/messages/...
    """
    if "/messages/" not in url_str:
        return url_str

    # Pattern to match URLs like http://host:port/mount_path/messages/...

    pattern = r"(https?://[^/]+)/([^/]+)(/messages/.*)"
    match = re.match(pattern, url_str)

    if match:
        base_url = match.group(1)  # http://host:port
        mount_path = match.group(2)  # currenttime, fininfo, etc.
        messages_path = match.group(3)  # /messages/...

        # Skip common paths that aren't mount paths
        if mount_path in ["api", "static", "health"]:
            return url_str

        normalized = f"{base_url}{messages_path}"
        logger.debug(f"Normalized request URL: {url_str} -> {normalized}")
        return normalized

    return url_str


async def rewrite_sse_request_url(request: httpx2.Request) -> None:
    """httpx2 request hook that strips SSE mount-path prefixes off the POST-back URL.

    v2's ``sse_client`` uses httpx2, so the old process-wide monkey-patch of ``httpx.AsyncClient.request``
    silently stopped applying. This per-client hook replaces it (and removes the non-concurrency-safe
    global patch).
    """
    url_str = str(request.url)
    if MCPClientConfig.ENDPOINT_MESSAGES in url_str:
        request.url = httpx2.URL(normalize_sse_endpoint_url_for_request(url_str))


def create_sse_client_factory(
    recorder: ResponseRecorder | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> McpHttpClientFactory:
    """Build an httpx2 client factory for ``sse_client`` with the mount-path rewrite hook.

    Reuses the SDK's ``create_mcp_http_client`` (so its own defaults stand) and attaches the request
    hook; when a recorder is given, also attaches it as a response hook to capture status/session id.
    """

    def factory(
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | None = None,
        timeout: httpx2.Timeout | None = None,
    ) -> httpx2.AsyncClient:
        client = (
            httpx2.AsyncClient(
                transport=SharedTransport(transport),
                headers=headers,
                auth=auth,
                timeout=timeout or httpx2.Timeout(30.0, read=60.0),
                follow_redirects=True,
            )
            if transport is not None
            else create_mcp_http_client(headers=headers, auth=auth, timeout=timeout)
        )
        client.event_hooks["request"].append(rewrite_sse_request_url)
        if recorder is not None:
            client.event_hooks["request"].append(recorder.on_request)
            client.event_hooks["response"].append(recorder)
        return client

    return factory
