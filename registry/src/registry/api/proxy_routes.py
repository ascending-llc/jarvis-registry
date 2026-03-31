"""
Dynamic MCP server proxy routes.
"""

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from registry_pkgs.models.extended_mcp_server import MCPServerDocument

from ..auth.dependencies import CurrentUser
from ..core.exceptions import InternalServerException, UrlElicitationRequiredException
from ..deps import get_mcp_proxy_client, get_oauth_service, get_server_service
from ..mcpgw.tools.utils import build_authenticated_headers
from ..services.oauth.oauth_service import MCPOAuthService
from ..services.server_service import ServerServiceV1

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Proxy"])


async def _extract_request_id(request: Request) -> str | int | None:
    """Extract JSON-RPC request ID from request body."""
    try:
        body = await request.body()
        if body:
            json_body = json.loads(body)
            return json_body.get("id")
    except Exception:
        pass
    return None


def _build_jsonrpc_error_result(request_id: str | int | None, error_text: str) -> dict[str, Any]:
    """Build JSON-RPC result response with isError=true."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": error_text}], "isError": True},
    }


def _build_jsonrpc_error(request_id: str | int | None, code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Build JSON-RPC error response."""
    error_response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data is not None:
        error_response["error"]["data"] = data
    return error_response


def _generate_elicitation_id(auth_url: str) -> str:
    """Generate elicitation ID from auth URL."""
    import hashlib

    return hashlib.sha256(auth_url.encode()).hexdigest()[:16]


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
        InternalServerException: If server URL is not configured
    """
    config = server.config or {}
    base_url = config.get("url")

    if not base_url:
        raise InternalServerException("Server URL not configured")

    # If no remaining path, return base URL as-is
    if not remaining_path:
        return base_url

    # Ensure base URL has trailing slash before appending path
    if not base_url.endswith("/"):
        base_url += "/"

    return base_url + remaining_path


async def proxy_to_mcp_server(
    request: Request,
    target_url: str,
    auth_context: dict[str, Any],
    server: MCPServerDocument,
    oauth_service: MCPOAuthService,
    proxy_client: httpx.AsyncClient,
) -> Response:
    """
    Proxy request to MCP server with auth headers.
    Handles both regular HTTP and SSE streaming, including OAuth token injection.

    Args:
        request: Incoming FastAPI request
        target_url: Backend MCP server URL
        auth_context: Gateway authentication context
        server: MCPServerDocument
        oauth_service: OAuth service for building auth headers
        proxy_client: Shared httpx client for connection pooling
    """
    request_id = await _extract_request_id(request)

    # Build proxy headers - start with request headers
    headers = dict(request.headers)

    # Add context headers for tracing/logging
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

    # Build complete authentication headers using shared utility
    try:
        headers = await build_authenticated_headers(
            oauth_service=oauth_service, server=server, auth_context=auth_context, additional_headers=headers
        )
    except UrlElicitationRequiredException as exc:
        elicitation_id = _generate_elicitation_id(exc.auth_url)
        error_data = {
            "elicitations": [
                {
                    "mode": "url",
                    "message": f"The tokens for the '{exc.server_name}' MCP server managed by Jarvis Registry have expired. "
                    "Please follow the URL to perform re-authorization in a browser window and come back again.",
                    "url": exc.auth_url,
                    "elicitationId": elicitation_id,
                }
            ]
        }
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error(
                request_id,
                -32042,
                "OAuth re-authentication required. Please complete the authorization flow.",
                error_data,
            ),
        )
    except InternalServerException as exc:
        logger.error(f"Internal server exception: {exc}")
        return JSONResponse(
            status_code=200, content=_build_jsonrpc_error(request_id, -32603, f"Internal server error: {str(exc)}")
        )
    except Exception as exc:
        logger.exception(f"Unexpected exception building auth headers: {exc}")
        return JSONResponse(
            status_code=200, content=_build_jsonrpc_error(request_id, -32603, f"Internal server error: {str(exc)}")
        )

    body = await request.body()

    try:
        accept_header = request.headers.get("accept", "")
        client_accepts_sse = "text/event-stream" in accept_header

        logger.debug(f"Accept: {accept_header}, Client SSE: {client_accepts_sse}")

        if not client_accepts_sse:
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
        return JSONResponse(status_code=200, content=_build_jsonrpc_error(request_id, -32603, "Gateway timeout"))
    except Exception as e:
        logger.exception(f"Error proxying to {target_url}: {e}")
        return JSONResponse(status_code=200, content=_build_jsonrpc_error(request_id, -32603, f"Bad gateway: {str(e)}"))


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


@router.api_route("/{full_path:path}", methods=["GET", "POST"])
async def dynamic_mcp_proxy(
    request: Request,
    full_path: str,
    server_service: ServerServiceV1 = Depends(get_server_service),
    oauth_service: MCPOAuthService = Depends(get_oauth_service),
    proxy_client: httpx.AsyncClient = Depends(get_mcp_proxy_client),
):
    """
    Dynamic catch-all route for MCP server proxying.
    Enables developers to connect directly to all MCP servers through the registry.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.

    MCP protocol only uses GET and POST methods.
    """
    path = f"/{full_path}"
    request_id = await _extract_request_id(request)

    # Extract registered server path from request URL
    server_path = await extract_server_path_from_request(path, server_service)
    if not server_path:
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(request_id, f"Server not found for path: {path}"),
        )

    # Get server by the extracted path
    server = await server_service.get_server_by_path(server_path)
    if not server:
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(request_id, f"Server not found for path: {server_path}"),
        )

    # Check if server is enabled
    config = server.config or {}
    if not config.get("enabled", False):
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(request_id, f"Server '{server.serverName}' is disabled"),
        )

    # Get auth context from middleware
    auth_context = getattr(request.state, "user", None)
    if not auth_context:
        logger.warning(f"Auth failed for {path}: No authentication context")
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error(request_id, -32603, "Authentication context not found (internal error)"),
        )

    # Extract remaining path after server path
    remaining_path = path[len(server_path) :].lstrip("/")

    # Build target URL
    try:
        target_url = _build_target_url(server, remaining_path)
    except Exception as exc:
        logger.error(f"Error building target URL: {exc}")
        return JSONResponse(
            status_code=200, content=_build_jsonrpc_error(request_id, -32603, f"Server URL not configured: {str(exc)}")
        )

    # Proxy the request
    logger.info(f"Proxying {request.method} {path} → {target_url}")
    return await proxy_to_mcp_server(
        request=request,
        target_url=target_url,
        auth_context=auth_context,
        server=server,
        oauth_service=oauth_service,
        proxy_client=proxy_client,
    )
