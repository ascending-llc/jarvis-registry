"""
Dynamic MCP server proxy routes.
"""

import json
import logging
from typing import Any
from uuid import uuid4

import httpx
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from redis import Redis
from starlette.routing import get_route_path

from registry_pkgs.models import ResourceType
from registry_pkgs.models.a2a_agent import AgentConfig
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode, FederationProviderType
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

from ..auth.dependencies import CurrentUser, UserContextDict
from ..auth.oauth.types import ClientBranding
from ..core.a2a_proxy import A2AProxyClientRegistry
from ..core.config import settings
from ..core.exceptions import InternalServerException, UrlElicitationRequiredException
from ..deps import (
    get_a2a_agent_service,
    get_a2a_proxy_client_registry,
    get_acl_service,
    get_mcp_proxy_client,
    get_oauth_service,
    get_redis_client,
    get_server_service,
)
from ..mcpgw.tools.utils import build_authenticated_headers, get_target_url, parse_elicitation_id
from ..services.a2a_agent_service import A2AAgentService
from ..services.access_control_service import ACLService
from ..services.oauth.oauth_service import MCPOAuthService
from ..services.server_service import ServerServiceV1

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Proxy"])


async def _parse_json_rpc_body(request: Request) -> dict[str, Any] | None:
    """
    Parse the JSON RPC message body and returns a dictionary. If parsing fails, return None.

    Raises: Nothing.
    """
    try:
        body = await request.body()

        json_body = json.loads(body)

        if not isinstance(json_body, dict):
            logger.error("The JSON RPC message body is not a JSON object.")

            return None

        return json_body
    except Exception:
        logger.exception("failed to parse JSON RPC message body")

        return None


def _is_notification(msg_dict: dict[str, Any]) -> bool:
    if "id" in msg_dict:
        # Notifications are not allowed to have "id".
        return False
    elif "method" not in msg_dict or not isinstance(msg_dict["method"], str):
        # Notifications must have the "method" field and it's a string.
        return False
    else:
        # If the message is a notification, its "method" field must start with "notifications/"
        return msg_dict["method"].startswith("notifications/")


def _extract_request_id(request_dict: dict[str, Any]) -> str | int | None:
    """Extract JSON-RPC request ID from request body."""
    try:
        id_ = request_dict["id"]

        if not isinstance(id_, str) and not isinstance(id_, int):
            return None

        return id_
    except Exception:
        logger.exception("failed to extract MCP request ID")

        return None


# Hop-by-hop headers defined by RFC 2616 §13.5.1 that proxies MUST strip.
# Date is also excluded to avoid duplicate-header warnings when upstream sets it too.
# Content-Length is intentionally absent — it is popped explicitly at each forward branch
# because the correct treatment differs: buffered (Response) branches let Starlette
# recalculate it from the actual de-chunked bytes; streaming (StreamingResponse) branches
# must not set it at all because the total length is indeterminate.
_HOP_BY_HOP_HEADERS = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "date",
    ]
)


def _sanitize_hop_by_hop_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


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


async def proxy_to_mcp_server(
    request_id: str | int,
    request: Request,
    target_url: str,
    auth_context: UserContextDict,
    server: ExtendedMCPServer,
    oauth_service: MCPOAuthService,
    proxy_client: httpx.AsyncClient,
    redis_client: Redis,
) -> Response:
    """
    Proxy request to MCP server with auth headers.
    Handles both regular HTTP and SSE streaming, including OAuth token injection.

    Args:
        request_id: JSON-RPC request ID
        request: Incoming FastAPI request
        target_url: Backend MCP server URL
        auth_context: UserContextDict
        server: ExtendedMCPServer
        oauth_service: OAuth service for building auth headers
        proxy_client: Shared httpx client for connection pooling
        redis_client: Redis client for JWT token caching
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
            oauth_service=oauth_service,
            server=server,
            auth_context=auth_context,
            additional_headers=headers,
            state_metadata={"client_branding": ClientBranding.UNRECOGNIZED, "notify_elicitation_complete": False},
            redis_client=redis_client,
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

                response_headers = _sanitize_hop_by_hop_headers(backend_response.headers)
                # Starlette recalculates Content-Length from the buffered body; the upstream's
                # value may be stale or wrong (e.g. when upstream mis-reports under chunked encoding).
                response_headers.pop("content-length", None)
                return Response(
                    content=content_bytes,
                    status_code=backend_response.status_code,
                    headers=response_headers,
                    media_type=backend_content_type or "application/json",
                )

            logger.info("Streaming SSE from backend")

            response_headers = _sanitize_hop_by_hop_headers(backend_response.headers)
            # Content-Length cannot be set for a stream of indeterminate length.
            response_headers.pop("content-length", None)
            response_headers.update(
                {
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",  # hop-by-hop header re-added intentionally for the outbound leg
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


def _jsonrpc_a2a_error_response(code: int, message: str) -> JSONResponse:
    """Return a A2A-spec-compliant JSON-RPC error response (HTTP 200)."""
    return JSONResponse(
        status_code=200,
        content=_build_jsonrpc_error(None, code, message),
    )


async def _forward_a2a(
    request: Request,
    target_url: str,
    proxy_client: httpx.AsyncClient,
    agent_path: str,
    is_jsonrpc: bool = False,
) -> Response:
    """
    For AgentCore Runtime agent with JWT inbound auth, AuthServerJwtAuth transparently swap our JWT into the Authorization header.
    **For other agents, we simply pass the Authorization header through.** We might change this when the use case of A2A
    is more clear in the future. As things stand now, non-AgentCore agents should be rare.
    """

    headers = dict(request.headers)
    headers.pop("host", None)

    body = await request.body()
    params = request.query_params

    try:
        # Use 5 min timeout when forwarding GET stream.
        # NOTE: This applies to one read operation, i.e. one async loop in the `backend_response.aiter_bytes()` below.
        stream_context = proxy_client.stream(
            request.method, target_url, headers=headers, content=body, params=params, timeout=httpx.Timeout(300)
        )
        backend_response = await stream_context.__aenter__()

        backend_content_type = backend_response.headers.get("content-type", "")
        is_stream = "text/event-stream" in backend_content_type

        try:
            logger.debug(
                f"A2A backend [{agent_path}]: status={backend_response.status_code}, content-type={backend_content_type}"
            )

            if not is_stream:
                content_bytes = await backend_response.aread()

                if backend_response.status_code >= 400:
                    try:
                        error_body = content_bytes.decode("utf-8")
                        logger.error(f"A2A backend [{agent_path}] error ({backend_response.status_code}): {error_body}")
                    except Exception:
                        logger.error(
                            f"A2A backend [{agent_path}] error ({backend_response.status_code}): "
                            f"[binary, {len(content_bytes)} bytes]"
                        )

                response_headers = _sanitize_hop_by_hop_headers(backend_response.headers)
                # Starlette recalculates Content-Length from the buffered body; the upstream's
                # value may be stale or wrong (e.g. AgentCore adds Transfer-Encoding: chunked
                # to complete JSON responses, causing the reported Content-Length to be unreliable).
                response_headers.pop("content-length", None)
                return Response(
                    content=content_bytes,
                    status_code=backend_response.status_code,
                    headers=response_headers,
                    media_type=backend_content_type or "application/json",
                )

            logger.info(f"Streaming SSE from A2A agent [{agent_path}]")

            response_headers = _sanitize_hop_by_hop_headers(backend_response.headers)
            # Content-Length cannot be set for a stream of indeterminate length.
            response_headers.pop("content-length", None)
            response_headers.update(
                {
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",  # hop-by-hop header re-added intentionally for the outbound leg
                    "Content-Type": backend_content_type or "text/event-stream",
                }
            )

            async def stream_sse():
                try:
                    async for chunk in backend_response.aiter_bytes():
                        yield chunk
                except Exception:
                    logger.exception(f"A2A SSE streaming error [{agent_path}]")
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
        logger.error(f"A2A proxy timeout for agent [{agent_path}] {target_url}")
        if is_jsonrpc:
            return _jsonrpc_a2a_error_response(-32603, "Gateway timeout communicating with agent")
        return JSONResponse(status_code=504, content={"error": "Gateway timeout communicating with agent"})
    except Exception as e:
        logger.error(f"A2A proxy error for agent [{agent_path}] {target_url}: {e}", exc_info=True)
        if is_jsonrpc:
            return _jsonrpc_a2a_error_response(-32603, "Failed to communicate with agent")
        return JSONResponse(status_code=502, content={"error": "Failed to communicate with agent"})


def _is_agentcore_jwt(
    agent_config: AgentConfig | None,
    federation_metadata: dict[str, Any] | None,
) -> bool:
    fed = federation_metadata or {}
    return (
        agent_config is not None
        and agent_config.runtimeAccess is not None
        and fed.get("providerType") == FederationProviderType.AWS_AGENTCORE
    )


# Route 1: JSON-RPC binding — bare base path, POST only
@router.post("/a2a/{agent_path}")
async def jsonrpc_proxy(
    request: Request,
    agent_path: str,
    user_context: CurrentUser,
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
    acl_service: ACLService = Depends(get_acl_service),
    proxy_client_registry: A2AProxyClientRegistry = Depends(get_a2a_proxy_client_registry),
):
    try:
        user_id = user_context.get("user_id")

        agent = await a2a_agent_service.get_agent_by_path(agent_path)
        if agent is None:
            return _jsonrpc_a2a_error_response(-32603, f"A2A agent with path '{agent_path}' not found")

        try:
            await acl_service.check_user_permission(
                user_id=PydanticObjectId(user_id),
                resource_type=ResourceType.REMOTE_AGENT.value,
                resource_id=agent.id,
                required_permission="VIEW",
            )
        except HTTPException:
            return _jsonrpc_a2a_error_response(-32001, f"Access denied to A2A agent '{agent_path}'")

        if not agent.isEnabled:
            return _jsonrpc_a2a_error_response(-32004, f"A2A agent '{agent_path}' is disabled")

        if (
            agent.config
            and agent.config.runtimeAccess
            and agent.config.runtimeAccess.mode == AgentCoreRuntimeAccessMode.IAM
        ):
            return _jsonrpc_a2a_error_response(
                -32004,
                f"A2A agent '{agent_path}' uses IAM inbound auth which is not supported by this gateway",
            )
        # card.url is the spec-defined invocation endpoint
        if agent.card and agent.card.url:
            base_url = str(agent.card.url)
        elif agent.config and agent.config.url:
            base_url = str(agent.config.url)
            logger.warning(
                f"Agent {agent_path} has no fetched card; falling back to config.url for invocation: "  # nosec B608
                f"{base_url}. Card may not have been synced yet."
            )
        else:
            return _jsonrpc_a2a_error_response(-32603, f"No invocation URL available for agent '{agent_path}'")

        agentcore_jwt = _is_agentcore_jwt(agent.config, agent.federationMetadata)
        proxy_client = proxy_client_registry.get(agent_path, agentcore_jwt=agentcore_jwt)

        logger.info(f"A2A JSON-RPC proxy: agent={agent_path} agentcore={agentcore_jwt} {base_url}")

        return await _forward_a2a(request, base_url, proxy_client, agent_path, is_jsonrpc=True)
    except Exception:
        logger.exception(f"Unexpected error in jsonrpc_proxy for agent [{agent_path}]")
        return _jsonrpc_a2a_error_response(-32603, "Internal server error")


# Route 2: HTTP+JSON binding — all paths with at least one segment
@router.route("/a2a/{agent_path}/{http_json_path:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def http_json_proxy(
    request: Request,
    agent_path: str,
    http_json_path: str,
    user_context: CurrentUser,
    a2a_agent_service: A2AAgentService = Depends(get_a2a_agent_service),
    acl_service: ACLService = Depends(get_acl_service),
    proxy_client_registry: A2AProxyClientRegistry = Depends(get_a2a_proxy_client_registry),
):
    try:
        user_id = user_context.get("user_id")

        agent = await a2a_agent_service.get_agent_by_path(agent_path)
        if agent is None:
            return JSONResponse(status_code=404, content={"error": f"A2A agent with path '{agent_path}' not found"})

        try:
            await acl_service.check_user_permission(
                user_id=PydanticObjectId(user_id),
                resource_type=ResourceType.REMOTE_AGENT.value,
                resource_id=agent.id,
                required_permission="VIEW",
            )
        except HTTPException:
            return JSONResponse(status_code=403, content={"error": f"Access denied to A2A agent '{agent_path}'"})

        if not agent.isEnabled:
            return JSONResponse(status_code=403, content={"error": f"A2A agent '{agent_path}' is disabled"})

        if (
            agent.config
            and agent.config.runtimeAccess
            and agent.config.runtimeAccess.mode == AgentCoreRuntimeAccessMode.IAM
        ):
            return JSONResponse(
                status_code=501,
                content={"error": f"A2A agent '{agent_path}' uses IAM inbound auth which is not supported"},
            )

        if agent.card and agent.card.url:
            base_url = str(agent.card.url)
        elif agent.config and agent.config.url:
            base_url = str(agent.config.url)
            logger.warning(
                f"Agent {agent_path} has no fetched card; falling back to config.url for invocation: "  # nosec B608
                f"{base_url}. Card may not have been synced yet."
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"error": f"No invocation URL available for agent '{agent_path}'"},
            )

        agentcore_jwt = _is_agentcore_jwt(agent.config, agent.federationMetadata)
        proxy_client = proxy_client_registry.get(agent_path, agentcore_jwt=agentcore_jwt)

        target_url = base_url.rstrip("/") + "/" + http_json_path

        logger.info(
            f"A2A HTTP+JSON proxy: agent={agent_path} path=/{http_json_path} agentcore={agentcore_jwt} {target_url}"
        )

        return await _forward_a2a(request, target_url, proxy_client, agent_path)
    except Exception:
        logger.exception(f"Unexpected error in http_json_proxy for agent [{agent_path}]")
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@router.post("/server/{full_path:path}")
async def dynamic_mcp_post_proxy(
    request: Request,
    full_path: str,
    auth_context: CurrentUser,
    server_service: ServerServiceV1 = Depends(get_server_service),
    oauth_service: MCPOAuthService = Depends(get_oauth_service),
    proxy_client: httpx.AsyncClient = Depends(get_mcp_proxy_client),
    redis_client: Redis = Depends(get_redis_client),
):
    """
    Dynamic catch-all route for MCP server proxying, but only works for POST.
    Enables developers to connect directly to all MCP servers through the registry.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.

    MCP protocol only uses GET and POST methods.
    """
    # If client accidentally tries to connect to our MCP Gateway via the dynamic catch-all route,
    # respond with a permanent redirect.
    if get_route_path(request.scope) == "/proxy/server/mcpgw/mcp":
        return RedirectResponse(f"{settings.registry_url.rstrip('/')}/proxy/mcpgw/mcp", status_code=308)

    msg_body = await _parse_json_rpc_body(request)
    if msg_body is None:
        # Bad request body. Just return 400.
        return Response(status_code=400)

    if _is_notification(msg_body):
        # On notification (e.g. `notifications/initialized`), MCP spec requires 202 with empty body.
        return Response(status_code=202)

    request_id = _extract_request_id(msg_body)
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

    # Get target URL
    try:
        target_url = get_target_url(server)
    except InternalServerException:
        # POST requests use JSON-RPC. There is no problem at the HTTP layer, so use status code 200 for HTTP
        # and embed JSON-RPC error code -32603 in response body.
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
        redis_client=redis_client,
    )


@router.get("/server/{full_path:path}")
async def dynamic_mcp_get_proxy(
    request: Request,
    full_path: str,
    auth_context: CurrentUser,
    server_service: ServerServiceV1 = Depends(get_server_service),
    oauth_service: MCPOAuthService = Depends(get_oauth_service),
    proxy_client: httpx.AsyncClient = Depends(get_mcp_proxy_client),
    redis_client: Redis = Depends(get_redis_client),
):
    """
    Dynamic catch-all route for MCP server proxying, but only works for GET, i.e. the event stream.
    Enables developers to connect directly to all MCP servers through the registry.

    CRITICAL: This catch-all route matches ANY path pattern, so it must be defined LAST.
    FastAPI matches routes in order, so this will capture all unmatched routes.

    MCP protocol only uses GET and POST methods.
    """
    # If client accidentally tries to connect to our MCP Gateway via the dynamic catch-all route,
    # respond with a permanent redirect.
    if get_route_path(request.scope) == "/proxy/server/mcpgw/mcp":
        return RedirectResponse(f"{settings.registry_url.rstrip('/')}/proxy/mcpgw/mcp", status_code=308)

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

    # Get target URL
    try:
        target_url = get_target_url(server)
    except InternalServerException:
        # GET requests doesn't use JSON-RPC, so use HTTP status code 500.
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
            oauth_service=oauth_service,
            server=server,
            auth_context=auth_context,
            additional_headers=headers,
            state_metadata={"client_branding": ClientBranding.UNRECOGNIZED, "notify_elicitation_complete": False},
            redis_client=redis_client,
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

        response_headers = _sanitize_hop_by_hop_headers(backend_response.headers)
        # Content-Length cannot be set for a stream of indeterminate length.
        response_headers.pop("content-length", None)
        response_headers.update(
            {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",  # hop-by-hop header re-added intentionally for the outbound leg
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
