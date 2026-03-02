"""
Dynamic MCP server proxy routes.
"""

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from registry.auth.dependencies import CurrentUser, effective_scopes_from_context
from registry.core.mcp_client import clear_session, get_session, initialize_mcp_session
from registry.core.telemetry_decorators import (
    track_prompt_execution,
    track_resource_access,
    track_tool_execution,
)
from registry.schemas.errors import AuthenticationError, MissingUserIdError, OAuthReAuthRequiredError, OAuthTokenError
from registry.schemas.proxy_tool_schema import (
    PromptExecutionRequest,
    PromptExecutionResponse,
    ResourceReadRequest,
    ResourceReadResponse,
    ToolExecutionRequest,
    ToolExecutionResponse,
)
from registry.services.server_service import _build_complete_headers_for_server, server_service_v1
from registry.utils.otel_metrics import record_server_request
from registry_pkgs.models.extended_mcp_server import MCPServerDocument

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Proxy"])
MCPGW_PATH = "/mcpgw"

# Shared httpx client for connection pooling
proxy_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, read=60.0),
    follow_redirects=True,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
)


async def _build_authenticated_headers(
    server: MCPServerDocument, auth_context: dict[str, Any], additional_headers: dict[str, str] | None = None
) -> dict[str, str]:
    """
    Build complete headers with authentication for MCP server requests.
    Consolidates auth logic used by all proxy endpoints.

    Supports dual authentication:
    - setting.auth_egress_header: OAuth/external access token (RFC 6750) for MCP server resource access
    - setting.internal_auth_header: Internal JWT for gateway-to-MCP authentication (always included)

    Args:
        server: MCP server document
        auth_context: Gateway authentication context (user, client_id, scopes, jwt_token)
        additional_headers: Optional additional headers to merge

    Returns:
        Complete headers dict with authentication

    Raises:
        HTTPException: For auth errors (401 with appropriate details)
    """
    # Validate user_id is present (auth-server always includes it in JWT)
    if not auth_context.get("user_id"):
        logger.error(f"Missing user_id in auth_context. Available keys: {list(auth_context.keys())}")
        raise HTTPException(status_code=401, detail="Invalid authentication context: missing user_id")

    # Build base headers (filter out empty values to avoid httpx errors)
    effective_scopes = effective_scopes_from_context(auth_context)
    headers = {
        "X-User-Id": auth_context.get("user_id") or "",
        "X-Username": auth_context.get("username") or "",
        "X-Client-Id": auth_context.get("client_id") or "",
        "X-Scopes": " ".join(effective_scopes),
    }
    # Remove empty header values (httpx requires non-empty strings)
    headers = {k: v for k, v in headers.items() if v}

    # Merge additional headers if provided
    if additional_headers:
        headers.update(additional_headers)

    # Special handling for MCPGW - skip auth header building
    if server.path == MCPGW_PATH:
        return headers

    # Build complete authentication headers (OAuth, apiKey, custom)
    try:
        user_id = auth_context.get("user_id")  # Already validated above
        auth_headers = await _build_complete_headers_for_server(server, user_id)

        # Merge auth headers with case-insensitive override logic
        # Protected headers that won't be overridden by auth headers
        protected_headers = {"x-user-id", "x-username", "x-client-id", "x-scopes", "accept"}

        # Build a case-insensitive map of existing header names to their original keys
        lowercase_header_map = {k.lower(): k for k in headers}

        for auth_key, auth_value in auth_headers.items():
            auth_key_lower = auth_key.lower()
            if auth_key_lower in protected_headers:
                continue

            # Remove any existing header with same name (case-insensitive)
            existing_key = lowercase_header_map.get(auth_key_lower)
            if existing_key is not None:
                headers.pop(existing_key, None)

            # Add/override with the auth header and update the lowercase map
            headers[auth_key] = auth_value
            lowercase_header_map[auth_key_lower] = auth_key

        logger.debug(f"Built complete authentication headers for {server.serverName}")
        return headers

    except OAuthReAuthRequiredError as e:
        raise HTTPException(
            status_code=401, detail="OAuth re-authentication required", headers={"X-OAuth-URL": e.auth_url or ""}
        )
    except MissingUserIdError as e:
        raise HTTPException(status_code=401, detail=f"User authentication required: {str(e)}")
    except (OAuthTokenError, AuthenticationError) as e:
        raise HTTPException(status_code=401, detail=f"Authentication error: {str(e)}")


def _build_target_url(server: MCPServerDocument, remaining_path: str = "") -> str:
    """
    Build complete target URL for proxying to MCP server.
    Consolidates URL building logic used across all proxy endpoints.

    Args:
        server: MCP server document
        remaining_path: Optional path to append after server base URL

    Returns:
        Complete target URL

    Raises:
        HTTPException: If server URL is not configured
    """
    config = server.config or {}
    base_url = config.get("url")

    if not base_url:
        raise HTTPException(status_code=500, detail="Server URL not configured")

    # If no remaining path, return base URL as-is
    if not remaining_path:
        return base_url

    # Ensure base URL has trailing slash before appending path
    if not base_url.endswith("/"):
        base_url += "/"

    return base_url + remaining_path


async def _proxy_json_rpc_request(
    target_url: str, json_body: dict[str, Any], headers: dict[str, str], accept_sse: bool = False
) -> Response:
    """
    Proxy a JSON-RPC request to an MCP server with SSE support.
    Consolidates proxy logic used by tool/resource/prompt endpoints.

    Args:
        target_url: Backend MCP server URL
        json_body: MCP JSON-RPC request body
        headers: Complete request headers (including auth)
        accept_sse: Whether client accepts SSE responses

    Returns:
        FastAPI Response (SSE stream or JSON)

    Raises:
        HTTPException: For timeout or proxy errors
    """
    try:
        # For JSON-RPC, we always POST with JSON content
        if not accept_sse:
            # Regular JSON request (most common case)
            response = await proxy_client.post(target_url, json=json_body, headers=headers)

            # Log error responses for debugging
            if response.status_code >= 400:
                try:
                    error_body = response.content.decode("utf-8")
                    logger.error(f"Backend error response ({response.status_code}): {error_body}")
                except Exception:
                    logger.error(f"Backend error response ({response.status_code}): [binary content]")

            # Forward all headers including mcp-session-id
            response_headers = dict(response.headers)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type"),
            )

        # Client accepts SSE - use streaming to check response type
        stream_context = proxy_client.stream("POST", target_url, json=json_body, headers=headers)
        backend_response = await stream_context.__aenter__()

        backend_content_type = backend_response.headers.get("content-type", "")
        is_sse = "text/event-stream" in backend_content_type

        # If backend doesn't return SSE, read full response and close stream
        if not is_sse:
            content_bytes = await backend_response.aread()
            await stream_context.__aexit__(None, None, None)

            if backend_response.status_code >= 400:
                try:
                    error_body = content_bytes.decode("utf-8")
                    logger.error(f"Backend error response ({backend_response.status_code}): {error_body}")
                except Exception:
                    logger.error(f"Backend error response ({backend_response.status_code}): [binary content]")

            # Forward all headers including mcp-session-id
            response_headers = dict(backend_response.headers)
            return Response(
                content=content_bytes,
                status_code=backend_response.status_code,
                headers=response_headers,
                media_type=backend_content_type or "application/json",
            )

        # Backend returned SSE - stream it without buffering
        logger.info("Streaming SSE response from backend")

        # Start with all backend headers, then override with SSE-specific ones
        response_headers = dict(backend_response.headers)
        response_headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

        # Stream content from backend to client - cleanup when done
        async def stream_sse():
            try:
                async for chunk in backend_response.aiter_bytes():
                    yield chunk
            except Exception as e:
                logger.error(f"SSE streaming error: {e}")
                raise
            finally:
                # Close stream when done streaming
                await stream_context.__aexit__(None, None, None)

        return StreamingResponse(
            stream_sse(),
            status_code=backend_response.status_code,
            media_type="text/event-stream",
            headers=response_headers,
        )

    except httpx.TimeoutException:
        logger.error(f"Timeout proxying to {target_url}")
        raise HTTPException(status_code=504, detail="Gateway timeout")
    except httpx.HTTPError as e:
        logger.error(f"HTTP error proxying to {target_url}: {e}")
        raise HTTPException(status_code=502, detail=f"Bad gateway: {str(e)}")
    except Exception as e:
        logger.error(f"Error proxying to {target_url}: {e}")
        raise HTTPException(status_code=502, detail=f"Bad gateway: {str(e)}")


async def proxy_to_mcp_server(
    request: Request, target_url: str, auth_context: dict[str, Any], server: MCPServerDocument
) -> Response:
    """
    Proxy request to MCP server with auth headers.
    Handles both regular HTTP and SSE streaming, including OAuth token injection.

    Args:
        request: Incoming FastAPI request
        target_url: Backend MCP server URL
        auth_context: Gateway authentication context
        server: MCPServerDocument
    """

    # Build proxy headers - start with request headers
    headers = dict(request.headers)

    # Add context headers for tracing/logging (filter out None values)
    context_headers = {
        "X-Auth-Method": auth_context.get("auth_method") or "",
        "X-Server-Name": auth_context.get("server_name") or "",
        "X-Tool-Name": auth_context.get("tool_name") or "",
        "X-Original-URL": str(request.url),
    }
    # Only add headers with non-empty values
    headers.update({k: v for k, v in context_headers.items() if v})

    # Remove host header to avoid conflicts
    headers.pop("host", None)
    headers.pop("Authorization", None)  # Remove existing Authorization header

    # Build complete authentication headers using shared helper
    # This already handles all header merging with case-insensitive logic
    headers = await _build_authenticated_headers(server=server, auth_context=auth_context, additional_headers=headers)

    body = await request.body()

    try:
        # we can't use server_info.get("type") because sometime httpstreamable is also sse header
        accept_header = request.headers.get("accept", "")
        client_accepts_sse = "text/event-stream" in accept_header

        logger.debug(f"Accept: {accept_header}, Client SSE: {client_accepts_sse}")

        # Optimize: Only use streaming if client accepts SSE
        if not client_accepts_sse:
            # Regular HTTP request (most common case for MCP JSON-RPC)
            response = await proxy_client.request(method=request.method, url=target_url, headers=headers, content=body)

            # Log error responses for debugging
            if response.status_code >= 400:
                try:
                    error_body = response.content.decode("utf-8")
                    logger.error(f"Backend error response ({response.status_code}): {error_body}")
                except Exception:
                    logger.error(
                        f"Backend error response ({response.status_code}): [binary content, {len(response.content)} bytes]"
                    )

            # Forward all headers including mcp-session-id
            response_headers = dict(response.headers)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type"),
            )

        # Client accepts SSE - check if backend returns SSE
        logger.debug("Client accepts SSE - checking backend response type")

        # Manually manage stream lifecycle (can't use async with - it closes too early)
        stream_context = proxy_client.stream(request.method, target_url, headers=headers, content=body)
        backend_response = await stream_context.__aenter__()

        backend_content_type = backend_response.headers.get("content-type", "")
        is_sse = "text/event-stream" in backend_content_type

        logger.debug(f"Backend: status={backend_response.status_code}, content-type={backend_content_type or 'none'}")

        # If backend doesn't return SSE, read full response and close stream
        if not is_sse:
            content_bytes = await backend_response.aread()
            await stream_context.__aexit__(None, None, None)

            # Log error responses for debugging
            if backend_response.status_code >= 400:
                try:
                    error_body = content_bytes.decode("utf-8")
                    logger.error(f"Backend error response ({backend_response.status_code}): {error_body}")
                except Exception:
                    logger.error(
                        f"Backend error response ({backend_response.status_code}): [binary content, {len(content_bytes)} bytes]"
                    )

            # Forward all headers including mcp-session-id
            response_headers = dict(backend_response.headers)
            return Response(
                content=content_bytes,
                status_code=backend_response.status_code,
                headers=response_headers,
                media_type=backend_content_type or "application/octet-stream",
            )

        # Backend is returning true SSE - keep stream open and forward it
        logger.info("Streaming SSE from backend")

        # Start with all backend headers, then override with SSE-specific ones
        response_headers = dict(backend_response.headers)
        response_headers.update(
            {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            }
        )

        # Stream content from backend to client - cleanup when done
        async def stream_sse():
            try:
                async for chunk in backend_response.aiter_bytes():
                    yield chunk
            except Exception as e:
                logger.error(f"SSE streaming error: {e}")
                raise
            finally:
                # Close stream when done streaming
                await stream_context.__aexit__(None, None, None)

        return StreamingResponse(
            stream_sse(),
            status_code=backend_response.status_code,
            media_type="text/event-stream",
            headers=response_headers,
        )

    except httpx.TimeoutException:
        logger.error(f"Timeout proxying to {target_url}")
        return JSONResponse(status_code=504, content={"error": "Gateway timeout"})
    except Exception as e:
        logger.error(f"Error proxying to {target_url}: {e}")
        return JSONResponse(status_code=502, content={"error": "Bad gateway", "detail": str(e)})


async def extract_server_path_from_request(request_path: str) -> str | None:
    """
    Extract registered server path prefix from request URL.

    Tries progressively shorter path segments until finding a registered server.
    For example, "/github/repos/list" will check:
    1. /github/repos/list
    2. /github/repos
    3. /github

    Args:
        request_path: Full incoming request path (e.g., /github/repos/list)

    Returns:
        Registered server path if found, None otherwise
    """
    # Split path into segments
    segments = [s for s in request_path.split("/") if s]

    # Try progressively shorter paths (longest first for specificity)
    for i in range(len(segments), 0, -1):
        candidate_path = "/" + "/".join(segments[:i])

        # Query database for this exact path
        server = await server_service_v1.get_server_by_path(candidate_path)
        if server:
            return candidate_path

    return None


@router.post(
    "/tools/call",
    response_model=None,
    responses={
        200: {
            "description": "Tool execution result",
            "content": {
                "application/json": {
                    "model": ToolExecutionResponse,
                    "example": {
                        "success": True,
                        "server_id": "12345",
                        "server_path": "/tavilysearch",
                        "tool_name": "tavily_search_mcp_tavily_search",
                        "result": {"data": "..."},
                    },
                },
                "text/event-stream": {"example": "event: message\ndata: {...}\n\n"},
            },
        }
    },
)
@track_tool_execution
async def execute_tool(body: ToolExecutionRequest, user_context: CurrentUser) -> Response | ToolExecutionResponse:
    """
    Execute a tool on an MCP server.

    Request body:
    {
        "server_id": "12345",
        "server_path": "/tavilysearch",
        "tool_name": "tavily_search_mcp_tavily_search",
        "arguments": {
            "query": "Donald Trump news"
        }
    }

    Returns:
        - SSE stream (text/event-stream) if backend returns SSE format
        - JSON (ToolExecutionResponse) otherwise
    """
    tool_name = body.tool_name
    arguments = body.arguments

    username = user_context.get("username", "unknown")
    user_id = user_context.get("user_id", "unknown")

    server = await server_service_v1.get_server_by_id(body.server_id)
    logger.info(f"Tool execution from user '{username}:{user_id}': {tool_name} on {body.server_id}")

    if not server:
        raise HTTPException(status_code=404, detail=f"Server not found: {body.server_id}")

    server_name = getattr(server, "serverName", None) or server.path.strip("/")

    # Track server request count
    record_server_request(server_name)

    # Build target URL using shared helper
    target_url = _build_target_url(server)

    # Build MCP JSON-RPC request
    mcp_request_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    logger.info(f"MCP JSON-RPC request body: {json.dumps(mcp_request_body, indent=2)}")

    # Prepare base headers for downstream MCP server
    additional_headers = {
        "X-Tool-Name": tool_name,
        "Accept": "application/json, text/event-stream",  # MCP servers require both
    }

    # Check if server requires initialization (default True for safety/compatibility)
    requires_init = server.config.get("requiresInit", True)

    # Session management logic - only if server requires initialization
    session_key = None
    stored_session_id = None
    if requires_init:
        # Key format: "user_id:server_id" to track per-user, per-server sessions
        session_key = f"{user_id}:{body.server_id}"
        session_info = get_session(session_key)

        if session_info:
            # Existing session found - check if it's initialized
            stored_session_id, session_initialized = session_info

            if session_initialized:
                additional_headers["mcp-Session-Id"] = stored_session_id
                logger.info(f"Reusing initialized session for {server.serverName}: {stored_session_id}")

        if not stored_session_id:
            init_headers = await _build_authenticated_headers(
                server=server,
                auth_context=user_context,
                additional_headers=additional_headers,
            )
            # Get transport type from server config (default to streamable-http)
            transport_type = server.config.get("type", "streamable-http")
            session_id = await initialize_mcp_session(target_url, init_headers, session_key, transport_type)

            if session_id:
                additional_headers["mcp-Session-Id"] = session_id
            else:
                logger.warning("Failed to initialize session, will attempt tool call without session")
    else:
        logger.debug("Stateless server (requiresInit=False), skipping session management")

    # Build final authenticated headers with session ID (if applicable)
    headers = await _build_authenticated_headers(
        server=server, auth_context=user_context, additional_headers=additional_headers
    )

    # Client can accept both JSON and SSE (as indicated in Accept header)
    accept_sse = True

    try:
        # Use shared proxy logic
        response = await _proxy_json_rpc_request(
            target_url=target_url, json_body=mcp_request_body, headers=headers, accept_sse=accept_sse
        )

        # If response is SSE, return it directly
        if response.media_type == "text/event-stream":
            logger.info(f"Returning SSE response for tool: {tool_name}")
            return response

        # Parse response body
        try:
            result_text = response.body.decode("utf-8") if isinstance(response.body, bytes) else str(response.body)
            result = json.loads(result_text)
        except Exception:
            result = {"raw": response.body.decode("utf-8") if isinstance(response.body, bytes) else str(response.body)}

        # Check for non-200 status code or MCP error in result
        if response.status_code != 200 and response.status_code != 202:
            logger.error(f"Non-200 status code: {response.status_code}, clearing session")
            if requires_init and session_key:
                clear_session(session_key)
            return ToolExecutionResponse(
                success=False,
                server_id=body.server_id,
                server_path=server.path,
                tool_name=tool_name,
                result=result,
                error=f"Server error (status {response.status_code})",
            )

        logger.info(f"Tool execution successful: {tool_name}")

        return ToolExecutionResponse(
            success=True,
            server_id=body.server_id,
            server_path=server.path,
            tool_name=tool_name,
            result=result,
        )

    except HTTPException as e:
        # HTTP exceptions from proxy helper - convert to structured response
        logger.error(f"Tool execution failed: {e.detail}")

        # Clear session on auth errors (might be stale session)
        if e.status_code == 401 and requires_init and session_key:
            clear_session(session_key)

        return ToolExecutionResponse(
            success=False,
            server_id=body.server_id,
            server_path=server.path,
            tool_name=tool_name,
            error=f"Error: {e.detail}",
        )


@router.post(
    "/resources/read",
    response_model=None,
    responses={
        200: {
            "description": "Resource read result",
            "content": {
                "application/json": {
                    "model": ResourceReadResponse,
                    "example": {
                        "success": True,
                        "server_id": "12345",
                        "server_path": "/tavilysearch",
                        "resource_uri": "tavily://search-results/AI",
                        "contents": [{"uri": "...", "mimeType": "...", "text": "..."}],
                    },
                }
            },
        }
    },
)
@track_resource_access
async def read_resource(body: ResourceReadRequest, user_context: CurrentUser) -> Response | ResourceReadResponse:
    """
    Read/access an MCP resource.

    Request body:
    {
        "server_id": "12345",
        "resource_uri": "tavily://search-results/AI"
    }

    Returns:
        Resource contents (text, JSON, binary, etc.)
    """
    resource_uri = body.resource_uri
    username = user_context.get("username", "unknown")

    server = await server_service_v1.get_server_by_id(body.server_id)
    logger.info(f"Resource read from user '{username}': {resource_uri} on {body.server_id}")

    if not server:
        raise HTTPException(status_code=404, detail=f"Server not found: {body.server_id}")

    server_name = server.serverName or server.path.strip("/").split("/")[0]

    # Track server request count
    record_server_request(server_name)

    # MOCK: Return hardcoded response for POC
    logger.info(f"(MOCK) Returning cached search results for: {resource_uri}")

    return ResourceReadResponse(
        success=True,
        server_id=body.server_id,
        server_path=server.path,
        resource_uri=resource_uri,
        contents=[
            {
                "uri": resource_uri,
                "mimeType": "application/json",
                "text": '{"results": [{"title": "AI News", "snippet": "Latest AI developments..."}]}',
            }
        ],
    )


@router.delete("/sessions/{server_id}")
async def clear_session_endpoint(server_id: str, user_context: CurrentUser) -> JSONResponse:
    """
    Clear/disconnect MCP session for a server (useful for debugging stale sessions).

    DELETE /api/v1/proxy/sessions/{server_id}
    """
    user_id = user_context.get("user_id", "unknown")
    session_key = f"{user_id}:{server_id}"

    clear_session(session_key)

    return JSONResponse(
        status_code=200,
        content={"success": True, "message": f"Session cleared for server {server_id}", "session_key": session_key},
    )


@router.post(
    "/prompts/execute",
    response_model=None,
    responses={
        200: {
            "description": "Prompt execution result",
            "content": {
                "application/json": {
                    "model": PromptExecutionResponse,
                    "example": {
                        "success": True,
                        "server_id": "12345",
                        "server_path": "/tavilysearch",
                        "prompt_name": "research_assistant",
                        "messages": [{"role": "...", "content": {...}}],
                    },
                }
            },
        }
    },
)
@track_prompt_execution
async def execute_prompt(body: PromptExecutionRequest, user_context: CurrentUser) -> Response | PromptExecutionResponse:
    """
    Execute an MCP prompt (get prompt template with arguments filled in).

    Request body:
    {
        "server_id": "12345",
        "prompt_name": "research_assistant",
        "arguments": {
            "topic": "Artificial Intelligence",
            "depth": "comprehensive"
        }
    }

    Returns:
        Prompt messages ready for LLM consumption
    """
    prompt_name = body.prompt_name
    arguments = body.arguments or {}
    username = user_context.get("username", "unknown")

    server = await server_service_v1.get_server_by_id(body.server_id)
    logger.info(f"Prompt execution from user '{username}': {prompt_name} on {body.server_id}")

    if not server:
        raise HTTPException(status_code=404, detail=f"Server not found: {body.server_id}")

    server_name = server.serverName or server.path.strip("/").split("/")[0]

    # Track server request count
    record_server_request(server_name)

    # MOCK: Return hardcoded prompt response for POC
    topic = arguments.get("topic", "general topic")
    depth = arguments.get("depth", "basic")

    logger.info(f"(MOCK) Returning prompt messages for: {prompt_name} (topic={topic}, depth={depth})")

    return PromptExecutionResponse(
        success=True,
        server_id=body.server_id,
        server_path=server.path,
        prompt_name=prompt_name,
        description=f"AI research assistant for {topic}",
        messages=[
            {
                "role": "system",
                "content": {
                    "type": "text",
                    "text": f"You are a research assistant specializing in {topic}. Provide {depth} analysis.",
                },
            },
            {"role": "user", "content": {"type": "text", "text": f"Research and analyze: {topic}"}},
        ],
    )


# ========== Catch-All Dynamic Proxy Route ==========
@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def dynamic_mcp_proxy(request: Request, full_path: str):
    """
    Dynamic catch-all route for MCP server proxying.
    This replaces nginx {{LOCATION_BLOCKS}}.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.
    """
    path = f"/{full_path}"

    # Extract registered server path from request URL
    server_path = await extract_server_path_from_request(path)
    if not server_path:
        raise HTTPException(status_code=404, detail="Not found")

    # Get server by the extracted path (guaranteed unique in database)
    server = await server_service_v1.get_server_by_path(server_path)
    if not server:
        raise HTTPException(status_code=404, detail="Not found")

    # Check if server is enabled
    config = server.config or {}
    if not config.get("enabled", False):
        return JSONResponse(status_code=503, content={"error": "Service disabled", "service": server.path})
    # Get auth context from middleware (already validated by AuthMiddleware)
    auth_context = getattr(request.state, "user", None)
    if not auth_context:
        logger.warning(f"Auth failed for {path}: No authentication context")
        raise HTTPException(status_code=401, detail="Authentication required")

    # Extract remaining path after server path
    remaining_path = path[len(server_path) :].lstrip("/")

    # Build target URL using shared helper
    target_url = _build_target_url(server, remaining_path)

    # Proxy the request
    logger.info(f"Proxying {request.method} {path} → {target_url}")
    return await proxy_to_mcp_server(request=request, target_url=target_url, auth_context=auth_context, server=server)


async def shutdown_proxy_client():
    """Cleanup proxy client on shutdown."""
    await proxy_client.aclose()
    logger.info("Proxy client closed")
