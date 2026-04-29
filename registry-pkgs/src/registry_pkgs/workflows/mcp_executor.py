from __future__ import annotations

import logging
from typing import Any

from agno.agent import Agent
from agno.models.base import Model
from agno.tools.mcp import MCPTools
from agno.tools.mcp.params import StreamableHTTPClientParams
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows.helpers import build_prompt

logger = logging.getLogger(__name__)


def make_mcp_executor(
    mcp_server: ExtendedMCPServer,
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
) -> StepExecutor:
    """Wrap an MCP server as a workflow executor via the Jarvis gateway proxy.

    Args:
        mcp_server:     Active ``ExtendedMCPServer`` document from MongoDB.
        llm:            agno-compatible model used by the inner Agent.
        registry_url:   Base URL of the Jarvis Registry gateway.
        registry_token: User-scoped Bearer token for the MCP proxy.

    Returns:
        An async callable that accepts ``(StepInput, session_state)`` and
        returns a ``StepOutput``.

    Raises:
        ValueError: When ``registry_token`` is empty.
    """
    if not registry_token:
        raise ValueError(
            "registry_token is required for MCP executors. "
            "Pass a user-scoped Registry access token so the gateway can resolve OAuth state."
        )

    proxy_url = f"{registry_url.rstrip('/')}/proxy/server{mcp_server.path}"
    description = mcp_server.config.get("description", "")

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

    async def executor(step_input: StepInput, session_state: dict[str, Any] | None = None) -> StepOutput:
        prompt = build_prompt(step_input)
        logger.debug("  → calling MCP server %r  url=%s  prompt=%r", mcp_server.serverName, proxy_url, prompt[:120])
        try:
            response = await agent.arun(prompt)
            content = response.content or ""
            logger.debug("  ← MCP server %r responded: %r", mcp_server.serverName, content[:200])
            return StepOutput(content=content)
        except Exception as exc:
            logger.exception("MCP executor %r failed", mcp_server.serverName)
            return StepOutput(content=str(exc), success=False, error=str(exc))

    executor.__name__ = f"{mcp_server.serverName}_mcp_executor"
    return executor
