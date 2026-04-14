"""
Dynamic MCP server proxy routes.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.types import MessageSendParams
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from registry_pkgs.models import ResourceType
from registry_pkgs.models.a2a_agent import TRANSPORT_GRPC, TRANSPORT_HTTP_JSON, TRANSPORT_JSONRPC
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

from ..auth.dependencies import CurrentUser, UserContextDict
from ..core.config import settings
from ..core.exceptions import InternalServerException, UrlElicitationRequiredException
from ..deps import get_a2a_agent_service, get_acl_service, get_mcp_proxy_client, get_oauth_service, get_server_service
from ..mcpgw.tools.utils import build_authenticated_headers, parse_elicitation_id
from ..services.a2a_agent_service import A2AAgentService
from ..services.access_control_service import ACLService
from ..services.oauth.oauth_service import MCPOAuthService
from ..services.server_service import ServerServiceV1

try:
    import grpc
    from a2a.client.transports.grpc import GrpcTransport

    _grpc_available = True
except ImportError:
    _grpc_available = False

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Proxy"])


async def _extract_request_id(request: Request) -> str | int | None:
    """Extract JSON-RPC request ID from request body."""
    try:
        body = await request.body()

        json_body = json.loads(body)

        id_ = json_body["id"]

        if not isinstance(id_, str) and not isinstance(id_, int):
            return None

        return id_
    except Exception:
        logger.exception("failed to extract MCP request ID")

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


def _get_elicitation_id(auth_url: str) -> str:
    """Parse elicitation ID from auth URL, or generate a new UUID if one doesn't exist."""
    id_ = parse_elicitation_id(auth_url)

    if id_ is None:
        return str(uuid4())

    return id_


def _build_target_url(server: ExtendedMCPServer, remaining_path: str = "") -> str:
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
    request_id: str | int,
    request: Request,
    target_url: str,
    auth_context: UserContextDict,
    server: ExtendedMCPServer,
    oauth_service: MCPOAuthService,
    proxy_client: httpx.AsyncClient,
) -> Response:
    """
    Proxy request to MCP server with auth headers.
    Handles both regular HTTP and SSE streaming, including OAuth token injection.

    Args:
        request: Incoming FastAPI request
        target_url: Backend MCP server URL
        auth_context: UserContextDict
        server: ExtendedMCPServer
        oauth_service: OAuth service for building auth headers
        proxy_client: Shared httpx client for connection pooling
    """
    # Build proxy headers - start with request headers
    headers = dict(request.headers)

    # Add context headers for tracing/logging
    context_headers: dict[str, str] = {
        "X-Auth-Method": auth_context["auth_method"],
        "X-Server-Name": server.serverName,
        "X-Original-URL": str(request.url),
    }
    headers.update({k: v for k, v in context_headers.items() if v})

    # Remove host header to avoid conflicts
    headers.pop("host", None)
    headers.pop("authorization", None)

    # Build complete authentication headers using shared utility
    try:
        headers = await build_authenticated_headers(
            oauth_service=oauth_service, server=server, auth_context=auth_context, additional_headers=headers
        )
    except UrlElicitationRequiredException as exc:
        llm_msg = (
            f"In order to make tool calls to the '{exc.server_name}' MCP server, the client must first perform "
            "out-of-band re-authorization in a browser window. Please direct the client to open the provided URL "
            "in a browser window, finish re-authorization, and come back to retry the same tool call again."
        )

        user_msg = (
            f"The tokens for the '{exc.server_name}' MCP server managed by Jarvis Registry have expired. "
            "Please follow the URL to perform re-authorization in a browser window and come back again.",
        )

        elicitation_id = _get_elicitation_id(exc.auth_url)

        error_data = {
            "elicitations": [
                {
                    "mode": "url",
                    "message": user_msg,
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
                llm_msg,
                error_data,
            ),
        )
    except InternalServerException:
        logger.exception("Internal server exception")

        return JSONResponse(status_code=200, content=_build_jsonrpc_error(request_id, -32603, "Internal server error"))

    body = await request.body()

    try:
        accept_header = request.headers.get("accept", "")
        client_accepts_sse = "text/event-stream" in accept_header

        # We only support directly reaching streamable-http downstream servers anyway.
        if not client_accepts_sse:
            logger.error(f"Accept header: '{accept_header}' from a client of a streamable-http MCP server")

            return JSONResponse(
                status_code=200,
                content=_build_jsonrpc_error_result(
                    request_id,
                    "The client is trying to communicate with an MCP server on Streamable HTTP transport, so its "
                    f"Accept header MUST include 'text/event-stream'. However, the Accept header is '{accept_header}'.",
                ),
            )

        stream_context = proxy_client.stream(request.method, target_url, headers=headers, content=body)
        backend_response = await stream_context.__aenter__()

        backend_content_type = backend_response.headers.get("content-type", "")
        is_stream = "text/event-stream" in backend_content_type

        try:
            logger.debug(f"Backend: status={backend_response.status_code}, content-type={backend_content_type}")

            if not is_stream:
                content_bytes = await backend_response.aread()

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
                    media_type=backend_content_type or "application/json",
                )

            logger.info("Streaming SSE from backend")

            response_headers = dict(backend_response.headers)
            response_headers.update(
                {
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                    "Content-Type": backend_content_type or "text/event-stream",
                }
            )

            async def stream_sse():
                try:
                    async for chunk in backend_response.aiter_bytes():
                        yield chunk
                except Exception:
                    logger.exception("SSE streaming error")

                    raise
                finally:
                    await stream_context.__aexit__(None, None, None)

            return StreamingResponse(
                stream_sse(),
                status_code=backend_response.status_code,
                media_type=backend_content_type or "text/event-stream",
                headers=response_headers,
            )

        finally:
            if not is_stream:
                await stream_context.__aexit__(None, None, None)

    except httpx.TimeoutException:
        logger.error(f"Timeout proxying to {target_url}")

        return JSONResponse(status_code=200, content=_build_jsonrpc_error(request_id, -32603, "gateway timeout"))
    except Exception:
        logger.exception(f"Error proxying to {target_url}")

        return JSONResponse(status_code=200, content=_build_jsonrpc_error(request_id, -32603, "gateway internal error"))


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


@router.post("/a2a/{agent_path:path}")
async def a2a_agent_proxy(
    request: Request,
    agent_path: str,
    user_context: CurrentUser,
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
    acl_service: ACLService = Depends(get_acl_service),
    proxy_client: httpx.AsyncClient = Depends(get_mcp_proxy_client),
):
    """
    Proxy POST requests to A2A agents via the registry.

    Route: POST /proxy/a2a/{agent_path}

    Dispatches to the correct A2A transport based on agent config.type:
      - jsonrpc  → JsonRpcTransport  (A2A JSON-RPC over HTTP)
      - grpc     → GrpcTransport     (A2A over gRPC)
      - http_json → RestTransport    (A2A HTTP+JSON / REST)

    Request body must be a valid MessageSendParams JSON object.
    If the Accept header includes 'text/event-stream', the response will
    be streamed as Server-Sent Events; otherwise a single JSON response
    is returned.
    """
    path = f"/{agent_path}"
    user_id = user_context.get("user_id")

    # Resolve agent by path
    agent = await a2a_agent_service.get_agent_by_path(path)
    if agent is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"A2A agent with path '{path}' not found"},
        )

    # ACL: require VIEW permission
    await acl_service.check_user_permission(
        user_id=PydanticObjectId(user_id),
        resource_type=ResourceType.REMOTE_AGENT.value,
        resource_id=agent.id,
        required_permission="VIEW",
    )

    # Agent must be enabled
    if not agent.isEnabled:
        return JSONResponse(
            status_code=403,
            content={"error": f"A2A agent '{path}' is disabled"},
        )

    # Parse MessageSendParams from request body
    try:
        body_bytes = await request.body()
        params = MessageSendParams.model_validate_json(body_bytes)
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid request body: {e}"},
        )

    transport_type = (agent.config.type if agent.config else TRANSPORT_JSONRPC).lower()
    
    # Use user-provided URL from config for runtime operations
    if agent.config and agent.config.url:
        agent_url = str(agent.config.url)
        logger.debug(f"Using config.url for agent {path}: {agent_url}")
    else:
        # Fallback to card.url for backward compatibility with old data
        agent_url = str(agent.card.url)
        logger.warning(
            f"Agent {path} missing config.url, falling back to card.url: {agent_url}. "
            "This may fail if card.url is not accessible. Please update the agent to set config.url."
        )
    
    # Create a modified agent_card with the runtime URL (config.url)
    # Transport classes use agent_card.url internally, so we need to override it
    agent_card = agent.card.model_copy(deep=True)
    agent_card.url = agent_url

    accept_header = request.headers.get("accept", "")
    is_streaming = "text/event-stream" in accept_header

    logger.info(f"A2A proxy: {path} transport={transport_type} streaming={is_streaming} → {agent_url}")

    try:
        if transport_type == TRANSPORT_JSONRPC:
            transport = JsonRpcTransport(httpx_client=proxy_client, agent_card=agent_card)
            if is_streaming:
                return await _stream_a2a_response(transport.send_message_streaming(params))
            result = await transport.send_message(params)
            return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))

        elif transport_type == TRANSPORT_HTTP_JSON:
            transport = RestTransport(httpx_client=proxy_client, agent_card=agent_card)
            if is_streaming:
                return await _stream_a2a_response(transport.send_message_streaming(params))
            result = await transport.send_message(params)
            return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))

        elif transport_type == TRANSPORT_GRPC:
            if not _grpc_available:
                return JSONResponse(
                    status_code=501,
                    content={
                        "error": "gRPC transport is not available. Please ensure grpcio is installed (requires a2a-sdk[grpc] extra)"
                    },
                )
            parsed = urlparse(agent_url)
            if not parsed.hostname:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Invalid gRPC agent URL: '{agent_url}'"},
                )

            scheme = parsed.scheme.lower()
            if parsed.port is not None:
                grpc_port = parsed.port
            elif scheme in {"https", "grpcs"}:
                grpc_port = 443
            elif scheme == "http":
                grpc_port = 80
            else:
                grpc_port = 50051

            grpc_target = f"{parsed.hostname}:{grpc_port}"

            if scheme in {"https", "grpcs"}:
                channel = grpc.aio.secure_channel(grpc_target, grpc.ssl_channel_credentials())
            else:
                channel = grpc.aio.insecure_channel(grpc_target)

            try:
                transport = GrpcTransport(channel=channel, agent_card=agent_card)
                if is_streaming:
                    return await _stream_a2a_response(transport.send_message_streaming(params))
                result = await transport.send_message(params)
                return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))
            finally:
                await channel.close()

        else:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"Unsupported transport type: '{transport_type}'. Must be one of: jsonrpc, grpc, http_json"
                },
            )

    except httpx.TimeoutException:
        logger.error(f"A2A proxy timeout for agent {path}")
        return JSONResponse(status_code=504, content={"error": "Gateway timeout communicating with agent"})
    except httpx.HTTPStatusError as e:
        logger.error(f"A2A proxy HTTP error for agent {path}: {e}")
        return JSONResponse(status_code=502, content={"error": f"Agent returned HTTP error: {e.response.status_code}"})
    except Exception as e:
        logger.error(f"A2A proxy error for agent {path}: {e}", exc_info=True)
        return JSONResponse(status_code=502, content={"error": "Failed to communicate with agent"})


async def _stream_a2a_response(event_generator: AsyncGenerator[Any, None]) -> StreamingResponse:
    """Wrap an A2A async generator into an SSE StreamingResponse.

    Args:
        event_generator: Async generator yielding A2A SDK event objects with model_dump() method

    Returns:
        StreamingResponse configured for Server-Sent Events
    """

    async def _sse_body():
        try:
            async for event in event_generator:
                data = event.model_dump(mode="json", exclude_none=True)
                yield f"data: {json.dumps(data)}\n\n"
        except Exception:
            logger.exception("A2A SSE streaming error")
            raise

    return StreamingResponse(
        _sse_body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/server/{full_path:path}")
async def dynamic_mcp_post_proxy(
    request: Request,
    full_path: str,
    auth_context: CurrentUser,
    server_service: ServerServiceV1 = Depends(get_server_service),
    oauth_service: MCPOAuthService = Depends(get_oauth_service),
    proxy_client: httpx.AsyncClient = Depends(get_mcp_proxy_client),
):
    """
    Dynamic catch-all route for MCP server proxying, but only works for POST.
    Enables developers to connect directly to all MCP servers through the registry.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.

    MCP protocol only uses GET and POST methods.
    """
    request_id = await _extract_request_id(request)
    if request_id is None:
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(
                request_id,
                "The JSON-RPC request body is malformed. The 'id' field doesn't exist or is not of the right type.",
            ),
        )

    # Extract registered server path from request URL
    path = f"/{full_path}"
    server_path = await extract_server_path_from_request(path, server_service)
    if server_path is None:
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(
                request_id, "The path portion of the MCP server URL is misconfigured. Prompt user to re-configure it."
            ),
        )

    # Get server by the extracted path
    server = await server_service.get_server_by_path(server_path)
    if server is None:
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(
                request_id, "The path portion of the MCP server URL is misconfigured. Prompt user to re-configure it."
            ),
        )

    # Check if server is enabled
    config = server.config or {}
    if not config.get("enabled", False):
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(request_id, f"Server '{server.serverName}' is disabled"),
        )
    elif config.get("type", "") != "streamable-http":
        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error_result(
                request_id,
                f"The target server with path '{server_path}' is on legacy SSE transport. Such servers much be "
                "reached via the Jarvis Registry MCP Gateway. Only servers on Streamable HTTP transport can be reached directly.",
            ),
        )

    # Build target URL
    try:
        # First extract remaining path after server path
        remaining_path = path[len(server_path) :].lstrip("/")

        target_url = _build_target_url(server, remaining_path)
    except Exception:
        logger.exception("Error building target URL")

        return JSONResponse(
            status_code=200,
            content=_build_jsonrpc_error(
                request_id, -32603, "Server URL is not configured. The MCP server is not reachable."
            ),
        )

    # Proxy the request. At this point, auth_context must exist as UserContextDict. Otherwise the UnifiedAuthMiddleware
    # would have responded with protected resource metadata doc according to RFC 9728.
    logger.info(f"Proxying {request.method} {path} → {target_url}")

    return await proxy_to_mcp_server(
        request_id,
        request=request,
        target_url=target_url,
        auth_context=auth_context,
        server=server,
        oauth_service=oauth_service,
        proxy_client=proxy_client,
    )


@router.get("/server/{full_path:path}")
async def dynamic_mcp_get_proxy(
    request: Request,
    full_path: str,
    auth_context: CurrentUser,
    server_service: ServerServiceV1 = Depends(get_server_service),
    oauth_service: MCPOAuthService = Depends(get_oauth_service),
    proxy_client: httpx.AsyncClient = Depends(get_mcp_proxy_client),
):
    """
    Dynamic catch-all route for MCP server proxying, but only works for GET, i.e. the event stream.
    Enables developers to connect directly to all MCP servers through the registry.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.

    MCP protocol only uses GET and POST methods.
    """
    # Extract registered server path from request URL
    path = f"/{full_path}"
    server_path = await extract_server_path_from_request(path, server_service)
    if server_path is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Unknown MCP server with path '{path}'."},
        )

    # Get server by the extracted path
    server = await server_service.get_server_by_path(server_path)
    if server is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Unknown MCP server with path '{server_path}'."},
        )

    # Check if server is enabled
    config = server.config or {}
    if not config.get("enabled", False):
        return JSONResponse(
            status_code=404,
            content={"error": f"MCP server with path '{server_path}' is disabled."},
        )
    elif config.get("type", "") != "streamable-http":
        return JSONResponse(
            status_code=404,
            content={"error": f"MCP server with path '{server_path}' is not on streamable-http transport."},
        )

    # Build target URL
    try:
        # First extract remaining path after server path
        remaining_path = path[len(server_path) :].lstrip("/")

        target_url = _build_target_url(server, remaining_path)
    except Exception:
        logger.exception("Error building target URL")

        return JSONResponse(
            status_code=500, content={"error": "Server URL is not configured. The MCP server is not reachable."}
        )

    # Proxy the request. At this point, auth_context must exist as UserContextDict. Otherwise the UnifiedAuthMiddleware
    # would have responded with protected resource metadata doc according to RFC 9728.
    logger.info(f"Proxying {request.method} {path} → {target_url}")

    # Build proxy headers - start with request headers
    headers = dict(request.headers)

    # Add context headers for tracing/logging
    context_headers: dict[str, str] = {
        "X-Auth-Method": auth_context["auth_method"],
        "X-Server-Name": server.serverName,
        "X-Original-URL": str(request.url),
    }
    headers.update({k: v for k, v in context_headers.items() if v})

    # Remove host header to avoid conflicts
    headers.pop("host", None)
    headers.pop("authorization", None)

    # Build complete authentication headers using shared utility
    try:
        headers = await build_authenticated_headers(
            oauth_service=oauth_service, server=server, auth_context=auth_context, additional_headers=headers
        )
    except UrlElicitationRequiredException as exc:
        # If token expired for a GET request, follow RFC 9457 and RFC 7807.
        return JSONResponse(
            status_code=401,
            headers={
                "Content-Type": "application/problem+json",
                "WWW-Authenticate": f'Bearer realm="{settings.jarvis_realm}", error="invalid_token"',
            },
            content={
                "type": f"{settings.registry_client_url.rstrip('/')}/errors/token-expired",
                "title": "Both access and refresh tokens of downstream MCP server have expired",
                "status": 401,
                "detail": f"Tokens of downstream MCP server have expired. Please re-authenticate at {exc.auth_url}.",
            },
        )
    except InternalServerException:
        logger.exception("Internal server exception")

        return JSONResponse(status_code=500, content={"error": "internal server error"})

    body = await request.body()

    accept_header = request.headers.get("accept", "")
    client_accepts_sse = "text/event-stream" in accept_header

    if not client_accepts_sse:
        logger.error(f"Accept header: '{accept_header}' from a client of a streamable-http MCP server")

        return JSONResponse(
            status_code=406,
            content={
                "type": f"{settings.registry_client_url.rstrip('/')}/errors/not-acceptable",
                "title": "Not Acceptable",
                "status": 406,
                "detail": "GET requests must include 'text/event-stream' in the Accept header.",
            },
        )

    try:
        # Use 5 min timeout when forwarding GET stream.
        # NOTE: This applies to one read operation, i.e. one async loop in the `backend_response.aiter_bytes()` below.
        stream_context = proxy_client.stream(
            request.method, target_url, headers=headers, content=body, timeout=httpx.Timeout(300)
        )
        backend_response = await stream_context.__aenter__()

        logger.info("Streaming SSE from backend")

        response_headers = dict(backend_response.headers)
        response_headers.update(
            {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

        async def stream_sse():
            try:
                # NOTE: Every chunk-reading has a 5 min timeout.
                async for chunk in backend_response.aiter_bytes():
                    yield chunk
            except httpx.TimeoutException:
                logger.info("No event from downstream for 5 min. Disconnecting.")

                raise
            except Exception:
                logger.exception("SSE streaming error")

                raise
            finally:
                await stream_context.__aexit__(None, None, None)

        return StreamingResponse(
            stream_sse(),
            status_code=backend_response.status_code,
            media_type=backend_response.headers.get("content-type", "text/event-stream"),
            headers=response_headers,
        )
    except Exception:
        logger.exception(f"Error proxying to {target_url}")

        return JSONResponse(status_code=500, content={"error": "internal server error"})
