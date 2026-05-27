
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.base_client import BaseClient
from a2a.client.middleware import ClientCallContext
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskQueryParams,
    TaskState,
    TextPart,
    TransportProtocol,
)
from a2a.utils.artifact import get_artifact_text
from a2a.utils.message import get_message_text

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.a2a_agent import TRANSPORT_HTTP_JSON, TRANSPORT_JSONRPC, A2AAgent
from registry_pkgs.models.enums import FederationProviderType

logger = logging.getLogger(__name__)

_A2A_JWT_TTL_SECONDS = 300
_A2A_HTTP_TIMEOUT = 300

_IN_PROGRESS_STATES: frozenset[TaskState] = frozenset({TaskState.submitted, TaskState.working})

_AGENTCORE_IAM_UNSUPPORTED_MSG = (
    "IAM-authenticated AgentCore A2A runtime is not supported for direct invocation. "
    "Please configure JWT auth on the AgentCore runtime (or use a proxy path that handles SigV4)."
)

_PROTOCOL_MAP: dict[str, TransportProtocol] = {
    TRANSPORT_JSONRPC: TransportProtocol.jsonrpc,
    TRANSPORT_HTTP_JSON: TransportProtocol.http_json,
}


@dataclass
class A2ACallResult:
    """
        Result of a call_a2a invocation.

    message: parts[] Part(RootModel[TextPart | FilePart | DataPart])
    task: list[Artifact] Artifact list[Part]
    """

    message: Message | None = None
    task: Task | None = None
    success: bool = True
    error: str | None = None

    @property
    def task_state(self) -> TaskState | None:
        return self.task.status.state if self.task else None

    def render_text(self) -> str:
        """Flatten the agent response to a single string for non-MCP consumers.

        Order matches the a2a-samples host_agent pattern:
          1. `task.status.message` (agent's status commentary), if present
          2. Each `task.artifact` rendered as `[<name>]\\n<text>`
        Blocks join with blank lines. Files and structured data are omitted —
        callers needing them should inspect `message`/`task` directly.
        """
        if self.message is not None:
            return get_message_text(self.message)
        if self.task is None:
            return ""
        blocks: list[str] = []
        if self.task.status.message is not None:
            status_text = get_message_text(self.task.status.message)
            if status_text:
                blocks.append(status_text)
        for a in self.task.artifacts or []:
            text = get_artifact_text(a, delimiter="")
            if not text:
                continue
            blocks.append(f"[{a.name}]\n{text}" if a.name else text)
        return "\n\n".join(blocks)


def _normalize_claim_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalize_claim_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_claim_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalize_claim_value(item) for key, item in value.items()}
    return value


def is_agentcore_runtime(agent: A2AAgent) -> bool:
    """Return True when the agent is a federated AWS Bedrock AgentCore runtime."""
    meta = agent.federationMetadata or {}
    return meta.get("providerType") == FederationProviderType.AWS_AGENTCORE


def get_agentcore_auth_mode(agent: A2AAgent) -> str:
    """Detect AgentCore data-plane auth mode from agent config or metadata."""
    if agent.config and agent.config.runtimeAccess:
        mode = agent.config.runtimeAccess.mode
        return str(mode.value if hasattr(mode, "value") else mode).upper()

    meta = agent.federationMetadata or {}
    config = meta.get("authorizerConfiguration") or {}
    if not isinstance(config, dict) or not config:
        return "IAM"

    authorizer_type = str(config.get("authorizerType") or config.get("type") or "").strip().upper()
    if authorizer_type in ("JWT", "OAUTH"):
        return "JWT"

    for key in ("customJWTAuthorizerConfiguration", "jwtAuthorizerConfiguration"):
        candidate = config.get(key)
        if isinstance(candidate, dict) and any(v not in (None, "", [], {}, ()) for v in candidate.values()):
            return "JWT"

    return "IAM"


def make_agent_jwt(
    *,
    agent_url: str,
    jwt_config: JwtSigningConfig,
    expires_in_seconds: int = _A2A_JWT_TTL_SECONDS,
) -> str:
    """Sign a short-lived JWT for direct service-to-agent authentication."""
    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=jwt_config.jwt_issuer,
        audience=agent_url,
        expires_in_seconds=expires_in_seconds,
    )
    return encode_jwt(payload, jwt_config.jwt_private_key, kid=jwt_config.jwt_self_signed_kid)


def _make_agentcore_jwt(
    agent: A2AAgent,
    *,
    jwt_config: JwtSigningConfig,
    expires_in_seconds: int = _A2A_JWT_TTL_SECONDS,
) -> str:
    runtime_jwt = None
    if agent.config and agent.config.runtimeAccess and agent.config.runtimeAccess.jwt:
        runtime_jwt = agent.config.runtimeAccess.jwt

    extra_claims: dict[str, Any] = {}
    issuer = jwt_config.jwt_issuer
    audience = jwt_config.jwt_audience

    if runtime_jwt:
        if runtime_jwt.allowedClients:
            extra_claims["client_id"] = _normalize_claim_value(runtime_jwt.allowedClients[0])
        if runtime_jwt.allowedScopes:
            cleaned = [s for s in (_normalize_claim_value(s) for s in runtime_jwt.allowedScopes) if s]
            if cleaned:
                extra_claims["scope"] = " ".join(cleaned)
        if runtime_jwt.customClaims:
            extra_claims.update(_normalize_claim_value(runtime_jwt.customClaims))
        if runtime_jwt.discoveryUrl:
            parsed = urlparse(runtime_jwt.discoveryUrl)
            issuer = f"{parsed.scheme}://{parsed.netloc}"
        if runtime_jwt.audiences:
            audience = _normalize_claim_value(runtime_jwt.audiences[0])

    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=issuer,
        audience=audience,
        expires_in_seconds=expires_in_seconds,
        extra_claims=extra_claims or None,
    )
    return encode_jwt(payload, jwt_config.jwt_private_key, kid=jwt_config.jwt_self_signed_kid)


def agent_base_url(agent: A2AAgent) -> str:
    """Return the agent's base URL from its A2A card."""
    return str(agent.card.url).rstrip("/") if agent.card else "unknown"


def raise_if_iam_unsupported(agent: A2AAgent) -> None:
    """Raise NotImplementedError if the agent uses unsupported IAM auth."""
    if is_agentcore_runtime(agent) and get_agentcore_auth_mode(agent) == "IAM":
        raise NotImplementedError(_AGENTCORE_IAM_UNSUPPORTED_MSG)


def build_headers(agent: A2AAgent, *, jwt_config: JwtSigningConfig) -> dict[str, str]:
    """Build per-call HTTP auth headers for an A2A invocation."""
    if is_agentcore_runtime(agent):
        return {
            "Authorization": f"Bearer {_make_agentcore_jwt(agent, jwt_config=jwt_config)}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
        }
    token = make_agent_jwt(agent_url=str(agent.card.url).rstrip("/"), jwt_config=jwt_config)
    return {"Authorization": f"Bearer {token}"}


def _create_message(text: str) -> Message:
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text=text))],
        message_id=uuid.uuid4().hex,
    )


async def _maybe_emit_chunk(
    on_chunk: Callable[[str], Awaitable[None]] | None,
    text: str,
) -> None:
    if not text or on_chunk is None:
        return
    try:
        await on_chunk(text)
    except Exception as e:
        logger.warning("on_chunk callback failed:%s, continuing accumulation", e, exc_info=True)


async def _consume_stream(
    client: BaseClient,
    message: Message,
    *,
    context: ClientCallContext,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> Message | Task | None:
    """Drain client.send_message events. Returns the first Message reply,
    the last seen Task, or None if no events were produced.

    Modeled after a2a-samples `RemoteAgentConnections.send_message`. The SDK
    aggregates streaming artifacts onto the yielded Task via ClientTaskManager,
    so `latest_task.artifacts` at the end already reflects the merged state —
    no manual merge needed here.
    """
    latest_task: Task | None = None
    async for event in client.send_message(message, context=context):
        if isinstance(event, Message):
            await _maybe_emit_chunk(on_chunk, get_message_text(event))
            return event
        task, _ = event
        await _maybe_emit_chunk(on_chunk, get_artifact_text(task.artifacts[-1]) if task.artifacts else "")
        latest_task = task
    return latest_task


async def _poll_until_terminal(
    client: BaseClient,
    task_id: str,
    *,
    context: ClientCallContext,
) -> Task:
    """Poll `client.get_task` until the task leaves the in-progress states.

    `blocking=True` is only a hint; servers may ignore it and return a
    `submitted`/`working` Task immediately. Caller-side polling is the
    documented workaround (see a2a-send-message-1.md, Bug 2). Returns the
    final Task. Raises TimeoutError if the 60s budget is exceeded.
    """
    # Exponential backoff capped at 8s; total budget 60s.
    back_offs = (0.5, 1.0, 2.0, 4.0, 8.0)
    deadline = time.monotonic() + 60.0
    delay_iter = iter(back_offs)
    while True:
        try:
            delay = next(delay_iter)
        except StopIteration:
            delay = back_offs[-1]

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"polling timed out after 60s for task {task_id!r}")
        await asyncio.sleep(min(delay, remaining))

        task = await client.get_task(TaskQueryParams(id=task_id), context=context)
        if task.status.state not in _IN_PROGRESS_STATES:
            return task


def _result_from_task(task: Task) -> A2ACallResult:
    """Build A2ACallResult from a non-pollable Task.

    Per the A2A spec, both `task.artifacts` and `task.status.message` carry
    content (status.message is the agent's lifecycle commentary; artifacts
    are the deliverables). Either counts toward "has content".

    `success=True` requires `state==completed` AND content present. Other
    shapes return success=False while still surfacing the Task so callers
    can inspect what came back; the error message distinguishes interrupted
    (awaiting input/auth) from terminal-but-not-completed failures.
    """
    state = task.status.state
    has_content = bool(task.artifacts) or task.status.message is not None

    if not has_content:
        return A2ACallResult(
            task=task,
            success=False,
            error=f"A2A agent returned no content (task_state={state.value})",
        )

    if state == TaskState.completed:
        return A2ACallResult(task=task, success=True)

    if state == TaskState.input_required:
        return A2ACallResult(
            task=task,
            success=False,
            error=f"agent paused awaiting additional user input (task_state={state.value}); start a new conversation to provide it",
        )
    if state == TaskState.auth_required:
        return A2ACallResult(
            task=task,
            success=False,
            error=f"agent paused awaiting authentication (task_state={state.value}); start a new conversation to provide it",
        )

    # Remaining: failed / canceled / rejected / unknown.
    return A2ACallResult(
        task=task,
        success=False,
        error=f"task terminated in non-completed state: {state.value}",
    )


async def _call_with_open_client(
    client: BaseClient,
    agent_name: str,
    text: str | Message,
    context: ClientCallContext,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> A2ACallResult:
    """Run the three-phase consume/poll/build pipeline against an already-open client."""
    # 1. drain the event stream.
    msg = text if isinstance(text, Message) else _create_message(text)
    outcome = await _consume_stream(client, msg, context=context, on_chunk=on_chunk)

    if isinstance(outcome, Message):
        logger.debug("← A2A agent %r responded with Message", agent_name)
        return A2ACallResult(message=outcome, success=True)
    if outcome is None:
        logger.warning("← A2A agent %r returned no events", agent_name)
        return A2ACallResult(success=False, error="A2A agent returned no events")

    task = outcome

    # 2. — poll if the agent is still working
    if task.status.state in _IN_PROGRESS_STATES:
        logger.debug("polling task %r from state=%s", task.id, task.status.state.value)
        try:
            task = await _poll_until_terminal(client, task.id, context=context)
        except TimeoutError as exc:
            logger.warning("polling timed out: %s", exc)
            return A2ACallResult(task=task, success=False, error=str(exc))

    # 3. classify the final Task
    result = _result_from_task(task)
    logger.debug(
        "← A2A agent %r finished (state=%s, success=%s)",
        agent_name,
        task.status.state.value,
        result.success,
    )
    return result


async def call_a2a(
    agent: A2AAgent,
    text: str | Message,
    *,
    jwt_config: JwtSigningConfig,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    httpx_client: httpx.AsyncClient | None = None,
) -> A2ACallResult:
    """Invoke an A2A agent via the a2a-sdk ClientFactory.

    Transport (jsonrpc / http_json) is selected from agent.config.type.
    Auth headers and timeout are injected per-call via ClientCallContext.

    Args:
        agent:        A2AAgent document from MongoDB.
        text:         User message / task description to send.
        jwt_config:   JWT signing config for service-to-agent auth.
        httpx_client: Optional shared httpx client.

    Returns:
        A2ACallResult. `success=True` requires content present AND (for the
        Task path) `task.status.state == completed`.
    """
    agent_name = agent.config.title if agent.config else agent.card.name
    transport_type = (agent.config.type if agent.config else TRANSPORT_JSONRPC).lower()

    if transport_type not in _PROTOCOL_MAP:
        return A2ACallResult(
            success=False,
            error=f"Unsupported transport type '{transport_type}' for agent {agent_name!r}. Supported: {sorted(_PROTOCOL_MAP)}",
        )

    base_url = str(agent.config.url if agent.config and agent.config.url else agent.card.url).rstrip("/")

    logger.debug(
        "→ calling A2A agent %r  transport=%s  url=%s  prompt=%r",
        agent_name,
        transport_type,
        base_url,
        text[:120] if isinstance(text, str) else repr(text)[:120],
    )

    agent_card = agent.card.model_copy(deep=True)
    agent_card.url = base_url  # type: ignore[assignment]
    protocol = _PROTOCOL_MAP.get(transport_type, TransportProtocol.jsonrpc)

    context = ClientCallContext(
        state={
            "http_kwargs": {
                "headers": build_headers(agent, jwt_config=jwt_config),
                "timeout": _A2A_HTTP_TIMEOUT,
            }
        }
    )

    try:
        config = ClientConfig(
            supported_transports=[protocol],
            httpx_client=httpx_client,
        )

        # ClientFactory.create() is annotated -> Client, but always returns BaseClient in a2a-sdk==0.3.24.
        client: BaseClient = ClientFactory(config).create(agent_card)  # type: ignore[assignment]
        if httpx_client is None:
            async with client:
                return await _call_with_open_client(client, agent_name, text, context, on_chunk)
        return await _call_with_open_client(client, agent_name, text, context, on_chunk)

    except Exception as exc:
        logger.exception("A2A call to %r failed", agent_name)
        return A2ACallResult(success=False, error=str(exc))
