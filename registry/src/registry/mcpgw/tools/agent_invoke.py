import logging
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlparse
from uuid import uuid4

import grpc
import httpx
from a2a.client.errors import (
    A2AClientError,
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientJSONRPCError,
    A2AClientTimeoutError,
)
from a2a.client.transports.grpc import GrpcTransport
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.client.transports.rest import RestTransport
from a2a.types import Message, MessageSendParams, Part, Role, Task, TextPart
from beanie import PydanticObjectId
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from registry_pkgs.models import ResourceType
from registry_pkgs.models.a2a_agent import (
    TRANSPORT_GRPC,
    TRANSPORT_HTTP_JSON,
    TRANSPORT_JSONRPC,
)

from ...core.exceptions import InternalServerException
from ...utils.otel_metrics import record_server_request
from ..core.types import McpAppContext

logger = logging.getLogger(__name__)


def parts_to_text(parts: list[Part]) -> str:
    return "\n".join(p.root.text for p in parts if isinstance(p.root, TextPart))


def extract_text(result: Task | Message) -> str:
    """
    Extract plain text from a send_message() result.

    Priority order for Task:
      1. Artifact parts (primary output)
      2. status.message parts (agent status commentary)
      3. Full JSON fallback so the LLM can still reason about the response
    """
    if isinstance(result, Message):
        return parts_to_text(result.parts)

    texts: list[str] = []
    if result.artifacts:
        for artifact in result.artifacts:
            t = parts_to_text(artifact.parts)
            if t:
                texts.append(t)

    if not texts and result.status and result.status.message:
        texts.append(parts_to_text(result.status.message.parts))

    return "\n\n".join(texts) if texts else result.model_dump_json(exclude_none=True)


async def invoke_agent_impl(
    ctx: Context[ServerSession, McpAppContext],
    agent_id: str,
    message: str,
    skill_name: str | None = None,
) -> CallToolResult:
    """
    Invoke a registered A2A agent by ID.

    Args:
        ctx: FastMCP context (carries lifespan + request state).
        agent_id: MongoDB document ID string from discover_agents result.
        message: Natural-language task or question for the agent.
        skill_name: Optional skill name to target within the agent.

    Returns:
        CallToolResult with the agent's response text, or isError=True on failure.
    """
    lifespan = ctx.request_context.lifespan_context
    user_context = ctx.request_context.request.state.user
    user_id: str = user_context.get("user_id", "unknown")
    username: str = user_context.get("username", "unknown")

    logger.info(f"invoke_agent: user='{username}:{user_id}' agent_id={agent_id!r}")

    # 1. Resolve agent from MongoDB
    agent = await lifespan.a2a_agent_service.get_agent_by_id(agent_id)
    if agent is None:
        return CallToolResult(
            content=[TextContent(type="text", text=f"No agent found with id '{agent_id}'.")],
            isError=True,
        )

    # 2. ACL check — raises HTTPException(403) if VIEW permission is missing
    await lifespan.acl_service.check_user_permission(
        user_id=PydanticObjectId(user_id),
        resource_type=str(ResourceType.REMOTE_AGENT.value),
        resource_id=agent.id,
        required_permission="VIEW",
    )

    # 3. Enabled check
    if not agent.isEnabled:
        display_name = (agent.config.title if agent.config else None) or agent.card.name
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent '{display_name}' is currently disabled.")],
            isError=True,
        )

    # 4. Build MessageSendParams
    metadata: dict | None = {"skill": skill_name} if skill_name else None
    params = MessageSendParams(
        message=Message(
            message_id=str(uuid4()),
            role=Role.user,
            parts=[Part(root=TextPart(kind="text", text=message))],
            metadata=metadata,
        )
    )

    # 5. Resolve base URL — mirrors proxy_routes.py logic exactly
    base_url = str(agent.config.url) if agent.config and agent.config.url else str(agent.card.url)
    agent_card = agent.card.model_copy(deep=True)
    agent_card.url = base_url.rstrip("/")

    transport_type = (agent.config.type if agent.config else TRANSPORT_JSONRPC).lower()

    proxy_client: httpx.AsyncClient = lifespan.proxy_client
    record_server_request(agent.config.title if agent.config and agent.config.title else agent.card.name)

    logger.info(f"invoke_agent: agent_id={agent_id!r} transport={transport_type} url={base_url!r}")

    # 6. Dispatch via transport and return
    try:
        result: Task | Message

        if transport_type == TRANSPORT_JSONRPC:
            transport = JsonRpcTransport(httpx_client=proxy_client, agent_card=agent_card)
            result = await transport.send_message(params)

        elif transport_type == TRANSPORT_HTTP_JSON:
            transport = RestTransport(httpx_client=proxy_client, agent_card=agent_card)
            result = await transport.send_message(params)

        elif transport_type == TRANSPORT_GRPC:
            parsed = urlparse(base_url)
            port = parsed.port or (443 if parsed.scheme in {"https", "grpcs"} else 50051)
            grpc_target = f"{parsed.hostname}:{port}"
            channel = (
                grpc.aio.secure_channel(grpc_target, grpc.ssl_channel_credentials())
                if parsed.scheme in {"https", "grpcs"}
                else grpc.aio.insecure_channel(grpc_target)
            )
            try:
                transport = GrpcTransport(channel=channel, agent_card=agent_card)
                result = await transport.send_message(params)
            finally:
                await channel.close()
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unsupported transport type: '{transport_type}'.")],
                isError=True,
            )

        response_text = extract_text(result)
        logger.info(f"invoke_agent: success agent_id={agent_id!r}")
        return CallToolResult(content=[TextContent(type="text", text=response_text)])

    except (httpx.TimeoutException, A2AClientTimeoutError):
        logger.error(f"invoke_agent: timeout agent_id={agent_id!r}")
        return CallToolResult(
            content=[TextContent(type="text", text="Agent did not respond within the timeout window.")],
            isError=True,
        )
    except A2AClientHTTPError as exc:
        logger.error(f"invoke_agent: HTTP {exc.status_code} agent_id={agent_id!r}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent returned HTTP error {exc.status_code}.")],
            isError=True,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(f"invoke_agent: HTTP {exc.response.status_code} agent_id={agent_id!r}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent returned HTTP error {exc.response.status_code}.")],
            isError=True,
        )
    except A2AClientJSONRPCError as exc:
        logger.error(f"invoke_agent: JSON-RPC error agent_id={agent_id!r}: {exc.error}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent returned a JSON-RPC error: {exc.error}.")],
            isError=True,
        )
    except A2AClientJSONError as exc:
        logger.error(f"invoke_agent: JSON parse error agent_id={agent_id!r}: {exc.message}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent returned an unparseable response: {exc.message}.")],
            isError=True,
        )
    except A2AClientError as exc:
        logger.error(f"invoke_agent: A2A client error agent_id={agent_id!r}: {exc}")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Agent communication error: {exc}.")],
            isError=True,
        )
    except Exception as exc:
        logger.error(f"invoke_agent: unexpected error agent_id={agent_id!r}: {exc}", exc_info=True)
        raise InternalServerException(f"Failed to invoke agent '{agent_id}'") from exc


def get_tools() -> list[tuple[str, Callable]]:
    """Export (tool_name, tool_function) pairs for registration in server.py."""

    async def invoke_agent(
        ctx: Context[ServerSession, McpAppContext],
        agent_id: Annotated[
            str,
            Field(
                description=(
                    "The `agent_id` from the SAME discover_agents result. "
                    "Use the exact value returned — do not shorten or transform it."
                )
            ),
        ],
        message: Annotated[
            str,
            Field(
                min_length=1,
                max_length=32768,
                description=(
                    "Natural-language task or question for the agent. "
                    "Include all relevant context — the agent has no memory of prior turns. "
                    "For CHAIN tasks, embed the upstream MCP tool output directly in this field."
                ),
            ),
        ],
        skill_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional: target a specific skill by its exact name from the agent card. "
                    "Omit to let the agent route the request itself."
                ),
            ),
        ] = None,
    ) -> CallToolResult:
        """
            Invoke a registered A2A agent discovered via discover_agents.

        Call this after discover_agents returns an agent result to actually run the agent
        and receive its response. Handles jsonrpc, http_json, and grpc transports.

        For CHAIN tasks: embed the upstream tool output directly in the message string.

        Parameter mapping from a discover_agents result:

          discovery result:
            {
              "entity_type": "agent",
              "agent_id":    "abc123",   ← agent_id parameter
              "agent_name":  "Deep Intel Agent"
            }

          invocation:
            invoke_agent(
                agent_id="abc123",
                message="Analyze Q1 competitor pricing trends",
            )
        """
        return await invoke_agent_impl(ctx, agent_id, message, skill_name)

    return [("invoke_agent", invoke_agent)]
