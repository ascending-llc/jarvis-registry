from dataclasses import dataclass

from httpx import AsyncClient

from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ...core.mcp_client import MCPClientService
from ...core.session_store import SessionStore
from ...services.oauth.oauth_service import MCPOAuthService
from ...services.server_service import ServerServiceV1


@dataclass
class McpAppContext:
    """MCP application context with typed dependencies."""

    proxy_client: AsyncClient
    server_service: ServerServiceV1
    mcp_server_repo: MCPServerRepository
    mcp_client_service: MCPClientService
    oauth_service: MCPOAuthService
    session_store: SessionStore
    redis_client: object | None = None
