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

from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows.compiler import WorkflowExecutor

logger = logging.getLogger(__name__)


async def build_executor_registry(
    executor_keys: list[str],
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
) -> dict[str, WorkflowExecutor]:
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

    Returns:
        dict mapping each executor_key → WorkflowExecutor, ready for WorkflowRunner.

    Raises:
        KeyError: If an executor_key cannot be resolved to any active server or agent.
    """
    registry: dict[str, WorkflowExecutor] = {}

    for key in dict.fromkeys(executor_keys):  # deduplicate while preserving order
        registry[key] = await _resolve_executor(
            key,
            llm=llm,
            registry_url=registry_url,
            registry_token=registry_token,
        )

    return registry


async def _resolve_executor(
    key: str,
    *,
    llm: Model,
    registry_url: str,
    registry_token: str,
) -> WorkflowExecutor:
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
) -> WorkflowExecutor:
    """Wrap an MCP server as a workflow executor via the Jarvis gateway proxy."""
    proxy_url = f"{registry_url.rstrip('/')}/proxy/server{mcp_server.path}"
    description = mcp_server.config.get("description", "")

    if not registry_token:
        raise ValueError(
            "registry_token is required for MCP executors. "
            "Pass a user-scoped Registry access token so the gateway can resolve OAuth state."
        )

    mcp_tools = MCPTools(
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

    async def executor(step_input: StepInput, session_state: dict[str, Any]) -> StepOutput:
        prompt = _build_prompt(step_input)
        try:
            response = await agent.arun(prompt)
            return StepOutput(content=response.content or "")
        except Exception as exc:
            logger.exception("MCP executor %r failed", mcp_server.serverName)
            return StepOutput(content=str(exc), success=False, error=str(exc))

    executor.__name__ = f"{mcp_server.serverName}_mcp_executor"
    return executor


def _make_a2a_executor(
    agent: A2AAgent,
    *,
    registry_url: str,
    registry_token: str,
) -> WorkflowExecutor:
    """Wrap an A2A agent as a workflow executor using agno's A2A client."""
    url = f"{registry_url.rstrip('/')}/proxy/a2a/{agent.path.lstrip('/')}"
    agent_name = agent.config.title if agent.config else agent.card.name
    protocol_version = _a2a_protocol_version(agent)

    if not registry_token:
        raise ValueError(
            "registry_token is required for A2A executors. "
            "Pass a user-scoped Registry access token so the proxy can enforce ACL and runtime auth."
        )

    async def executor(step_input: StepInput, session_state: dict[str, Any]) -> StepOutput:
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
