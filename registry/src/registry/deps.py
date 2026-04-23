from __future__ import annotations

import httpx
from fastapi import Depends, Request
from redis import Redis

from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from .auth.oauth.reconnection import OAuthReconnectionManager
from .container import RegistryContainer
from .core.session_store import SessionStore
from .health.service import HealthMonitoringService
from .services.a2a_agent_service import A2AAgentService
from .services.access_control_service import ACLService
from .services.federation_crud_service import FederationCrudService
from .services.federation_job_service import FederationJobService
from .services.federation_service import FederationService
from .services.federation_sync_service import FederationSyncService
from .services.group_service import GroupService
from .services.oauth.connection_service import MCPConnectionService
from .services.oauth.mcp_service import MCPService
from .services.oauth.oauth_service import MCPOAuthService
from .services.oauth.status_resolver import ConnectionStatusResolver
from .services.oauth.token_service import TokenService
from .services.search.base import VectorSearchService
from .services.server_service import ServerServiceV1
from .services.user_service import UserService


def get_container(request: Request) -> RegistryContainer:
    return request.app.state.container


def get_vector_service(container: RegistryContainer = Depends(get_container)) -> VectorSearchService:
    return container.vector_service


def get_mcp_server_repo(container: RegistryContainer = Depends(get_container)) -> MCPServerRepository:
    return container.mcp_server_repo


def get_session_store(container: RegistryContainer = Depends(get_container)) -> SessionStore:
    return container.session_store


def get_health_service(container: RegistryContainer = Depends(get_container)) -> HealthMonitoringService:
    return container.health_service


def get_federation_service(container: RegistryContainer = Depends(get_container)) -> FederationService:
    return container.federation_service


def get_user_service(container: RegistryContainer = Depends(get_container)) -> UserService:
    return container.user_service


def get_group_service(container: RegistryContainer = Depends(get_container)) -> GroupService:
    return container.group_service


def get_acl_service(container: RegistryContainer = Depends(get_container)) -> ACLService:
    return container.acl_service


def get_token_service(container: RegistryContainer = Depends(get_container)) -> TokenService:
    return container.token_service


def get_oauth_service(container: RegistryContainer = Depends(get_container)) -> MCPOAuthService:
    return container.oauth_service


def get_connection_service(container: RegistryContainer = Depends(get_container)) -> MCPConnectionService:
    return container.connection_service


def get_mcp_service(container: RegistryContainer = Depends(get_container)) -> MCPService:
    return container.mcp_service


def get_reconnection_manager(container: RegistryContainer = Depends(get_container)) -> OAuthReconnectionManager:
    return container.reconnection_manager


def get_status_resolver(container: RegistryContainer = Depends(get_container)) -> ConnectionStatusResolver:
    return container.status_resolver


def get_server_service(container: RegistryContainer = Depends(get_container)) -> ServerServiceV1:
    return container.server_service


def get_a2a_agent_service(container: RegistryContainer = Depends(get_container)) -> A2AAgentService:
    return container.a2a_agent_service


def get_mcp_proxy_client(container: RegistryContainer = Depends(get_container)) -> httpx.AsyncClient:
    return container.mcp_proxy_client


def get_federation_crud_service(container: RegistryContainer = Depends(get_container)) -> FederationCrudService:
    return container.federation_crud_service


def get_federation_job_service(container: RegistryContainer = Depends(get_container)) -> FederationJobService:
    return container.federation_job_service


def get_federation_sync_service(container: RegistryContainer = Depends(get_container)) -> FederationSyncService:
    return container.federation_sync_service


def get_redis_client(container: RegistryContainer = Depends(get_container)) -> Redis:
    """Get Redis client for caching."""
    return container.redis_client
