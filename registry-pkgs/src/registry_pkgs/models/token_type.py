"""Token type enumeration.

Lives in ``registry-pkgs`` (alongside the ``Token`` Beanie document) so both the registry and
the auth-server can reference it within the allowed dependency direction
(``registry`` → ``registry-pkgs`` ← ``auth-server``). ``registry.schemas.enums`` re-exports it
for backward compatibility.
"""

from enum import StrEnum


class TokenType(StrEnum):
    """Token type enumeration"""

    MCP_OAUTH_ACCESS = "mcp_oauth"  # Access token (canonical name, value unchanged for DB compatibility)
    MCP_OAUTH = MCP_OAUTH_ACCESS
    MCP_OAUTH_REFRESH = "mcp_oauth_refresh"  # Refresh token
    MCP_OAUTH_CLIENT = "mcp_oauth_client"  # Client credentials (client_id, client_secret)
