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
        record_prompt_get,
    )

    # Using domain functions (recommended)
    record_registry_operation("create", "server", success=True, duration_seconds=0.1)
    record_tool_discovery("my-server", success=True, duration_seconds=0.5, tools_count=10)

    # Using generic client directly
    metrics.record_counter("custom_metric", 1, {"label": "value"})
"""

import logging
from typing import Literal

from registry_pkgs.telemetry.metrics_client import create_metrics_client, load_metrics_config

from ..constants import ExecutionOutcome, ProxyOutcome

# Which side of Registry handled the request counted by mcp_server_requests_total.
ServerRequestMode = Literal["gateway", "proxy"]
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
    error_type: str = "none",
    *,
    input_required: bool = False,
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
        error_type: Failure-mode label (e.g., exception class name or a semantic tag like
            "server_not_found"); "none" on success. Must be a bounded, low-cardinality value.
        input_required: The call is awaiting client input, rather than succeeding or failing.
    """
    outcome = (
        ExecutionOutcome.INPUT_REQUIRED
        if input_required
        else (ExecutionOutcome.SUCCESS if success else ExecutionOutcome.FAILURE)
    )
    attributes = {
        "tool_name": tool_name,
        "server_name": server_name,
        "status": outcome.value,
        "method": method,
        "error_type": error_type,
    }

    metrics.record_counter("mcp_tool_execution_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_tool_execution_duration_seconds", duration_seconds, attributes)


def record_resource_access(
    server_name: str,
    success: bool,
    duration_seconds: float | None = None,
    error_type: str = "none",
) -> None:
    """
    Record resource access operation.

    Requires these metrics in config:
    - counter: mcp_resource_access_total
    - histogram: mcp_resource_access_duration_seconds

    Note: resource_uri is intentionally NOT recorded as a label - it is unbounded
    (e.g., "tavily://search-results/<query>") and would cause Prometheus cardinality
    explosion. Use server_name for per-server breakdown instead.

    Args:
        server_name: Name of the MCP server
        success: Whether the access was successful
        duration_seconds: Access duration in seconds
        error_type: Failure-mode label (exception class name or semantic tag);
            "none" on success. Must be a bounded, low-cardinality value.
    """
    attributes = {
        "server_name": server_name,
        "status": "success" if success else "failure",
        "error_type": error_type,
    }

    metrics.record_counter("mcp_resource_access_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_resource_access_duration_seconds", duration_seconds, attributes)


def record_prompt_get(
    prompt_name: str,
    server_name: str,
    success: bool,
    duration_seconds: float | None = None,
    error_type: str = "none",
) -> None:
    """
    Record prompt retrieval operation.

    Requires these metrics in config:
    - counter: mcp_prompt_get_total
    - histogram: mcp_prompt_get_duration_seconds

    Args:
        prompt_name: Name of the retrieved prompt
        server_name: Name of the MCP server
        success: Whether the retrieval was successful
        duration_seconds: Retrieval duration in seconds
        error_type: Failure-mode label (exception class name or semantic tag);
            "none" on success. Must be a bounded, low-cardinality value.
    """
    attributes = {
        "prompt_name": prompt_name,
        "server_name": server_name,
        "status": "success" if success else "failure",
        "error_type": error_type,
    }

    metrics.record_counter("mcp_prompt_get_total", 1, attributes)

    if duration_seconds is not None:
        metrics.record_histogram("mcp_prompt_get_duration_seconds", duration_seconds, attributes)


def record_server_request(server_name: str, mode: ServerRequestMode) -> None:
    """
    Record a request to a specific MCP server.

    Requires this metric in config:
    - counter: mcp_server_requests_total

    Args:
        server_name: Name of the MCP server
        mode: "gateway" when Registry ran the operation itself (a tool call, a resource read or
            a prompt get); "proxy" when the direct proxy forwarded it to the server
    """
    attributes = {"server_name": server_name, "mode": mode}
    metrics.record_counter("mcp_server_requests_total", 1, attributes)


def record_proxy_request(
    server_name: str,
    http_method: str,
    mcp_method: str,
    outcome: ProxyOutcome,
    duration_seconds: float,
) -> None:
    """
    Record a request the direct MCP proxy (``/proxy/server/...``) sent to a server.

    ``outcome`` says how it ended: the server's response was relayed (``success`` if its status is
    below 400, else ``failure``), the proxy itself failed (timeout, internal error, rejected
    downstream authorization), Registry is waiting on the user (``input_required``), or the client
    went away (``cancelled``). Requests the proxy refuses before sending anything are not recorded.
    The proxy relays bytes verbatim and never parses tool results, so this is the HTTP outcome of
    the exchange, unlike the gateway's ``record_tool_execution``.

    Requires these metrics in config:
    - counter: mcp_proxy_requests_total
    - histogram: mcp_proxy_request_duration_seconds
    """
    attributes = {
        "server_name": server_name,
        "http_method": http_method,
        "mcp_method": mcp_method,
        "status": outcome.value,
    }

    metrics.record_counter("mcp_proxy_requests_total", 1, attributes)
    metrics.record_histogram("mcp_proxy_request_duration_seconds", duration_seconds, attributes)


def record_proxy_tool_call(tool_name: str, server_name: str, outcome: ProxyOutcome) -> None:
    """
    Record a ``tools/call`` the direct MCP proxy sent to a server.

    ``tool_name`` must already be bounded by the caller: the raw name comes from the client's
    JSON-RPC params, which the direct proxy does not validate, so only a name the server has
    registered is used as-is and anything else is recorded as "other".

    Requires this metric in config:
    - counter: mcp_proxy_tool_calls_total
    """
    attributes = {
        "tool_name": tool_name,
        "server_name": server_name,
        "status": outcome.value,
    }

    metrics.record_counter("mcp_proxy_tool_calls_total", 1, attributes)


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
