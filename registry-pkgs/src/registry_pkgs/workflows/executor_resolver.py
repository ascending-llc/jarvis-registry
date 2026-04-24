"""Resolve workflow executor keys to MCP- or A2A-backed ExecutorFn objects.

MCP executors always go through the Jarvis gateway proxy. OAuth/token refresh
stays in the gateway; callers must pass a user-scoped ``registry_token``.
"""

from __future__ import annotations

import logging
from typing import Any

from agno.agent import Agent
from agno.client.a2a import A2AClient
from agno.models.base import Model
from agno.tools.mcp import MCPTools
from agno.tools.mcp.params import StreamableHTTPClientParams
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowNode
from registry_pkgs.workflows.types import POOL_KEY_PREFIX

logger = logging.getLogger(__name__)


async def build_executor_registry(
    executor_keys: list[str],
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
    pool_nodes: list[WorkflowNode] | None = None,
    selector_llm: Model | None = None,
) -> dict[str, StepExecutor]:
    """Resolve each executor key to an MCP server or A2A agent.

    Args:
        executor_keys:  All executor_key values referenced by a WorkflowDefinition.
                        Duplicates are resolved only once.
        llm:            Any agno-compatible Model instance (Claude, OpenAIChat, Gemini, …).
                        Used to back MCP-server executors.  A2A executors do not use it.
        registry_url:   Base URL of the Jarvis Registry (e.g. https://jarvis.ascendingdc.com).
        registry_token: User-scoped Bearer token for authenticating with the registry gateway.
                        For MCP executors this must identify the end user, not just the service,
                        so the gateway can resolve OAuth state from MongoDB on their behalf.
        pool_nodes:     STEP nodes that use a2a_pool instead of executor_key.  For each node
                        a pool executor is built and stored under ``__pool__{node.id}``.
        selector_llm:   Model used only for A2A pool selection.  Falls back to ``llm`` when
                        not provided.

    Returns:
        dict mapping each executor_key / pool synthetic-key → StepExecutor.

    Raises:
        KeyError: If an executor_key cannot be resolved to any active server or agent.
    """
    registry: dict[str, StepExecutor] = {}

    for key in dict.fromkeys(executor_keys):  # deduplicate while preserving order
        registry[key] = await _resolve_executor(
            key,
            llm=llm,
            registry_url=registry_url,
            registry_token=registry_token,
        )

    _selector = selector_llm or llm
    for node in pool_nodes or []:
        synthetic_key = f"{POOL_KEY_PREFIX}{node.id}"
        registry[synthetic_key] = _make_a2a_pool_executor(
            node_name=node.name,
            pool_keys=node.a2a_pool,
            selector_llm=_selector,
            registry_url=registry_url,
            registry_token=registry_token,
        )
        logger.info("pool executor registered: %r → %s", node.name, synthetic_key)
    return registry


async def _resolve_executor(
    key: str,
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
) -> StepExecutor:
    """Resolve a single executor key to a workflow executor."""
    mcp_server = await ExtendedMCPServer.find_one(
        ExtendedMCPServer.serverName == key,
        ExtendedMCPServer.status == "active",
    )
    if mcp_server is not None:
        logger.info("executor_key %r → MCP server %r", key, mcp_server.serverName)
        return _make_mcp_executor(mcp_server, llm=llm, registry_url=registry_url, registry_token=registry_token)

    path = f"/{key}" if not key.startswith("/") else key
    a2a_agent = await A2AAgent.find_one(
        A2AAgent.path == path,
        A2AAgent.status == "active",
    )
    if a2a_agent is not None:
        logger.info("executor_key %r → A2A agent %r", key, a2a_agent.path)
        return _make_a2a_executor(a2a_agent, registry_url=registry_url, registry_token=registry_token)

    raise KeyError(
        f"executor_key {key!r} not resolved: "
        f"no active MCP server with serverName={key!r} "
        f"or A2A agent with path={path!r}"
    )


def _make_mcp_executor(
    mcp_server: ExtendedMCPServer,
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
) -> StepExecutor:
    """Wrap an MCP server as a workflow executor via the Jarvis gateway proxy."""
    proxy_url = f"{registry_url.rstrip('/')}/proxy/server{mcp_server.path}"
    description = mcp_server.config.get("description", "")

    if not registry_token:
        raise ValueError(
            "registry_token is required for MCP executors. "
            "Pass a user-scoped Registry access token so the gateway can resolve OAuth state."
        )

    mcp_tools = MCPTools(
        transport="streamable-http",
        server_params=StreamableHTTPClientParams(
            url=proxy_url,
            headers={"Authorization": f"Bearer {registry_token}"},
        ),
    )
    agent = Agent(
        model=llm,
        tools=[mcp_tools],
        name=f"{mcp_server.serverName}-agent",
        description=description,
    )

    # session_state has a default so the closure matches StepExecutor
    # (Callable[[StepInput], ...]).  agno detects the parameter name and
    # injects the live session dict as a keyword argument at runtime.
    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        prompt = _build_prompt(step_input)
        try:
            response = await agent.arun(prompt)
            return StepOutput(content=response.content or "")
        except Exception as exc:
            logger.exception("MCP executor %r failed", mcp_server.serverName)
            return StepOutput(content=str(exc), success=False, error=str(exc))

    executor.__name__ = f"{mcp_server.serverName}_mcp_executor"
    return executor


def _make_a2a_pool_executor(
    node_name: str,
    pool_keys: list[str],
    *,
    selector_llm: Model,
    registry_url: str,
    registry_token: str,
) -> StepExecutor:
    """Build an executor that picks the best A2A agent from a pool at runtime.

    Selection is cached in ``session_state`` so retries reuse the same agent.
    The selector Agent is created once per pool executor (not per call).
    """
    if not registry_token:
        raise ValueError("registry_token is required for A2A pool executors.")

    # Create the selector agent once — reused across all calls to this executor.
    # A cheap, fast model (e.g. Nova Micro / gpt-4o-mini) is recommended.
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
        task = _build_prompt(step_input)
        # Use the live session dict when agno injects it; fall back to a local dict so
        # the executor is still callable without session_state (type-compatible with StepExecutor).
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
            # 1. Retry guard — if this key is present when the executor runs again,
            #    we skip LLM selection and reuse the same agent.
            # 2. Persistence — WorkflowRunSyncer reads this key to populate
            #    NodeRun.selected_a2a_key after the step completes.
            state[cache_key] = selected_path
            logger.info("pool %r → selected agent %r", node_name, selected_path)

        url = f"{registry_url.rstrip('/')}/proxy/a2a/{selected_path.lstrip('/')}"
        protocol_version = _a2a_protocol_version(selected_agent) if selected_agent else None
        try:
            result_text = await _a2a_send(
                url,
                task,
                registry_token=registry_token,
                protocol_version=protocol_version,
            )
            return StepOutput(content=result_text)
        except Exception as exc:
            logger.exception("A2A pool executor %r failed (agent=%r)", node_name, selected_path)
            return StepOutput(content=str(exc), success=False, error=str(exc))

    executor.__name__ = f"{node_name}_pool_executor"
    return executor


async def _select_agent_with_llm(
    agents: list[A2AAgent],
    task_description: str,
    selector_agent: Agent,
) -> A2AAgent:
    """Use an LLM to pick the best-fit agent from the pool. Returns the selected A2AAgent."""
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
    chosen_path = (result.content or "").strip()

    agent_by_path = {a.path: a for a in agents}
    selected = agent_by_path.get(chosen_path)
    if selected is None:
        logger.warning("selector returned %r not in pool %r; falling back", chosen_path, set(agent_by_path))
        selected = agents[0]

    return selected


def _make_a2a_executor(
    agent: A2AAgent,
    *,
    registry_url: str,
    registry_token: str,
) -> StepExecutor:
    """Wrap an A2A agent as a workflow executor using agno's A2A client."""
    url = f"{registry_url.rstrip('/')}/proxy/a2a/{agent.path.lstrip('/')}"
    agent_name = agent.config.title if agent.config else agent.card.name
    protocol_version = _a2a_protocol_version(agent)

    if not registry_token:
        raise ValueError(
            "registry_token is required for A2A executors. "
            "Pass a user-scoped Registry access token so the proxy can enforce ACL and runtime auth."
        )

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        text = _build_prompt(step_input)
        try:
            result_text = await _a2a_send(url, text, registry_token=registry_token, protocol_version=protocol_version)
            return StepOutput(content=result_text)
        except Exception as exc:
            logger.exception("A2A executor %r failed", agent_name)
            return StepOutput(content=str(exc), success=False, error=str(exc))

    executor.__name__ = f"{agent.path.lstrip('/')}_a2a_executor"
    return executor


async def _a2a_send(
    url: str,
    text: str,
    *,
    registry_token: str,
    protocol_version: str | None = None,
    timeout: int = 300,
) -> str:
    """Send a message through agno's A2A client and return spec-compliant text content."""
    headers = {"Authorization": f"Bearer {registry_token}"}
    if protocol_version:
        headers["A2A-Version"] = protocol_version
    client = A2AClient(base_url=url, timeout=timeout, protocol="json-rpc")
    result = await client.send_message(text, headers=headers)
    return result.content or ""


def _a2a_protocol_version(agent: A2AAgent) -> str | None:
    """Determine the A2A protocol version."""
    version = getattr(agent.card, "protocol_version", None)
    if not version:
        return None
    parts = str(version).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(version)


def _build_prompt(step_input: StepInput) -> str:
    """Assemble a prompt string from step_input fields."""
    parts: list[str] = []
    if step_input.previous_step_content:
        parts.append(f"Context from previous step:\n{step_input.previous_step_content}")
    if step_input.input:
        parts.append(step_input.input)
    return "\n\n".join(parts) or "(no input)"
