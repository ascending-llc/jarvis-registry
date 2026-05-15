from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Annotated

from a2a.types import DataPart, FilePart, FileWithBytes, FileWithUri
from beanie import PydanticObjectId
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl, Field

from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.workflows.a2a_client import A2ACallResult, call_a2a, raise_if_iam_unsupported

from ..core.types import McpAppContext

logger = logging.getLogger(__name__)

_AGENT_INVOKE_LOGGER = "execute_agent"


def _convert_artifacts(result: A2ACallResult) -> list[EmbeddedResource | TextContent]:
    """Convert A2A artifact parts to MCP content items.

    FilePart with bytes  → EmbeddedResource(BlobResourceContents) — base64-encoded blob
    FilePart with URI    → EmbeddedResource(TextResourceContents)  — URI reference
    DataPart             → TextContent with JSON-serialised payload
    """
    items: list[EmbeddedResource | TextContent] = []
    for part in result.artifacts:
        root = part.root
        if isinstance(root, FilePart):
            f = root.file
            if isinstance(f, FileWithBytes):
                items.append(
                    EmbeddedResource(
                        type="resource",
                        resource=BlobResourceContents(
                            uri=AnyUrl(f"urn:a2a:file:{uuid.uuid4().hex}"),
                            blob=f.bytes,  # already base64-encoded by the a2a SDK
                            mimeType=f.mime_type or "application/octet-stream",
                        ),
                    )
                )
            elif isinstance(f, FileWithUri):
                items.append(
                    EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=AnyUrl(str(f.uri)),
                            mimeType=f.mime_type,
                            text=str(f.uri),
                        ),
                    )
                )
        elif isinstance(root, DataPart):
            items.append(TextContent(type="text", text=json.dumps(root.data, default=str)))
    return items


async def execute_agent_impl(
    agent_id: str,
    message: str,
    ctx: Context[ServerSession, McpAppContext],
) -> CallToolResult:
    """Invoke an A2A agent and return its response."""
    try:
        oid = PydanticObjectId(agent_id)
    except Exception as e:
        logger.warning("execute_agent: invalid agent_id format %r, e: %s", agent_id, e)
        return CallToolResult(
            isError=True,
            content=[
                TextContent(
                    type="text", text=f"Invalid agent_id format: {agent_id!r}. Use the agent_id from discover_agents."
                )
            ],
        )

    agent: A2AAgent | None = await A2AAgent.find_one(A2AAgent.id == oid, A2AAgent.status == "active")
    if agent is None:
        logger.warning("execute_agent: agent not found or inactive agent_id=%s", agent_id)
        return CallToolResult(
            isError=True,
            content=[
                TextContent(
                    type="text",
                    text=f"Agent {agent_id!r} not found or no longer active. Run discover_agents to get a fresh agent_id.",
                )
            ],
        )

    try:
        raise_if_iam_unsupported(agent)
    except NotImplementedError as exc:
        logger.warning("execute_agent: IAM auth not supported agent_id=%s path=%s", agent_id, agent.path)
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=str(exc))],
        )

    jwt_config = ctx.request_context.lifespan_context.jwt_signing_config

    async def on_chunk(chunk: str) -> None:
        await ctx.log("info", chunk, logger_name=_AGENT_INVOKE_LOGGER)

    logger.info("execute_agent: invoking agent_id=%s path=%s", agent_id, agent.path)
    result = await call_a2a(agent, message, jwt_config=jwt_config, on_chunk=on_chunk)

    if not result.success:
        logger.warning("execute_agent: agent_id=%s failed: %s", agent_id, result.error)
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=f"Agent invocation failed: {result.error}")],
        )

    content: list[TextContent | EmbeddedResource] = []
    if result.text:
        content.append(TextContent(type="text", text=result.text))
    content.extend(_convert_artifacts(result))

    logger.info("execute_agent: agent_id=%s responded (%d chars)", agent_id, len(result.text))
    return CallToolResult(content=content)


def get_tools() -> list[tuple[str, Callable]]:
    async def execute_agent(
        ctx: Context[ServerSession, McpAppContext],
        agent_id: Annotated[
            str,
            Field(
                description=(
                    "The agent_id from a discover_agents result. "
                    "This is the MongoDB ObjectId string that uniquely identifies the A2A agent."
                ),
            ),
        ],
        message: Annotated[
            str,
            Field(
                min_length=1,
                max_length=8192,
                description=(
                    "The task or question to send to the agent. "
                    "Be explicit and complete — the agent has no prior conversation context."
                ),
            ),
        ],
    ) -> CallToolResult:
        """Invoke an A2A agent by its agent_id and receive its response.

        Use this after discover_agents to delegate a complex task to the selected agent.
        The agent runs autonomously and returns its final result.

        Streaming: partial responses are sent as MCP log notifications during execution.
        The final CallToolResult contains the complete response.

        Error handling:
        - Invalid or unknown agent_id → isError=True with a retry hint; call discover_agents again.
        - Agent invocation failure → isError=True with the error message; consider retrying
          or trying a different agent.

        Workflow:
          1. discover_agents(query='…') → pick agent_id from results
          2. execute_agent(agent_id='…', message='<full task description>')
          3. Return the agent's response to the user."""
        return await execute_agent_impl(agent_id, message, ctx)

    return [("execute_agent", execute_agent)]
