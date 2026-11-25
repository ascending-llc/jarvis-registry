"""
MCP Gateway Registry API Tools
- execute_tool: Execute tools from any MCP server
- read_resource: Read/access resources from any MCP server
- get_prompt: Retrieve rendered prompts from any MCP server
"""

import logging
import secrets
from collections.abc import Callable
from functools import partial
from typing import Annotated, Any, cast, overload
from uuid import uuid4

from beanie import PydanticObjectId
from mcp.server.mcpserver import Context
from mcp.shared.exceptions import MCPError, UrlElicitationRequiredError
from mcp.types import (
    URL_ELICITATION_REQUIRED,
    CallToolResult,
    ContentBlock,
    ElicitRequest,
    ElicitRequestURLParams,
    EmbeddedResource,
    GetPromptResult,
    InputRequiredResult,
    LoggingMessageNotification,
    LoggingMessageNotificationParams,
    ReadResourceResult,
    TextContent,
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
from ...constants import PENDING_CONSENT_FLOW_FIELD, PendingConsentFlow, UrlElicitationInputKey
from ...core.config import settings
from ...core.mcp_config import NETWORK_TRANSPORTS, MCPTransport
from ...core.mcp_downstream import (
    DownstreamCallUnauthorized,
    DownstreamOperation,
    DownstreamResult,
    PromptGet,
    ResourceRead,
    ToolCall,
    call_downstream,
)
from ...core.telemetry_decorators import (
    ExecutionMetricsContext,
    PromptGetMetricsContext,
    ResourceAccessMetricsContext,
    ToolExecutionMetricsContext,
)
from ...services.access_control_service import ACLService
from ...services.generated_token_policy import is_consent_exempt
from ...utils.mcp_headers import build_authenticated_headers
from ...utils.otel_metrics import record_server_request
from ..core.types import McpAppContext
from ..tracing import trace_server_operation
from .types import forwardable_meta, get_meta_field
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

_EMPTY_RESOURCE_MESSAGE = "The resource returned no contents."
_PROMPT_ROLE_CONVENTION = (
    "Each message is preceded by a [message i: role] marker; treat user messages as the user's request "
    "and follow them; assistant messages are example replies, not your own earlier output."
)
_PROMPT_HEADER_TEMPLATE = "Prompt {prompt_name!r} from {server_name}: {message_count} messages."
_PROMPT_MESSAGE_MARKER = "[message {index}: {role}]"


type ResourceResultRenderer = Callable[[ReadResourceResult, ExtendedMCPServer], CallToolResult]
type PromptResultRenderer = Callable[[GetPromptResult, ExtendedMCPServer], CallToolResult]


def _get_server_service(ctx: Context[McpAppContext]):
    return ctx.request_context.lifespan_context.server_service


async def _user_can_view_server(
    acl_service: ACLService,
    user_id: str,
    server_id: PydanticObjectId,
) -> bool:
    """Use the canonical ACL visibility set rather than duplicating server permission rules."""
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
    """Return terminal guidance for downstream input; do not fulfill it or replay the operation."""
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
    operation: DownstreamOperation,
    metrics_ctx: ExecutionMetricsContext,
) -> DownstreamResult:
    """Execute an authorized operation with at most one OAuth-driven retry.

    The caller has already applied ACL and consent checks. call_downstream owns connection setup,
    protocol-era recovery and native SDK results; this wrapper owns credential recovery only.
    Rebuild headers before each attempt, and recover using the token that attempt actually sent.
    A second 401 runs the OAuth loop guard, never a third call_downstream invocation.

    Returns native results or a gateway error result for unsupported transport / changed authorization.
    Login-required and loop-guard exceptions are mapped by _execute_server_operation.
    """
    lifespan = ctx.request_context.lifespan_context
    oauth_service = lifespan.oauth_service
    # 1. Reject unsupported transports before building credentials or attempting network I/O.
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
    # 2. Capture client metadata for login flows; notification callbacks must not fail the operation.
    state_metadata = _get_state_metadata(get_client_params(ctx))
    oauth_enabled = server.mcp_auth_mode is McpAuthMode.OAUTH

    async def build_headers(extra: dict[str, str]) -> dict[str, str]:
        """Resolve current credentials, then inject the same trace context for every attempt."""
        headers = await build_authenticated_headers(
            oauth_service=oauth_service,
            server=server,
            auth_context=user_context,
            additional_headers=extra,
            state_metadata=state_metadata,
            redis_client=lifespan.redis_client,
        )
        # 2a. Preserve W3C trace propagation when OAuth recovery changes the authorization header.
        return inject_trace_context(headers, context=ctx_baggage)

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

    async def send(headers: dict[str, str]) -> DownstreamResult:
        """Send with this attempt's explicit header snapshot, without changing closure state."""
        return await call_downstream(
            url=target_url,
            transport_type=transport_type,
            server_id=str(server.id),
            user_id=user_context["user_id"],
            headers=headers,
            operation=operation,
            downstream_transport=lifespan.downstream_transport,
            response_cache=lifespan.downstream_response_cache,
            era_cache=lifespan.downstream_era_cache,
            on_progress=on_progress
            if isinstance(operation, ToolCall) and get_progress_token(ctx) is not None
            else None,
            on_log=on_log if ctx.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS else None,
        )

    sent_headers: dict[str, str] = {}
    is_retry = False
    while True:
        try:
            # 3. Rebuild from a fresh base-header copy. Keep the snapshot local to this loop
            #    and pass it explicitly to send so recovery uses the credential actually sent.
            sent_headers = await build_headers(dict(base_headers))
            # 4. Return completed native results unchanged. Only tool results carry is_error here.
            result = await send(sent_headers)
            if isinstance(result, CallToolResult) and result.is_error:
                metrics_ctx.set_error_type("downstream_tool_error")
            return result
        except DownstreamUnauthorizedException as exc:
            # 5. Recover only OAuth failures from retry-safe phases. Result validation and
            #    teardown failures must not cause the business operation to run again.
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
                # 6. The retry was also rejected. A header rebuild may request login or observe
                #    a concurrent token update, but neither outcome permits a third send.
                await build_headers(dict(base_headers))
                metrics_ctx.set_error_type("downstream_auth_changed")
                return _auth_changed_result(server.serverName)
            is_retry = True


def _operation_label(operation: DownstreamOperation) -> str:
    return {ToolCall: "execute_tool", ResourceRead: "read_resource", PromptGet: "get_prompt"}[type(operation)]


async def _authorize_server_call(
    ctx: Context[McpAppContext],
    operation: DownstreamOperation,
    server_id: str,
    metrics_ctx: ExecutionMetricsContext,
) -> tuple[ExtendedMCPServer, UserContextDict] | CallToolResult:
    """Apply access gates in order, returning an authorized server/user pair or a gateway error.

    Keep consent last: missing identity, stale IDs, ACL denial, declined authorization and disabled
    tools must stop before pending-consent state is created. ConsentRequiredException is handled
    by the shared execution pipeline, not converted into an internal failure here.
    """
    label = _operation_label(operation)
    # 1. Require an authenticated user before accessing server records or authorization stores.
    user_context = _extract_authenticated_user_context(ctx)
    if user_context is None:
        logger.warning("%s: missing or invalid authenticated user context; rejecting server_id=%s", label, server_id)
        metrics_ctx.set_error_type("unauthenticated")
        return CallToolResult(
            content=[TextContent(type="text", text="Authentication required: missing user context.")],
            isError=True,
        )

    user_id = user_context["user_id"]
    # Tool names are bounded and safe to log; resource URIs and prompt arguments never are.
    tool_field = f" tool_name={operation.tool_name}" if isinstance(operation, ToolCall) else ""
    logger.info(
        "%s: server call%s server_id=%s request_id=%s",
        label,
        tool_field,
        server_id,
        getattr(ctx.request_context, "request_id", None),
    )

    # 2. Resolve the discovered server ID; stale IDs are recoverable tool-result errors.
    server = await _get_server_service(ctx).get_server_by_id(server_id)
    if server is None:
        metrics_ctx.set_error_type("server_not_found")
        return CallToolResult(
            content=[TextContent(type="text", text=_STALE_SERVER_ID_MESSAGE)],
            isError=True,
        )

    metrics_ctx.set_server_name(server.serverName)

    # 3. Enforce server visibility. A failed ACL lookup is unavailable, never implicit permission.
    lifespan = ctx.request_context.lifespan_context
    try:
        can_view = await _user_can_view_server(lifespan.acl_service, user_id, server.id)
    except RuntimeError:
        logger.error(
            "%s: ACL lookup failed user_id=%s server_id=%s",
            label,
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
            "%s: user_id=%s denied access to server_id=%s path=%s",
            label,
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

    # 4. Honor a declined authorization answer before opening another consent flow.
    declined = authorization_declined_result(ctx)
    if declined is not None:
        metrics_ctx.set_error_type("authorization_declined")
        return declined

    # 5. Apply the disabled-tool gate only to ToolCall; it is not a server-wide resource/prompt gate.
    if isinstance(operation, ToolCall) and server.is_tool_disabled(operation.tool_name):
        tool_name = operation.tool_name
        logger.info(
            "%s: tool_name=%s is disabled on server_id=%s",
            label,
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

    # 6. Check consent last, preserving exempt clients and persisting state for browser approval.
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
                PENDING_CONSENT_FLOW_FIELD: PendingConsentFlow.SERVER,
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

    return server, user_context


@overload
async def _execute_server_operation(
    ctx: Context[McpAppContext],
    operation: ToolCall,
    server_id: str,
    *,
    metrics_ctx: ExecutionMetricsContext,
) -> CallToolResult | InputRequiredResult: ...


@overload
async def _execute_server_operation(
    ctx: Context[McpAppContext],
    operation: ResourceRead,
    server_id: str,
    *,
    metrics_ctx: ExecutionMetricsContext,
    render: ResourceResultRenderer,
) -> CallToolResult | InputRequiredResult: ...


@overload
async def _execute_server_operation(
    ctx: Context[McpAppContext],
    operation: PromptGet,
    server_id: str,
    *,
    metrics_ctx: ExecutionMetricsContext,
    render: PromptResultRenderer,
) -> CallToolResult | InputRequiredResult: ...


@trace_server_operation
async def _execute_server_operation(
    ctx: Context[McpAppContext],
    operation: DownstreamOperation,
    server_id: str,
    *,
    metrics_ctx: ExecutionMetricsContext,
    render: ResourceResultRenderer | PromptResultRenderer | None = None,
) -> CallToolResult | InputRequiredResult:
    """Coordinate authorization, downstream execution and gateway-result mapping for all operations.

    The decorator traces this whole pipeline, including rendering and handled failures. The caller
    owns the metrics context; this function updates it without opening a second metrics lifecycle.
    Registry consent / re-auth may return InputRequiredResult. Downstream interactive requests instead
    become terminal guidance, so they do not start an automatic downstream-input replay loop.
    """
    try:
        # 1. Complete every access gate before counting or sending a downstream request.
        authorized = await _authorize_server_call(ctx, operation, server_id, metrics_ctx)
        if isinstance(authorized, CallToolResult):
            return authorized
        server, user_context = authorized
        # 2. Count the authorized server request and resolve its configured endpoint.
        record_server_request(server.serverName)

        target_url = get_target_url(server)

        # 3. Share Accept and server baggage across operations; add tool identifiers only for ToolCall.
        additional_headers = {
            "Accept": "application/json, text/event-stream",
        }

        ctx_baggage = baggage.set_baggage(BAGGAGE_KEY_MCP_SERVER_ID, bounded_baggage_value(server_id))
        if isinstance(operation, ToolCall):
            additional_headers["X-Tool-Name"] = bounded_baggage_value(operation.tool_name)
            ctx_baggage = baggage.set_baggage(
                BAGGAGE_KEY_MCP_TOOL_NAME, bounded_baggage_value(operation.tool_name), context=ctx_baggage
            )

        # 4. Delegate bounded credential recovery; call_downstream manages the SDK connection.
        try:
            result = await _call_with_oauth_recovery(
                ctx,
                server=server,
                user_context=user_context,
                target_url=target_url,
                base_headers=additional_headers,
                ctx_baggage=ctx_baggage,
                operation=operation,
                metrics_ctx=metrics_ctx,
            )
        except MCPError as exc:
            # 5a. Convert legacy downstream URL elicitation into terminal user guidance.
            if exc.code != URL_ELICITATION_REQUIRED:
                raise
            try:
                requests = UrlElicitationRequiredError.from_error(exc.error).elicitations
            except (ValidationError, ValueError, TypeError, AttributeError):
                requests = []
            metrics_ctx.set_error_type("downstream_input_required")
            return _downstream_input_result(requests)
        # 5b. Apply the same terminal mapping to modern downstream input-required results.
        if isinstance(result, InputRequiredResult):
            requests = [
                request.params
                for request in (result.input_requests or {}).values()
                if isinstance(request, ElicitRequest) and isinstance(request.params, ElicitRequestURLParams)
            ]
            metrics_ctx.set_error_type("downstream_input_required")
            has_other = len(requests) != len(result.input_requests or {}) or not requests
            return _downstream_input_result(requests, has_other=has_other)
        # 6. Render native resource/prompt results inside the span. Already-built tool or gateway
        #    error results bypass renderers, which expect operation-specific native result fields.
        if isinstance(result, CallToolResult):
            # Downstream tool results keep their content and non-reserved _meta; reserved keys describe the
            # downstream connection, not this gateway response.
            rendered = result
            if result.meta and len(meta := forwardable_meta(result.meta)) != len(result.meta):
                rendered = result.model_copy(update={"meta": meta or None})
        elif isinstance(operation, ResourceRead) and isinstance(result, ReadResourceResult) and render is not None:
            # 6a. Overloads enforce the operation/renderer pairing at the call site; the union
            #     implementation needs an explicit narrowing for the corresponding callback.
            rendered = cast(ResourceResultRenderer, render)(result, server)
        elif isinstance(operation, PromptGet) and isinstance(result, GetPromptResult) and render is not None:
            rendered = cast(PromptResultRenderer, render)(result, server)
        else:
            raise TypeError("Downstream result requires a matching operation and renderer")
        metrics_ctx.set_success(not rendered.is_error)
        return rendered
    except UrlElicitationRequiredException as exc:
        # 7a. Registry re-auth is a handled outcome; modern clients may pause for browser login.
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
        # 7b. Registry consent uses its own input key so a retry can distinguish it from re-auth.
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
        # 7c. A rejected refreshed credential is terminal; do not initiate another login loop.
        metrics_ctx.set_error_type("downstream_auth_rejected")
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], isError=True)
    except (McpGatewayException, MCPError):
        # 7d. Preserve classified gateway / protocol errors. The outer metrics context records failure.
        raise
    except Exception as exc:
        # 7e. Wrap unexpected failures without exposing implementation details; retain the cause.
        msg = f"unexpected exception during {_operation_label(operation)}_impl"
        if isinstance(operation, ToolCall):
            logger.exception(msg)
        else:
            # 7f. Exception text and tracebacks can carry the resource URI or prompt arguments
            #     (e.g. a pydantic ValidationError echoes its input), so log only the type.
            logger.error("%s: %s", msg, type(exc).__name__)

        raise InternalServerException(msg) from exc


async def execute_tool_impl(
    ctx: Context[McpAppContext],
    tool_name: str,
    arguments: dict[str, Any],
    server_id: str,
) -> CallToolResult | InputRequiredResult:
    """Keep the public tool signature while delegating to the shared server-operation pipeline.

    The shared pipeline first runs _authorize_server_call, then _call_with_oauth_recovery.
    The OAuth wrapper delegates native requests to call_downstream. Tracing wraps the shared
    pipeline; tool results already match the gateway wire type and need no resource/prompt renderer.
    """
    async with ToolExecutionMetricsContext(tool_name=tool_name, method="POST") as metrics_ctx:
        return await _execute_server_operation(
            ctx,
            ToolCall(tool_name, arguments),
            server_id,
            metrics_ctx=metrics_ctx,
        )


def _render_resource_result(result: ReadResourceResult, server: ExtendedMCPServer) -> CallToolResult:
    """Preserve downstream resource contents while adding gateway provenance at result level."""
    content = [EmbeddedResource(type="resource", resource=item) for item in result.contents]
    return CallToolResult(
        content=content or [TextContent(type="text", text=_EMPTY_RESOURCE_MESSAGE)],
        _meta={**forwardable_meta(result.meta), **get_meta_field(True, str(server.id), server.path)},
    )


def _render_prompt_result(result: GetPromptResult, server: ExtendedMCPServer, *, prompt_name: str) -> CallToolResult:
    """Keep message boundaries and content; role markers do not create native conversation roles."""
    header = _PROMPT_HEADER_TEMPLATE.format(
        prompt_name=prompt_name, server_name=server.serverName, message_count=len(result.messages)
    )
    if result.description:
        header += f"\n{result.description}"
    header += f"\n{_PROMPT_ROLE_CONVENTION}"
    content: list[ContentBlock] = [TextContent(type="text", text=header)]
    for index, message in enumerate(result.messages, start=1):
        content.extend(
            [
                TextContent(type="text", text=_PROMPT_MESSAGE_MARKER.format(index=index, role=message.role)),
                message.content,
            ]
        )
    return CallToolResult(
        content=content,
        _meta={**forwardable_meta(result.meta), **get_meta_field(True, str(server.id), server.path)},
    )


async def read_resource_impl(
    ctx: Context[McpAppContext], server_id: str, resource_uri: str
) -> CallToolResult | InputRequiredResult:
    """Read a downstream resource through the shared authorization and execution pipeline."""
    async with ResourceAccessMetricsContext() as metrics_ctx:
        return await _execute_server_operation(
            ctx, ResourceRead(resource_uri), server_id, metrics_ctx=metrics_ctx, render=_render_resource_result
        )


async def get_prompt_impl(
    ctx: Context[McpAppContext], server_id: str, prompt_name: str, arguments: dict[str, str] | None
) -> CallToolResult | InputRequiredResult:
    """Retrieve a rendered downstream prompt through the shared execution pipeline."""
    async with PromptGetMetricsContext(prompt_name=prompt_name) as metrics_ctx:
        return await _execute_server_operation(
            ctx,
            PromptGet(prompt_name, arguments),
            server_id,
            metrics_ctx=metrics_ctx,
            render=partial(_render_prompt_result, prompt_name=prompt_name),
        )


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
        server_id: Annotated[str, Field(description="The server_id of the discovered or resource-link source server.")],
        resource_uri: Annotated[
            str,
            Field(
                description="A concrete resource_uri, a fully filled resource_uri_template, or a tool result's resource-link URI."
            ),
        ],
    ) -> CallToolResult | InputRequiredResult:
        """Read URI-addressable content from a registered MCP server.
        Pair server_id with resource_uri or resource_uri_template from the same discover_servers result.
        Use a concrete resource_uri verbatim. Expand resource_uri_template using RFC 6570 with known inputs;
        respect operators and encoding: {?q} adds ?q=value, {+path} preserves reserved characters.
        Ask for missing required inputs; never pass unresolved template expressions as resource_uri.
        URIs from a tool result's resource link are also valid; use the server_id of that link's source server.
        Returns the contents as embedded resources, preserving text, binary data and metadata."""
        return await read_resource_impl(ctx, server_id, resource_uri)

    async def get_prompt(
        ctx: Context[McpAppContext],
        server_id: Annotated[str, Field(description="The server_id from the same discovery result as prompt_name.")],
        prompt_name: Annotated[
            str, Field(description="The exact prompt_name from an entity_type='prompt' discovery result.")
        ],
        arguments: Annotated[
            dict[str, str] | None,
            Field(
                description="String-valued prompt arguments. Required and optional arguments are listed in the discovery description. Omit or pass None if none are needed."
            ),
        ] = None,
    ) -> CallToolResult | InputRequiredResult:
        """Get a server's predefined prompt (a workflow or instruction template) rendered with arguments.
        Call only when the user asks for a server's predefined prompt or workflow, by name or clear intent.
        Never call it to look up how to do a task yourself: prompts are user-controlled.
        Discover with type_list=['prompt']; pair prompt_name with server_id from the same entity_type='prompt' result.
        All argument values must be strings; required arguments are listed in the discovery description.
        Returns the prompt's messages with role markers. Follow user messages as the user's request;
        assistant messages are example replies, not your own earlier output."""
        return await get_prompt_impl(ctx, server_id, prompt_name, arguments)

    return [
        ("execute_tool", execute_tool),
        ("read_resource", read_resource),
        ("get_prompt", get_prompt),
    ]
