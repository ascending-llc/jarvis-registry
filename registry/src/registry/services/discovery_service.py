"""
Discovery service for progressive disclosure (Level 2).

Responsibilities:
  - get_server_capabilities: reads tool/resource/prompt summaries from MongoDB.
    Returns ServerCapabilities without parameter schemas, keeping token cost low.
"""

import logging
from typing import Any

from registry_pkgs.models import ExtendedMCPServer

from ..schemas.discovery import PromptSummary, ResourceSummary, ServerCapabilities, ToolSummary

logger = logging.getLogger(__name__)


def _build_capabilities(server: ExtendedMCPServer) -> ServerCapabilities:
    """Build ServerCapabilities from a MongoDB ExtendedMCPServer document."""
    config: dict[str, Any] = server.config or {}

    tools: list[ToolSummary] = []
    for _fn_name, tool_data in config.get("toolFunctions", {}).items():
        if not isinstance(tool_data, dict) or "function" not in tool_data:
            continue
        func = tool_data["function"]
        mcp_name = tool_data.get("mcpToolName") or func.get("name") or _fn_name
        description = func.get("description", "")
        tools.append(ToolSummary(name=mcp_name, description=description))

    resources: list[ResourceSummary] = [
        ResourceSummary(
            name=r.get("name", ""),
            uri=r.get("uri", ""),
            description=r.get("description", ""),
        )
        for r in config.get("resources", [])
    ]

    prompts: list[PromptSummary] = [
        PromptSummary(
            name=p.get("name", ""),
            description=p.get("description", ""),
        )
        for p in config.get("prompts", [])
    ]

    requires_auth = bool(config.get("requiresOAuth") or config.get("apiKey"))

    return ServerCapabilities(
        server_name=server.serverName,
        server_id=str(server.id),
        path=server.path or "",
        description=config.get("description", ""),
        requires_auth=requires_auth,
        tools=tools,
        resources=resources,
        prompts=prompts,
    )


async def get_server_capabilities(server_name: str) -> ServerCapabilities | None:
    """
    Return tool/resource/prompt summaries for *server_name* from MongoDB.

    Summaries contain name + description only — no parameter schemas.

    Args:
        server_name: serverName field in MongoDB (e.g. "github").

    Returns:
        ServerCapabilities, or None if the server is not found.
    """
    server = await ExtendedMCPServer.find_one({"serverName": server_name})
    if not server:
        logger.warning("get_server_capabilities: server not found: %s", server_name)
        return None

    return _build_capabilities(server)
