"""
Dynamic MCP server proxy routes.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from registry_pkgs.models.extended_mcp_server import MCPServerDocument

from ..auth.dependencies import CurrentUser, effective_scopes_from_context
from ..schemas.errors import AuthenticationError, MissingUserIdError, OAuthReAuthRequiredError, OAuthTokenError
from ..services.server_service import build_complete_headers_for_server

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Proxy"])

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

    Args:
        server: MCP server document
        auth_context: Gateway authentication context (user, client_id, scopes, jwt_token)
        additional_headers: Optional additional headers to merge

    Returns:
        Complete headers dict with authentication

    Raises:
        HTTPException: For auth errors (401 with appropriate details)
    """
    # Validate user_id is present
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

    # Build complete authentication headers (OAuth, apiKey, custom)
    try:
        user_id = auth_context.get("user_id")  # Already validated above
        auth_headers = await build_complete_headers_for_server(server, user_id)

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
    headers.update({k: v for k, v in context_headers.items() if v})

    # Remove host header to avoid conflicts
    headers.pop("host", None)
    headers.pop("Authorization", None)

    # Build complete authentication headers using shared helper
    headers = await _build_authenticated_headers(server=server, auth_context=auth_context, additional_headers=headers)

    body = await request.body()

    try:
        accept_header = request.headers.get("accept", "")
        client_accepts_sse = "text/event-stream" in accept_header

        logger.debug(f"Accept: {accept_header}, Client SSE: {client_accepts_sse}")

        if not client_accepts_sse:
            # Regular HTTP request
            response = await proxy_client.request(method=request.method, url=target_url, headers=headers, content=body)

            if response.status_code >= 400:
                try:
                    error_body = response.content.decode("utf-8")
                    logger.error(f"Backend error response ({response.status_code}): {error_body}")
                except Exception:
                    logger.error(
                        f"Backend error response ({response.status_code}): [binary content, {len(response.content)} bytes]"
                    )

            response_headers = dict(response.headers)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type"),
            )

        # Client accepts SSE - check if backend returns SSE
        logger.debug("Client accepts SSE - checking backend response type")

        stream_context = proxy_client.stream(request.method, target_url, headers=headers, content=body)
        backend_response = await stream_context.__aenter__()

        backend_content_type = backend_response.headers.get("content-type", "")
        is_sse = "text/event-stream" in backend_content_type

        logger.debug(f"Backend: status={backend_response.status_code}, content-type={backend_content_type or 'none'}")

        if not is_sse:
            content_bytes = await backend_response.aread()
            await stream_context.__aexit__(None, None, None)

            if backend_response.status_code >= 400:
                try:
                    error_body = content_bytes.decode("utf-8")
                    logger.error(f"Backend error response ({backend_response.status_code}): {error_body}")
                except Exception:
                    logger.error(
                        f"Backend error response ({backend_response.status_code}): [binary content, {len(content_bytes)} bytes]"
                    )

            response_headers = dict(backend_response.headers)
            return Response(
                content=content_bytes,
                status_code=backend_response.status_code,
                headers=response_headers,
                media_type=backend_content_type or "application/octet-stream",
            )

        # Backend is returning true SSE - keep stream open and forward it
        logger.info("Streaming SSE from backend")

        response_headers = dict(backend_response.headers)
        response_headers.update(
            {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            }
        )

        async def stream_sse():
            try:
                async for chunk in backend_response.aiter_bytes():
                    yield chunk
            except Exception as e:
                logger.error(f"SSE streaming error: {e}")
                raise
            finally:
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


async def extract_server_path_from_request(request_path: str, server_service) -> str | None:
    """
    Extract registered server path prefix from request URL.

    Tries progressively shorter path segments until finding a registered server.
    For example, "/github/repos/list" will check:
    1. /github/repos/list
    2. /github/repos
    3. /github

    Args:
        request_path: Full incoming request path (e.g., /github/repos/list)
        server_service: Server service instance

    Returns:
        Registered server path if found, None otherwise
    """
    segments = [s for s in request_path.split("/") if s]

    for i in range(len(segments), 0, -1):
        candidate_path = "/" + "/".join(segments[:i])

        server = await server_service.get_server_by_path(candidate_path)
        if server:
            return candidate_path

    return None


@router.delete("/sessions/{server_id}")
async def clear_session_endpoint(request: Request, server_id: str, user_context: CurrentUser) -> JSONResponse:
    """
    Clear/disconnect MCP session for a server (useful for debugging stale sessions).

    DELETE /api/v1/proxy/sessions/{server_id}
    """
    user_id = user_context.get("user_id", "unknown")
    session_key = f"{user_id}:{server_id}"

    request.app.state.container.mcp_client_service.clear_session(session_key)

    return JSONResponse(
        status_code=200,
        content={"success": True, "message": f"Session cleared for server {server_id}", "session_key": session_key},
    )


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def dynamic_mcp_proxy(request: Request, full_path: str):
    """
    Dynamic catch-all route for MCP server proxying.
    This replaces nginx {{LOCATION_BLOCKS}}.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.
    """
    path = f"/{full_path}"

    # Get server service from container
    container = request.app.state.container
    server_service = container.server_service

    # Extract registered server path from request URL
    server_path = await extract_server_path_from_request(path, server_service)
    if not server_path:
        raise HTTPException(status_code=404, detail="Not found")

    # Get server by the extracted path
    server = await server_service.get_server_by_path(server_path)
    if not server:
        raise HTTPException(status_code=404, detail="Not found")

    # Check if server is enabled
    config = server.config or {}
    if not config.get("enabled", False):
        return JSONResponse(status_code=503, content={"error": "Service disabled", "service": server.path})

    # Get auth context from middleware
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
