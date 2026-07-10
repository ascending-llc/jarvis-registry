from dataclasses import dataclass

from httpx import AsyncClient
from redis import Redis

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ...core.mcp_client import MCPClientService
from ...core.session_store import SessionStore
from ...services.access_control_service import ACLService
from ...services.federation.azure_foundry_proxy_auth import A2aHeadersProvider
from ...services.oauth.oauth_service import MCPOAuthService
from ...services.search.service import SearchService
from ...services.server_service import ServerServiceV1


@dataclass
class McpAppContext:
    """MCP application context with typed dependencies."""

    proxy_client: AsyncClient
    a2a_httpx_client: AsyncClient
    server_service: ServerServiceV1
    mcp_server_repo: MCPServerRepository
    a2a_agent_repo: A2AAgentRepository
    search_service: SearchService
    mcp_client_service: MCPClientService
    oauth_service: MCPOAuthService
    session_store: SessionStore
    redis_client: Redis
    jwt_signing_config: JwtSigningConfig
    acl_service: ACLService
    consent_store: ConsentStore
    pending_consent_store: PendingConsentStore
    a2a_headers_provider: A2aHeadersProvider
