"""
MCP Client Configuration

Centralized configuration constants for MCP client operations.
Eliminates hardcoded values scattered across the codebase.
"""

from dataclasses import dataclass, field


@dataclass
class MCPClientConfig:
    """Centralized MCP client configuration"""

    # Timeouts (in seconds)
    INIT_TIMEOUT: float = 30.0
    TOOLS_TIMEOUT: float = 45.0
    HEALTH_CHECK_TIMEOUT: float = 10.0
    OAUTH_METADATA_TIMEOUT: float = 10.0

    # Transport types
    TRANSPORT_HTTP: str = "streamable-http"
    TRANSPORT_SSE: str = "sse"
    TRANSPORT_STDIO: str = "stdio"

    # Endpoint paths
    ENDPOINT_MCP: str = "/mcp"
    ENDPOINT_SSE: str = "/sse"
    ENDPOINT_MESSAGES: str = "/messages/"

    # Well-known OAuth paths
    WELLKNOWN_OAUTH_RESOURCE: str = "/.well-known/oauth-protected-resource"
    WELLKNOWN_OAUTH_SERVER: str = "/.well-known/oauth-authorization-server"

    # Special server handling
    ANTHROPIC_QUERY_PARAM: str = "instance_id=default"
    ANTHROPIC_TAG: str = "anthropic-registry"

    # Header defaults
    DEFAULT_HEADERS: dict[str, str] = field(
        default_factory=lambda: {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    )

    # HTTP status codes considered healthy
    HEALTHY_STATUS_CODES: list = field(default_factory=lambda: [200, 400, 405])
    AUTH_REQUIRED_STATUS_CODES: list = field(default_factory=lambda: [401, 403])


# Global configuration instance
mcp_config = MCPClientConfig()
