"""
Metrics client and domain functions for the Registry service.

This module exports a pre-configured metrics client and domain-specific
helper functions for recording registry metrics.

Usage:
    from registry.utils.otel_metrics import (
        metrics,
        record_registry_operation,
        record_tool_execution,
        record_tool_discovery,
        record_server_request,
        record_resource_access,
        record_prompt_execution,
    )

    # Using domain functions (recommended)
    record_registry_operation("create", "server", success=True, duration_seconds=0.1)
    record_tool_discovery("my-server", success=True, duration_seconds=0.5, tools_count=10)

    # Using generic client directly
    metrics.record_counter("custom_metric", 1, {"label": "value"})
"""

import logging

from registry_pkgs.telemetry.metrics_client import create_metrics_client, load_metrics_config

from ..core.config import settings

logger = logging.getLogger(__name__)


# Load configuration and create service-specific metrics client
_config = load_metrics_config("registry", settings.telemetry_config)
metrics = create_metrics_client("registry", config=_config)


# =============================================================================
# Domain-Specific Recording Functions
# =============================================================================


def record_registry_operation(
    operation: str,
    resource_type: str,
    success: bool,
    duration_seconds: float | None = None,
) -> None:
    """
    Record a registry operation with latency tracking.

    Requires these metrics in config:
    - counter: registry_operations_total
    - histogram: registry_operation_duration_seconds

    Args:
        operation: Type of operation (e.g., "read", "create", "update", "delete", "list", "search")
        resource_type: Type of resource (e.g., "server", "tool", "search")
        success: Whether the operation was successful
        duration_seconds: Operation duration in seconds for p50/p95/p99 calculation
    """
    attributes = {
        "operation": operation,
        "resource_type": resource_type,
        "status": "success" if success else "failure",
    }

    metrics.record_counter("registry_operations_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("registry_operation_duration_seconds", duration_seconds, attributes)


def record_tool_execution(
    tool_name: str,
    server_name: str,
    success: bool,
    duration_seconds: float | None = None,
    method: str = "UNKNOWN",
) -> None:
    """
    Record tool execution with success rate and latency tracking.

    Requires these metrics in config:
    - counter: mcp_tool_execution_total
    - histogram: mcp_tool_execution_duration_seconds

    Args:
        tool_name: Name of the tool executed
        server_name: Name of the MCP server
        success: Whether the execution was successful
        duration_seconds: Execution duration in seconds for p50/p95/p99 calculation
        method: HTTP method (e.g., "POST", "GET") - defaults to "UNKNOWN"
    """
    attributes = {
        "tool_name": tool_name,
        "server_name": server_name,
        "status": "success" if success else "failure",
        "method": method,
    }

    metrics.record_counter("mcp_tool_execution_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_tool_execution_duration_seconds", duration_seconds, attributes)


def record_resource_access(
    resource_uri: str,
    server_name: str,
    success: bool,
    duration_seconds: float | None = None,
) -> None:
    """
    Record resource access operation.

    Requires these metrics in config:
    - counter: mcp_resource_access_total
    - histogram: mcp_resource_access_duration_seconds

    Args:
        resource_uri: URI of the accessed resource
        server_name: Name of the MCP server
        success: Whether the access was successful
        duration_seconds: Access duration in seconds
    """
    attributes = {
        "resource_uri": resource_uri,
        "server_name": server_name,
        "status": "success" if success else "failure",
    }

    metrics.record_counter("mcp_resource_access_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_resource_access_duration_seconds", duration_seconds, attributes)


def record_prompt_execution(
    prompt_name: str,
    server_name: str,
    success: bool,
    duration_seconds: float | None = None,
) -> None:
    """
    Record prompt execution operation.

    Requires these metrics in config:
    - counter: mcp_prompt_execution_total
    - histogram: mcp_prompt_execution_duration_seconds

    Args:
        prompt_name: Name of the executed prompt
        server_name: Name of the MCP server
        success: Whether the execution was successful
        duration_seconds: Execution duration in seconds
    """
    attributes = {
        "prompt_name": prompt_name,
        "server_name": server_name,
        "status": "success" if success else "failure",
    }

    metrics.record_counter("mcp_prompt_execution_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_prompt_execution_duration_seconds", duration_seconds, attributes)


def record_server_request(server_name: str) -> None:
    """
    Record a request to a specific MCP server.

    Requires this metric in config:
    - counter: mcp_server_requests_total

    Args:
        server_name: Name of the MCP server
    """
    attributes = {"server_name": server_name}
    metrics.record_counter("mcp_server_requests_total", 1, attributes)


def record_auth_request(
    mechanism: str,
    success: bool,
    duration_seconds: float | None = None,
) -> None:
    """
    Record an authentication attempt with optional duration for latency percentiles.

    Note: This is included for registry's auth middleware. For auth_server,
    use auth_server.utils.otel_metrics.record_auth_request instead.

    Requires these metrics in config:
    - counter: auth_requests_total
    - histogram: auth_request_duration_seconds

    Args:
        mechanism: Authentication mechanism (e.g., "jwt", "api_key", "basic")
        success: Whether the auth attempt was successful
        duration_seconds: Request duration in seconds for p50/p95/p99 calculation
    """
    attributes = {
        "mechanism": mechanism,
        "status": "success" if success else "failure",
    }

    metrics.record_counter("auth_requests_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("auth_request_duration_seconds", duration_seconds, attributes)


def record_tool_discovery(
    server_name: str,
    success: bool,
    duration_seconds: float | None = None,
    transport_type: str = "unknown",
    tools_count: int = 0,
) -> None:
    """
    Record tool discovery operation with latency tracking.

    This metric captures tool discovery operations including:
    - Search/discovery via registry API (server_name="registry")
    - Backend fetching of tools from MCP servers (server_name=<mcp-server-name>)

    Requires these metrics in config:
    - counter: mcp_tool_discovery_total
    - histogram: mcp_tool_discovery_duration_seconds

    Args:
        server_name: Source of discovery - "registry" for API search, or MCP server name for backend
        success: Whether the discovery was successful
        duration_seconds: Discovery duration in seconds for p50/p95/p99 calculation
        transport_type: Transport/search type (e.g., "streamable-http", "sse", "hybrid", "semantic")
        tools_count: Number of tools/results discovered (0 if failed)
    """
    attributes = {
        "source": server_name,  # Used by Grafana dashboard for grouping
        "server_name": server_name,
        "status": "success" if success else "failure",
        "transport_type": transport_type,
        "tools_count": str(tools_count),
    }

    metrics.record_counter("mcp_tool_discovery_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_tool_discovery_duration_seconds", duration_seconds, attributes)
