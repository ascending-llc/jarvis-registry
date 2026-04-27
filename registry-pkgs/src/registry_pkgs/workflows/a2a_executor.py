from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from agno.agent import Agent
from agno.client.a2a import A2AClient
from agno.models.base import Model
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry import settings
from registry_pkgs.core.jwt_utils import build_jwt_payload, encode_jwt
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.workflows.helpers import build_prompt

logger = logging.getLogger(__name__)

_A2A_JWT_TTL_SECONDS = 300
_A2A_HTTP_TIMEOUT = 300


def _is_agentcore_runtime(agent: A2AAgent) -> bool:
    """Return True when the agent is a federated AWS Bedrock AgentCore runtime."""
    meta = agent.federationMetadata or {}
    return meta.get("providerType") == "aws_agentcore"


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


def agent_base_url(agent: A2AAgent) -> str:
    """Resolve the direct-call endpoint for an A2A agent.

    Prefers ``agent.config.url`` (user-configured, where the card was
    originally fetched) over ``agent.card.url`` (from the card itself,
    may be a discovery URL).
    """
    if agent.config and agent.config.url:
        return str(agent.config.url).rstrip("/")
    return str(agent.card.url).rstrip("/")


def map_a2a_protocol(transport_type: str | None) -> Literal["rest", "json-rpc"]:
    """Map the MongoDB ``config.type`` value to agno's A2AClient ``protocol`` literal.

    +--------------+-----------+
    | config.type  | protocol  |
    +==============+===========+
    | jsonrpc      | json-rpc  |
    | http_json    | rest      |
    | grpc         | rest      |  ← A2AClient has no gRPC; REST is the safe fallback
    | (None/other) | rest      |
    +--------------+-----------+
    """
    if transport_type == "jsonrpc":
        return "json-rpc"
    if transport_type == "grpc":
        logger.warning("A2A transport 'grpc' is not natively supported by A2AClient; falling back to 'rest'")
    return "rest"


def make_agent_jwt(*, agent_url: str, expires_in_seconds: int = _A2A_JWT_TTL_SECONDS) -> str:
    """Sign a short-lived JWT for direct service-to-agent authentication.

    JWT signing config (private key, issuer, kid) is read from the global
    Settings singleton.

    Claims:
    - ``sub``: ``"jarvis-workflow"`` — identifies the calling service.
    - ``iss``: ``settings.jwt_issuer`` — the registry's public issuer URL.
    - ``aud``: ``agent_url`` — RFC 8707 resource indicator (target agent's base URL).
    - ``exp``: ``now + expires_in_seconds``.
    """

    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=settings.jwt_issuer,
        audience=agent_url,
        expires_in_seconds=expires_in_seconds,
    )
    return encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)


def _make_agentcore_jwt(agent: A2AAgent, expires_in_seconds: int = _A2A_JWT_TTL_SECONDS) -> str:
    """Sign a JWT for an AWS Bedrock AgentCore runtime (JWT auth mode only).

    Uses the agent's own ``runtimeAccess.jwt`` configuration when present,
    otherwise falls back to the global Settings singleton defaults.
    """
    jwt_config = None
    if agent.config and agent.config.runtimeAccess and agent.config.runtimeAccess.jwt:
        jwt_config = agent.config.runtimeAccess.jwt

    extra_claims: dict[str, Any] = {}
    issuer = settings.jwt_issuer
    audience = settings.jwt_audience

    if jwt_config:
        if jwt_config.allowedClients:
            extra_claims["client_id"] = jwt_config.allowedClients[0]
        if jwt_config.allowedScopes:
            extra_claims["scope"] = " ".join(jwt_config.allowedScopes)
        if jwt_config.customClaims:
            extra_claims.update(jwt_config.customClaims)
        if jwt_config.discoveryUrl:
            parsed = urlparse(jwt_config.discoveryUrl)
            issuer = f"{parsed.scheme}://{parsed.netloc}"
        if jwt_config.audiences:
            audience = jwt_config.audiences[0]

    payload = build_jwt_payload(
        subject="jarvis-workflow",
        issuer=issuer,
        audience=audience,
        expires_in_seconds=expires_in_seconds,
        extra_claims=extra_claims or None,
    )
    return encode_jwt(payload, settings.jwt_private_key, kid=settings.jwt_self_signed_kid)


def _extract_agentcore_response_content(data: dict[str, Any]) -> str:
    """Best-effort extraction of text from an AgentCore A2A runtime response.

    AgentCore A2A runtimes return JSON-RPC 2.0 envelopes.  The actual agent
    output lives inside ``result.artifacts[].parts[].text``.
    """
    if not isinstance(data, dict):
        return str(data)

    result = data.get("result") or data
    if not isinstance(result, dict):
        return str(result)

    # AgentCore shape: result.artifacts[0].parts[0].text
    artifacts = result.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        parts = artifacts[0].get("parts")
        if isinstance(parts, list) and parts:
            first = parts[0]
            if isinstance(first, dict):
                text = first.get("text")
                if text is not None:
                    return str(text)

    # Standard A2A shape (fallback for non-AgentCore responses)
    msg = result.get("message") or {}
    if isinstance(msg, dict):
        parts = msg.get("parts")
        if isinstance(parts, list) and parts:
            first = parts[0]
            if isinstance(first, dict):
                text = first.get("text")
                if text is not None:
                    return str(text)

    # Fallback keys
    for key in ("content", "text", "response"):
        val = result.get(key)
        if val is not None:
            if isinstance(val, str):
                return val
            return json.dumps(val, ensure_ascii=False)

    return json.dumps(data, ensure_ascii=False)


async def _call_agentcore_a2a(agent: A2AAgent, text: str) -> StepOutput:
    """Execute a task against an AWS Bedrock AgentCore A2A runtime via HTTP.

    AgentCore runtimes do **not** expose ``/v1/message:send``; the invocation
    URL (ending in ``/invocations``) is the data-plane endpoint itself.
    """
    agent_name = agent.config.title if agent.config else agent.card.name
    base_url = agent_base_url(agent)

    token = _make_agentcore_jwt(agent)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4().hex[:12]}",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": text}],
                "messageId": f"msg-{uuid.uuid4().hex[:12]}",
            }
        },
    }

    try:
        async with httpx.AsyncClient(timeout=_A2A_HTTP_TIMEOUT) as client:
            response = await client.post(base_url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
            return StepOutput(content=_extract_agentcore_response_content(result))
    except Exception as exc:
        logger.exception("AgentCore A2A executor %r failed", agent_name)
        return StepOutput(content=str(exc), success=False, error=str(exc))


async def _call_standard_a2a(agent: A2AAgent, text: str) -> StepOutput:
    """Execute a task against a standard (non-AgentCore) A2A agent.

    Generates a fresh JWT per call and uses agno's ``A2AClient``.
    """
    agent_name = agent.config.title if agent.config else agent.card.name
    base_url = agent_base_url(agent)
    protocol = map_a2a_protocol(agent.config.type if agent.config else None)

    try:
        token = make_agent_jwt(agent_url=base_url)
        client = A2AClient(base_url=base_url, timeout=_A2A_HTTP_TIMEOUT, protocol=protocol)
        result = await client.send_message(text, headers={"Authorization": f"Bearer {token}"})
        return StepOutput(content=result.content or "")
    except Exception as exc:
        logger.exception("A2A executor %r failed", agent_name)
        return StepOutput(content=str(exc), success=False, error=str(exc))


def make_a2a_executor(agent: A2AAgent) -> StepExecutor:
    """Wrap an A2A agent as a workflow executor via a direct call to the agent URL.

    A fresh JWT is generated per invocation (TTL = ``_A2A_JWT_TTL_SECONDS``).
    JWT signing configuration is read from the global Settings singleton.
    The registry proxy is **not** involved.

    Args:
        agent: Active ``A2AAgent`` document from MongoDB.

    Returns:
        An async callable that accepts ``(StepInput, session_state)`` and
        returns a ``StepOutput``.
    """
    if _is_agentcore_runtime(agent):
        return _make_agentcore_executor(agent)

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        return await _call_standard_a2a(agent, build_prompt(step_input))

    executor.__name__ = f"{agent.path.lstrip('/')}_a2a_executor"
    return executor


def _make_agentcore_executor(agent: A2AAgent) -> StepExecutor:
    """Build an executor for an AWS Bedrock AgentCore runtime.

    Raises ``NotImplementedError`` at call-time for IAM auth (SigV4 not implemented).
    """
    mode = _get_agentcore_auth_mode(agent)
    executor_name = f"{agent.path.lstrip('/')}_agentcore_executor"

    if mode == "IAM":
        # SigV4 signing for direct workflow execution is not yet implemented.
        async def iam_executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
            raise NotImplementedError(
                "IAM-authenticated AgentCore A2A runtime is not supported for direct workflow execution. "
                "Please configure JWT auth on the AgentCore runtime (or use a proxy path that handles SigV4)."
            )

        iam_executor.__name__ = executor_name
        return iam_executor

    async def agentcore_executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        return await _call_agentcore_a2a(agent, build_prompt(step_input))

    agentcore_executor.__name__ = executor_name
    return agentcore_executor


def make_a2a_pool_executor(
    node_name: str,
    pool_keys: list[str],
    *,
    selector_llm: Model,
) -> StepExecutor:
    """Build an executor that picks the best A2A agent from a pool at runtime.

    Selection is performed by an LLM on first call, then cached in
    ``session_state`` so retries reuse the same agent without re-running the
    LLM.  Each call generates a fresh short-lived JWT for the selected agent.
    JWT signing configuration is read from the global Settings singleton.

    Args:
        node_name:    Workflow node name — used for logging and cache keys.
        pool_keys:    Agent path segments (without leading ``/``) that form the pool.
        selector_llm: Model used for LLM-based agent selection.

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

            if not agents:
                return StepOutput(
                    content=f"No active A2A agents found for pool {pool_keys!r}",
                    success=False,
                    error="pool resolution failed: no active agents",
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

        if _is_agentcore_runtime(selected_agent):
            mode = _get_agentcore_auth_mode(selected_agent)
            if mode == "IAM":
                raise NotImplementedError(
                    "IAM-authenticated AgentCore A2A runtime is not supported for direct workflow execution. "
                )
            return await _call_agentcore_a2a(selected_agent, task)
        return await _call_standard_a2a(selected_agent, task)

    executor.__name__ = f"{node_name}_pool_executor"
    return executor


async def _select_agent_with_llm(
    agents: list[A2AAgent],
    task_description: str,
    selector_agent: Agent,
) -> A2AAgent:
    """Use an LLM to pick the best-fit agent from the pool.

    Falls back to the first agent in the list when the LLM returns a path
    that is not in the pool (e.g. hallucination or unexpected format).
    """
    summaries: list[str] = []
    for agent in agents:
        skills = ", ".join(f"{s.name}: {s.description}" for s in (agent.card.skills or []) if s.name)
        summaries.append(
            f"Path: {agent.path}\n"
            f"Name: {agent.card.name}\n"
            f"Description: {agent.card.description or ''}\n"
            f"Skills: {skills or '(none)'}"
        )

    prompt = (
        f"Task: {task_description}\n\n"
        f"Available agents:\n\n" + "\n---\n".join(summaries) + "\n\nWhich agent path is the best fit for this task? "
        "Reply with ONLY the agent path (e.g. /agent-name), nothing else."
    )

    result = await selector_agent.arun(prompt)
    logger.info(f"_select_agent_with_llm, result: {result}")
    chosen_path = (result.content or "").strip()

    agent_by_path = {a.path: a for a in agents}
    selected = agent_by_path.get(chosen_path)
    if selected is None:
        raise ValueError(f"LLM selector returned unknown path {chosen_path!r}; pool: {list(agent_by_path)}")
    logger.info(f"final selected: {selected}")
    return selected
