from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Annotated

from a2a.types import Artifact, DataPart, FilePart, FileWithBytes, FileWithUri, Message, Part, Role, Task, TextPart
from a2a.utils.artifact import get_artifact_text
from a2a.utils.message import get_message_text
from a2a.utils.parts import get_data_parts, get_file_parts
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
from pydantic import AnyUrl, BaseModel, Field

from registry_pkgs.models import ResourceType
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.workflows.a2a_client import A2ACallResult, call_a2a, raise_if_iam_unsupported

from ...services.access_control_service import ACLService
from ..core.types import McpAppContext

logger = logging.getLogger(__name__)


def _file_to_resource(f: FileWithBytes | FileWithUri) -> EmbeddedResource | None:
    """Convert one A2A file payload to an MCP EmbeddedResource. None for unsupported."""
    if isinstance(f, FileWithBytes):
        return EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri=AnyUrl(f"urn:a2a:file:{uuid.uuid4().hex}"),
                blob=f.bytes,  # already base64-encoded by the a2a SDK
                mimeType=f.mime_type or "application/octet-stream",
            ),
        )
    if isinstance(f, FileWithUri):
        return EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri=AnyUrl(str(f.uri)),
                mimeType=f.mime_type,
                text=str(f.uri),
            ),
        )
    logger.warning("Skipping unsupported A2A file payload type: %s", type(f).__name__)
    return None


def _render_artifact(artifact: Artifact) -> list[TextContent | EmbeddedResource]:
    """Render one artifact as text (with `[<name>]` label) + files + data."""
    items: list[TextContent | EmbeddedResource] = []
    text = get_artifact_text(artifact, delimiter="")
    if text:
        labelled = f"[{artifact.name}]\n{text}" if artifact.name else text
        items.append(TextContent(type="text", text=labelled))
    for f in get_file_parts(artifact.parts or []):
        resource = _file_to_resource(f)
        if resource is not None:
            items.append(resource)
    for payload in get_data_parts(artifact.parts or []):
        items.append(TextContent(type="text", text=json.dumps(payload, default=str)))
    return items


def _render_message(message: Message) -> list[TextContent | EmbeddedResource]:
    """Render a Message reply as text + files + data (no label — single block)."""
    items: list[TextContent | EmbeddedResource] = []
    text = get_message_text(message)
    if text:
        items.append(TextContent(type="text", text=text))
    parts = message.parts or []
    for f in get_file_parts(parts):
        resource = _file_to_resource(f)
        if resource is not None:
            items.append(resource)
    for payload in get_data_parts(parts):
        items.append(TextContent(type="text", text=json.dumps(payload, default=str)))
    return items


def _render_task(task: Task) -> list[TextContent | EmbeddedResource]:
    """Render a Task's content. Per the A2A spec, both `task.status.message`
    and `task.artifacts` carry content — surface both in that order
    (matches a2a-samples host_agent.py)."""
    items: list[TextContent | EmbeddedResource] = []
    if task.status.message is not None:
        items.extend(_render_message(task.status.message))
    for artifact in task.artifacts or []:
        items.extend(_render_artifact(artifact))
    return items


def _convert_response(result: A2ACallResult) -> list[TextContent | EmbeddedResource]:
    """Render the successful A2ACallResult into MCP content items."""
    if result.message is not None:
        return _render_message(result.message)
    if result.task is not None:
        return _render_task(result.task)
    return []


def _error_result(text: str) -> CallToolResult:
    """Wrap an error message in an MCP CallToolResult with isError=True."""
    return CallToolResult(isError=True, content=[TextContent(type="text", text=text)])


def _extract_authenticated_user_id(ctx: Context[ServerSession, McpAppContext]) -> str | None:
    """Pull user_id from the gateway auth context attached to the request.

    Returns None if the request lacks a user context (unauthenticated). The
    caller is responsible for failing closed.
    """
    try:
        user_context = ctx.request_context.request.state.user  # type: ignore[union-attr]
    except AttributeError:
        return None
    if not user_context:
        return None
    return user_context.get("user_id")


async def _user_can_view_agent(
    acl_service: ACLService,
    user_id: str,
    agent_id: PydanticObjectId,
) -> bool:
    """Return True iff the user holds a VIEW ACL entry for this remote agent.

    Mirrors executor_resolver._resolve_executor's ACL gate so direct mcpgw
    invocations enforce the same access rules as workflow steps.
    """
    accessible = await acl_service.get_accessible_resource_ids(
        user_id=PydanticObjectId(user_id),
        resource_type=ResourceType.REMOTE_AGENT.value,
    )
    return str(agent_id) in accessible


async def _resolve_active_agent(agent_id: str) -> tuple[A2AAgent | None, CallToolResult | None]:
    """
    Parse `agent_id` and load the active A2AAgent.
    """
    try:
        oid = PydanticObjectId(agent_id)
    except Exception as e:
        logger.warning("execute_agent: invalid agent_id format %r, e: %s", agent_id, e)
        return None, _error_result(f"Invalid agent_id format: {agent_id!r}. Use the agent_id from discover_agents.")

    agent = await A2AAgent.find_one(A2AAgent.id == oid, A2AAgent.status == "active")
    if agent is None:
        logger.warning("execute_agent: agent not found or inactive agent_id=%s", agent_id)
        return None, _error_result(
            f"Agent {agent_id!r} not found or no longer active. Run discover_agents to get a fresh agent_id."
        )
    return agent, None


AgentPart = Annotated[TextPart | FilePart | DataPart, Field(discriminator="kind")]


class AgentMessageInput(BaseModel):
    """Content to send to the A2A agent.

    Each element of `parts` is one of:
      - TextPart  (kind="text")  — natural language instruction or context
      - DataPart  (kind="data")  — structured parameters as a JSON object
      - FilePart  (kind="file")  — a file by URI reference or inline base64

    Parts can be combined in a single message.
    """

    parts: list[AgentPart] = Field(
        min_length=1,
        description=(
            "One or more content parts that form the message body. "
            "Use TextPart for plain instructions, DataPart for structured input, "
            "FilePart for file references or inline content."
        ),
    )


async def execute_agent_impl(
    agent_id: str,
    message: AgentMessageInput,
    ctx: Context[ServerSession, McpAppContext],
) -> CallToolResult:
    """
    Invoke an A2A agent and return its response.
    """
    # 1. Resolve agent
    agent, error = await _resolve_active_agent(agent_id)
    if error:
        return error

    # 2. AuthN — fail closed when user context is absent
    user_id = _extract_authenticated_user_id(ctx)
    if not user_id:
        logger.warning("execute_agent: missing authenticated user_id; rejecting agent_id=%s", agent_id)
        return _error_result("Authentication required: missing user context.")

    # 3. AuthZ — same VIEW-permission rule as workflow A2A executors
    lifespan = ctx.request_context.lifespan_context
    if not await _user_can_view_agent(lifespan.acl_service, user_id, agent.id):
        logger.warning(
            "execute_agent: user_id=%s denied access to agent_id=%s path=%s",
            user_id,
            agent_id,
            agent.path,
        )
        return _error_result(f"Access denied: agent {agent_id!r} is not in your accessible set.")

    # 4. Transport capability
    try:
        raise_if_iam_unsupported(agent)
    except NotImplementedError as exc:
        logger.warning("execute_agent: IAM auth not supported agent_id=%s path=%s", agent_id, agent.path)
        return _error_result(str(exc))

    # 5. Invoke
    # AgentPart is TextPart | FilePart | DataPart — bare concrete types from a2a-sdk.
    # a2a.types.Part is RootModel[...] — the SDK wrapper call_a2a expects.
    a2a_message = Message(
        kind="message",
        role=Role.user,
        message_id=uuid.uuid4().hex,
        parts=[Part(root=p) for p in message.parts],
    )

    logger.info("execute_agent: invoking agent_id=%s path=%s", agent_id, agent.path)
    result = await call_a2a(
        agent,
        a2a_message,
        jwt_config=lifespan.jwt_signing_config,
        httpx_client=lifespan.a2a_httpx_client,
    )
    if not result.success:
        logger.warning("execute_agent: agent_id=%s failed: %s", agent_id, result.error)
        return _error_result(f"Agent invocation failed: {result.error}")

    # 6. Render
    artifact_count = len(result.task.artifacts) if result.task and result.task.artifacts else 0
    logger.info(
        "execute_agent: agent_id=%s responded (artifacts=%d, message=%s)",
        agent_id,
        artifact_count,
        result.message is not None,
    )
    return CallToolResult(content=_convert_response(result))


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
            AgentMessageInput,
            Field(
                description=(
                    "The message to send to the agent. "
                    "Must contain at least one part. "
                    "Use TextPart for plain task descriptions (most common). "
                    "Use DataPart to pass structured parameters, FilePart to pass files."
                ),
            ),
        ],
    ) -> CallToolResult:
        """Invoke an A2A agent by its agent_id and receive its response.

        Use this after discover_agents to delegate a complex task to the selected agent.
        The agent runs autonomously and returns its final result.

        Error handling:
        - Invalid or unknown agent_id → isError=True with a retry hint; call discover_agents again.
        - Agent invocation failure → isError=True with the error message; consider retrying
          or trying a different agent.

        Workflow:
        1. discover_agents(query='…') → pick agent_id from results
        2. execute_agent(agent_id='…', message={parts: [...]})
        3. Return the agent's response to the user.

        Choosing a part type:
        - Check the agent's description from discover_agents first — if it includes
          an Input Schema, use DataPart with fields matching that schema.
        - If no Input Schema is present, use a single TextPart with a natural-language
          instruction.
        - Use FilePart to pass file content — URI reference for large files,
          inline base64 only for small payloads.

        Message parts:

        kind="text"  — natural language instruction or context (most common)
        kind="data"  — structured parameters matching the agent's Input Schema
        kind="file"  — file by URI reference or inline base64

        Example:
            message={"parts": [{"kind": "text", "text": "Run a full code review of this repo."}]}"""
        return await execute_agent_impl(agent_id, message, ctx)

    return [("execute_agent", execute_agent)]
