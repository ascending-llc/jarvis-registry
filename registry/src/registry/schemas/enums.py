from enum import StrEnum


class OAuthFlowStatus(StrEnum):
    """OAuth flow status"""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class ConnectionState(StrEnum):
    """Connection state enumeration"""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PENDING_OAUTH = "pending_oauth"
    ERROR = "error"
    UNKNOWN = "unknown"


class TokenType(StrEnum):
    """Token type enumeration"""

    MCP_OAUTH = "mcp_oauth"  # Access token
    MCP_OAUTH_REFRESH = "mcp_oauth_refresh"  # Refresh token
    MCP_OAUTH_CLIENT = "mcp_oauth_client"  # Client credentials (client_id, client_secret)
