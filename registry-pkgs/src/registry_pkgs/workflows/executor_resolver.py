"""Resolve workflow executor keys to MCP- or A2A-backed StepExecutor objects.

This module is **orchestration only**.  It queries MongoDB to decide which
backend handles a given key, then delegates to the appropriate factory:

- ``mcp_executor.make_mcp_executor``      — direct MCP server calls
- ``a2a_executor.make_a2a_executor``      — direct A2A agent calls (JWT auth)
- ``a2a_executor.make_a2a_pool_executor`` — A2A pool with LLM-based selection
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any

import httpx
from agno.models.base import Model
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import WorkflowNode
from registry_pkgs.telemetry.workflow_metrics import record_agent_invocation
from registry_pkgs.types import UserContextDict
from registry_pkgs.workflows.a2a_client import HeadersProvider
from registry_pkgs.workflows.a2a_executor import make_a2a_executor, make_a2a_pool_executor
from registry_pkgs.workflows.mcp_executor import McpHeadersProvider, make_mcp_executor
from registry_pkgs.workflows.model_resolution import AzureAdTokenProvider, resolve_model
from registry_pkgs.workflows.types import (
    BUILTIN_ECHO_EXECUTOR_KEY,
    BUILTIN_SET_VALUE_EXECUTOR_KEY,
    POOL_KEY_PREFIX,
)

logger = logging.getLogger(__name__)

_AGENT_TYPE_SUFFIXES = {
    "_mcp_executor": "mcp",
    "_a2a_executor": "a2a",
    "_pool_executor": "a2a_pool",
}


def _detect_agent_type(executor: StepExecutor) -> str:
    name = getattr(executor, "__name__", "")
    for suffix, agent_type in _AGENT_TYPE_SUFFIXES.items():
        if name.endswith(suffix):
            return agent_type
    if name.startswith("builtin_"):
        return "builtin"
    return "unknown"


def _instrumented_executor(
    executor: StepExecutor,
    agent_type: str,
    executor_key: str,
) -> StepExecutor:
    """Wrap a StepExecutor with invocation metrics."""

    @functools.wraps(executor)
    async def _wrapper(
        step_input: StepInput,
        session_state: dict[str, Any] | None = None,
        run_context: Any | None = None,
    ) -> StepOutput:
        start = time.perf_counter()
        error_type = "none"
        success = True
        try:
            kwargs: dict[str, Any] = {}
            if session_state is not None:
                kwargs["session_state"] = session_state
            if run_context is not None:
                kwargs["run_context"] = run_context
            result = await executor(step_input, **kwargs)
            success = getattr(result, "success", True)
            if not success:
                error_type = "StepOutputFailure"
            return result
        except asyncio.CancelledError:
            success = False
            error_type = "CancelledError"
            raise
        except Exception as exc:
            success = False
            error_type = type(exc).__name__
            raise
        finally:
            try:
                record_agent_invocation(agent_type, executor_key, success, time.perf_counter() - start, error_type)
            except Exception:
                logger.warning("Failed to record agent invocation metrics", exc_info=True)

    return _wrapper


def _builtin_executor(key: str) -> StepExecutor | None:
    """Return a lightweight in-process executor for builtin workflow demo steps."""
    if key == BUILTIN_ECHO_EXECUTOR_KEY:

        async def _echo(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
            # Demo executor: not LLM-backed, echoes the raw trigger text.
            # Uses get_input_as_string() rather than build_prompt() to avoid
            # storing the structured Markdown prompt in session_state.
            state = session_state if session_state is not None else {}
            state["echo_count"] = int(state.get("echo_count", 0)) + 1
            return StepOutput(content=step_input.get_input_as_string() or "", success=True)

        _echo.__name__ = "builtin_echo_executor"
        return _echo

    if key == BUILTIN_SET_VALUE_EXECUTOR_KEY:

        async def _set_value(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
            # Demo executor: not LLM-backed, stores raw trigger text in session_state.
            state = session_state if session_state is not None else {}
            state["value"] = step_input.get_input_as_string() or ""
            return StepOutput(content=str(state["value"]), success=True)

        _set_value.__name__ = "builtin_set_value_executor"
        return _set_value

    return None


async def build_executor_registry(
    nodes: list[WorkflowNode],
    *,
    default_model: Model,
    auth_context: UserContextDict | None,
    jwt_config: JwtSigningConfig,
    encryption_key: bytes,
    azure_ad_token_provider: AzureAdTokenProvider | None,
    pool_nodes: list[WorkflowNode] | None = None,
    a2a_httpx_client: httpx.AsyncClient | None = None,
    headers_provider: HeadersProvider | None = None,
    redis_client: Any | None = None,
    redis_key_prefix: str | None = None,
    mcp_headers_provider: McpHeadersProvider | None = None,
) -> dict[str, StepExecutor]:
    """Resolve each STEP node to an MCP server / A2A agent / A2A pool executor.

    The registry is keyed by ``node.id`` so two nodes referencing the same backend with different
    ``model_source_id`` overrides get distinct executors. The actual resolve+build work is still
    deduplicated per ``(executor_key, model_source_id)`` combo, so the common case (many nodes, no
    override) costs no more than before.

    Args:
        nodes:            Non-pool STEP nodes with ``executor_key`` set.
        default_model:    Model used when a node has no ``model_source_id`` override.
        auth_context:     Triggering user's auth context for manually-registered MCP servers.
        jwt_config:       JWT signing config used by A2A executors and AgentCore MCP servers.
        encryption_key:   Key used to decrypt a ModelSource's stored Azure credential.
        azure_ad_token_provider: Shared Azure AD bearer-token provider for Workload-Identity models.
        pool_nodes:       STEP nodes that use ``a2a_pool`` instead of ``executor_key``.
        a2a_httpx_client: Optional shared httpx client passed to A2A executors.
        headers_provider: Optional shared headers provider passed to A2A executors.
        redis_client:     Optional shared Redis client for AgentCore JWT caching.
        redis_key_prefix: Prefix for Redis cache keys.
        mcp_headers_provider: Optional headers provider for manually-registered MCP servers.

    Returns:
        dict mapping each ``node.id`` / pool synthetic-key → ``StepExecutor``.

    Raises:
        KeyError:        If an executor_key cannot be resolved to any active server or agent.
    """
    registry: dict[str, StepExecutor] = {}
    built_by_combo: dict[tuple[str, str | None], StepExecutor] = {}

    for node in nodes:
        combo = (node.executor_key, node.model_source_id)
        if combo not in built_by_combo:
            node_model = await resolve_model(
                node.model_source_id,
                fallback_model=default_model,
                encryption_key=encryption_key,
                azure_ad_token_provider=azure_ad_token_provider,
            )
            raw = await _resolve_executor(
                node.executor_key,
                llm=node_model,
                auth_context=auth_context,
                jwt_config=jwt_config,
                a2a_httpx_client=a2a_httpx_client,
                headers_provider=headers_provider,
                redis_client=redis_client,
                redis_key_prefix=redis_key_prefix,
                mcp_headers_provider=mcp_headers_provider,
            )
            built_by_combo[combo] = _instrumented_executor(raw, _detect_agent_type(raw), node.executor_key)
        registry[node.id] = built_by_combo[combo]

    for node in pool_nodes or []:
        synthetic_key = f"{POOL_KEY_PREFIX}{node.id}"
        node_model = await resolve_model(
            node.model_source_id,
            fallback_model=default_model,
            encryption_key=encryption_key,
            azure_ad_token_provider=azure_ad_token_provider,
        )
        raw = make_a2a_pool_executor(
            node_name=node.name,
            pool_keys=node.a2a_pool,
            selector_llm=node_model,
            jwt_config=jwt_config,
            httpx_client=a2a_httpx_client,
            headers_provider=headers_provider,
        )
        registry[synthetic_key] = _instrumented_executor(raw, "a2a_pool", synthetic_key)
        logger.debug("pool executor registered: %r → %s", node.name, synthetic_key)

    return registry


async def _resolve_executor(
    key: str,
    *,
    llm: Model,
    auth_context: UserContextDict | None,
    jwt_config: JwtSigningConfig,
    a2a_httpx_client: httpx.AsyncClient | None = None,
    headers_provider: HeadersProvider | None = None,
    redis_client: Any | None = None,
    redis_key_prefix: str | None = None,
    mcp_headers_provider: McpHeadersProvider | None = None,
) -> StepExecutor:
    """Resolve a single executor key to its MCP or A2A executor.

    Raises:
        KeyError: When neither an active MCP server nor A2A agent matches ``key``.
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
        return make_mcp_executor(
            mcp_server,
            llm=llm,
            auth_context=auth_context,
            jwt_config=jwt_config,
            redis_client=redis_client,
            redis_key_prefix=redis_key_prefix or "jarvis-registry",
            mcp_headers_provider=mcp_headers_provider,
        )

    path = key.lstrip("/")
    a2a_agent = await A2AAgent.find_one(
        A2AAgent.path == path,
        {"config.enabled": True},
    )
    if a2a_agent is not None:
        logger.debug("executor_key %r → A2A agent %r (direct)", key, a2a_agent.path)
        return make_a2a_executor(
            a2a_agent,
            jwt_config=jwt_config,
            httpx_client=a2a_httpx_client,
            headers_provider=headers_provider,
        )

    raise KeyError(
        f"executor_key {key!r} not resolved: "
        f"no enabled MCP server with serverName={key!r} "
        f"or enabled A2A agent with path={path!r}"
    )
