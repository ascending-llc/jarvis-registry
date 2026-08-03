import httpx
from fastapi import Depends, Request
from redis import Redis

from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStoreProtocol
from registry_pkgs.workflows.runner import WorkflowRunner

from .auth.oauth.reconnection import OAuthReconnectionManager
from .container import RegistryContainer
from .core.session_store import SessionStore
from .health.service import HealthMonitoringService
from .services.a2a_agent_service import A2AAgentService
from .services.access_control_service import ACLService
from .services.federation.a2a_client_registry import A2AClientRegistry
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
from .services.search.service import SearchService
from .services.server_service import ServerServiceV1
from .services.user_service import UserService
from .services.workflow_control_service import WorkflowControlService
from .services.workflow_service import WorkflowService


def get_container(request: Request) -> RegistryContainer:
    return request.app.state.container


def get_search_service(container: RegistryContainer = Depends(get_container)) -> SearchService:
    return container.search_service


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


def get_a2a_client_registry(container: RegistryContainer = Depends(get_container)) -> A2AClientRegistry:
    return container.a2a_client_registry


def get_redis_client(container: RegistryContainer = Depends(get_container)) -> Redis:
    """Get Redis client for caching."""
    return container.redis_client


def get_oauth_state_store(container: RegistryContainer = Depends(get_container)) -> DownstreamOAuthStoreProtocol:
    """Redis-backed OAuth state store for the direct-connect downstream flow."""
    return container.oauth_state_store


def get_consent_store(container: RegistryContainer = Depends(get_container)) -> ConsentStore:
    return container.consent_store


def get_pending_consent_store(container: RegistryContainer = Depends(get_container)) -> PendingConsentStore:
    return container.pending_consent_store


def get_workflow_control_service(
    container: RegistryContainer = Depends(get_container),
) -> WorkflowControlService:
    return container.workflow_control_service


def check_if_https(request: Request) -> bool:
    x_forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return x_forwarded_proto == "https" or request.url.scheme == "https"


def get_workflow_service(container: RegistryContainer = Depends(get_container)) -> WorkflowService:
    return container.workflow_service


def get_workflow_runner(container: RegistryContainer = Depends(get_container)) -> WorkflowRunner:
    """Get WorkflowRunner instance."""
    return container.workflow_runner
