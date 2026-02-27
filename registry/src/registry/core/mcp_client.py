"""
MCP Client Service

Handles connections to MCP servers and tool list retrieval.
Refactored with centralized configuration and strategy pattern.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from authlib.oauth2.rfc8414 import AuthorizationServerMetadata
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from registry.core.config import settings

# Internal imports
from registry.core.mcp_config import mcp_config
from registry.core.server_strategies import get_server_strategy
from registry_pkgs.database.redis_client import get_redis_client

logger = logging.getLogger(__name__)


# ========== Session Management ==========
# Redis-based session store for downstream MCP servers with TTL
# Key: f"mcp_session:{user_id}:{server_id}" -> Value: JSON({"session_id": str, "initialized": bool})
# Sessions expire after 15 minutes of inactivity (MCP server session timeout)
SESSION_TTL_MINUTES = 15  # MCP session timeout
SESSION_KEY_PREFIX = "mcp_session:"


def get_session(session_key: str) -> tuple[str, bool] | None:
    """Get session ID and initialization status from Redis if not expired."""
    redis_client = get_redis_client()
    if not redis_client:
        logger.warning("Redis not available, session management disabled")
        return None

    try:
        redis_key = f"{SESSION_KEY_PREFIX}{session_key}"
        session_data = redis_client.get(redis_key)

        if not session_data:
            return None

        # Parse JSON data

        data = json.loads(session_data)
        session_id = data.get("session_id")
        initialized = data.get("initialized", False)

        logger.debug(f"Session retrieved from Redis: {session_key} (initialized={initialized})")
        return (session_id, initialized)

    except Exception as e:
        logger.error(f"Failed to get session from Redis: {e}")
        return None


def store_session(session_key: str, session_id: str, initialized: bool = False) -> None:
    redis_client = get_redis_client()
    """Store session ID with TTL and initialization status in Redis."""
    if not redis_client:
        logger.warning("Redis not available, session not stored")
        return

    try:
        redis_key = f"{SESSION_KEY_PREFIX}{session_key}"
        session_data = json.dumps({"session_id": session_id, "initialized": initialized})

        # Store with TTL in seconds
        ttl_seconds = SESSION_TTL_MINUTES * 60
        redis_client.setex(redis_key, ttl_seconds, session_data)

        logger.debug(
            f"Session stored in Redis with {SESSION_TTL_MINUTES}min TTL: {session_key} (initialized={initialized})"
        )

    except Exception as e:
        logger.error(f"Failed to store session in Redis: {e}")


def clear_session(session_key: str) -> None:
    redis_client = get_redis_client()
    """Clear/disconnect a session from Redis."""
    if not redis_client:
        logger.warning("Redis not available, session not cleared")
        return

    try:
        redis_key = f"{SESSION_KEY_PREFIX}{session_key}"
        redis_client.delete(redis_key)
        logger.info(f"🗑️ Session cleared from Redis: {session_key}")

    except Exception as e:
        logger.error(f"Failed to clear session from Redis: {e}")


async def initialize_mcp(
    target_url: str, headers: dict[str, str] = None, transport_type: str = "streamable-http"
) -> Any | None:
    """
    Perform MCP initialization using MCP client library and return the init_result.

    This is a pure transport layer function that performs the MCP initialize handshake
    and returns the raw InitializeResult. The caller is responsible for interpreting
    the result (e.g., for health checks, validation, etc.).

    Args:
        target_url: MCP server URL
        headers: HTTP headers (optional, defaults to standard MCP headers)
        transport_type: Transport type ("streamable-http" or "sse")

    Returns:
        InitializeResult object from MCP library, or None if initialization failed

    Raises:
        httpx.HTTPStatusError: Re-raised to allow caller to inspect HTTP status codes
    """

    # Use provided headers or default MCP headers
    if headers is None:
        headers = mcp_config.DEFAULT_HEADERS.copy()

    # Prepare URL based on transport type
    strategy = get_server_strategy({"type": transport_type})
    mcp_url = strategy.modify_url(target_url)

    logger.info(f"Initializing MCP connection to {mcp_url} using {transport_type} transport")

    try:
        # Create custom httpx client with headers
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as http_client:
            if transport_type == "streamable-http":
                async with streamable_http_client(url=mcp_url, http_client=http_client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        # Perform MCP initialization
                        init_result = await asyncio.wait_for(session.initialize(), timeout=mcp_config.INIT_TIMEOUT)
                        logger.info(f"MCP initialization successful for {target_url}")
                        return init_result

            elif transport_type == "sse":
                async with sse_client(mcp_url, http_client=http_client) as (read, write):
                    async with ClientSession(read, write) as session:
                        # Perform MCP initialization
                        init_result = await asyncio.wait_for(session.initialize(), timeout=mcp_config.INIT_TIMEOUT)
                        logger.info(f"MCP initialization successful for {target_url}")
                        return init_result
            else:
                logger.error(f"Unsupported transport type: {transport_type}")
                return None

    except ExceptionGroup as eg:
        # Unwrap ExceptionGroup to extract HTTPStatusError (from asyncio.TaskGroup)
        for exc in eg.exceptions:
            if isinstance(exc, httpx.HTTPStatusError):
                logger.debug(f"HTTP error during MCP initialization for {target_url}: {exc.response.status_code}")
                raise exc  # Re-raise the unwrapped HTTPStatusError
        logger.error(f"MCP initialization failed for {target_url}: ExceptionGroup - {eg}")
        return None
    except httpx.HTTPStatusError as e:
        # Re-raise HTTP errors so caller can inspect status code (e.g., 401 for health checks)
        logger.debug(f"HTTP error during MCP initialization for {target_url}: {e.response.status_code}")
        raise
    except Exception as e:
        logger.error(f"MCP initialization failed for {target_url}: {type(e).__name__} - {e}")
        return None


async def perform_health_check(
    url: str,
    transport: str = "streamable-http",
) -> tuple[bool, str, int | None, Any | None]:
    """
    Perform health check on an MCP server URL.

    Application-level health check logic that interprets initialization results:
    - Success with init_result: Server is healthy, initialization handshake successful
    - 401 Unauthorized: Server is healthy but requires authentication
    - Other failures: Server is unhealthy

    Args:
        url: MCP server URL
        transport: Transport type (streamable-http, sse, stdio)

    Returns:
        Tuple of (is_healthy, status_message, response_time_ms, init_result)
        - is_healthy: True if server is reachable (including 401 responses)
        - status_message: Granular status for frontend display
        - init_result: Contains serverInfo and capabilities if successful, None for 401
    """
    if not url:
        return False, "No URL provided", None, None

    # Skip health checks for stdio transport
    if transport == mcp_config.TRANSPORT_STDIO:
        logger.info(f"Skipping health check for stdio transport: {url}")
        return True, "healthy (stdio transport skipped)", None, None

    # Build basic headers for health check (no authentication needed for initialize)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.debug(f"Performing MCP initialization health check for {url}")

    # Measure response time

    start_time = datetime.now(UTC)

    # Attempt MCP initialization
    init_result = None
    http_status_code = None

    try:
        init_result = await initialize_mcp(target_url=url, headers=headers, transport_type=transport)
    except httpx.HTTPStatusError as e:
        http_status_code = e.response.status_code
        logger.debug(f"HTTP error {http_status_code} during health check for {url}")
    except Exception as e:
        logger.debug(f"Non-HTTP error during health check for {url}: {type(e).__name__} - {e}")

    # Calculate response time
    end_time = datetime.now(UTC)
    response_time_ms = int((end_time - start_time).total_seconds() * 1000)

    # Application logic: Interpret the initialization result

    # Check for 401/403 Unauthorized/Forbidden - server is healthy but requires auth
    if http_status_code in mcp_config.AUTH_REQUIRED_STATUS_CODES:
        logger.info(
            f"Health check passed for {url}: Connected successfully, initialize requires authentication ({http_status_code})"
        )
        return True, "connected (initialize requires authentication)", response_time_ms, None

    # Validate init_result structure per MCP spec
    if init_result is None:
        logger.warning(f"Health check failed for {url}: initialization failed")
        return False, "unhealthy: initialization failed", response_time_ms, None

    # Check for required fields: protocolVersion and serverInfo
    if hasattr(init_result, "protocolVersion") and hasattr(init_result, "serverInfo"):
        remote_server_name = init_result.serverInfo.name if hasattr(init_result.serverInfo, "name") else "unknown"

        # Convert capabilities to dict if present (normalize Pydantic models to dicts)
        if hasattr(init_result, "capabilities"):
            init_result.capabilities = _convert_pydantic_to_dict(init_result.capabilities)

        logger.info(
            f"Health check passed for {url}: Initialize handshake successful - {remote_server_name} (protocol: {init_result.protocolVersion})"
        )
        return True, "initialize handshake successful", response_time_ms, init_result
    else:
        logger.warning(
            f"Health check failed for {url}: invalid initialize response (missing protocolVersion or serverInfo)"
        )
        return False, "unhealthy: invalid initialize response", response_time_ms, None


async def initialize_mcp_session(
    target_url: str, headers: dict[str, str], session_key: str, transport_type: str = "streamable-http"
) -> str | None:
    """
    Perform MCP initialization handshake using raw JSON-RPC.

    Sends initialize/initialized handshake and extracts session ID from headers,
    but does NOT keep the connection open. The session ID is used for subsequent
    requests which will maintain their own connections.

    Args:
        target_url: MCP server URL
        headers: HTTP headers (including authentication)
        session_key: Session storage key (user_id:server_id)
        transport_type: Transport type ("streamable-http" or "sse")

    Returns:
        Session ID if successful, None otherwise
    """
    logger.info(f"🔄 Initializing MCP session using JSON-RPC ({transport_type} transport)")

    try:
        # Create httpx client with custom headers for authentication
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as http_client:
            # Send initialize request (JSON-RPC 2.0)
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
                },
            }

            logger.info("📤 Sending initialize request")
            response = await http_client.post(target_url, json=init_request)
            response.raise_for_status()

            # Extract session ID from response headers
            session_id = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")

            if not session_id:
                logger.warning("⚠️ No session ID in response headers")
                return None

            logger.info(f"✅ Session ID received: {session_id}")

            # Send initialized notification (completes handshake)
            initialized_notification = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}

            # Include session ID in subsequent request
            headers_with_session = {**headers, "Mcp-Session-Id": session_id}

            logger.info("📤 Sending initialized notification")
            await http_client.post(target_url, json=initialized_notification, headers=headers_with_session)

            logger.info(f"✅ MCP session fully initialized: {session_id}")

            # Store session as initialized
            store_session(session_key, session_id, initialized=True)

            return session_id

    except Exception as e:
        logger.error(f"❌ Failed to initialize MCP session: {e}", exc_info=True)
        # Clear any partial session state
        clear_session(session_key)
        return None


@dataclass
class MCPServerData:
    """MCP server data container for tools, resources, prompts, and capabilities."""

    tools: list[dict[str, Any]] | None
    resources: list[dict[str, Any]] | None
    prompts: list[dict[str, Any]] | None
    capabilities: dict[str, Any] | None
    error_message: str | None = None
    requires_init: bool | None = False


def _convert_pydantic_to_dict(obj: Any) -> dict:
    """
    Convert Pydantic model or object to dict.

    Args:
        obj: The object to convert (Pydantic model, object with __dict__, or regular dict)

    Returns:
        Dictionary representation of the object
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    elif hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return obj


def normalize_sse_endpoint_url(endpoint_url: str) -> str:
    """
    Normalize SSE endpoint URLs by removing mount path prefixes.

    For example:
    - Input: "/fininfo/messages/?session_id=123"
    - Output: "/messages/?session_id=123"

    Args:
        endpoint_url: The endpoint URL from the SSE event data

    Returns:
        The normalized URL with mount path stripped
    """
    if not endpoint_url:
        return endpoint_url

    # Pattern to match mount paths like /fininfo/, /currenttime/, etc.
    # We look for paths that start with /word/ followed by messages/
    mount_path_pattern = r"^(/[^/]+)(/messages/.*)"

    match = re.match(mount_path_pattern, endpoint_url)
    if match:
        mount_path = match.group(1)  # e.g., "/fininfo"
        rest_of_url = match.group(2)  # e.g., "/messages/?session_id=123"

        logger.debug(f"Stripping mount path '{mount_path}' from endpoint URL: {endpoint_url}")
        return rest_of_url

    # If no mount path pattern detected, return as-is
    return endpoint_url


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


async def detect_server_transport_aware(base_url: str, server_info: dict = None) -> str:
    """
    Detect which transport a server supports by checking configuration and testing endpoints.
    Uses server_info type if available, otherwise falls back to auto-detection.

    Args:
        base_url: The base URL of the MCP server
        server_info: Optional server configuration dict containing type

    Returns:
        The preferred transport type (uses mcp_config constants)
    """
    # If URL already has a transport endpoint, detect from it
    if base_url.endswith(mcp_config.ENDPOINT_SSE) or mcp_config.ENDPOINT_SSE in base_url:
        logger.debug(f"Server URL {base_url} already has SSE endpoint")
        return mcp_config.TRANSPORT_SSE
    elif base_url.endswith(mcp_config.ENDPOINT_MCP) or mcp_config.ENDPOINT_MCP in base_url:
        logger.debug(f"Server URL {base_url} already has MCP endpoint")
        return mcp_config.TRANSPORT_HTTP

    # Use server configuration if available
    if server_info:
        transport_type = server_info.get("type")
        if transport_type:
            logger.debug(f"Server configuration specifies transport type: {transport_type}")
            return transport_type

    # Fall back to auto-detection
    return await detect_server_transport(base_url)


async def detect_server_transport(base_url: str) -> str:
    """
    Detect which transport a server supports by testing endpoints.
    Returns the preferred transport type.
    """
    # If URL already has a transport endpoint, detect from it
    if base_url.endswith(mcp_config.ENDPOINT_SSE) or mcp_config.ENDPOINT_SSE in base_url:
        logger.debug(f"Server URL {base_url} already has SSE endpoint")
        return mcp_config.TRANSPORT_SSE
    elif base_url.endswith(mcp_config.ENDPOINT_MCP) or mcp_config.ENDPOINT_MCP in base_url:
        logger.debug(f"Server URL {base_url} already has MCP endpoint")
        return mcp_config.TRANSPORT_HTTP

    # Test streamable-http first (default preference)
    try:
        mcp_url = base_url.rstrip("/") + mcp_config.ENDPOINT_MCP + "/"
        async with streamable_http_client(url=mcp_url):
            logger.debug(f"Server at {base_url} supports streamable-http transport")
            return mcp_config.TRANSPORT_HTTP
    except Exception as e:
        logger.debug(f"Streamable-HTTP test failed for {base_url}: {e}")

    # Fallback to SSE
    try:
        sse_url = base_url.rstrip("/") + mcp_config.ENDPOINT_SSE
        async with sse_client(sse_url):
            logger.debug(f"Server at {base_url} supports SSE transport")
            return mcp_config.TRANSPORT_SSE
    except Exception as e:
        logger.debug(f"SSE test failed for {base_url}: {e}")

    # Default to streamable-http if detection fails
    logger.warning(f"Could not detect transport for {base_url}, defaulting to streamable-http")
    return mcp_config.TRANSPORT_HTTP


async def get_tools_from_server_with_transport(base_url: str, transport: str = "auto") -> list[dict] | None:
    """
    Connects to an MCP server using the specified transport, lists tools, and returns their details.

    Args:
        base_url: The base URL of the MCP server (e.g., http://localhost:8000).
        transport: Transport type ("streamable-http", "sse", or "auto")

    Returns:
        A list of tool detail dictionaries, or None if connection/retrieval fails.
    """
    if not base_url:
        logger.error("MCP Check Error: Base URL is empty.")
        return None

    # Auto-detect transport if needed
    if transport == "auto":
        transport = await detect_server_transport(base_url)

    logger.info(f"Attempting to connect to MCP server at {base_url} using {transport} transport...")

    try:
        if transport == "streamable-http":
            return await _get_tools_streamable_http(base_url)
        elif transport == "sse":
            return await _get_tools_sse(base_url)
        else:
            logger.error(f"Unsupported transport type: {transport}")
            return None

    except Exception as e:
        logger.error(
            f"MCP Check Error: Failed to get tool list from {base_url} with {transport}: {type(e).__name__} - {e}"
        )
        return None


async def _is_requires_init(get_session_id):
    try:
        session_id = get_session_id() if callable(get_session_id) else None
        requires_init = session_id is not None
        logger.info(
            f"streamable-http: session_id={'present' if session_id else 'absent'}, requiresInit={requires_init}"
        )
    except Exception as e:
        logger.warning(f"Failed to get session_id: {e}, assuming stateless")
        requires_init = False
    return requires_init


async def _get_from_streamable_http(
    base_url: str,
    headers: dict[str, str] = None,
    transport_type: str = "streamable-http",
    include_capabilities: bool = True,
    include_resources: bool = True,
    include_prompts: bool = True,
) -> MCPServerData:
    """
    Consolidated method to get tools, resources, prompts, and optionally capabilities using streamable-http transport.

    Pure transport layer - accepts pre-built headers from caller.
    No authentication logic - that's handled by the caller (server_service, proxy_routes, etc.)

    Args:
        base_url: The URL to connect to (should contain everything needed)
        headers: Pre-built HTTP headers (including authentication)
        transport_type: Transport type for strategy selection
        include_capabilities: Whether to retrieve and validate capabilities
        include_resources: Whether to retrieve resources
        include_prompts: Whether to retrieve prompts

    Returns:
        MCPServerData containing tools, resources, prompts, and capabilities
        - If include_capabilities=True: Returns empty MCPServerData if capabilities cannot be retrieved
        - If include_capabilities=False: Returns MCPServerData with tools, resources, prompts (capabilities=None)
    """
    # Use provided headers or default MCP headers
    if headers is None:
        headers = mcp_config.DEFAULT_HEADERS.copy()

    # Use the URL as provided - it should contain everything needed
    mcp_url = base_url

    # Apply server-specific URL modifications via strategy pattern
    strategy = get_server_strategy({"type": transport_type})
    mcp_url = strategy.modify_url(mcp_url)

    logger.info(f"Connecting to MCP server: {mcp_url}")

    # Import httpx for custom client

    try:
        # Create custom httpx client with headers
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as http_client:
            async with streamable_http_client(url=mcp_url, http_client=http_client) as (read, write, get_session_id):
                async with ClientSession(read, write) as session:
                    init_result = await asyncio.wait_for(session.initialize(), timeout=mcp_config.INIT_TIMEOUT)
                    tools_response = await asyncio.wait_for(session.list_tools(), timeout=mcp_config.TOOLS_TIMEOUT)

                    # Extract capabilities if requested
                    capabilities = None
                    if include_capabilities:
                        capabilities = _extract_capabilities(init_result)

                        # If capabilities required but not retrieved, consider it a failed server
                        if not capabilities:
                            logger.error(f"Failed to retrieve capabilities from {mcp_url} - server considered failed")
                            return MCPServerData(None, None, None, None, "Failed to retrieve capabilities")

                        logger.info(f"Successfully retrieved capabilities from {mcp_url}: {capabilities}")

                    # Extract tool details
                    tool_list = _extract_tool_details(tools_response)

                    # Extract resources if requested
                    resource_list = []
                    if include_resources:
                        try:
                            resources_response = await asyncio.wait_for(
                                session.list_resources(), timeout=mcp_config.TOOLS_TIMEOUT
                            )
                            resource_list = _extract_resource_details(resources_response)
                        except Exception as e:
                            logger.warning(f"Failed to retrieve resources from {mcp_url}: {e}")
                            resource_list = []

                    # Extract prompts if requested
                    prompt_list = []
                    if include_prompts:
                        try:
                            prompts_response = await asyncio.wait_for(
                                session.list_prompts(), timeout=mcp_config.TOOLS_TIMEOUT
                            )
                            prompt_list = _extract_prompt_details(prompts_response)
                        except Exception as e:
                            logger.warning(f"Failed to retrieve prompts from {mcp_url}: {e}")
                            prompt_list = []

                    requires_init = await _is_requires_init(get_session_id)
                    return MCPServerData(
                        tools=tool_list,
                        resources=resource_list,
                        prompts=prompt_list,
                        capabilities=capabilities,
                        requires_init=requires_init,
                    )

    except TimeoutError:
        logger.error(f"Timeout connecting to {mcp_url}")
        return MCPServerData(None, None, None, None, "Timeout connecting to server")
    except Exception as e:
        logger.error(f"Failed to connect to {mcp_url}: {type(e).__name__} - {e}")
        return MCPServerData(None, None, None, None, f"Connection failed: {type(e).__name__} - {e}")


async def _get_tools_streamable_http(
    base_url: str, headers: dict[str, str] = None, transport_type: str = "streamable-http"
) -> list[dict] | None:
    """
    Get tools using streamable-http transport (legacy method, without capabilities, resources, or prompts).
    Wraps the consolidated method for backward compatibility.
    """
    result = await _get_from_streamable_http(
        base_url, headers, transport_type, include_capabilities=False, include_resources=False, include_prompts=False
    )
    return result.tools


async def _get_from_sse(
    base_url: str,
    headers: dict[str, str] = None,
    transport_type: str = "sse",
    include_capabilities: bool = True,
    include_resources: bool = True,
    include_prompts: bool = True,
) -> MCPServerData:
    """
    Consolidated method to get tools, resources, prompts, and optionally capabilities using SSE transport.

    Pure transport layer - accepts pre-built headers from caller.
    No authentication logic - that's handled by the caller.

    Args:
        base_url: The URL to connect to (should contain everything needed)
        headers: Pre-built HTTP headers (including authentication)
        transport_type: Transport type for strategy selection
        include_capabilities: Whether to retrieve and validate capabilities
        include_resources: Whether to retrieve resources
        include_prompts: Whether to retrieve prompts

    Returns:
        MCPServerData containing tools, resources, prompts, and capabilities
        - If include_capabilities=True: Returns empty MCPServerData if capabilities cannot be retrieved
        - If include_capabilities=False: Returns MCPServerData with tools, resources, prompts (capabilities=None)
    """
    # Use provided headers or default MCP headers
    if headers is None:
        headers = mcp_config.DEFAULT_HEADERS.copy()

    # Use the URL as provided - it should contain everything needed
    sse_url = base_url

    secure_prefix = "s" if sse_url.startswith("https://") else ""
    mcp_server_url = f"http{secure_prefix}://{sse_url[len(f'http{secure_prefix}://') :]}"

    # Apply server-specific URL modifications via strategy pattern
    strategy = get_server_strategy({"type": transport_type})
    mcp_server_url = strategy.modify_url(mcp_server_url)

    logger.info(f"Connecting to SSE server: {mcp_server_url}")

    requires_init = False
    logger.info("SSE transport: always stateful (requiresInit=True)")

    # Import httpx for custom client and monkey patching

    try:
        # Monkey patch httpx to fix mount path issues (legacy SSE support)
        original_request = httpx.AsyncClient.request

        async def patched_request(self, method, url, **kwargs):
            if isinstance(url, str) and mcp_config.ENDPOINT_MESSAGES in url:
                url = normalize_sse_endpoint_url_for_request(url)
            elif hasattr(url, "__str__") and mcp_config.ENDPOINT_MESSAGES in str(url):
                url = normalize_sse_endpoint_url_for_request(str(url))
            return await original_request(self, method, url, **kwargs)

        httpx.AsyncClient.request = patched_request

        try:
            # Create custom httpx client with headers
            async with httpx.AsyncClient(headers=headers, timeout=30.0) as http_client:
                async with sse_client(mcp_server_url, http_client=http_client) as (read, write):
                    async with ClientSession(read, write, sampling_callback=None) as session:
                        init_result = await asyncio.wait_for(session.initialize(), timeout=mcp_config.INIT_TIMEOUT)
                        tools_response = await asyncio.wait_for(session.list_tools(), timeout=mcp_config.TOOLS_TIMEOUT)

                        # Extract capabilities if requested
                        capabilities = None
                        if include_capabilities:
                            capabilities = _extract_capabilities(init_result)

                            # If capabilities required but not retrieved, consider it a failed server
                            if not capabilities:
                                logger.error(
                                    f"Failed to retrieve capabilities from {mcp_server_url} - server considered failed"
                                )
                                return MCPServerData(None, None, None, None, "Failed to retrieve capabilities")

                            logger.info(f"Successfully retrieved capabilities from {mcp_server_url}: {capabilities}")

                        # Extract tool details
                        tool_list = _extract_tool_details(tools_response)

                        # Extract resources if requested
                        resource_list = []
                        if include_resources:
                            try:
                                resources_response = await asyncio.wait_for(
                                    session.list_resources(), timeout=mcp_config.TOOLS_TIMEOUT
                                )
                                resource_list = _extract_resource_details(resources_response)
                            except Exception as e:
                                logger.warning(f"Failed to retrieve resources from {mcp_server_url}: {e}")
                                resource_list = []

                        # Extract prompts if requested
                        prompt_list = []
                        if include_prompts:
                            try:
                                prompts_response = await asyncio.wait_for(
                                    session.list_prompts(), timeout=mcp_config.TOOLS_TIMEOUT
                                )
                                prompt_list = _extract_prompt_details(prompts_response)
                            except Exception as e:
                                logger.warning(f"Failed to retrieve prompts from {mcp_server_url}: {e}")
                                prompt_list = []

                        return MCPServerData(
                            tools=tool_list,
                            resources=resource_list,
                            prompts=prompt_list,
                            capabilities=capabilities,
                            requires_init=requires_init,
                        )
        finally:
            httpx.AsyncClient.request = original_request

    except TimeoutError:
        logger.error(f"Timeout connecting to {mcp_server_url}")
        return MCPServerData(None, None, None, None, "Timeout connecting to server")
    except Exception as e:
        logger.error(f"Failed to connect to {mcp_server_url}: {type(e).__name__} - {e}")
        return MCPServerData(None, None, None, None, f"Connection failed: {type(e).__name__} - {e}")


async def _get_tools_sse(
    base_url: str, headers: dict[str, str] = None, transport_type: str = "sse"
) -> list[dict] | None:
    """
    Get tools using SSE transport (legacy method, without capabilities, resources, or prompts).
    Wraps the consolidated method for backward compatibility.
    """
    result = await _get_from_sse(
        base_url, headers, transport_type, include_capabilities=False, include_resources=False, include_prompts=False
    )
    return result.tools


def _extract_capabilities(init_result: Any) -> dict | None:
    """
    Extract capabilities from MCP initialize result.

    Args:
        init_result: The result from session.initialize()

    Returns:
        Capabilities dictionary or None if not found
    """
    capabilities = {}

    if hasattr(init_result, "capabilities"):
        capabilities_obj = init_result.capabilities
        # Convert to dict if it's a Pydantic model or similar
        if hasattr(capabilities_obj, "model_dump"):
            capabilities = capabilities_obj.model_dump()
        elif hasattr(capabilities_obj, "__dict__"):
            capabilities = capabilities_obj.__dict__
        else:
            capabilities = capabilities_obj
    elif isinstance(init_result, dict) and "capabilities" in init_result:
        capabilities = init_result["capabilities"]

    return capabilities if capabilities else None


def _extract_tool_details(tools_response) -> list[dict]:
    """Extract tool details from MCP tools response"""
    tool_details_list = []

    if tools_response and hasattr(tools_response, "tools"):
        for tool in tools_response.tools:
            tool_name = getattr(tool, "name", "Unknown Name")
            tool_desc = getattr(tool, "description", None) or getattr(tool, "__doc__", None)

            # Parse docstring into sections
            parsed_desc = {
                "main": "No description available.",
                "args": None,
                "returns": None,
                "raises": None,
            }
            if tool_desc:
                tool_desc = tool_desc.strip()
                lines = tool_desc.split("\n")
                main_desc_lines = []
                current_section = "main"
                section_content = []

                for line in lines:
                    stripped_line = line.strip()
                    if stripped_line.startswith("Args:"):
                        parsed_desc["main"] = "\n".join(main_desc_lines).strip()
                        current_section = "args"
                        section_content = [stripped_line[len("Args:") :].strip()]
                    elif stripped_line.startswith("Returns:"):
                        if current_section != "main":
                            parsed_desc[current_section] = "\n".join(section_content).strip()
                        else:
                            parsed_desc["main"] = "\n".join(main_desc_lines).strip()
                        current_section = "returns"
                        section_content = [stripped_line[len("Returns:") :].strip()]
                    elif stripped_line.startswith("Raises:"):
                        if current_section != "main":
                            parsed_desc[current_section] = "\n".join(section_content).strip()
                        else:
                            parsed_desc["main"] = "\n".join(main_desc_lines).strip()
                        current_section = "raises"
                        section_content = [stripped_line[len("Raises:") :].strip()]
                    elif current_section == "main":
                        main_desc_lines.append(line.strip())
                    else:
                        section_content.append(line.strip())

                # Add the last collected section
                if current_section != "main":
                    parsed_desc[current_section] = "\n".join(section_content).strip()
                elif not parsed_desc["main"] and main_desc_lines:
                    parsed_desc["main"] = "\n".join(main_desc_lines).strip()

                # Ensure main description has content
                if not parsed_desc["main"] and (parsed_desc["args"] or parsed_desc["returns"] or parsed_desc["raises"]):
                    parsed_desc["main"] = "(No primary description provided)"
            else:
                parsed_desc["main"] = "No description available."

            # Get inputSchema - properly handle conversion to dict
            tool_schema = getattr(tool, "inputSchema", {})

            # Convert Pydantic model to dict if necessary
            tool_schema = _convert_pydantic_to_dict(tool_schema)

            # Use simple description (not parsed) for standard MCP format
            simple_desc = tool_desc if tool_desc else "No description available."

            tool_details_list.append(
                {
                    "name": tool_name,
                    "description": simple_desc,
                    "inputSchema": tool_schema,  # Changed from "schema" to "inputSchema"
                    "parsed_description": parsed_desc,
                }
            )

    tool_names = [tool["name"] for tool in tool_details_list]
    logger.info(f"Successfully retrieved details for {len(tool_details_list)} tools: {', '.join(tool_names)}")
    return tool_details_list


def _extract_resource_details(resources_response) -> list[dict]:
    """Extract resource details from MCP resources response"""
    resource_details_list = []

    if resources_response and hasattr(resources_response, "resources"):
        for resource in resources_response.resources:
            resource_uri = getattr(resource, "uri", "Unknown URI")
            resource_name = getattr(resource, "name", None)
            resource_desc = getattr(resource, "description", None)
            resource_mime = getattr(resource, "mimeType", None)

            # Get annotations if present
            annotations = getattr(resource, "annotations", None)
            if annotations:
                annotations = _convert_pydantic_to_dict(annotations)

            resource_details_list.append(
                {
                    "uri": resource_uri,
                    "name": resource_name,
                    "description": resource_desc,
                    "mimeType": resource_mime,
                    "annotations": annotations,
                }
            )

    resource_uris = [r["uri"] for r in resource_details_list]
    logger.info(
        f"Successfully retrieved details for {len(resource_details_list)} resources: {', '.join(resource_uris)}"
    )
    return resource_details_list


def _extract_prompt_details(prompts_response) -> list[dict]:
    """Extract prompt details from MCP prompts response"""
    prompt_details_list = []

    if prompts_response and hasattr(prompts_response, "prompts"):
        for prompt in prompts_response.prompts:
            prompt_name = getattr(prompt, "name", "Unknown Name")
            prompt_desc = getattr(prompt, "description", None)

            # Get arguments if present
            arguments = getattr(prompt, "arguments", None)
            if arguments:
                # Convert each argument to dict
                arguments = [_convert_pydantic_to_dict(arg) for arg in arguments]

            prompt_details_list.append({"name": prompt_name, "description": prompt_desc, "arguments": arguments or []})

    prompt_names = [p["name"] for p in prompt_details_list]
    logger.info(f"Successfully retrieved details for {len(prompt_details_list)} prompts: {', '.join(prompt_names)}")
    return prompt_details_list


async def get_tools_from_server_with_server_info(base_url: str, server_info: dict = None) -> list[dict] | None:
    """
    Get tools from server using server configuration to determine optimal transport.

    Args:
        base_url: The base URL of the MCP server (e.g., http://localhost:8000).
        server_info: Optional server configuration dict containing supported_transports

    Returns:
        A list of tool detail dictionaries (keys: name, description, schema),
        or None if connection/retrieval fails.
    """

    if not base_url:
        logger.error("MCP Check Error: Base URL is empty.")
        return None

    # Use transport-aware detection
    transport = await detect_server_transport_aware(base_url, server_info)

    logger.info(f"Attempting to connect to MCP server at {base_url} using {transport} transport (server-info aware)...")

    try:
        if transport == "streamable-http":
            return await _get_tools_streamable_http(base_url, server_info)
        elif transport == "sse":
            return await _get_tools_sse(base_url, server_info)
        else:
            logger.error(f"Unsupported transport type: {transport}")
            return None

    except Exception as e:
        logger.error(
            f"MCP Check Error: Failed to get tool list from {base_url} with {transport}: {type(e).__name__} - {e}"
        )
        return None


async def get_tools_and_capabilities_from_server(
    base_url: str,
    headers: dict[str, str] = None,
    transport_type: str = None,
    include_resources: bool = True,
    include_prompts: bool = True,
) -> MCPServerData:
    """
    Get tools, resources, prompts, and capabilities from server.

    Pure transport layer - accepts pre-built headers and transport type.

    Args:
        base_url: The base URL of the MCP server (e.g., http://localhost:8000)
        headers: Pre-built HTTP headers (including authentication)
        transport_type: Transport type ("streamable-http" or "sse"), auto-detected if None
        include_resources: Whether to retrieve resources (default: True)
        include_prompts: Whether to retrieve prompts (default: True)

    Returns:
        MCPServerData containing:
        - tools: List of tool dictionaries or None if failed
        - resources: List of resource dictionaries or None if failed
        - prompts: List of prompt dictionaries or None if failed
        - capabilities: Server capabilities dictionary or None if failed
        - error_message: Error message if operation failed
    """

    if not base_url:
        logger.error("MCP Check Error: Base URL is empty.")
        return MCPServerData(None, None, None, None, "Base URL is empty")

    # Auto-detect transport if not provided
    if transport_type is None:
        transport_type = await detect_server_transport(base_url)

    logger.info(f"Attempting to connect to MCP server at {base_url} using {transport_type} transport...")

    try:
        if transport_type == mcp_config.TRANSPORT_HTTP or transport_type == "streamable-http":
            return await _get_from_streamable_http(
                base_url,
                headers,
                transport_type,
                include_capabilities=True,
                include_resources=include_resources,
                include_prompts=include_prompts,
            )
        elif transport_type == mcp_config.TRANSPORT_SSE or transport_type == "sse":
            return await _get_from_sse(
                base_url,
                headers,
                transport_type,
                include_capabilities=True,
                include_resources=include_resources,
                include_prompts=include_prompts,
            )
        else:
            logger.error(f"Unsupported transport type: {transport_type}")
            return MCPServerData(None, None, None, None, f"Unsupported transport type: {transport_type}")

    except Exception as e:
        logger.error(
            f"MCP Check Error: Failed to get tools, resources, prompts, and capabilities from {base_url} with {transport_type}: {type(e).__name__} - {e}"
        )
        return MCPServerData(None, None, None, None, f"Failed to get server data: {type(e).__name__} - {e}")


async def get_oauth_metadata_from_server(base_url: str) -> dict | None:
    """
    Get OAuth metadata from MCP server's well-known endpoint using RFC 8414 discovery.

    Manually fetches OAuth server metadata from well-known endpoints:
    - /.well-known/oauth-authorization-server (RFC 8414)
    - /.well-known/openid-configuration (OIDC Discovery)

    Uses Authlib's AuthorizationServerMetadata to parse and validate the response.

    Args:
        base_url: The base URL of the MCP server (e.g., http://localhost:8000).
        server_info: Optional server configuration dict (unused, kept for compatibility)

    Returns:
        OAuth metadata dictionary or None if failed/not available
    """
    if not base_url:
        logger.error("OAuth metadata retrieval: Base URL is empty.")
        return None

    # Remove trailing slashes and path segments to get the base domain
    parsed = urlparse(base_url.rstrip("/"))

    # Validate URL: require http/https scheme and non-empty netloc
    if parsed.scheme not in ["http", "https"]:
        logger.error(f"OAuth metadata retrieval: Invalid URL scheme '{parsed.scheme}' (only http/https allowed)")
        return None

    if not parsed.netloc:
        logger.error(f"OAuth metadata retrieval: Invalid URL - missing host/netloc in '{base_url}'")
        return None

    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    logger.info(f"Attempting OAuth metadata discovery for {base_domain}")

    # Well-known endpoints to try (RFC 8414 and OIDC Discovery)
    well_known_urls = [
        f"{base_domain}/.well-known/oauth-authorization-server",
        f"{base_domain}/.well-known/openid-configuration",
    ]

    try:
        # Create httpx client with timeout and headers for better compatibility
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": settings.registry_app_name,
                "Accept": "application/json",
            },
            follow_redirects=True,
        ) as http_client:
            # Try each well-known endpoint
            for url in well_known_urls:
                try:
                    logger.debug(f"Trying OAuth discovery endpoint: {url}")
                    response = await http_client.get(url)

                    if response.status_code == 200:
                        metadata_dict = response.json()

                        # Validate using Authlib's RFC 8414 implementation
                        try:
                            metadata = AuthorizationServerMetadata(metadata_dict)
                            metadata.validate()
                            logger.info(f"Successfully discovered and validated OAuth metadata from {url}")
                            logger.debug(f"OAuth metadata: {metadata_dict}")
                            return metadata_dict
                        except Exception as validation_error:
                            logger.warning(
                                f"OAuth metadata validation failed for {url}: {type(validation_error).__name__} - {validation_error}"
                            )
                            # Return anyway if validation fails (some servers may not be fully RFC 8414 compliant)
                            logger.info(f"Returning unvalidated OAuth metadata from {url}")
                            return metadata_dict
                    else:
                        logger.debug(f"Discovery endpoint {url} returned status {response.status_code}")

                except Exception as e:
                    logger.debug(f"Discovery failed for {url}: {type(e).__name__} - {e}")

            # All endpoints failed
            logger.info(
                f"No OAuth metadata found for {base_domain} (this is normal for servers without OAuth autodiscovery)"
            )
            return None

    except Exception as e:
        # Discovery failure is normal for servers without OAuth support
        logger.debug(f"OAuth discovery failed for {base_domain}: {type(e).__name__} - {e}")
        logger.info(
            f"No OAuth metadata found for {base_domain} (this is normal for servers without OAuth autodiscovery)"
        )
        return None


class MCPClientService:
    """Service wrapper for the MCP client function to maintain compatibility."""

    async def get_tools_from_server_with_server_info(
        self, base_url: str, server_info: dict = None
    ) -> list[dict] | None:
        """Wrapper method that uses server configuration for transport selection."""
        return await get_tools_from_server_with_server_info(base_url, server_info)


# Global MCP client service instance
mcp_client_service = MCPClientService()
