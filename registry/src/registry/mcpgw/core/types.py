from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from httpx import AsyncClient

if TYPE_CHECKING:
    from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

    from ...core.session_store import SessionStore
    from ...services.oauth.oauth_service import MCPOAuthService
    from ...services.server_service import ServerServiceV1


@dataclass
class McpAppContext:
    """MCP application context with typed dependencies."""

    proxy_client: AsyncClient
    server_service: "ServerServiceV1 | Any"
    mcp_server_repo: "MCPServerRepository | Any"
    oauth_service: "MCPOAuthService | Any"
    session_store: "SessionStore | Any"
