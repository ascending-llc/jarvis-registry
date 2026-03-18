"""
Service for transforming internal A2A agent data to Anthropic API schema.

This bridges our internal agent data model with the external Anthropic API format,
following the same pattern as the server transform service.
"""

import logging
from typing import Any

from ..core.config import settings
from ..schemas.anthropic_schema import (
    Package,
    PaginationMetadata,
    ServerDetail,
    ServerList,
    ServerResponse,
)

# Get logger - logging is configured centrally in main.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def _create_agent_transport_config(
    agent_info: dict[str, Any],
) -> dict[str, Any]:
    """
    Create transport configuration from internal agent info.

    For A2A agents, the transport URL is the agent's endpoint URL.

    Args:
        agent_info: Internal agent data structure

    Returns:
        Transport configuration dict
    """
    agent_url = agent_info.get("url", "")

    return {"type": "streamable-http", "url": agent_url}


def _determine_agent_version(agent_info: dict[str, Any]) -> str:
    """
    Determine agent version from metadata.

    Uses protocol_version from agent card if available, defaults to "1.0.0".

    Args:
        agent_info: Internal agent data

    Returns:
        Version string
    """
    # Check if we have protocol version from agent card
    if "protocol_version" in agent_info:
        return agent_info["protocol_version"]

    # Check metadata
    if "_meta" in agent_info and "version" in agent_info["_meta"]:
        return agent_info["_meta"]["version"]

    # Default version for all agents
    return "1.0.0"


def _create_agent_name(agent_info: dict[str, Any]) -> str:
    """
    Create reverse-DNS style agent name.

    Transforms our path-based naming (/code-reviewer) to reverse-DNS format
    (io.mcpgateway/code-reviewer).

    Args:
        agent_info: Internal agent data

    Returns:
        Reverse-DNS formatted agent name
    """
    path = agent_info.get("path", "")

    # Remove leading and trailing slashes from path
    clean_path = path.strip("/")

    # Use our domain as prefix
    namespace = settings.anthropic_server_namespace
    return f"{namespace}/{clean_path}"


def transform_to_agent_detail(
    agent_info: dict[str, Any],
) -> ServerDetail:
    """
    Transform internal agent info to Anthropic ServerDetail format.

    A2A agents are exposed as ServerDetail objects in the Anthropic schema
    to maintain compatibility with the existing API structure.

    Maps from our internal agent schema to Anthropic schema.

    Args:
        agent_info: Internal agent data structure

    Returns:
        ServerDetail object
    """
    # Create reverse-DNS name
    name = _create_agent_name(agent_info)

    # Get version
    version = _determine_agent_version(agent_info)

    # Create transport config
    transport = _create_agent_transport_config(agent_info)

    # Create package entry
    # Use "mcpb" as registry type for our A2A agents
    package = Package(
        registryType="mcpb",
        identifier=name,
        version=version,
        transport=transport,
        runtimeHint="docker",
    )

    # Build metadata with agent-specific info
    namespace = settings.anthropic_server_namespace
    meta = {
        f"{namespace}/internal": {
            "path": agent_info.get("path"),
            "type": "a2a-agent",
            "is_enabled": agent_info.get("is_enabled", True),
            "visibility": agent_info.get("visibility", "public"),
            "trust_level": agent_info.get("trust_level", "community"),
            "skills": agent_info.get("skills", []),
            "tags": agent_info.get("tags", []),
            "protocol_version": agent_info.get("protocol_version", "1.0"),
        }
    }

    # Create ServerDetail with agent info
    return ServerDetail(
        name=name,
        description=agent_info.get("description", ""),
        version=version,
        title=agent_info.get("name"),
        repository=None,  # Agents typically don't have GitHub repos
        packages=[package],
        meta=meta,
    )


def transform_to_agent_response(
    agent_info: dict[str, Any],
    include_registry_meta: bool = True,
) -> ServerResponse:
    """
    Transform internal agent info to Anthropic ServerResponse format.

    Args:
        agent_info: Internal agent data
        include_registry_meta: Whether to include registry metadata

    Returns:
        ServerResponse object
    """
    agent_detail = transform_to_agent_detail(agent_info)

    registry_meta = None
    if include_registry_meta:
        namespace = settings.anthropic_server_namespace
        registry_meta = {
            f"{namespace}/registry": {
                "last_checked": agent_info.get("last_checked_iso"),
                "health_status": agent_info.get("health_status", "unknown"),
            }
        }

    return ServerResponse(server=agent_detail, meta=registry_meta)


def transform_to_agent_list(
    agents_data: list[dict[str, Any]],
    cursor: str | None = None,
    limit: int | None = None,
) -> ServerList:
    """
    Transform list of internal agents to Anthropic ServerList format.

    Implements cursor-based pagination following the same pattern as servers.

    Args:
        agents_data: List of internal agent data structures
        cursor: Current pagination cursor (agent name to start after)
        limit: Maximum number of results to return

    Returns:
        ServerList object with pagination metadata
    """
    # Default limit
    if limit is None or limit <= 0:
        limit = 100

    # Enforce maximum limit
    limit = min(limit, 1000)

    # Sort agents by name for consistent pagination
    sorted_agents = sorted(agents_data, key=lambda a: _create_agent_name(a))

    # Apply cursor-based pagination
    start_index = 0
    if cursor:
        # Find the index of the agent matching the cursor
        for idx, agent in enumerate(sorted_agents):
            if _create_agent_name(agent) == cursor:
                start_index = idx + 1
                break

    # Slice the results
    end_index = start_index + limit
    page_agents = sorted_agents[start_index:end_index]

    # Transform to ServerResponse objects
    agent_responses = [transform_to_agent_response(agent, include_registry_meta=True) for agent in page_agents]

    # Determine next cursor
    next_cursor = None
    if end_index < len(sorted_agents):
        # More results available
        next_cursor = _create_agent_name(sorted_agents[end_index - 1])

    # Build pagination metadata
    metadata = PaginationMetadata(nextCursor=next_cursor, count=len(agent_responses))

    return ServerList(servers=agent_responses, metadata=metadata)
