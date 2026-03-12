"""
Service for transforming internal server data to Anthropic API schema.

This bridges our internal data model with the external Anthropic API format.
"""

import logging
from typing import Any

from ..core.config import settings
from ..schemas.anthropic_schema import (
    Package,
    PaginationMetadata,
    Repository,
    ServerDetail,
    ServerList,
    ServerResponse,
)

# Get logger - logging is configured centrally in main.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def _create_transport_config(server_info: dict[str, Any]) -> dict[str, Any]:
    """
    Create transport configuration from internal server info.

    Args:
        server_info: Internal server data structure

    Returns:
        Transport configuration dict
    """
    proxy_pass_url = server_info.get("proxy_pass_url", "")

    return {"type": "streamable-http", "url": proxy_pass_url}


def _extract_repository_from_description(description: str) -> Repository | None:
    """
    Extract repository info from description or tags if available.

    For now, returns None. Future: parse GitHub URLs from description.

    Args:
        description: Server description text

    Returns:
        Repository object or None
    """
    # TODO: Implement GitHub URL extraction from description
    # For now, return None - this is optional per spec
    return None


def _determine_version(server_info: dict[str, Any]) -> str:
    """
    Determine server version.

    Since we don't currently track versions, we use "1.0.0" as default.

    Args:
        server_info: Internal server data

    Returns:
        Version string
    """
    # Check if we have version metadata
    if "_meta" in server_info and "version" in server_info["_meta"]:
        return server_info["_meta"]["version"]

    # Default version for all servers
    return "1.0.0"


def _create_server_name(server_info: dict[str, Any]) -> str:
    """
    Create reverse-DNS style server name.

    Transforms our path-based naming (/example-server) to reverse-DNS format
    (io.mcpgateway/example-server).

    Args:
        server_info: Internal server data

    Returns:
        Reverse-DNS formatted server name
    """
    path = server_info.get("path", "")

    # Remove leading and trailing slashes from path
    clean_path = path.strip("/")

    # Use our domain as prefix
    namespace = settings.anthropic_server_namespace
    return f"{namespace}/{clean_path}"


def transform_to_server_detail(server_info: dict[str, Any]) -> ServerDetail:
    """
    Transform internal server info to Anthropic ServerDetail format.

    Maps from our internal schema to Anthropic schema.

    Args:
        server_info: Internal server data structure

    Returns:
        ServerDetail object
    """
    # Create reverse-DNS name
    name = _create_server_name(server_info)

    # Get version
    version = _determine_version(server_info)

    # Create transport config
    transport = _create_transport_config(server_info)

    # Create package entry
    # Note: We use "mcpb" as registry type for our custom servers
    package = Package(
        registryType="mcpb",
        identifier=name,
        version=version,
        transport=transport,
        runtimeHint="docker",
    )

    # Try to extract repository info
    repository = _extract_repository_from_description(server_info.get("description", ""))

    # Build metadata
    namespace = settings.anthropic_server_namespace
    meta = {
        f"{namespace}/internal": {
            "path": server_info.get("path"),
            "is_enabled": server_info.get("is_enabled", False),
            "health_status": server_info.get("health_status", "unknown"),
            "num_tools": server_info.get("num_tools", 0),
            "tags": server_info.get("tags", []),
            "license": server_info.get("license", "N/A"),
        }
    }

    # Create ServerDetail
    return ServerDetail(
        name=name,
        description=server_info.get("description", ""),
        version=version,
        title=server_info.get("server_name"),
        repository=repository,
        packages=[package],
        meta=meta,
    )


def transform_to_server_response(
    server_info: dict[str, Any],
    include_registry_meta: bool = True,
) -> ServerResponse:
    """
    Transform internal server info to Anthropic ServerResponse format.

    Args:
        server_info: Internal server data
        include_registry_meta: Whether to include registry metadata

    Returns:
        ServerResponse object
    """
    server_detail = transform_to_server_detail(server_info)

    registry_meta = None
    if include_registry_meta:
        namespace = settings.anthropic_server_namespace
        registry_meta = {
            f"{namespace}/registry": {
                "last_checked": server_info.get("last_checked_iso"),
                "health_status": server_info.get("health_status", "unknown"),
            }
        }

    return ServerResponse(server=server_detail, meta=registry_meta)


def transform_to_server_list(
    servers_data: list[dict[str, Any]],
    cursor: str | None = None,
    limit: int | None = None,
) -> ServerList:
    """
    Transform list of internal servers to Anthropic ServerList format.

    Implements cursor-based pagination.

    Args:
        servers_data: List of internal server data structures
        cursor: Current pagination cursor (server name to start after)
        limit: Maximum number of results to return

    Returns:
        ServerList object with pagination metadata
    """
    # Default limit
    if limit is None or limit <= 0:
        limit = 100

    # Enforce maximum limit
    limit = min(limit, 1000)

    # Sort servers by name for consistent pagination
    sorted_servers = sorted(servers_data, key=lambda s: _create_server_name(s))

    # Apply cursor-based pagination
    start_index = 0
    if cursor:
        # Find the index of the server matching the cursor
        for idx, server in enumerate(sorted_servers):
            if _create_server_name(server) == cursor:
                start_index = idx + 1
                break

    # Slice the results
    end_index = start_index + limit
    page_servers = sorted_servers[start_index:end_index]

    # Transform to ServerResponse objects
    server_responses = [transform_to_server_response(server, include_registry_meta=True) for server in page_servers]

    # Determine next cursor
    next_cursor = None
    if end_index < len(sorted_servers):
        # More results available
        next_cursor = _create_server_name(sorted_servers[end_index - 1])

    # Build pagination metadata
    metadata = PaginationMetadata(nextCursor=next_cursor, count=len(server_responses))

    return ServerList(servers=server_responses, metadata=metadata)
