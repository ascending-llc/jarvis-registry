from __future__ import annotations

import logging
from typing import Any

from agno.agent import Agent
from agno.models.base import Model
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.workflows.a2a_client import (
    call_a2a,
    raise_if_iam_unsupported,
)
from registry_pkgs.workflows.helpers import build_prompt

logger = logging.getLogger(__name__)


def make_a2a_executor(agent: A2AAgent, *, jwt_config: JwtSigningConfig) -> StepExecutor:
    """Wrap an A2A agent as a workflow StepExecutor via a direct A2A call."""

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        raise_if_iam_unsupported(agent)
        result = await call_a2a(agent, build_prompt(step_input), jwt_config=jwt_config)
        return StepOutput(content=result.text, success=result.success, error=result.error)

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
    """Build a StepExecutor that picks the best A2A agent from a pool at runtime.

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

        raise_if_iam_unsupported(selected_agent)
        result = await call_a2a(selected_agent, task, jwt_config=jwt_config)
        return StepOutput(content=result.text, success=result.success, error=result.error)

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
