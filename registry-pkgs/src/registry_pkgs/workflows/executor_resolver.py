"""Resolve workflow executor keys to MCP- or A2A-backed StepExecutor objects.

This module is **orchestration only**.  It queries MongoDB to decide which
backend handles a given key, then delegates to the appropriate factory:

- ``mcp_executor.make_mcp_executor``      — gateway-proxied MCP server calls
- ``a2a_executor.make_a2a_executor``      — direct A2A agent calls (JWT auth)
- ``a2a_executor.make_a2a_pool_executor`` — A2A pool with LLM-based selection
"""

from __future__ import annotations

import logging

from agno.models.base import Model
from agno.workflow.step import StepExecutor

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowNode
from registry_pkgs.workflows.a2a_executor import make_a2a_executor, make_a2a_pool_executor
from registry_pkgs.workflows.mcp_executor import make_mcp_executor
from registry_pkgs.workflows.types import POOL_KEY_PREFIX

logger = logging.getLogger(__name__)


async def build_executor_registry(
    executor_keys: list[str],
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
    jwt_config: JwtSigningConfig,
    accessible_agent_ids: set[str] | None,
    pool_nodes: list[WorkflowNode] | None = None,
    selector_llm: Model | None = None,
) -> dict[str, StepExecutor]:
    """Resolve each executor key to an MCP server or A2A agent executor.

    Args:
        executor_keys:        All ``executor_key`` values referenced by a WorkflowDefinition.
                              Duplicates are resolved only once.
        llm:                  agno-compatible Model used by MCP-server executors.
        registry_url:         Base URL of the Jarvis Registry (MCP proxy calls only).
        registry_token:       User-scoped Bearer token for the MCP gateway proxy.
                              **Not used for A2A executors** — those self-sign a JWT.
        jwt_config:           JWT signing config used by A2A executors to mint
                              short-lived service-to-agent tokens.
        accessible_agent_ids: ACL filter — set of A2AAgent ID strings the caller
                              is authorized to invoke. ``None`` = unrestricted
                              (only safe for trusted service / script contexts).
        pool_nodes:           STEP nodes that use ``a2a_pool`` instead of ``executor_key``.
        selector_llm:         Model used only for A2A pool selection; falls back to ``llm``.

    Returns:
        dict mapping each ``executor_key`` / pool synthetic-key → ``StepExecutor``.

    Raises:
        KeyError:        If an executor_key cannot be resolved to any active server or agent.
        PermissionError: If a resolved A2A agent is not in ``accessible_agent_ids``.
    """
    registry: dict[str, StepExecutor] = {}

    for key in dict.fromkeys(executor_keys):  # deduplicate while preserving order
        registry[key] = await _resolve_executor(
            key,
            llm=llm,
            registry_url=registry_url,
            registry_token=registry_token,
            jwt_config=jwt_config,
            accessible_agent_ids=accessible_agent_ids,
        )

    _selector = selector_llm or llm
    for node in pool_nodes or []:
        synthetic_key = f"{POOL_KEY_PREFIX}{node.id}"
        registry[synthetic_key] = make_a2a_pool_executor(
            node_name=node.name,
            pool_keys=node.a2a_pool,
            selector_llm=_selector,
            jwt_config=jwt_config,
            accessible_agent_ids=accessible_agent_ids,
        )
        logger.info("pool executor registered: %r → %s", node.name, synthetic_key)

    return registry


async def _resolve_executor(
    key: str,
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
    jwt_config: JwtSigningConfig,
    accessible_agent_ids: set[str] | None,
) -> StepExecutor:
    """Resolve a single executor key to its MCP or A2A executor.

    Raises:
        KeyError:        When neither an active MCP server nor A2A agent matches ``key``.
        PermissionError: When a matching A2A agent is not in ``accessible_agent_ids``.
    """
    mcp_server = await ExtendedMCPServer.find_one(
        ExtendedMCPServer.serverName == key,
        ExtendedMCPServer.status == "active",
    )
    if mcp_server is not None:
        logger.info("executor_key %r → MCP server %r", key, mcp_server.serverName)
        return make_mcp_executor(mcp_server, llm=llm, registry_url=registry_url, registry_token=registry_token)

    path = f"/{key}" if not key.startswith("/") else key
    a2a_agent = await A2AAgent.find_one(
        A2AAgent.path == path,
        A2AAgent.status == "active",
    )
    if a2a_agent is not None:
        if accessible_agent_ids is not None and str(a2a_agent.id) not in accessible_agent_ids:
            raise PermissionError(
                f"executor_key {key!r} → A2A agent {path!r}: user lacks access (agent_id={a2a_agent.id})"
            )
        logger.info("executor_key %r → A2A agent %r (direct)", key, a2a_agent.path)
        return make_a2a_executor(a2a_agent, jwt_config=jwt_config)

    raise KeyError(
        f"executor_key {key!r} not resolved: "
        f"no active MCP server with serverName={key!r} "
        f"or A2A agent with path={path!r}"
    )
