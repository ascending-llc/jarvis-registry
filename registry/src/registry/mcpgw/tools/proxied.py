"""
MCP Gateway Registry API Tools
- execute_tool: Execute tools from any MCP server
- read_resource: Read/access resources from any MCP server
- execute_prompt: Execute prompts from any MCP server
"""

import json
import logging
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from httpx_sse import EventSource
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from mcp.shared.exceptions import McpError, UrlElicitationRequiredError
from mcp.types import (
    CallToolResult,
    ElicitRequestURLParams,
    EmbeddedResource,
    ErrorData,
    InitializeRequestParams,
    TextContent,
    TextResourceContents,
)
from pydantic import Field
from pydantic.networks import AnyUrl

from ...auth.dependencies import UserContextDict
from ...auth.oauth.flow_state_manager import FlowStateManager
from ...auth.oauth.types import ClientBranding, StateMetadata
from ...core.mcp_client import call_tool_via_sse_ephemeral
from ...utils.otel_metrics import record_server_request
from ..core.types import McpAppContext
from ..exceptions import (
    DownstreamHttpFailureException,
    InternalServerException,
    McpGatewayException,
    MisimplementedSpecException,
    UrlElicitationRequiredException,
)
from .types import get_meta_field
from .utils import build_authenticated_headers, build_target_url, forward_notification, parse_data_field

logger = logging.getLogger(__name__)


def _get_server_service(ctx: Context[ServerSession, McpAppContext]):
    return ctx.request_context.lifespan_context.server_service


def _get_session_store(ctx: Context[ServerSession, McpAppContext]):
    return ctx.request_context.lifespan_context.session_store


def _get_mcp_client_service(ctx: Context[ServerSession, McpAppContext]):
    return ctx.request_context.lifespan_context.mcp_client_service


async def _downstream_tool_call(
    ctx: Context[ServerSession, McpAppContext],
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    transport_type: str = "streamable-http",
    sse_url: str | None = None,
) -> dict:
    """
    Make a tool call to downstream MCP. This function only handles making the HTTP POST request and parsing the response,
    so url, request body, and request headers must be passed in explicitly.

    Args:
        ctx: The mcp.server.fastmcp.Context object that can be DI'ed into a tool call handler function.
        url: Downstream MCP URL
        body: The raw JSON-RPC request body for the tool call
        headers: All HTTP headers for the tool call POST request

    Returns: A Python dictionary parsed from the JSON-RPC response to the tool call.

    Raises:
        DownstreamHttpFailureException: When the HTTP request receives a >=300 status code
        MisimplementedSpecException: When the response deviates from the 2025-11-25 MCP spec
        InternalServerException: Other unknown or rare runtime exceptions
    """
    client = ctx.request_context.lifespan_context.proxy_client

    response_obj: dict | None = None

    try:
        if transport_type == "sse":
            if not sse_url:
                raise MisimplementedSpecException("SSE tool call requires SSE endpoint URL.")

            async def _on_notification(obj: dict[str, Any]) -> None:
                await forward_notification(ctx.session, obj, related_request_id=ctx.request_id)

            try:
                return await call_tool_via_sse_ephemeral(
                    http_client=client,
                    sse_url=sse_url,
                    headers=headers,
                    request_body=body,
                    on_notification=_on_notification,
                    timeout_seconds=30,
                )
            except ConnectionError as exc:
                logger.error(str(exc))
                raise DownstreamHttpFailureException("Error calling downstream MCP server over SSE.") from exc
            except TimeoutError as exc:
                raise MisimplementedSpecException(
                    "Timed out waiting for SSE JSON-RPC response after tool POST (30s)."
                ) from exc
            except ValueError as exc:
                raise MisimplementedSpecException(str(exc)) from exc

        async with client.stream("POST", url, json=body, headers=headers) as resp:
            if not resp.is_success:
                raw_body = await resp.aread()

                logger.error(
                    f"Error calling downstream MCP: status code: {resp.status_code}, body: {raw_body.decode('utf-8')}"
                )

                raise DownstreamHttpFailureException("Error calling downstream MCP server.")
            elif resp.headers.get("content-type", "").startswith("application/json"):
                # If content-type is application/json, read the whole response body and return the parsed dictionary.
                raw_body = await resp.aread()

                response_obj = json.loads(raw_body.decode("utf-8"))

                if not isinstance(response_obj, dict):
                    raise MisimplementedSpecException(
                        "Dowstream MCP responded with content-type application/json to a tool call, "
                        "but the response body is not a valid JSON object."
                    )

                return response_obj
            elif resp.headers.get("content-type", "").startswith("text/event-stream"):
                # Server decided to upgrade to SSE stream, use an EventSource parse the events.
                event_source = EventSource(resp)

                async for event in event_source.aiter_sse():
                    obj, possible_event_type = parse_data_field(event)

                    match possible_event_type:
                        case "notification":
                            # If the event seems a notification, try forwarding it to our client.
                            await forward_notification(ctx.session, obj, related_request_id=ctx.request_id)
                        case "response":
                            # If the event seems a response (error or result), set response_obj to the parsed "data" field of the event.
                            # Only set once because server is not supposed to send a response unrelated to the request on this SSE stream.
                            # After finding the response, we should continue the async for loop to exhaust the response body and
                            # avoid leaking TCP connection resources. If a server is well-implemented according to spec,
                            # it should end the SSE stream after sending the response anyway.
                            # Reference: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#sending-messages-to-the-server
                            if response_obj is None:
                                response_obj = obj
                        case _:
                            # With an irrelevant event, just log it.
                            # NOTE: We are NOT handling reconnect/retry primers from the server.
                            logger.error(f"irrelevant SSE event - data: {event.data}")

                if response_obj is None:
                    raise MisimplementedSpecException(
                        "Downstream MCP server responded to a tool call POST request with SSE stream, "
                        "ended the stream without disconnection, but did not include a JSON-RPC response."
                    )

                return response_obj
            else:
                raise MisimplementedSpecException(
                    "Downstream MCP server did not reply with a Content-Type of application/json or text/event-stream, "
                    "disregarding the Accept header of the request."
                )
    except McpGatewayException as exc:
        logger.error(str(exc))

        raise
    except Exception as exc:
        msg = "unexpected exception when making downstream tool call"
        logger.exception(msg)  # Want stack trace here.

        raise InternalServerException(msg) from exc


def _get_state_metadata(client_params: InitializeRequestParams | None) -> StateMetadata:
    """
    Check if client is one of the three brands: VS Code, Claude, and Cursor.
    If yes, relay this info all the way to frontend for it to use the deep link technology with them.
    """

    if client_params is None:
        return {"client_branding": ClientBranding.UNRECOGNIZED}

    # As of 2026-03-17, below are how mainstream AI agents support URL mode elicitation and how that relate to deep link.
    # VSCode: Perfect support. Its client name is "Visual Studio Code". We recognize this and provide deep link
    #   back to the VSCode app window from our re-auth success page.
    # Claude Desktop: Doesn't support URL mode elicitation, but does recognize our fallback CallToolResult and
    #   provides a link to its user, so UX is good. Its client name starts with "claude-ai", and we recognize this
    #   to provide deep link back to it.
    # Claude Code CLI: Perfect support in the latest version. However, Claude Code CLI runs in the terminal emulator's app window,
    #   so there is no way we can deep link back to it. Its client name is "claude-code".
    # Claude extension for VSCode/Cursor: The extension is basically a bridge between the editor UI and the Claude Code CLI.
    #   Even though Claude Code CLI support URL mode elicitation, when it relays the elicitation back to the extension,
    #   the extension just silently denies it without prompting the user at all. This is a bug and we have filed
    #   an [issue](https://github.com/anthropics/claude-code/issues/35353) for it.
    #   The client names are both "claude-code", so even after the bug is fixed, we cannot provide deep link support
    #   because we cannot tell apart the CLI from the two extensions.
    # Cursor: Doesn't support, but does recognize our fallback, similar to Claude Desktop. Its client name starts with
    #   "probe (via mcp-remote" or "mcp-stdio-client (via mcp-remote", depending on how the MCP server is configured,
    #   and we recognize them to provide deep link back to Cursor.
    name = client_params.clientInfo.name.strip().lower()
    if name == "visual studio code":
        return {"client_branding": ClientBranding.VSCODE}
    elif name.startswith("claude-ai"):
        return {"client_branding": ClientBranding.CLAUDE}
    elif name.startswith("probe (via mcp-remote") or name.startswith("mcp-stdio-client (via mcp-remote"):
        return {"client_branding": ClientBranding.CURSOR}
    else:
        return {"client_branding": ClientBranding.UNRECOGNIZED}


def _support_url_elicitation(client_params: InitializeRequestParams | None) -> bool:
    """
    Report if MCP client supports URL mode elicitation by checking its capabilities.
    """

    if client_params is None:
        return False

    elicitation = client_params.capabilities.elicitation
    if elicitation is None:
        return False

    return elicitation.url is not None


def _get_elicitation_id(auth_url: str) -> str | None:
    """
    Parse the auth_url to obtain the elicitation_id from the "state" query string parameter.
    """

    try:
        parsed = urlsplit(auth_url)

        qs_dict = parse_qs(parsed.query)

        state_str = qs_dict["state"][0]

        state_dict = FlowStateManager.decode_state(state_str)

        elicitation_id = state_dict["meta"]["elicitation_id"]

        if UUID(elicitation_id).version != 4:
            logger.error("elicitation_id from the state dictionary is not a valid UUID4 string.")

            return None

        return elicitation_id
    except Exception:
        logger.exception("failed to extract elicitation_id from auth_url.")

        return None


async def execute_tool_impl(
    ctx: Context[ServerSession, McpAppContext],
    tool_name: str,
    arguments: dict[str, Any],
    server_id: str,
) -> CallToolResult:
    """
    Execute a specific downstream MCP tool.

    Args:
        ctx: The mcp.server.fastmcp.Context object that can be DI'ed into a tool call handler function.
        tool_name: Resolved tool name to send to MCP server (e.g., 'tavily_search')
        arguments: Tool-specific arguments as key-value pairs
        server_id: Server ID from discovery (e.g., '6972e222755441652c23090f')

    Returns: CallToolResult

    Raises:
        UrlElicitationRequiredError: The `mcp` package turns this into a URL mode elicitation error response to client.
        McpGatewayException: All classifiable exceptions are raised as subclasses of McpGatewayException.
            Caller of this function should filter on the subclasses to deal with specific exceptions differently.
            E.g. catch a UrlElicitationRequiredException in order to return a URL mode elicitation to client.
        McpError: Raised when downstream MCP returned a JSON-RPC error response. We raise this exception from
            the MCP Python SDK to forward the error response to our client.
    """

    try:
        user_context: UserContextDict = ctx.request_context.request.state.user  # type: ignore[union-attr]

        username = user_context.get("username", "unknown")
        user_id = user_context.get("user_id", "unknown")
        logger.info(f"Tool execution from user '{username}:{user_id}': {tool_name} on {server_id}")

        server = await _get_server_service(ctx).get_server_by_id(server_id)
        if server is None:
            # Invalid input. Return a JSON-RPC **result response** with `isError=True` so that LLM can try another request.
            return CallToolResult(
                content=[TextContent(type="text", text="There is no server with the given server_id.")],
                isError=True,
            )

        # Track server request count
        record_server_request(server.serverName)

        # Build target URL using shared helper
        target_url = build_target_url(server)

        # Prepare base headers for downstream MCP server
        additional_headers = {
            "X-Tool-Name": tool_name,
            "Accept": "application/json, text/event-stream",  # MCP servers require both
        }

        # Check if server requires initialization (default True for safety/compatibility)
        requires_init = server.config.get("requiresInit", True)
        transport_type = server.config.get("type", "streamable-http")

        state_metadata = _get_state_metadata(ctx.session.client_params)

        # Session management logic - only for streamable-http when initialization is required.
        # For SSE we intentionally do not persist/reuse session IDs or messages URLs.
        # Each SSE tool call opens a fresh stream and completes within _downstream_tool_call.
        if requires_init and transport_type != "sse":
            # Key format: "user_id:server_id" to track per-user, per-server sessions
            session_key = f"{user_id}:{server_id}"
            session_info = _get_mcp_client_service(ctx).get_session(session_key)
            stored_session_id = None

            if session_info:
                # Existing session found - check if it's initialized
                stored_session_id, session_initialized = session_info

                if session_initialized:
                    additional_headers["mcp-Session-Id"] = stored_session_id
                    logger.info(f"Reusing initialized session for {server.serverName}: {stored_session_id}")

            if not stored_session_id:
                init_headers = await build_authenticated_headers(
                    oauth_service=ctx.request_context.lifespan_context.oauth_service,
                    server=server,
                    auth_context=user_context,
                    additional_headers=additional_headers,
                    state_metadata=state_metadata,
                )
                session_id = await _get_mcp_client_service(ctx).initialize_mcp_session(
                    target_url,
                    init_headers,
                    session_key,
                    transport_type,
                )

                if session_id:
                    additional_headers["mcp-Session-Id"] = session_id
                else:
                    logger.warning("Failed to initialize session, will attempt tool call without session")
        elif transport_type == "sse":
            logger.debug("SSE transport selected: using per-call ephemeral downstream session")
        else:
            logger.debug("Stateless server (requiresInit=False), skipping session management")

        # Build final authenticated headers with session ID (if applicable)
        headers = await build_authenticated_headers(
            oauth_service=ctx.request_context.lifespan_context.oauth_service,
            server=server,
            auth_context=user_context,
            additional_headers=additional_headers,
            state_metadata=state_metadata,
        )

        # Build MCP JSON-RPC request
        mcp_request_body = {
            "jsonrpc": "2.0",
            "id": ctx.request_id,  # Forward the "id" field from clients of mcpgw to downstream MCPs.
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        logger.info(f"MCP JSON-RPC request body: {json.dumps(mcp_request_body, indent=2)}")

        resp_obj = await _downstream_tool_call(
            ctx,
            target_url,
            mcp_request_body,
            headers,
            transport_type=transport_type,
            sse_url=target_url if transport_type == "sse" else None,
        )

        if "error" in resp_obj:
            error_data = ErrorData.model_validate(resp_obj["error"])

            logger.error(
                "Downstream MCP server returned an error response: "
                f"code {error_data.code}, messsage: {error_data.message}, data: {str(error_data.data)}"
            )

            raise McpError(error_data)
        elif "result" in resp_obj:
            return CallToolResult.model_validate(resp_obj["result"])
        else:
            raise MisimplementedSpecException("Downstream MCP did not return a JSONRPCResponse message.")
    except UrlElicitationRequiredException as exc:
        auth_url, server_name = exc.auth_url, exc.server_name

        template = (
            f"In order to make tool calls to the '{server_name}' MCP server, the client must first perform "
            "out-of-band re-authorization in a browser window. Please direct the client to open the {} "
            "in a browser window, finish re-authorization, and come back to retry the same tool call again."
        )

        elicitation_id = _get_elicitation_id(auth_url)
        if elicitation_id is not None and _support_url_elicitation(ctx.session.client_params):
            msg = template.format("provided URL")

            logger.info(f"sending back the URL mode elicitation error response with ID {elicitation_id}.")

            _get_session_store(ctx).append(elicitation_id, ctx.session)

            raise UrlElicitationRequiredError(
                # This message is for LLM.
                message=msg,
                elicitations=[
                    ElicitRequestURLParams(
                        elicitationId=elicitation_id,
                        url=auth_url,
                        # This message is for human users.
                        message=(
                            f"The tokens for the '{server_name}' MCP server managed by Jarvis Registry have expired. "
                            "Please follow the URL to perform re-authorization in a browser window and come back again."
                        ),
                    )
                ],
            )

        logger.info(
            "Client doesn't support URL mode elicitation. Sending back a tool call result to prompt LLM for re-auth."
        )

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=template.format(f"URL `{auth_url}`"),
                )
            ],
            isError=True,
        )
    except (McpGatewayException, McpError):
        # These exceptions have been logged and should just bubble up to the caller as a way of communication.
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
    ctx: Context[ServerSession, McpAppContext],
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
    try:
        username = user_context.get("username", "unknown")
        logger.info(f"resource read request from user '{username}' - {resource_uri} on {server_id}")

        server = await _get_server_service(ctx).get_server_by_id(server_id)
        if server is None:
            # Invalid input. Return a JSON-RPC **result response** with `isError=True` so that LLM can try another request.
            return CallToolResult(
                content=[TextContent(type="text", text="There is no server with the given server_id.")],
                isError=True,
            )

        logger.info(f"Reading resource: {resource_uri} from server {server_id}")

        # Track server request count
        record_server_request(server.serverName)

        # MOCK: Return hardcoded response for POC
        logger.info(f"(MOCK) Returning cached search results for: {resource_uri}")

        # MOCK, NOTE: Use the following structure to return to our client the result of a `resource/read` from downstream MCPs.
        # Regarding the use of `EmbeddedResource`, see https://modelcontextprotocol.io/specification/2025-11-25/schema#embeddedresource
        # Regarding the use of the `_meta` field, see https://modelcontextprotocol.io/specification/2025-11-25/basic/index#_meta
        # In general, the `_meta` field should be attached to the **most specific** object whose schema allows it.
        return CallToolResult(
            content=[
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl(resource_uri),
                        text='{"results": [{"title": "AI News", "snippet": "Latest AI developments..."}]}',
                        mimeType="application/json",
                        _meta=get_meta_field(True, server_id, server.path),
                    ),
                )
            ]
        )

    except Exception:
        logger.exception(f"resource read failed for server_id: {server_id}")

        if server is not None:
            err_msg = (
                f"failed to read resource from downstream server {server.path if server.path else server.serverName}."
            )
        else:
            err_msg = "failed to read resource from downstream server."

        raise InternalServerException(err_msg)


async def execute_prompt_impl(
    user_context: UserContextDict,
    server_id: str,
    prompt_name: str,
    arguments: dict[str, Any] | None,
    ctx: Context[ServerSession, McpAppContext],
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
    try:
        username = user_context.get("username", "unknown")
        logger.info(f"Prompt execution request from user '{username}': {prompt_name} on {server_id}")

        server = await _get_server_service(ctx).get_server_by_id(server_id)
        if server is None:
            # Invalid input. Return a JSON-RPC **result response** with `isError=True` so that LLM can try another request.
            return CallToolResult(
                content=[TextContent(type="text", text="There is no server with the given server_id.")],
                isError=True,
            )

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
        return CallToolResult(
            content=[
                TextContent(type="text", text=combined_prompt_text, _meta=get_meta_field(True, server_id, server.path))
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

    except Exception:
        logger.exception(f"Prompt execution failed for server_id: {server_id}")

        if server is not None:
            err_msg = (
                f"failed to execute prompt from downstream server {server.path if server.path else server.serverName}."
            )
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
        ctx: Context[ServerSession, McpAppContext],
        tool_name: Annotated[
            str,
            Field(
                description="Final downstream MCP tool name to execute. If the previous discovery call used `type_list=[\"server\"]` and only `server`, first choose one tool entry from `$.config.toolFunctions`, then pass that chosen entry's `mcpToolName` as `tool_name` (or fall back to that chosen entry's key/name only if `mcpToolName` is missing). In every other discovery case, pass the returned `tool_name` unchanged. This exact string is forwarded as the value of the `$.params.name` field in the JSON-RPC payload of the `tools/call` request to downstream MCP."
            ),
        ],
        arguments: Annotated[dict[str, Any], Field(description="Tool parameters from input_schema")],
        server_id: Annotated[str, Field(description="Server ID from discovery")],
    ) -> CallToolResult:
        """
        🚀 AUTO-USE: Execute any discovered tool to get real-time data.

        **Common Examples:**
        ```
        # Web search
        execute_tool(
            tool_name="tavily_search",
            arguments={"query": "latest AI news", "max_results": 5},
            server_id="6972e222755441652c23090f"
        )

        # GitHub operations
        execute_tool(
            tool_name="search_pull_requests",
            arguments={"owner": "org", "repo": "project", "state": "open"},
            server_id="abc123..."
        )
        ```

        **Parameters:**
        - tool_name: Final downstream MCP tool name. This is the exact value that becomes the `$.params.name` field in the JSON-RPC payload of the `tools/call` request to downstream MCP.
        - arguments: Tool-specific parameters from input_schema
        - server_id: Server ID from discovery

        **How to set `tool_name`:**
        - Case 1: the previous discovery call used `type_list=["server"]` and only `server`.
          - The discovery response contains full server documents in `servers`.
          - Pick one server document.
          - Inspect the `$.config.toolFunctions` field of that server document.
          - Choose the single tool entry that best matches the user's task.
          - Set `server_id` to that server document's `id`.
          - Set `tool_name` to that chosen tool entry's `mcpToolName`.
          - Only if `mcpToolName` is missing, fall back to that chosen tool entry's key/name.
        - Case 2: every other discovery case, including `type_list=["tool"]`.
          - The discovery response already contains executable tool results.
          - Set `server_id` to the returned `server_id`.
          - Set `tool_name` to the returned `tool_name` unchanged.

        **Important constraints:**
        - `execute_tool` always runs exactly one tool.
        - `tool_name` must always be the final downstream MCP tool name.
        - Do not invent, rename, scope, or rewrite the tool name.
        - Do not pass a display label or registry-only alias.
        - Do not pass the full server document into `execute_tool`.
        - Pair the final `tool_name` with the matching `server_id` from the same discovery result.

        **Example:**
        - If discover_servers returns `{"tool_name": "tavily_search", "server_id": "abc123"}`,
          then call `execute_tool(tool_name="tavily_search", server_id="abc123", arguments={...})`.
        - If a server result contains:
          - `$.config.toolFunctions["add_numbers_mcp_minimal_mcp_iam"].mcpToolName = "add_numbers"`
          - `$.config.toolFunctions["greet_mcp_minimal_mcp_iam"].mcpToolName = "greet"`
          then first choose the correct tool entry for the task.
          - To execute the add tool, call `execute_tool(tool_name="add_numbers", server_id="<server id>", arguments={...})`.
          - To execute the greet tool, call `execute_tool(tool_name="greet", server_id="<server id>", arguments={...})`.

        ⚠️ Use after discover_servers to execute tools.
        Returns: Tool-specific results (format varies by tool)
        """
        return await execute_tool_impl(
            ctx,
            tool_name,
            arguments,
            server_id,
        )

    async def read_resource(
        ctx: Context[ServerSession, McpAppContext],
        server_id: Annotated[
            str, Field(description="Server ID from discover_servers (e.g., '6972e222755441652c23090f')")
        ],
        resource_uri: Annotated[
            str, Field(description="Resource URI to read (e.g., 'tavily://search-results/AI', 'file:///path/to/data')")
        ],
    ) -> CallToolResult:
        """
        📄 Read/access resources from any MCP server.

        **What are resources?**
        Resources are data sources, caches, URIs, or file-like objects exposed by MCP servers.
        Examples: cached search results, configuration files, data streams, API responses.

        **Common use cases:**
        - Access cached data: read_resource(server_id="...", resource_uri="tavily://search-results/AI")
        - Read configuration: read_resource(server_id="...", resource_uri="config://settings")
        - Access files: read_resource(server_id="...", resource_uri="file:///data/export.json")

        **Workflow:**
        1. Use discover_servers to find servers with resources
        2. Review the resources array in server config
        3. Use read_resource with the resource URI

        **Example:**
        ```
        # Discover servers with resources
        servers = discover_servers(query="search")

        # Find a resource URI from server config
        resource = servers[0]["config"]["resources"][0]["uri"]

        # Read the resource
        data = read_resource(
            server_id=servers[0]["_id"],
            resource_uri=resource
        )
        ```

        Returns: Resource contents (format varies: text, JSON, binary, etc.)
        """
        return await read_resource_impl(
            ctx.request_context.request.state.user,  # type: ignore[union-attr]
            server_id,
            resource_uri,
            ctx,
        )

    async def execute_prompt(
        ctx: Context[ServerSession, McpAppContext],
        server_id: Annotated[str, Field(description="Server ID from discover_servers")],
        prompt_name: Annotated[
            str, Field(description="Name of the prompt to execute (e.g., 'research_assistant', 'fact_checker')")
        ],
        arguments: Annotated[dict[str, Any] | None, Field(description="Prompt arguments as key-value pairs")] = None,
    ) -> CallToolResult:
        """
        💬 Execute prompts from any MCP server.

        **What are prompts?**
        Prompts are pre-configured, reusable prompt templates provided by MCP servers.
        They help standardize complex workflows and provide expert guidance.

        **Common use cases:**
        - Research workflows: execute_prompt(server_id="...", prompt_name="research_assistant", arguments={"topic": "AI"})
        - Fact checking: execute_prompt(server_id="...", prompt_name="fact_checker", arguments={"claim": "..."})
        - Code review: execute_prompt(server_id="...", prompt_name="code_reviewer", arguments={"language": "python"})

        **Workflow:**
        1. Use discover_servers to find servers with prompts
        2. Review the prompts array in server config
        3. Use execute_prompt with required arguments

        **Example:**
        ```
        # Discover servers with prompts
        servers = discover_servers(query="research")

        # Find available prompts
        prompts = servers[0]["config"]["prompts"]

        # Execute a prompt
        result = execute_prompt(
            server_id=servers[0]["_id"],
            prompt_name="research_assistant",
            arguments={"topic": "Quantum Computing", "depth": "advanced"}
        )

        # Result contains messages ready for LLM
        messages = result["messages"]
        ```

        Returns: Prompt messages ready for LLM consumption (role, content pairs)
        """
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
