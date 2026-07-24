"""Resolve workflow executor keys to MCP- or A2A-backed StepExecutor objects.

This module is **orchestration only**.  It queries MongoDB to decide which
backend handles a given key, then delegates to the appropriate factory:

- ``mcp_executor.make_mcp_executor``      — gateway-proxied MCP server calls
- ``a2a_executor.make_a2a_executor``      — direct A2A agent calls
- ``a2a_executor.make_a2a_pool_executor`` — A2A pool with LLM-based selection
"""

from __future__ import annotations

import logging

from agno.models.base import Model
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowNode
from registry_pkgs.workflows.a2a_client import ClientProvider
from registry_pkgs.workflows.a2a_executor import make_a2a_executor, make_a2a_pool_executor
from registry_pkgs.workflows.mcp_executor import make_mcp_executor
from registry_pkgs.workflows.types import POOL_KEY_PREFIX

logger = logging.getLogger(__name__)


def _builtin_executor(key: str) -> StepExecutor | None:
    """Return a lightweight in-process executor for builtin workflow demo steps."""
    if key == "echo":

        async def _echo(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
            # Demo executor: not LLM-backed, echoes the raw trigger text.
            # Uses get_input_as_string() rather than build_prompt() to avoid
            # storing the structured Markdown prompt in session_state.
            state = session_state if session_state is not None else {}
            state["echo_count"] = int(state.get("echo_count", 0)) + 1
            return StepOutput(content=step_input.get_input_as_string() or "", success=True)

        _echo.__name__ = "builtin_echo_executor"
        return _echo

    if key == "set_value":

        async def _set_value(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
            # Demo executor: not LLM-backed, stores raw trigger text in session_state.
            state = session_state if session_state is not None else {}
            state["value"] = step_input.get_input_as_string() or ""
            return StepOutput(content=str(state["value"]), success=True)

        _set_value.__name__ = "builtin_set_value_executor"
        return _set_value

    return None


async def build_executor_registry(
    executor_keys: list[str],
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
    pool_nodes: list[WorkflowNode] | None = None,
    selector_llm: Model | None = None,
    client_provider: ClientProvider | None = None,
) -> dict[str, StepExecutor]:
    """Resolve each executor key to an MCP server or A2A agent executor.

    ACL authorization for referenced resources is enforced at workflow
    authoring time (see ``WorkflowService._authorize_new_executor_refs``);
    resolution here is intentionally ACL-free.

    Args:
        executor_keys:    All ``executor_key`` values referenced by a WorkflowDefinition.
                          Duplicates are resolved only once.
        llm:              agno-compatible Model used by MCP-server executors.
        registry_url:     Base URL of the Jarvis Registry (MCP proxy calls only).
        registry_token:   User-scoped Bearer token for the MCP gateway proxy.
                          **Not used for A2A executors**.
        pool_nodes:       STEP nodes that use ``a2a_pool`` instead of ``executor_key``.
        selector_llm:     Model used only for A2A pool selection; falls back to ``llm``.
        client_provider:  Optional provider for agent-specific authenticated A2A clients.

    Returns:
        dict mapping each ``executor_key`` / pool synthetic-key → ``StepExecutor``.

    Raises:
        KeyError:        If an executor_key cannot be resolved to any active server or agent.
    """
    registry: dict[str, StepExecutor] = {}

    for key in dict.fromkeys(executor_keys):  # deduplicate while preserving order
        registry[key] = await _resolve_executor(
            key,
            llm=llm,
            registry_url=registry_url,
            registry_token=registry_token,
            client_provider=client_provider,
        )

    _selector = selector_llm or llm
    for node in pool_nodes or []:
        synthetic_key = f"{POOL_KEY_PREFIX}{node.id}"
        registry[synthetic_key] = make_a2a_pool_executor(
            node_name=node.name,
            pool_keys=node.a2a_pool,
            selector_llm=_selector,
            client_provider=client_provider,
        )
        logger.debug("pool executor registered: %r → %s", node.name, synthetic_key)

    return registry


async def _resolve_executor(
    key: str,
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
    client_provider: ClientProvider | None = None,
) -> StepExecutor:
    """Resolve a single executor key to its MCP or A2A executor.

    Raises:
        KeyError:        When neither an active MCP server nor A2A agent matches ``key``.
    """
    builtin = _builtin_executor(key)
    if builtin is not None:
        logger.debug("executor_key %r → builtin executor", key)
        return builtin

    mcp_server = await ExtendedMCPServer.find_one(
        ExtendedMCPServer.serverName == key,
        {"config.enabled": True},
    )
    if mcp_server is not None:
        logger.debug("executor_key %r → MCP server %r", key, mcp_server.serverName)
        return make_mcp_executor(mcp_server, llm=llm, registry_url=registry_url, registry_token=registry_token)

    path = key.lstrip("/")
    a2a_agent = await A2AAgent.find_one(
        A2AAgent.path == path,
        {"config.enabled": True},
    )
    if a2a_agent is not None:
        logger.debug("executor_key %r → A2A agent %r (direct)", key, a2a_agent.path)
        return make_a2a_executor(
            a2a_agent,
            client_provider=client_provider,
        )

    raise KeyError(
        f"executor_key {key!r} not resolved: "
        f"no enabled MCP server with serverName={key!r} "
        f"or enabled A2A agent with path={path!r}"
    )
