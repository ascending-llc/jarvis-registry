from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from a2a.types import Message, Part, Role, Task, TextPart
from agno.agent import Agent
from agno.models.base import Model
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.workflows.helpers import build_prompt

logger = logging.getLogger(__name__)

_A2A_JWT_TTL_SECONDS = 300
_A2A_HTTP_TIMEOUT = 300

_AGENTCORE_IAM_UNSUPPORTED_MSG = (
    "IAM-authenticated AgentCore A2A runtime is not supported for direct workflow execution. "
    "Please configure JWT auth on the AgentCore runtime (or use a proxy path that handles SigV4)."
)


def _is_agentcore_runtime(agent: A2AAgent) -> bool:
    """Return True when the agent is a federated AWS Bedrock AgentCore runtime."""
    meta = agent.federationMetadata or {}
    return meta.get("providerType") == FederationProviderType.AWS_AGENTCORE


def _get_agentcore_auth_mode(agent: A2AAgent) -> str:
    """Detect AgentCore data-plane auth mode from agent config or metadata.

    Prefers the explicit ``agent.config.runtimeAccess.mode`` value.
    Falls back to inspecting ``federationMetadata.authorizerConfiguration``.
    """
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
    """Sign a short-lived JWT for direct service-to-agent authentication.

    Claims:
    - ``sub``: ``"jarvis-workflow"`` — identifies the calling service.
    - ``iss``: ``jwt_config.jwt_issuer`` — the registry's public issuer URL.
    - ``aud``: ``agent_url`` — RFC 8707 resource indicator (target agent's base URL).
    - ``exp``: ``now + expires_in_seconds``.
    """
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
    """Sign a JWT for an AWS Bedrock AgentCore runtime (JWT auth mode only).

    Uses the agent's own ``runtimeAccess.jwt`` configuration when present,
    otherwise falls back to the values supplied via ``jwt_config``.
    """
    runtime_jwt = None
    if agent.config and agent.config.runtimeAccess and agent.config.runtimeAccess.jwt:
        runtime_jwt = agent.config.runtimeAccess.jwt

    extra_claims: dict[str, Any] = {}
    issuer = jwt_config.jwt_issuer
    audience = jwt_config.jwt_audience

    if runtime_jwt:
        if runtime_jwt.allowedClients:
            extra_claims["client_id"] = runtime_jwt.allowedClients[0]
        if runtime_jwt.allowedScopes:
            extra_claims["scope"] = " ".join(runtime_jwt.allowedScopes)
        if runtime_jwt.customClaims:
            extra_claims.update(runtime_jwt.customClaims)
        if runtime_jwt.discoveryUrl:
            parsed = urlparse(runtime_jwt.discoveryUrl)
            issuer = f"{parsed.scheme}://{parsed.netloc}"
        if runtime_jwt.audiences:
            audience = runtime_jwt.audiences[0]

    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=issuer,
        audience=audience,
        expires_in_seconds=expires_in_seconds,
        extra_claims=extra_claims or None,
    )
    return encode_jwt(payload, jwt_config.jwt_private_key, kid=jwt_config.jwt_self_signed_kid)


def _raise_if_iam_unsupported(agent: A2AAgent) -> None:
    """Guard: AgentCore IAM-auth runtimes are not supported for direct workflow execution."""
    if _is_agentcore_runtime(agent) and _get_agentcore_auth_mode(agent) == "IAM":
        raise NotImplementedError(_AGENTCORE_IAM_UNSUPPORTED_MSG)


def _build_headers(agent: A2AAgent, *, jwt_config: JwtSigningConfig) -> dict[str, str]:
    """Per-call HTTP headers for an A2A invocation.

    AgentCore runtimes need an extra ``X-Amzn-Bedrock-AgentCore-Runtime-Session-Id``
    header and a JWT with custom claims; standard A2A agents need only a Bearer JWT.
    """
    if _is_agentcore_runtime(agent):
        return {
            "Authorization": f"Bearer {_make_agentcore_jwt(agent, jwt_config=jwt_config)}",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
        }
    token = make_agent_jwt(agent_url=str(agent.card.url).rstrip("/"), jwt_config=jwt_config)
    return {"Authorization": f"Bearer {token}"}


def _create_message(text: str) -> Message:
    """Build an A2A user Message with a single TextPart."""
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid.uuid4().hex,
    )


def _extract_text(event: Any) -> str:
    """Extract concatenated text from a ``Message`` or ``(Task, update_event)`` tuple.

    Skips non-text parts (``FilePart`` / ``DataPart``).  Falls back to ``str(event)``
    for unrecognized event shapes.
    """
    parts: list[Part] = []
    if isinstance(event, Message):
        parts = event.parts
    elif isinstance(event, tuple) and len(event) == 2 and isinstance(event[0], Task):
        for artifact in event[0].artifacts or []:
            parts.extend(artifact.parts or [])
    else:
        return str(event)

    return "".join(p.root.text for p in parts if isinstance(p.root, TextPart))


async def _call_a2a(agent: A2AAgent, text: str, *, jwt_config: JwtSigningConfig) -> StepOutput:
    """Invoke an A2A agent (standard or AgentCore) via the official ``a2a-sdk``."""
    agent_name = agent.config.title if agent.config else agent.card.name

    try:
        async with httpx.AsyncClient(timeout=_A2A_HTTP_TIMEOUT) as httpx_client:
            headers = _build_headers(agent, jwt_config=jwt_config)
            httpx_client.headers.update(headers)

            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=agent.card.url)
            agent_card = await resolver.get_agent_card()
            config = ClientConfig(
                httpx_client=httpx_client,
                streaming=False,
            )
            client = ClientFactory(config).create(agent_card)
            async for event in client.send_message(_create_message(text)):
                return StepOutput(content=_extract_text(event))
        return StepOutput(content="", success=False, error="A2A agent returned no events")
    except Exception as exc:
        logger.exception("A2A executor %r failed", agent_name)
        return StepOutput(content=str(exc), success=False, error=str(exc))


def make_a2a_executor(agent: A2AAgent, *, jwt_config: JwtSigningConfig) -> StepExecutor:
    """
    Wrap an A2A agent as a workflow executor via a direct call to the agent URL.
    """

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        _raise_if_iam_unsupported(agent)
        return await _call_a2a(agent, build_prompt(step_input), jwt_config=jwt_config)

    executor.__name__ = f"{agent.path.lstrip('/')}_a2a_executor"
    return executor


def make_a2a_pool_executor(
    node_name: str,
    pool_keys: list[str],
    *,
    selector_llm: Model,
    jwt_config: JwtSigningConfig,
    accessible_agent_ids: set[str] | None,
) -> StepExecutor:
    """Build an executor that picks the best A2A agent from a pool at runtime.

    Selection is performed by an LLM on first call, then cached in
    ``session_state`` so retries reuse the same agent without re-running the
    LLM.  Each call generates a fresh short-lived JWT for the selected agent
    using the supplied ``jwt_config``.

    Args:
        node_name:            Workflow node name — used for logging and cache keys.
        pool_keys:            Agent path segments (without leading ``/``) that form the pool.
        selector_llm:         Model used for LLM-based agent selection.
        jwt_config:           JWT signing config (private key, issuer, kid, audience).
        accessible_agent_ids: ACL filter — set of A2AAgent ID strings the caller
                              is authorized to invoke. ``None`` = unrestricted.
                              Pool members outside this set are excluded BEFORE
                              LLM selection runs.

    Returns:
        An async callable that accepts ``(StepInput, session_state)`` and
        returns a ``StepOutput``.
    """
    selector_agent = Agent(
        name=f"A2A Pool Selector [{node_name}]",
        model=selector_llm,
        instructions=[
            "You are given a task and a list of agents with their capabilities.",
            "Pick the single best agent for the task.",
            "Respond with ONLY the agent path (starting with /), nothing else.",
        ],
    )

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        task = build_prompt(step_input)
        state = session_state if session_state is not None else {}
        cache_key = f"a2a_target_{node_name}"

        selected_path: str | None = state.get(cache_key)
        selected_agent: A2AAgent | None = None

        if selected_path is None:
            paths = [f"/{k.lstrip('/')}" for k in pool_keys]
            agents = await A2AAgent.find(
                {"path": {"$in": paths}, "status": "active"},
            ).to_list()

            if accessible_agent_ids is not None:
                agents = [a for a in agents if str(a.id) in accessible_agent_ids]

            if not agents:
                return StepOutput(
                    content=f"No accessible active A2A agents for pool {pool_keys!r}",
                    success=False,
                    error="pool resolution failed: no accessible active agents",
                )

            selected_agent = await _select_agent_with_llm(agents, task, selector_agent)
            selected_path = selected_agent.path
            # Single key serves two purposes:
            # 1. Retry guard — skip LLM selection on retry, reuse the same agent.
            # 2. Persistence — WorkflowRunSyncer reads this to populate NodeRun.selected_a2a_key.
            state[cache_key] = selected_path
            logger.info("pool %r → selected agent %r", node_name, selected_path)
        else:
            selected_agent = await A2AAgent.find_one({"path": selected_path, "status": "active"})
            if selected_agent is None:
                return StepOutput(
                    content=f"Selected agent {selected_path!r} is no longer active",
                    success=False,
                    error=f"pool retry failed: agent {selected_path!r} not found or inactive",
                )
            if accessible_agent_ids is not None and str(selected_agent.id) not in accessible_agent_ids:
                return StepOutput(
                    content=f"Selected agent {selected_path!r} no longer accessible",
                    success=False,
                    error=f"pool retry failed: agent {selected_path!r} not in accessible set",
                )

        _raise_if_iam_unsupported(selected_agent)
        return await _call_a2a(selected_agent, task, jwt_config=jwt_config)

    executor.__name__ = f"{node_name}_pool_executor"
    return executor


async def _select_agent_with_llm(
    agents: list[A2AAgent],
    task_description: str,
    selector_agent: Agent,
) -> A2AAgent:
    """Use an LLM to pick the best-fit agent from the pool."""
    summaries = [
        f"Path: {agent.path}\n"
        f"Name: {agent.card.name}\n"
        f"Description: {agent.card.description or ''}\n"
        f"Skills: {', '.join(f'{s.name}: {s.description}' for s in (agent.card.skills or []) if s.name) or '(none)'}"
        for agent in agents
    ]

    prompt = (
        f"Task: {task_description}\n\n"
        f"Available agents:\n\n" + "\n---\n".join(summaries) + "\n\nWhich agent path is the best fit for this task? "
        "Reply with ONLY the agent path (e.g. /agent-name), nothing else."
    )

    result = await selector_agent.arun(prompt)
    chosen_path = (result.content or "").strip()

    agent_by_path = {a.path: a for a in agents}
    selected = agent_by_path.get(chosen_path)
    if selected is None:
        raise ValueError(f"LLM selector returned unknown path {chosen_path!r}; pool: {list(agent_by_path)}")
    logger.info("pool selector chose %r", selected.path)
    return selected
