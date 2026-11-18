"""
MCP Client Service

Handles connections to MCP servers and tool list retrieval.
Refactored with centralized configuration and strategy pattern.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import httpx
import httpx2
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from registry_pkgs.oauth.discovery import discover_mcp_oauth
from registry_pkgs.oauth.errors import OAuthDiscoveryError

from .config import settings

# Internal imports
from .mcp_config import MCPClientConfig, MCPTransport
from .mcp_downstream import DownstreamEraCache
from .mcp_transport import ResponseRecorder, create_sse_client_factory
from .server_strategies import get_server_strategy

logger = logging.getLogger(__name__)


def _flatten_exception_group_messages(error: BaseException) -> list[str]:
    """
    Flatten nested ExceptionGroup instances into readable leaf error messages.
    """
    if isinstance(error, ExceptionGroup):
        flattened: list[str] = []
        for nested in error.exceptions:
            flattened.extend(_flatten_exception_group_messages(nested))
        return flattened
    return [f"{type(error).__name__}: {error}"]


def _format_exception_group(error: ExceptionGroup) -> str:
    details = _flatten_exception_group_messages(error)
    if not details:
        return "ExceptionGroup with no leaf exceptions"
    return " | ".join(details)


@dataclass
class _InitResult:
    """Snapshot of the v2 Client's post-handshake attributes.

    The ``mcp.Client`` is a context manager that closes when the transport exits, so we copy the
    handshake results out before the ``async with`` ends. Field names are snake_case to match the v2
    SDK; callers read ``protocol_version``, ``server_info`` and ``capabilities``.
    """

    protocol_version: str
    server_info: Any
    capabilities: Any


async def initialize_mcp(
    target_url: str,
    headers: dict[str, str] = None,
    transport_type: MCPTransport = MCPTransport.STREAMABLE_HTTP,
    *,
    server_id: str | None = None,
    era_cache: DownstreamEraCache | None = None,
) -> tuple[_InitResult | None, int | None]:
    """
    Perform MCP initialization using the v2 ``mcp.Client`` and return the handshake result.

    This is a pure transport layer function that performs the MCP initialize handshake. It returns a
    ``(_InitResult | None, status_code | None)`` tuple: the init snapshot (or None on failure) and the
    latest RPC response's status code recorded off the wire, since v2 hides the HTTP status inside
    ``MCPError``. The caller interprets both (e.g. for health checks).

    Args:
        target_url: MCP server URL
        headers: HTTP headers (optional, defaults to standard MCP headers)
        transport_type: Transport type ("streamable-http" or "sse")
    """

    # Use provided headers or default MCP headers
    if headers is None:
        headers = MCPClientConfig.DEFAULT_HEADERS.copy()

    transport_type = MCPTransport(transport_type)

    # Prepare URL based on transport type
    strategy = get_server_strategy({"type": transport_type})
    mcp_url = strategy.modify_url(target_url)

    logger.info(f"Initializing MCP connection to {mcp_url} using {transport_type} transport")

    recorder = ResponseRecorder()
    try:
        if transport_type == MCPTransport.STREAMABLE_HTTP:
            async with httpx2.AsyncClient(
                headers=headers, timeout=30.0, event_hooks={"request": [recorder.on_request], "response": [recorder]}
            ) as http_client:
                async with _connect_client(streamable_http_client(url=mcp_url, http_client=http_client)) as session:
                    logger.info(f"MCP initialization successful for {target_url}")
                    if era_cache:
                        await era_cache.record(server_id, session.protocol_version)
                    return _snapshot_init(session), recorder.status_code

        elif transport_type == MCPTransport.SSE:
            async with _connect_client(
                sse_client(
                    mcp_url,
                    headers=headers,
                    httpx_client_factory=create_sse_client_factory(recorder),
                )
            ) as session:
                logger.info(f"MCP initialization successful for {target_url}")
                if era_cache:
                    await era_cache.record(server_id, session.protocol_version)
                return _snapshot_init(session), recorder.status_code
        else:
            logger.error(f"Unsupported transport type: {transport_type}")
            return None, None

    except TimeoutError:
        logger.error(
            f"MCP initialization timed out for {target_url} after {MCPClientConfig.INIT_TIMEOUT}s "
            f"(latest RPC HTTP status: {recorder.status_code})"
        )
        return None, recorder.status_code
    except Exception as e:
        status_code = recorder.status_code
        # v2 transports surface failures wrapped in an ExceptionGroup; log the leaf causes instead.
        detail = _format_exception_group(e) if isinstance(e, ExceptionGroup) else f"{type(e).__name__}: {e}"
        if status_code is not None and status_code >= HTTPStatus.BAD_REQUEST:
            # An HTTP error status is an outcome the caller interprets (e.g. 401 → auth required).
            logger.debug(f"HTTP error {status_code} during MCP initialization for {target_url}: {detail}")
        else:
            logger.error(f"MCP initialization failed for {target_url}: {detail}")
        return None, status_code


def _snapshot_init(session: Client) -> _InitResult:
    return _InitResult(
        protocol_version=session.protocol_version,
        server_info=session.server_info,
        capabilities=session.server_capabilities,
    )


@asynccontextmanager
async def _connect_client(transport: Any) -> AsyncIterator[Client]:
    """Enter a ``mode="auto"`` ``mcp.Client`` with the handshake bounded by ``INIT_TIMEOUT``.

    ``Client.__aenter__`` runs the version negotiation (``server/discover`` and/or ``initialize``) with no
    session-level timeout (``read_timeout_seconds`` is None), so a server that never answers is limited
    only by the HTTP read timeout: 300 s per event on SSE, and unbounded when the server sends keepalives. Only the handshake is bounded here; later
    requests keep their own ``TOOLS_TIMEOUT``. Raises ``TimeoutError`` when the handshake overruns.
    """
    async with AsyncExitStack() as stack:
        async with asyncio.timeout(MCPClientConfig.INIT_TIMEOUT):
            client = await stack.enter_async_context(Client(transport, mode="auto"))
        yield client


async def perform_health_check(
    url: str,
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP,
    *,
    server_id: str | None = None,
    era_cache: DownstreamEraCache | None = None,
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

    transport = MCPTransport(transport)

    # Skip health checks for stdio transport
    if transport == MCPClientConfig.TRANSPORT_STDIO:
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

    # Attempt MCP initialization. v2 hides the HTTP status inside MCPError, so initialize_mcp records
    # the latest RPC response's status off the wire and returns it alongside the init snapshot.
    init_result, http_status_code = await initialize_mcp(
        target_url=url,
        headers=headers,
        transport_type=transport,
        **({"server_id": server_id, "era_cache": era_cache} if era_cache else {}),
    )

    # Calculate response time
    end_time = datetime.now(UTC)
    response_time_ms = int((end_time - start_time).total_seconds() * 1000)

    # Application logic: Interpret the initialization result

    # Check for 401/403 Unauthorized/Forbidden - server is healthy but requires auth
    if http_status_code in MCPClientConfig.AUTH_REQUIRED_STATUS_CODES:
        logger.info(
            f"Health check passed for {url}: Connected successfully, initialize requires authentication ({http_status_code})"
        )
        return True, "connected (initialize requires authentication)", response_time_ms, None

    # 404/405 on the initialize endpoint usually means the URL or transport is misconfigured:
    if http_status_code in (HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED):
        logger.warning(
            f"Health check failed for {url}: endpoint returned {http_status_code} for '{transport}' transport "
            "— likely a config.url/config.type mismatch"
        )
        return (
            False,
            f"unhealthy: endpoint returned {int(http_status_code)} for '{transport}' transport — verify config.url "
            "and config.type (a Streamable HTTP endpoint usually ends in /mcp, a legacy SSE endpoint in /sse)",
            response_time_ms,
            None,
        )

    # Validate init_result structure per MCP spec
    if init_result is None:
        logger.warning(f"Health check failed for {url}: initialization failed")
        return False, "unhealthy: initialization failed", response_time_ms, None

    # Check for required fields: protocol_version and server_info
    if hasattr(init_result, "protocol_version") and hasattr(init_result, "server_info"):
        remote_server_name = init_result.server_info.name if hasattr(init_result.server_info, "name") else "unknown"

        # Convert capabilities to dict if present (normalize Pydantic models to dicts)
        if hasattr(init_result, "capabilities"):
            init_result.capabilities = _convert_pydantic_to_dict(init_result.capabilities)

        logger.info(
            f"Health check passed for {url}: Initialize handshake successful - {remote_server_name} (protocol: {init_result.protocol_version})"
        )
        return True, "initialize handshake successful", response_time_ms, init_result
    else:
        logger.warning(
            f"Health check failed for {url}: invalid initialize response (missing protocolVersion or serverInfo)"
        )
        return False, "unhealthy: invalid initialize response", response_time_ms, None


@dataclass
class MCPServerData:
    """MCP server data container for tools, resources, prompts, and capabilities."""

    tools: list[dict[str, Any]] | None
    resources: list[dict[str, Any]] | None
    prompts: list[dict[str, Any]] | None
    capabilities: dict[str, Any] | None
    error_message: str | None = None
    requires_init: bool | None = None


def _convert_pydantic_to_dict(obj: Any) -> dict:
    """
    Convert Pydantic model or object to dict.

    Args:
        obj: The object to convert (Pydantic model, object with __dict__, or regular dict)

    Returns:
        Dictionary representation of the object
    """
    if hasattr(obj, "model_dump"):
        # by_alias keeps the stored shape in v1 camelCase (e.g. listChanged) under v2's snake_case models.
        return obj.model_dump(by_alias=True, mode="json")
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


async def detect_server_transport(base_url: str) -> MCPTransport:
    """
    Detect which transport a server supports by testing endpoints.
    Returns the preferred transport type.
    """
    # If URL already has a transport endpoint, detect from it
    if base_url.endswith(MCPClientConfig.ENDPOINT_SSE) or MCPClientConfig.ENDPOINT_SSE in base_url:
        logger.debug(f"Server URL {base_url} already has SSE endpoint")
        return MCPClientConfig.TRANSPORT_SSE
    elif base_url.endswith(MCPClientConfig.ENDPOINT_MCP) or MCPClientConfig.ENDPOINT_MCP in base_url:
        logger.debug(f"Server URL {base_url} already has MCP endpoint")
        return MCPClientConfig.TRANSPORT_HTTP

    # Test streamable-http first (default preference). v2 transports need an explicit httpx2 client.
    try:
        mcp_url = base_url.rstrip("/") + MCPClientConfig.ENDPOINT_MCP + "/"
        async with httpx2.AsyncClient(timeout=30.0) as http_client:
            async with streamable_http_client(url=mcp_url, http_client=http_client):
                logger.debug(f"Server at {base_url} supports streamable-http transport")
                return MCPClientConfig.TRANSPORT_HTTP
    except Exception as e:
        logger.debug(f"Streamable-HTTP test failed for {base_url}: {e}")

    # Fallback to SSE
    try:
        sse_url = base_url.rstrip("/") + MCPClientConfig.ENDPOINT_SSE
        async with sse_client(sse_url, timeout=30.0):
            logger.debug(f"Server at {base_url} supports SSE transport")
            return MCPClientConfig.TRANSPORT_SSE
    except Exception as e:
        logger.debug(f"SSE test failed for {base_url}: {e}")

    # Default to streamable-http if detection fails
    logger.warning(f"Could not detect transport for {base_url}, defaulting to streamable-http")
    return MCPClientConfig.TRANSPORT_HTTP


def _is_requires_init(session_id: str | None) -> bool:
    """Whether the server issued a session id (so it is stateful and requires init).

    v2's ``streamable_http_client`` no longer yields ``get_session_id``; the session id is recorded off
    the response headers by :class:`ResponseRecorder` instead.
    """
    requires_init = session_id is not None
    logger.info(f"streamable-http: session_id={'present' if session_id else 'absent'}, requiresInit={requires_init}")
    return requires_init


async def _get_from_streamable_http(
    base_url: str,
    headers: dict[str, str] = None,
    transport_type: MCPTransport = MCPTransport.STREAMABLE_HTTP,
    include_capabilities: bool = True,
    include_resources: bool = True,
    include_prompts: bool = True,
    httpx_auth: Any = None,
    server_id: str | None = None,
    era_cache: DownstreamEraCache | None = None,
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
        headers = MCPClientConfig.DEFAULT_HEADERS.copy()

    # Use the URL as provided - it should contain everything needed
    mcp_url = base_url

    # Apply server-specific URL modifications via strategy pattern
    strategy = get_server_strategy({"type": transport_type})
    mcp_url = strategy.modify_url(mcp_url)

    logger.info(f"Connecting to MCP server: {mcp_url}")

    # Import httpx for custom client

    try:
        # Create custom httpx2 client with headers; the recorder captures the session id off the wire
        # since v2's streamable_http_client no longer yields get_session_id.
        recorder = ResponseRecorder()
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=30.0,
            auth=httpx_auth,
            event_hooks={"request": [recorder.on_request], "response": [recorder]},
        ) as http_client:
            async with _connect_client(streamable_http_client(url=mcp_url, http_client=http_client)) as session:
                if era_cache:
                    await era_cache.record(server_id, session.protocol_version)
                init_result = _snapshot_init(session)
                tools_response = await asyncio.wait_for(session.list_tools(), timeout=MCPClientConfig.TOOLS_TIMEOUT)

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
                            session.list_resources(), timeout=MCPClientConfig.TOOLS_TIMEOUT
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
                            session.list_prompts(), timeout=MCPClientConfig.TOOLS_TIMEOUT
                        )
                        prompt_list = _extract_prompt_details(prompts_response)
                    except Exception as e:
                        logger.warning(f"Failed to retrieve prompts from {mcp_url}: {e}")
                        prompt_list = []

                requires_init = _is_requires_init(recorder.session_id)
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
    except ExceptionGroup as eg:
        detailed_error = _format_exception_group(eg)
        logger.error(f"Failed to connect to {mcp_url}: ExceptionGroup - {detailed_error}")
        return MCPServerData(None, None, None, None, f"Connection failed: {detailed_error}")
    except Exception as e:
        logger.error(f"Failed to connect to {mcp_url}: {type(e).__name__} - {e}")
        return MCPServerData(None, None, None, None, f"Connection failed: {type(e).__name__} - {e}")


async def _get_from_sse(
    base_url: str,
    headers: dict[str, str] = None,
    transport_type: MCPTransport = MCPTransport.SSE,
    include_capabilities: bool = True,
    include_resources: bool = True,
    include_prompts: bool = True,
    httpx_auth: Any = None,
    server_id: str | None = None,
    era_cache: DownstreamEraCache | None = None,
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
        headers = MCPClientConfig.DEFAULT_HEADERS.copy()

    # Use the URL as provided - it should contain everything needed
    sse_url = base_url

    secure_prefix = "s" if sse_url.startswith("https://") else ""
    mcp_server_url = f"http{secure_prefix}://{sse_url[len(f'http{secure_prefix}://') :]}"

    # Apply server-specific URL modifications via strategy pattern
    strategy = get_server_strategy({"type": transport_type})
    mcp_server_url = strategy.modify_url(mcp_server_url)

    logger.info(f"Connecting to SSE server: {mcp_server_url}")

    requires_init = True
    logger.info("SSE transport: always stateful (requiresInit=True)")

    try:
        # v2's sse_client uses httpx2, so the old global monkey-patch no longer applies. The client
        # factory attaches a per-client request hook that rewrites the mount-path POST-back URL, and
        # auth is honoured (passed through instead of logged as ignored).
        async with _connect_client(
            sse_client(
                mcp_server_url,
                headers=headers,
                auth=httpx_auth,
                httpx_client_factory=create_sse_client_factory(),
            )
        ) as session:
            if era_cache:
                await era_cache.record(server_id, session.protocol_version)
            init_result = _snapshot_init(session)
            tools_response = await asyncio.wait_for(session.list_tools(), timeout=MCPClientConfig.TOOLS_TIMEOUT)

            # Extract capabilities if requested
            capabilities = None
            if include_capabilities:
                capabilities = _extract_capabilities(init_result)

                # If capabilities required but not retrieved, consider it a failed server
                if not capabilities:
                    logger.error(f"Failed to retrieve capabilities from {mcp_server_url} - server considered failed")
                    return MCPServerData(None, None, None, None, "Failed to retrieve capabilities")

                logger.info(f"Successfully retrieved capabilities from {mcp_server_url}: {capabilities}")

            # Extract tool details
            tool_list = _extract_tool_details(tools_response)

            # Extract resources if requested
            resource_list = []
            if include_resources:
                try:
                    resources_response = await asyncio.wait_for(
                        session.list_resources(), timeout=MCPClientConfig.TOOLS_TIMEOUT
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
                        session.list_prompts(), timeout=MCPClientConfig.TOOLS_TIMEOUT
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

    except TimeoutError:
        logger.error(f"Timeout connecting to {mcp_server_url}")
        return MCPServerData(None, None, None, None, "Timeout connecting to server")
    except ExceptionGroup as eg:
        detailed_error = _format_exception_group(eg)
        logger.error(f"Failed to connect to {mcp_server_url}: ExceptionGroup - {detailed_error}")
        return MCPServerData(None, None, None, None, f"Connection failed: {detailed_error}")
    except Exception as e:
        logger.error(f"Failed to connect to {mcp_server_url}: {type(e).__name__} - {e}")
        return MCPServerData(None, None, None, None, f"Connection failed: {type(e).__name__} - {e}")


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
            # by_alias keeps camelCase capability keys (listChanged) under v2's snake_case models.
            capabilities = capabilities_obj.model_dump(by_alias=True, mode="json")
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

            # Get input_schema (snake_case in v2) - properly handle conversion to dict
            tool_schema = tool.input_schema

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
            resource_mime = resource.mime_type

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

    resource_uris = [str(r["uri"]) for r in resource_details_list]
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


async def get_tools_and_capabilities_from_server(
    base_url: str,
    headers: dict[str, str] = None,
    transport_type: MCPTransport | None = None,
    include_resources: bool = True,
    include_prompts: bool = True,
    httpx_auth: Any = None,
    server_id: str | None = None,
    era_cache: DownstreamEraCache | None = None,
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
    else:
        transport_type = MCPTransport(transport_type)

    logger.info(f"Attempting to connect to MCP server at {base_url} using {transport_type} transport...")

    try:
        if transport_type == MCPTransport.STREAMABLE_HTTP:
            return await _get_from_streamable_http(
                base_url,
                headers,
                transport_type,
                include_capabilities=True,
                include_resources=include_resources,
                include_prompts=include_prompts,
                httpx_auth=httpx_auth,
                **({"server_id": server_id, "era_cache": era_cache} if era_cache else {}),
            )
        elif transport_type == MCPTransport.SSE:
            return await _get_from_sse(
                base_url,
                headers,
                transport_type,
                include_capabilities=True,
                include_resources=include_resources,
                include_prompts=include_prompts,
                httpx_auth=httpx_auth,
                **({"server_id": server_id, "era_cache": era_cache} if era_cache else {}),
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
    Preview a server's OAuth authorization-server metadata (used by ``GET …/oauth/discover``).

    A thin wrapper over the spec-compliant ``discover_mcp_oauth`` (RFC 9728 protected-resource
    metadata, then RFC 8414 authorization-server metadata). Read-only: nothing is persisted; the
    login flow runs discovery itself.

    Args:
        base_url: The MCP server URL (config.url).

    Returns:
        The authorization-server metadata dict, plus ``resource`` when the server publishes
        protected-resource metadata, or None when discovery fails.
    """
    if not base_url:
        logger.error("OAuth metadata retrieval: Base URL is empty.")
        return None

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.registry_app_name}, follow_redirects=True
        ) as http_client:
            result = await discover_mcp_oauth(base_url, http_client=http_client)
    except OAuthDiscoveryError as e:
        logger.info(f"No OAuth metadata discovered for {base_url}: {e}")
        return None

    metadata = dict(result.authorization_server_metadata)
    if result.resource:
        metadata["resource"] = result.resource
    return metadata
