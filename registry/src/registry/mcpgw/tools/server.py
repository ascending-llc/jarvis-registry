"""
MCP Gateway Registry API Tools
- execute_tool: Execute tools from any MCP server
- read_resource: Read/access resources from any MCP server
- execute_prompt: Execute prompts from any MCP server
"""

import logging
import secrets
from collections.abc import Callable
from typing import Annotated, Any
from uuid import uuid4

from beanie import PydanticObjectId
from mcp.server.mcpserver import Context
from mcp.shared.exceptions import MCPError, UrlElicitationRequiredError
from mcp.types import (
    URL_ELICITATION_REQUIRED,
    CallToolResult,
    ElicitRequest,
    ElicitRequestURLParams,
    EmbeddedResource,
    InputRequiredResult,
    LoggingMessageNotification,
    LoggingMessageNotificationParams,
    TextContent,
    TextResourceContents,
)
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from opentelemetry import baggage
from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from registry_pkgs.core.exceptions import (
    ConsentRequiredException,
    DownstreamAuthRejectedException,
    DownstreamUnauthorizedException,
    InternalServerException,
    McpGatewayException,
    UrlElicitationRequiredException,
)
from registry_pkgs.models import ResourceType
from registry_pkgs.models.enums import McpAuthMode
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.telemetry.trace_propagation import (
    BAGGAGE_KEY_MCP_SERVER_ID,
    BAGGAGE_KEY_MCP_TOOL_NAME,
    bounded_baggage_value,
    inject_trace_context,
)

from ...auth.dependencies import UserContextDict
from ...constants import UrlElicitationInputKey
from ...core.config import settings
from ...core.mcp_config import NETWORK_TRANSPORTS, MCPTransport
from ...core.mcp_downstream import DownstreamCallUnauthorized, call_downstream_tool
from ...core.telemetry_decorators import (
    PromptExecutionMetricsContext,
    ResourceAccessMetricsContext,
    ToolExecutionMetricsContext,
)
from ...services.access_control_service import ACLService
from ...services.generated_token_policy import is_consent_exempt
from ...utils.mcp_headers import build_authenticated_headers
from ...utils.otel_metrics import record_server_request
from ..core.types import McpAppContext
from ..tracing import trace_tool_execution
from .types import get_meta_field
from .utils import (
    _build_url_elicitation_result,
    _extract_authenticated_user_context,
    _get_state_metadata,
    authorization_declined_result,
    get_client_params,
    get_progress_token,
    get_target_url,
)

logger = logging.getLogger(__name__)

_DOWNSTREAM_FAILURE_MESSAGE = "Error calling downstream MCP server."
_STALE_SERVER_ID_MESSAGE = (
    "There is no server with the given server_id; the ID is stale or wrong. "
    "Run discover_servers with the same query. Use the new result's tool_name, resource_uri "
    "or prompt_name together with its server_id. Never reuse this server_id."
)


def _get_server_service(ctx: Context[McpAppContext]):
    return ctx.request_context.lifespan_context.server_service


async def _user_can_view_server(
    acl_service: ACLService,
    user_id: str,
    server_id: PydanticObjectId,
) -> bool:
    accessible = await acl_service.get_accessible_resource_ids(
        user_id=PydanticObjectId(user_id),
        resource_type=ResourceType.MCPSERVER.value,
    )
    return str(server_id) in accessible


def _bearer_token(headers: dict[str, str]) -> str:
    """The bearer value of the Authorization header a request carried ("" when absent)."""
    for key, value in headers.items():
        if key.lower() == "authorization" and value.lower().startswith("bearer "):
            return value[len("bearer ") :]
    return ""


def _auth_changed_result(server_name: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Authorization for {server_name} changed during this call; retry it.")],
        isError=True,
    )


def _downstream_input_result(
    requests: list[ElicitRequestURLParams],
    *,
    has_other: bool = False,
) -> CallToolResult:
    lines = [
        f"{request.message} Show the user this URL: {request.url}. Retry after the user finishes."
        for request in requests
    ]
    if has_other or not lines:
        lines.append("The downstream server asked for interactive input that Jarvis Registry cannot relay.")
    return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))], isError=True)


async def _call_with_oauth_recovery(
    ctx: Context[McpAppContext],
    *,
    server: ExtendedMCPServer,
    user_context: UserContextDict,
    target_url: str,
    base_headers: dict[str, str],
    ctx_baggage: Any,
    tool_name: str,
    arguments: dict[str, Any] | None,
    metrics_ctx: ToolExecutionMetricsContext,
) -> CallToolResult | InputRequiredResult:
    """Build fresh headers and call through the SDK, recovering once from a downstream 401.

    For OAuth servers, a 401 (on session init or the call) gets one refresh and one retry; a 401 on
    the retry goes to the loop guard (``recover_from_unauthorized`` with ``is_retry=True``). At most
    two downstream sends happen: if the header rebuild after the retry's 401 still yields a token
    (a concurrent login wrote one), the caller gets a "changed during this call" result instead of
    a third send.

    Returns:
        The typed downstream result, or a ``CallToolResult`` error for the send limit.

    Raises:
        UrlElicitationRequiredException: The header rebuild requires an interactive login.
        DownstreamAuthRejectedException: The loop guard tripped.
    """
    lifespan = ctx.request_context.lifespan_context
    oauth_service = lifespan.oauth_service
    configured_type = server.config.get("type")
    try:
        transport_type = MCPTransport(configured_type)
    except ValueError:
        transport_type = None
    if transport_type not in NETWORK_TRANSPORTS:
        metrics_ctx.set_error_type("unsupported_transport")
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f"The '{server.serverName}' MCP server uses the '{configured_type}' transport, "
                    "which Jarvis Registry cannot call.",
                )
            ],
            isError=True,
        )
    state_metadata = _get_state_metadata(get_client_params(ctx))
    oauth_enabled = server.mcp_auth_mode is McpAuthMode.OAUTH

    async def build_headers(extra: dict[str, str]) -> dict[str, str]:
        headers = await build_authenticated_headers(
            oauth_service=oauth_service,
            server=server,
            auth_context=user_context,
            additional_headers=extra,
            state_metadata=state_metadata,
            redis_client=lifespan.redis_client,
        )
        # Propagate W3C trace context to the downstream MCP server.
        return inject_trace_context(headers, context=ctx_baggage)

    sent_headers: dict[str, str] = {}

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        try:
            await ctx.report_progress(progress, total, message)
        except Exception:
            logger.exception("Unable to forward downstream progress")

    async def on_log(params: LoggingMessageNotificationParams) -> None:
        try:
            await ctx.session.send_notification(
                LoggingMessageNotification(params=params), related_request_id=ctx.request_context.request_id
            )
        except Exception:
            logger.exception("Unable to forward downstream log")

    async def send() -> CallToolResult | InputRequiredResult:
        nonlocal sent_headers
        sent_headers = await build_headers(dict(base_headers))
        return await call_downstream_tool(
            url=target_url,
            transport_type=transport_type,
            server_id=str(server.id),
            user_id=user_context["user_id"],
            headers=sent_headers,
            tool_name=tool_name,
            arguments=arguments,
            downstream_transport=lifespan.downstream_transport,
            call_limiter=lifespan.downstream_call_limiter,
            response_cache=lifespan.downstream_response_cache,
            era_cache=lifespan.downstream_era_cache,
            on_progress=on_progress if get_progress_token(ctx) is not None else None,
            on_log=on_log if ctx.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS else None,
        )

    is_retry = False
    while True:
        try:
            result = await send()
            if isinstance(result, CallToolResult) and result.is_error:
                metrics_ctx.set_error_type("downstream_tool_error")
            return result
        except DownstreamUnauthorizedException as exc:
            if not oauth_enabled or (isinstance(exc, DownstreamCallUnauthorized) and not exc.oauth_retry_safe):
                raise
            logger.info(f"Downstream {server.serverName} answered 401 (retry={is_retry}); running OAuth recovery")
            await oauth_service.recover_from_unauthorized(
                user_context["user_id"],
                server,
                rejected_access_token=_bearer_token(sent_headers),
                www_authenticate=exc.www_authenticate,
                is_retry=is_retry,
            )
            if is_retry:
                # Send limit: a rebuild that raises (login required) propagates; one that yields a
                # token is not sent.
                await build_headers(dict(base_headers))
                metrics_ctx.set_error_type("downstream_auth_changed")
                return _auth_changed_result(server.serverName)
            is_retry = True


@trace_tool_execution
async def execute_tool_impl(
    ctx: Context[McpAppContext],
    tool_name: str,
    arguments: dict[str, Any],
    server_id: str,
) -> CallToolResult | InputRequiredResult:
    """
    Execute a specific downstream MCP tool.

    Args:
        ctx: The mcp.server.mcpserver.Context object that can be DI'ed into a tool call handler function.
        tool_name: Resolved tool name to send to MCP server (e.g., 'tavily_search')
        arguments: Tool-specific arguments as key-value pairs
        server_id: Server ID from discovery (e.g., '6972e222755441652c23090f')

    Returns: CallToolResult

    Raises:
        UrlElicitationRequiredError: The `mcp` package turns this into a URL mode elicitation error response to client.
        McpGatewayException: All classifiable exceptions are raised as subclasses of McpGatewayException.
            Caller of this function should filter on the subclasses to deal with specific exceptions differently.
            E.g. catch a UrlElicitationRequiredException in order to return a URL mode elicitation to client.
        MCPError: Raised when downstream MCP returned a JSON-RPC error response. We raise this exception from
            the MCP Python SDK to forward the error response to our client.
    """

    async with ToolExecutionMetricsContext(tool_name=tool_name, method="POST") as metrics_ctx:
        try:
            user_context = _extract_authenticated_user_context(ctx)
            if user_context is None:
                logger.warning(
                    "execute_tool: missing or invalid authenticated user context; rejecting server_id=%s", server_id
                )
                metrics_ctx.set_error_type("unauthenticated")
                return CallToolResult(
                    content=[TextContent(type="text", text="Authentication required: missing user context.")],
                    isError=True,
                )

            username = user_context.get("username", "unknown")
            user_id = user_context["user_id"]
            logger.info(f"Tool execution from user '{username}:{user_id}': {tool_name} on {server_id}")

            server = await _get_server_service(ctx).get_server_by_id(server_id)
            if server is None:
                # Invalid input. Return a JSON-RPC **result response** with `isError=True` so that LLM can try another request.
                metrics_ctx.set_error_type("server_not_found")
                return CallToolResult(
                    content=[TextContent(type="text", text=_STALE_SERVER_ID_MESSAGE)],
                    isError=True,
                )

            metrics_ctx.set_server_name(server.serverName)

            lifespan = ctx.request_context.lifespan_context
            try:
                can_view = await _user_can_view_server(lifespan.acl_service, user_id, server.id)
            except RuntimeError:
                logger.error(
                    "execute_tool: ACL lookup failed user_id=%s server_id=%s",
                    user_id,
                    server_id,
                    exc_info=True,
                )
                metrics_ctx.set_error_type("acl_lookup_failed")
                return CallToolResult(
                    content=[TextContent(type="text", text="Service temporarily unavailable. Please try again later.")],
                    isError=True,
                )
            if not can_view:
                logger.warning(
                    "execute_tool: user_id=%s denied access to server_id=%s path=%s",
                    user_id,
                    server_id,
                    server.path,
                )
                metrics_ctx.set_error_type("access_denied")
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Access denied: server {server_id!r} is not in your accessible set.",
                        )
                    ],
                    isError=True,
                )

            declined = authorization_declined_result(ctx)
            if declined is not None:
                metrics_ctx.set_error_type("authorization_declined")
                return declined

            if server.is_tool_disabled(tool_name):
                logger.info(
                    "execute_tool: tool_name=%s is disabled on server_id=%s",
                    tool_name,
                    server_id,
                )
                metrics_ctx.set_error_type("tool_disabled")
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=(
                                f"Tool {tool_name!r} is currently disabled on server {server_id!r}. "
                                "Do not retry this tool. Choose another matching discovered candidate "
                                "or tell the user it is unavailable."
                            ),
                        )
                    ],
                    isError=True,
                )

            client_id = user_context["client_id"]
            requires_server_consent = not is_consent_exempt(
                client_id,
                settings.headless_agent_client_id,
            )
            if requires_server_consent and not await run_in_threadpool(
                lifespan.consent_store.has_server_consent,
                user_id,
                client_id,
                server.path,
            ):
                nonce = secrets.token_urlsafe(32)
                elicitation_id = str(uuid4())
                state_metadata = _get_state_metadata(get_client_params(ctx))
                await run_in_threadpool(
                    lifespan.pending_consent_store.save,
                    nonce,
                    {
                        "user_id": user_id,
                        "client_id": client_id,
                        "server_path": server.path,
                        **state_metadata,
                        "elicitation_id": elicitation_id,
                    },
                )
                auth_url = f"{settings.registry_client_url}/consent/server?nonce={nonce}"
                raise ConsentRequiredException(
                    "Consent required to call this MCP server",
                    auth_url=auth_url,
                    server_name=server.serverName,
                    elicitation_id=elicitation_id,
                )

            # Track server request count
            record_server_request(server.serverName)

            # Build target URL using shared helper
            target_url = get_target_url(server)

            # Prepare base headers for downstream MCP server
            additional_headers = {
                "X-Tool-Name": bounded_baggage_value(tool_name),
                "Accept": "application/json, text/event-stream",  # MCP servers require both
            }

            # Trace-context baggage reused for every downstream call in this tool execution
            ctx_baggage = baggage.set_baggage(BAGGAGE_KEY_MCP_TOOL_NAME, bounded_baggage_value(tool_name))
            ctx_baggage = baggage.set_baggage(
                BAGGAGE_KEY_MCP_SERVER_ID, bounded_baggage_value(server_id), context=ctx_baggage
            )

            try:
                result = await _call_with_oauth_recovery(
                    ctx,
                    server=server,
                    user_context=user_context,
                    target_url=target_url,
                    base_headers=additional_headers,
                    ctx_baggage=ctx_baggage,
                    tool_name=tool_name,
                    arguments=arguments,
                    metrics_ctx=metrics_ctx,
                )
            except MCPError as exc:
                if exc.code != URL_ELICITATION_REQUIRED:
                    raise
                try:
                    requests = UrlElicitationRequiredError.from_error(exc.error).elicitations
                except (ValidationError, ValueError, TypeError, AttributeError):
                    requests = []
                metrics_ctx.set_error_type("downstream_input_required")
                return _downstream_input_result(requests)
            if isinstance(result, InputRequiredResult):
                requests = [
                    request.params
                    for request in (result.input_requests or {}).values()
                    if isinstance(request, ElicitRequest) and isinstance(request.params, ElicitRequestURLParams)
                ]
                metrics_ctx.set_error_type("downstream_input_required")
                has_other = len(requests) != len(result.input_requests or {}) or not requests
                return _downstream_input_result(requests, has_other=has_other)
            metrics_ctx.set_success(not result.is_error)
            return result
        except UrlElicitationRequiredException as exc:
            # Re-auth required: a handled failure mode, not a downstream/internal error.
            metrics_ctx.set_error_type("url_elicitation")
            template = (
                f"Calling the '{exc.server_name}' MCP server requires the user to authorize (or re-authorize) it in a "
                "browser. Show the user this {} so they can open it and authorize, then retry the same tool call once "
                "they confirm they are done."
            )
            human_message = (
                f"The tokens for the '{exc.server_name}' MCP server managed by Jarvis Registry have expired. "
                "Please follow the URL to perform re-authorization in a browser window and come back again."
            )
            result = _build_url_elicitation_result(
                ctx,
                exc.auth_url,
                template,
                human_message,
                input_key=UrlElicitationInputKey.AUTHORIZATION,
            )
            if isinstance(result, InputRequiredResult):
                metrics_ctx.set_input_required()
            return result
        except ConsentRequiredException as exc:
            metrics_ctx.set_error_type("consent_required")
            template = (
                f"Calling the '{exc.server_name}' MCP server requires the user's explicit consent in a browser. "
                "Show the user this {} so they can open it and approve, then retry the same tool call once they "
                "confirm they are done."
            )
            human_message = (
                "Jarvis Registry needs your explicit consent before this application can call the "
                f"'{exc.server_name}' MCP server on your behalf. Please follow the URL to review and approve."
            )
            result = _build_url_elicitation_result(
                ctx,
                exc.auth_url,
                template,
                human_message,
                input_key=UrlElicitationInputKey.CONSENT,
                elicitation_id=exc.elicitation_id,
            )
            if isinstance(result, InputRequiredResult):
                metrics_ctx.set_input_required()
            return result
        except DownstreamAuthRejectedException as exc:
            # Loop guard: a freshly issued token was rejected. Tell the LLM instead of starting another login.
            metrics_ctx.set_error_type("downstream_auth_rejected")
            return CallToolResult(content=[TextContent(type="text", text=str(exc))], isError=True)
        except (McpGatewayException, MCPError):
            # These exceptions have been logged and should just bubble up to the caller as a way of communication.
            # __aexit__ records status=failure with error_type = exception class name.
            raise
        except Exception as exc:
            # These are unclassified runtime exceptions. Log them and wrap them as InternalServerException
            # to avoid leaking implementation details to our clients.
            msg = "unexpected exception during execute_tool_impl"
            logger.exception(msg)  # Want stack trace here.

            raise InternalServerException(msg) from exc


async def read_resource_impl(
    user_context: UserContextDict,
    server_id: str,
    resource_uri: str,
    ctx: Context[McpAppContext],
) -> CallToolResult:
    """
    Read/access a resource from an MCP server.

    Args:
        user_context: The `Request.state.user: UserContextDict` attribute set by UnifiedAuthMiddleware
        server_id: Server ID from discovery
        resource_uri: Resource URI to read (e.g., 'tavily://search-results/AI')

    Returns:
        Resource contents (text, JSON, binary, etc.)

    Example:
        result = await read_resource_impl(
            user_context=user_context,
            server_id="6972e222755441652c23090f",
            resource_uri="tavily://search-results/AI"
        )
    """
    async with ResourceAccessMetricsContext() as metrics_ctx:
        try:
            username = user_context.get("username", "unknown")
            logger.info(f"resource read request from user '{username}' - {resource_uri} on {server_id}")

            server = None
            server = await _get_server_service(ctx).get_server_by_id(server_id)
            if server is None:
                # Invalid input. Return a JSON-RPC **result response** with `isError=True` so that LLM can try another request.
                metrics_ctx.set_error_type("server_not_found")
                return CallToolResult(
                    content=[TextContent(type="text", text=_STALE_SERVER_ID_MESSAGE)],
                    isError=True,
                )

            metrics_ctx.set_server_name(server.serverName)

            logger.info(f"Reading resource: {resource_uri} from server {server_id}")

            # Track server request count
            record_server_request(server.serverName)

            # MOCK: Return hardcoded response for POC
            logger.info(f"(MOCK) Returning cached search results for: {resource_uri}")

            # MOCK, NOTE: Use the following structure to return to our client the result of a `resource/read` from downstream MCPs.
            # Regarding the use of `EmbeddedResource`, see https://modelcontextprotocol.io/specification/2025-11-25/schema#embeddedresource
            # Regarding the use of the `_meta` field, see https://modelcontextprotocol.io/specification/2025-11-25/basic/index#_meta
            # In general, the `_meta` field should be attached to the **most specific** object whose schema allows it.
            result = CallToolResult(
                content=[
                    EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            # v2's TextResourceContents.uri is a plain str (was AnyUrl in v1).
                            uri=resource_uri,
                            text='{"results": [{"title": "AI News", "snippet": "Latest AI developments..."}]}',
                            mimeType="application/json",
                            _meta=get_meta_field(True, server_id, server.path),
                        ),
                    )
                ]
            )
            metrics_ctx.set_success(not result.is_error)
            return result

        except Exception:
            logger.exception(f"resource read failed for server_id: {server_id}")

            if server is not None:
                err_msg = f"failed to read resource from downstream server {server.path if server.path else server.serverName}."
            else:
                err_msg = "failed to read resource from downstream server."

            raise InternalServerException(err_msg)


async def execute_prompt_impl(
    user_context: UserContextDict,
    server_id: str,
    prompt_name: str,
    arguments: dict[str, Any] | None,
    ctx: Context[McpAppContext],
) -> CallToolResult:
    """
    Execute a prompt from an MCP server.

    Args:
        server_id: Server ID from discovery
        prompt_name: Name of the prompt to execute
        arguments: Optional prompt arguments
        ctx: FastMCP context with user auth

    Returns:
        Prompt messages ready for LLM consumption

    Example:
        result = await execute_prompt_impl(
            server_id="6972e222755441652c23090f",
            prompt_name="research_assistant",
            arguments={
                "topic": "Artificial Intelligence",
                "depth": "comprehensive"
            }
        )
    """
    async with PromptExecutionMetricsContext(prompt_name=prompt_name) as metrics_ctx:
        try:
            username = user_context.get("username", "unknown")
            logger.info(f"Prompt execution request from user '{username}': {prompt_name} on {server_id}")

            server = None
            server = await _get_server_service(ctx).get_server_by_id(server_id)
            if server is None:
                # Invalid input. Return a JSON-RPC **result response** with `isError=True` so that LLM can try another request.
                metrics_ctx.set_error_type("server_not_found")
                return CallToolResult(
                    content=[TextContent(type="text", text=_STALE_SERVER_ID_MESSAGE)],
                    isError=True,
                )

            metrics_ctx.set_server_name(server.serverName)

            logger.info(f"Executing prompt: {prompt_name} on server {server_id}")

            # Track server request count
            record_server_request(server.serverName)

            # MOCK: Return hardcoded prompt response for POC
            topic = arguments.get("topic", "general topic") if arguments is not None else "general topic"
            depth = arguments.get("depth", "basic") if arguments is not None else "basic"

            logger.info(f"(MOCK) Returning prompt messages for: {prompt_name} (topic={topic}, depth={depth})")

            # MOCK, NOTE: The client is using a tool call of mcpgw to fetch prompts from downstream MCP servers.
            # Therefore our handler function must return a CallToolResult, not a GetPromptResult, in order to be spec-compliant.
            # Maybe one day the MCP spec will explicitly specify the response schema in this forwarding-prompts-via-tool scenario.
            # Until then, we need to return all prompt texts combined, possibly with some prefix,
            # because the `content` field in CallToolResult is required.
            combined_prompt_text = "\n\n".join(
                [
                    "The client is using a tool call of mcpgw to fetch prompts from downstream MCP servers.",
                    "Therefore our handler function must return a CallToolResult, instead of GetPromptResult.",
                    "Maybe one day the MCP spec will explicitly specify the response schema in this forwarding-prompts-via-tool scenario.",
                    "Until then, we simply return all prompt texts joined by newlines.",
                ]
            )

            # MOCK, NOTE: Return hardcoded prompt response for POC. Maybe we can include the `result` field of the JSON-RPC
            # response from downstream MCP prompt call as the optional `structuredContent` field.
            result = CallToolResult(
                content=[
                    TextContent(
                        type="text", text=combined_prompt_text, _meta=get_meta_field(True, server_id, server.path)
                    )
                ],
                structuredContent={
                    "description": f"This is the structured response from the {prompt_name} prompt forwarded to downstream MCP server {server.serverName}",
                    "messages": [
                        {
                            "role": "system",
                            "content": {
                                "type": "text",
                                "text": f"You are a research assistant specializing in {topic}. Provide {depth} analysis.",
                            },
                        },
                        {"role": "user", "content": {"type": "text", "text": f"Research and analyze: {topic}"}},
                    ],
                },
            )
            metrics_ctx.set_success(not result.is_error)
            return result

        except Exception:
            logger.exception(f"Prompt execution failed for server_id: {server_id}")

            if server is not None:
                err_msg = f"failed to execute prompt from downstream server {server.path if server.path else server.serverName}."
            else:
                err_msg = "failed to execute prompt from downstream server."

            raise InternalServerException(err_msg)


# ============================================================================
# Tool Factory Functions for Registration
# ============================================================================


def get_tools() -> list[tuple[str, Callable]]:
    """
    Export tools for registration in server.py.

    Returns:
        List of (tool_name, tool_function) tuples ready for registration
    """

    # Define tool wrapper function with proper signature and decorators
    async def execute_tool(
        ctx: Context[McpAppContext],
        tool_name: Annotated[
            str,
            Field(
                description=(
                    "The canonical downstream MCP tool name from a discover_servers result. "
                    "Use the exact `tool_name` value returned by discovery. "
                    "Do not transform casing, do not derive from wrapper names, and do not use aliases."
                )
            ),
        ],
        arguments: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Tool input parameters as a key-value dict. "
                    "Read the `description` field of the discovery result for parameter names, types, "
                    "and required/optional status (format: 'Parameters: name (type, required/optional, description), ...'). "
                    "Pass all required parameters; include optional ones when they improve the result."
                )
            ),
        ],
        server_id: Annotated[
            str,
            Field(
                description=(
                    "The `server_id` from the SAME discover_servers result as `tool_name`. "
                    "Always pair the exact `tool_name` and `server_id` from the same entity result."
                )
            ),
        ],
    ) -> CallToolResult | InputRequiredResult:
        """Execute one downstream MCP tool returned by discover_servers.
        Use only a result with entity_type='tool', pairing its exact tool_name and server_id.

        Discovery already resolved the canonical downstream name for tools/call.params.name.
        Do not translate wrapper/display names, mcpToolName, mcp_tool_name, or casing variants.
        Do not perform a second lookup to resolve the tool name. Follow the discovered input schema.

        Example: discovery returns tool_name='tavily_search' and server_id='abc123'.
        execute_tool(tool_name="tavily_search", server_id="abc123", arguments={"query": "AI trends"})"""
        return await execute_tool_impl(
            ctx,
            tool_name,
            arguments,
            server_id,
        )

    async def read_resource(
        ctx: Context[McpAppContext],
        server_id: Annotated[
            str,
            Field(
                description=(
                    "The `server_id` field from the discover_servers result (e.g., '6972e222755441652c23090f'). "
                    "Must be from the same result as resource_uri."
                )
            ),
        ],
        resource_uri: Annotated[
            str,
            Field(
                description=(
                    "The `resource_uri` field from the discover_servers result. "
                    "If the URI is a template (e.g., 'github://repo/{owner}/{repo}'), fill in the variables before calling."
                )
            ),
        ],
    ) -> CallToolResult:
        """Read URI-addressable data or cached content from a registered MCP server.
        Call after discover_servers returns entity_type='resource'; pair resource_uri with server_id from that same result.

        Resources expose cached results, file-like content, API snapshots, or configuration data.
        Use this to read content rather than execute an action.
        Use the discovered URI verbatim, except to fill all {variable} placeholders in URI templates.

        Example: discovery returns resource_uri='tavily://search-results/{query}' and server_id='abc123'.
        read_resource(server_id="abc123", resource_uri="tavily://search-results/AI+trends")

        Returns resource contents: text, JSON, binary, or structured data depending on the resource type."""
        return await read_resource_impl(
            ctx.request_context.request.state.user,  # type: ignore[union-attr]
            server_id,
            resource_uri,
            ctx,
        )

    async def execute_prompt(
        ctx: Context[McpAppContext],
        server_id: Annotated[
            str,
            Field(
                description=(
                    "The `server_id` field from the discover_servers result. "
                    "Must be from the same result as prompt_name."
                )
            ),
        ],
        prompt_name: Annotated[
            str,
            Field(
                description=(
                    "The `prompt_name` field from the discover_servers result — use verbatim. "
                    "Examples: 'research_assistant', 'code_reviewer', 'fact_checker'."
                )
            ),
        ],
        arguments: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Prompt arguments as key-value pairs. "
                    "Read the `description` field of the discovery result for argument names and required/optional status "
                    "(format: 'Required: arg1 (description), ... | Optional: arg2 (description), ...'). "
                    "Pass None or omit if the prompt takes no arguments."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        """Execute a reusable prompt template from a registered MCP server.
        Call after discover_servers returns entity_type='prompt'; pair its exact prompt_name with server_id from the same result.

        Prompts provide expert guidance, standardized workflows, or multi-turn templates.
        The discovery result's description lists required and optional arguments.
        Pass all required arguments and any relevant optional ones; omit arguments if none are needed.
        Never invent or rename prompt_name.

        Example: discovery returns prompt_name='code_reviewer', server_id='abc123', and requires language.
        execute_prompt(server_id="abc123", prompt_name="code_reviewer", arguments={"language": "python"})

        Returns prompt messages ready for LLM consumption. Incorporate them into your context or present them to the user."""
        return await execute_prompt_impl(
            ctx.request_context.request.state.user,  # type: ignore[union-attr]
            server_id,
            prompt_name,
            arguments,
            ctx,
        )

    # Return list of (name, function) tuples
    return [
        ("execute_tool", execute_tool),
        ("read_resource", read_resource),
        ("execute_prompt", execute_prompt),
    ]
