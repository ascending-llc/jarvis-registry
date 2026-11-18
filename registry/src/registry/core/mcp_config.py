"""
MCP Client Configuration

Centralized configuration constants for MCP client operations.
Eliminates hardcoded values scattered across the codebase.
"""

from enum import StrEnum
from types import MappingProxyType


class MCPTransport(StrEnum):
    STREAMABLE_HTTP = "streamable-http"
    SSE = "sse"
    STDIO = "stdio"

    @classmethod
    def _missing_(cls, value: object) -> "MCPTransport | None":
        # An absent type has always meant streamable HTTP, and Jarvis Chat stores "http" as its alias.
        if value in (None, "", "http"):
            return cls.STREAMABLE_HTTP
        return None


# Transports the Registry can call over the network (stdio servers are not reachable from the gateway).
NETWORK_TRANSPORTS = frozenset({MCPTransport.STREAMABLE_HTTP, MCPTransport.SSE})


class MCPClientConfig:
    """Stateless configuration constants for MCP client operations."""

    # Timeouts (in seconds)
    INIT_TIMEOUT = 30.0
    TOOLS_TIMEOUT = 45.0
    HEALTH_CHECK_TIMEOUT = 10.0
    OAUTH_METADATA_TIMEOUT = 10.0

    # Transport types
    TRANSPORT_HTTP = MCPTransport.STREAMABLE_HTTP
    TRANSPORT_SSE = MCPTransport.SSE
    TRANSPORT_STDIO = MCPTransport.STDIO

    # Gateway downstream pools (see MethodRoutedTransport). The stream pool is larger because an open GET
    # stream holds its connection for the whole call, and legacy SSE calls cannot proceed without one.
    DOWNSTREAM_REQUEST_MAX_CONNECTIONS = 100
    DOWNSTREAM_STREAM_MAX_CONNECTIONS = 200
    DOWNSTREAM_MAX_KEEPALIVE_CONNECTIONS = 20

    # Endpoint paths
    ENDPOINT_MCP = "/mcp"
    ENDPOINT_SSE = "/sse"
    ENDPOINT_MESSAGES = "/messages/"

    # Well-known OAuth paths
    WELLKNOWN_OAUTH_RESOURCE = "/.well-known/oauth-protected-resource"
    WELLKNOWN_OAUTH_SERVER = "/.well-known/oauth-authorization-server"

    # Special server handling
    ANTHROPIC_QUERY_PARAM = "instance_id=default"
    ANTHROPIC_TAG = "anthropic-registry"

    # Header defaults
    DEFAULT_HEADERS = MappingProxyType(
        {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    )

    # HTTP status codes considered healthy
    HEALTHY_STATUS_CODES = (200, 400, 405)
    AUTH_REQUIRED_STATUS_CODES = (401, 403)
