from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING

import httpx
from agno.models.aws import AwsBedrock
from redis import Redis

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.vector.client import DatabaseClient
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.runner import WorkflowRunner

from .auth.oauth.flow_state_manager import FlowStateManager
from .auth.oauth.reconnection import OAuthReconnectionManager
from .core.mcp_client import MCPClientService
from .core.session_store import SessionStore
from .health.service import HealthMonitoringService
from .services.a2a_agent_service import A2AAgentService
from .services.access_control_service import ACLService
from .services.agent_scanner import AgentScannerService
from .services.agentcore_import_service import AgentCoreImportService
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
from .services.security_scanner import SecurityScannerService
from .services.server_service import ServerServiceV1
from .services.user_service import UserService
from .services.workflow_control_service import WorkflowControlService

if TYPE_CHECKING:
    from .core.config import Settings

logger = logging.getLogger(__name__)


class RegistryContainer:
    """App-scoped container for registry infrastructure and domain services.

    This container owns services for MCP server records managed by the registry.
    It is distinct from the mounted FastMCP gateway application configured in
    ``app_factory.py``.
    """

    def __init__(self, settings: Settings, *, db_client: DatabaseClient, redis_client: Redis):
        """Store shared infra clients and expose lazily-built app-scoped services."""
        self.settings = settings
        self.db_client = db_client
        self.redis_client = redis_client
        self.directive_queue = DirectiveQueue()

    @cached_property
    def mcp_server_repo(self) -> MCPServerRepository:
        return MCPServerRepository(self.db_client)

    @cached_property
    def a2a_agent_repo(self) -> A2AAgentRepository:
        return A2AAgentRepository(self.db_client)

    @cached_property
    def mcp_client_service(self) -> MCPClientService:
        return MCPClientService(redis_client=self.redis_client)

    @cached_property
    def session_store(self) -> SessionStore:
        return SessionStore()

    @cached_property
    def vector_service(self) -> VectorSearchService:
        """Build the single vector-search implementation used by routes and MCP tools.

        This property now owns the selection logic that used to live in
        ``create_vector_search_service(...)``.
        """
        if self.settings.use_external_discovery:
            from .services.search.external_service import ExternalVectorSearchService

            logger.info("Initializing Weaviate-based vector search service for MCP tools")
            return ExternalVectorSearchService(mcp_server_repo=self.mcp_server_repo)

        from .services.search.embedded_service import EmbeddedFaissService

        logger.info("Initializing embedded FAISS vector search service")
        return EmbeddedFaissService(self.settings)

    @cached_property
    def health_service(self) -> HealthMonitoringService:
        return HealthMonitoringService(server_service=self.server_service, mcp_client_service=self.mcp_client_service)

    @cached_property
    def federation_service(self) -> FederationService:
        return FederationService()

    @cached_property
    def user_service(self) -> UserService:
        return UserService()

    @cached_property
    def group_service(self) -> GroupService:
        return GroupService()

    @cached_property
    def acl_service(self) -> ACLService:
        return ACLService(user_service=self.user_service, group_service=self.group_service)

    @cached_property
    def token_service(self) -> TokenService:
        return TokenService(user_service=self.user_service)

    @cached_property
    def flow_state_manager(self) -> FlowStateManager:
        return FlowStateManager(redis_client=self.redis_client)

    @cached_property
    def oauth_service(self) -> MCPOAuthService:
        return MCPOAuthService(flow_manager=self.flow_state_manager, token_service_instance=self.token_service)

    @cached_property
    def connection_service(self) -> MCPConnectionService:
        return MCPConnectionService(server_service=self.server_service)

    @cached_property
    def mcp_service(self) -> MCPService:
        return MCPService(connection_service=self.connection_service, oauth_service=self.oauth_service)

    @cached_property
    def reconnection_manager(self) -> OAuthReconnectionManager:
        return OAuthReconnectionManager(
            mcp_service=self.mcp_service,
            oauth_service=self.oauth_service,
            flow_state_manager=self.flow_state_manager,
            server_service=self.server_service,
        )

    @cached_property
    def status_resolver(self) -> ConnectionStatusResolver:
        return ConnectionStatusResolver(
            flow_state_manager=self.flow_state_manager,
            reconnection_manager=self.reconnection_manager,
        )

    @cached_property
    def server_service(self) -> ServerServiceV1:
        return ServerServiceV1(
            user_service=self.user_service,
            token_service=self.token_service,
            oauth_service=self.oauth_service,
            mcp_server_repo=self.mcp_server_repo,
        )

    @cached_property
    def a2a_agent_service(self) -> A2AAgentService:
        return A2AAgentService()

    @cached_property
    def agentcore_import_service(self) -> AgentCoreImportService:
        return AgentCoreImportService(
            acl_service_instance=self.acl_service,
            server_service=self.server_service,
            user_service_instance=self.user_service,
            mcp_server_repo=self.mcp_server_repo,
            a2a_agent_repo=self.a2a_agent_repo,
        )

    @cached_property
    def security_scanner_service(self) -> SecurityScannerService:
        return SecurityScannerService(server_service=self.server_service)

    @cached_property
    def agent_scanner_service(self) -> AgentScannerService:
        return AgentScannerService()

    @cached_property
    def workflow_control_service(self) -> WorkflowControlService:
        return WorkflowControlService(
            directive_queue=self.directive_queue,
            runner_factory=lambda: self.workflow_runner,
        )

    @cached_property
    def workflow_runner(self) -> WorkflowRunner:
        llm = AwsBedrock(
            id=self.settings.llm_model,
            aws_region=self.settings.aws_region,
            aws_session_token=self.settings.aws_session_token,
            aws_access_key_id=self.settings.aws_access_key_id,
            aws_secret_access_key=self.settings.aws_secret_access_key,
        )
        return WorkflowRunner(
            llm=llm,
            registry_url=self.settings.registry_url,
            db_client=MongoDB.get_client(),
            db_name=MongoDB.database_name,
            jwt_config=self.settings.jwt_signing_config,
            directive_queue=self.directive_queue,
        )

    @cached_property
    def mcp_proxy_client(self) -> httpx.AsyncClient:
        """Shared httpx client for MCP proxy connection pooling."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=60.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    @cached_property
    def federation_crud_service(self) -> FederationCrudService:
        return FederationCrudService()

    @cached_property
    def federation_job_service(self) -> FederationJobService:
        return FederationJobService()

    @cached_property
    def federation_sync_service(self) -> FederationSyncService:
        return FederationSyncService(
            federation_crud_service=self.federation_crud_service,
            federation_job_service=self.federation_job_service,
            mcp_server_repo=self.mcp_server_repo,
            a2a_agent_repo=self.a2a_agent_repo,
            acl_service=self.acl_service,
            user_service=self.user_service,
        )

    async def startup(self) -> None:
        """Warm services that need async initialization before the app can serve traffic."""
        logger.info("Initializing services via registry container...")

        logger.info("Initializing vector search service...")
        await self.vector_service.initialize()
        if self.vector_service.is_initialized:
            logger.info("Vector search service initialized successfully")
        else:
            logger.warning("Vector search service not initialized - index update skipped")
            logger.info("App will continue without vector search features")

        logger.info("Initializing health monitoring service...")
        await self.health_service.initialize()

        logger.info("Initializing MCP connection service...")
        await self.connection_service.initialize_app_connections()

        logger.info("Initializing MCP service...")
        await self.mcp_service.initialize()

        logger.info("Initializing federation service...")
        self._initialize_federation()

    async def shutdown(self) -> None:
        """Shutdown services that hold background tasks or external resources."""
        await self.health_service.shutdown()
        await self.mcp_proxy_client.aclose()

    def _initialize_federation(self) -> None:
        """Run optional federation sync on startup without failing the whole application."""
        federation_service = self.federation_service
        if federation_service.config.is_any_federation_enabled():
            logger.info("Federation enabled for: %s", ", ".join(federation_service.config.get_enabled_federations()))

            sync_on_startup = (
                federation_service.config.anthropic.enabled and federation_service.config.anthropic.sync_on_startup
            ) or (federation_service.config.asor.enabled and federation_service.config.asor.sync_on_startup)

            if sync_on_startup:
                logger.info("Syncing servers from federated registries on startup...")
                try:
                    sync_results = federation_service.sync_all()
                    for source, servers in sync_results.items():
                        logger.info("Synced %s servers from %s", len(servers), source)
                except Exception as exc:
                    logger.error("Federation sync failed (continuing with startup): %s", exc, exc_info=True)
        else:
            logger.info("Federation is disabled")
