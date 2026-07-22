from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agno.agent import Agent
from agno.models.base import Model
from agno.tools.mcp import MCPTools
from agno.tools.mcp.params import StreamableHTTPClientParams
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.core.agentcore_jwt import parse_agentcore_runtime_access, sign_agentcore_jwt
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows.helpers import build_prompt
from registry_pkgs.workflows.types import WorkflowConfigError

logger = logging.getLogger(__name__)

McpHeadersProvider = Callable[[ExtendedMCPServer, dict[str, Any] | None], Awaitable[dict[str, str]]]
McpAccessAuthorizer = Callable[[ExtendedMCPServer, dict[str, Any]], Awaitable[None]]


def _get_target_url(server: ExtendedMCPServer) -> str:
    """Return the downstream MCP server URL stored in server config."""
    config = server.config or {}
    url = config.get("url")
    if url is None:
        raise WorkflowConfigError(f"Server URL is not configured for server '{server.serverName}'")
    return url


def _raise_if_agent_failed(response: Any) -> None:
    status = getattr(response, "status", None)
    status_value = getattr(status, "value", status)
    if str(status_value).lower() in {"error", "failed", "cancelled"}:
        content = getattr(response, "content", None)
        raise RuntimeError(str(content or f"Agent returned status {status_value}"))

    content = getattr(response, "content", None)
    if isinstance(content, str) and "unable to locate credentials" in content.lower():
        raise RuntimeError(content)


async def _execute_mcp_agent(
    *,
    server: ExtendedMCPServer,
    mcp_tools: MCPTools,
    agent: Agent,
    prompt: str,
    target_url: str,
) -> StepOutput:
    """Run the shared MCP connect/agent/error lifecycle for either auth strategy."""
    logger.debug("  → calling MCP server %r  url=%s  prompt=%r", server.serverName, target_url, prompt[:120])
    try:
        await mcp_tools.connect(force=not mcp_tools.initialized)
        if not mcp_tools.initialized:
            raise RuntimeError(f"Failed to initialize MCP toolkit at {target_url}")

        response = await agent.arun(prompt)
        _raise_if_agent_failed(response)
        content = response.content or ""
        logger.debug("  ← MCP server %r responded: %r", server.serverName, content[:200])
        return StepOutput(content=content)
    except Exception as exc:
        logger.exception("MCP executor %r failed", server.serverName)
        raise RuntimeError(f"MCP executor {server.serverName!r} failed: {exc}") from exc


def make_mcp_executor(
    mcp_server: ExtendedMCPServer,
    *,
    llm: Model,
    auth_context: dict[str, Any] | None,
    jwt_config: JwtSigningConfig,
    redis_client: Any,
    redis_key_prefix: str,
    mcp_access_authorizer: McpAccessAuthorizer | None = None,
    mcp_headers_provider: McpHeadersProvider | None,
) -> StepExecutor:
    """Wrap an MCP server as a workflow executor via a direct downstream call.

    Args:
        mcp_server:           Active ``ExtendedMCPServer`` document from MongoDB.
        llm:                  agno-compatible model used by the inner Agent.
        auth_context:         Triggering user's auth context; required for
                              manually-registered (OAuth/apiKey) servers.
        jwt_config:           JWT signing config used for AgentCore-federated
                              servers to self-sign a runtime JWT.
        redis_client:         Redis client for caching AgentCore JWTs.
        redis_key_prefix:     Prefix for Redis cache keys.
        mcp_access_authorizer: Async consent preflight for user-triggered calls.
        mcp_headers_provider: Async callable that builds authenticated headers
                              for manually-registered servers.

    Returns:
        An async callable that accepts ``(StepInput, session_state)`` and
        returns a ``StepOutput``.

    Raises:
        WorkflowConfigError: When a manually-registered server is invoked
            without an auth context.
    """
    description = mcp_server.config.get("description", "")
    runtime_access = mcp_server.config.get("runtimeAccess")

    if runtime_access:
        access_config = parse_agentcore_runtime_access(runtime_access)
        if access_config.mode == AgentCoreRuntimeAccessMode.IAM:
            raise NotImplementedError(
                f"IAM authentication is not supported for MCP workflow server {mcp_server.serverName!r}"
            )
        if access_config.mode != AgentCoreRuntimeAccessMode.JWT or access_config.jwt is None:
            raise WorkflowConfigError(
                f"Invalid AgentCore JWT runtimeAccess configuration for MCP server {mcp_server.serverName!r}"
            )

        # Case 1: AgentCore-federated server — sign a self-issued JWT synchronously
        # inside agno's header_provider callback.
        def _header_provider() -> dict[str, str]:
            token = sign_agentcore_jwt(
                access_config.jwt,
                subject=jwt_config.registry_app_name,
                signing=jwt_config,
                cache_key=f"{redis_key_prefix}:agentcore_jwt:{mcp_server.id}",
                redis_client=redis_client,
            )
            return {"Authorization": f"Bearer {token}"}

        mcp_tools = MCPTools(
            transport="streamable-http",
            server_params=StreamableHTTPClientParams(url=_get_target_url(mcp_server)),
            header_provider=_header_provider,
            refresh_connection=False,
        )
        agent = Agent(
            model=llm,
            tools=[mcp_tools],
            name=f"{mcp_server.serverName}-agent",
            description=description,
        )

        async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
            if auth_context is not None:
                if mcp_access_authorizer is None:
                    raise WorkflowConfigError(
                        f"No access authorizer configured for MCP server {mcp_server.serverName!r}"
                    )
                await mcp_access_authorizer(mcp_server, auth_context)
            prompt = build_prompt(step_input)
            target_url = _get_target_url(mcp_server)
            return await _execute_mcp_agent(
                server=mcp_server,
                mcp_tools=mcp_tools,
                agent=agent,
                prompt=prompt,
                target_url=target_url,
            )

    else:
        # Case 2: manually-registered server (OAuth/apiKey/etc) — build headers
        # fresh inside each step executor call.
        async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
            if auth_context is None:
                raise WorkflowConfigError(
                    f"No auth context available to call MCP server {mcp_server.serverName!r} "
                    "(script-driven run or missing triggering identity)"
                )
            if mcp_headers_provider is None:
                raise WorkflowConfigError(f"No headers provider configured for MCP server {mcp_server.serverName!r}")

            prompt = build_prompt(step_input)
            target_url = _get_target_url(mcp_server)
            headers = await mcp_headers_provider(mcp_server, auth_context)
            mcp_tools = MCPTools(
                transport="streamable-http",
                server_params=StreamableHTTPClientParams(url=target_url, headers=headers),
            )
            agent = Agent(
                model=llm,
                tools=[mcp_tools],
                name=f"{mcp_server.serverName}-agent",
                description=description,
            )

            return await _execute_mcp_agent(
                server=mcp_server,
                mcp_tools=mcp_tools,
                agent=agent,
                prompt=prompt,
                target_url=target_url,
            )

    executor.__name__ = f"{mcp_server.serverName}_mcp_executor"
    return executor
