from dataclasses import dataclass

from httpx import AsyncClient
from redis import Redis

from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ...core.mcp_client import MCPClientService
from ...core.session_store import SessionStore
from ...services.a2a_agent_service import A2AAgentService
from ...services.access_control_service import ACLService
from ...services.oauth.oauth_service import MCPOAuthService
from ...services.server_service import ServerServiceV1


@dataclass
class McpAppContext:
    """MCP application context with typed dependencies."""

    proxy_client: AsyncClient
    server_service: ServerServiceV1
    mcp_server_repo: MCPServerRepository
    a2a_agent_repo: A2AAgentRepository
    mcp_client_service: MCPClientService
    oauth_service: MCPOAuthService
    session_store: SessionStore
    redis_client: Redis
    a2a_agent_service: A2AAgentService
    acl_service: ACLService
