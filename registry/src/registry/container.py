from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING

import httpx
from agno.models.aws import AwsBedrock
from beanie import PydanticObjectId
from redis import Redis

from registry_pkgs.core.consent_store import ConsentStore, PendingConsentStore
from registry_pkgs.core.oauth_state_store import DownstreamOAuthStateStore, OAuthClientStore, OAuthStateStore
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.vector.client import DatabaseClient
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.runner import WorkflowRunner

from .auth.oauth.flow_state_manager import FlowStateManager
from .auth.oauth.reconnection import OAuthReconnectionManager
from .core.a2a_proxy import A2AProxyClientRegistry
from .core.mcp_client import MCPClientService
from .core.session_store import SessionStore
from .health.service import HealthMonitoringService
from .services.a2a_agent_service import A2AAgentService
from .services.access_control_service import ACLService, load_role_cache
from .services.agent_scanner import AgentScannerService
from .services.federation.a2a_client_registry import A2AClientRegistry
from .services.federation.azure_foundry_proxy_auth import (
    A2aHeadersProvider,
    AzureFoundryClientCache,
    make_a2a_headers_provider,
)
from .services.federation_crud_service import FederationCrudService
from .services.federation_job_service import FederationJobService
from .services.federation_service import FederationService
from .services.federation_sync_service import FederationSyncService
from .services.group_directory_client import (
    CognitoGroupDirectoryClient,
    EntraIdGroupDirectoryClient,
    IdPGroupDirectoryClient,
    KeycloakGroupDirectoryClient,
)
from .services.group_service import GroupService
from .services.oauth.connection_service import MCPConnectionService
from .services.oauth.mcp_service import MCPService
from .services.oauth.oauth_service import MCPOAuthService
from .services.oauth.status_resolver import ConnectionStatusResolver
from .services.oauth.token_service import TokenService
from .services.search.base import VectorSearchService
from .services.search.service import SearchService
from .services.security_scanner import SecurityScannerService
from .services.server_service import ServerServiceV1
from .services.user_service import UserService
from .services.workflow_control_service import WorkflowControlService
from .services.workflow_mcp_headers_provider import McpHeadersProvider, make_mcp_headers_provider
from .services.workflow_service import WorkflowService
from .services.workflow_shutdown import cancel_in_flight_runs

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
        self.role_cache: dict[tuple[str, int], PydanticObjectId] = {}

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
            return ExternalVectorSearchService(
                mcp_server_repo=self.mcp_server_repo,
                enable_rerank=self.settings.rerank_enabled,
            )

        from .services.search.embedded_service import EmbeddedFaissService

        logger.info("Initializing embedded FAISS vector search service")
        return EmbeddedFaissService(self.settings)

    @cached_property
    def search_service(self) -> SearchService:
        """Search orchestration shared by the HTTP /search route and mcpgw tools."""
        return SearchService(
            vector_service=self.vector_service,
            mcp_server_repo=self.mcp_server_repo,
            a2a_agent_repo=self.a2a_agent_repo,
            acl_service=self.acl_service,
        )

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
    def group_directory_client(self) -> IdPGroupDirectoryClient:
        provider = self.settings.auth_provider
        if provider == "entra":
            return EntraIdGroupDirectoryClient(
                tenant_id=self.settings.entra_tenant_id or "",
                client_id=self.settings.entra_client_id or "",
                client_secret=self.settings.entra_client_secret or "",
                graph_url=self.settings.entra_graph_url,
            )
        if provider == "cognito":
            return CognitoGroupDirectoryClient()
        return KeycloakGroupDirectoryClient()

    @cached_property
    def group_service(self) -> GroupService:
        return GroupService(group_directory_client=self.group_directory_client)

    @cached_property
    def acl_service(self) -> ACLService:
        return ACLService(
            user_service=self.user_service,
            group_service=self.group_service,
            role_cache=self.role_cache,
        )

    @cached_property
    def token_service(self) -> TokenService:
        return TokenService(user_service=self.user_service)

    @cached_property
    def flow_state_manager(self) -> FlowStateManager:
        return FlowStateManager(redis_client=self.redis_client)

    @cached_property
    def oauth_client_store(self) -> OAuthClientStore:
        """Read DCR client metadata from auth-server's Redis namespace."""
        return OAuthClientStore(
            redis_client=self.redis_client,
            key_prefix=self.settings.auth_server_redis_key_prefix,
            client_secret_hash_key=self.settings.secret_key,
        )

    @cached_property
    def consent_store(self) -> ConsentStore:
        """Share auth-server's consent namespace for cross-service OAuth flows."""
        return ConsentStore(
            redis_client=self.redis_client,
            key_prefix=self.settings.auth_server_redis_key_prefix,
        )

    @cached_property
    def pending_consent_store(self) -> PendingConsentStore:
        return PendingConsentStore(
            redis_client=self.redis_client,
            key_prefix=self.settings.auth_server_redis_key_prefix,
        )

    @cached_property
    def downstream_refresh_token_store(self) -> OAuthStateStore:
        """Persist registry-owned direct-connect refresh tokens under registry's Redis namespace."""
        return OAuthStateStore(
            redis_client=self.redis_client,
            key_prefix=self.settings.redis_key_prefix,
            client_secret_hash_key=self.settings.secret_key,
        )

    @cached_property
    def oauth_state_store(self) -> DownstreamOAuthStateStore:
        """Redis-backed OAuth state for the direct-connect downstream flow.

        Direct-connect refresh tokens and device-flow state live under registry's own
        ``redis_key_prefix``; DCR client records are read through an explicit client facade over
        auth-server's namespace.
        """
        return DownstreamOAuthStateStore(
            client_store=self.oauth_client_store,
            refresh_token_store=self.downstream_refresh_token_store,
            device_store=self.downstream_refresh_token_store,
        )

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
        return A2AAgentService(
            a2a_agent_repo=self.a2a_agent_repo,
            jwt_config=self.settings.jwt_signing_config,
        )

    @cached_property
    def security_scanner_service(self) -> SecurityScannerService:
        return SecurityScannerService(server_service=self.server_service)

    @cached_property
    def agent_scanner_service(self) -> AgentScannerService:
        return AgentScannerService()

    @cached_property
    def workflow_service(self) -> WorkflowService:
        return WorkflowService()

    @cached_property
    def a2a_client_registry(self) -> A2AClientRegistry:
        return A2AClientRegistry(
            agentcore_registry=self.a2a_proxy_client_registry,
            azure_client_cache=AzureFoundryClientCache(),
        )

    @cached_property
    def a2a_headers_provider(self) -> A2aHeadersProvider:
        """App-scoped A2A headers provider; resolves Azure Entra credentials fresh per call (no caching)."""
        return make_a2a_headers_provider(jwt_config=self.settings.jwt_signing_config)

    @cached_property
    def mcp_headers_provider(self) -> McpHeadersProvider:
        """App-scoped MCP headers provider for manually-registered workflow MCP servers."""
        return make_mcp_headers_provider(
            oauth_service=self.oauth_service,
            redis_client=self.redis_client,
        )

    @cached_property
    def workflow_runner(self) -> WorkflowRunner:
        """Build the app-scoped WorkflowRunner used by API-triggered runs."""
        try:
            llm = AwsBedrock(
                id=self.settings.workflow_llm_model_id,
                aws_region=self.settings.aws_region,
                aws_access_key_id=self.settings.aws_access_key_id,
                aws_secret_access_key=self.settings.aws_secret_access_key,
                aws_session_token=self.settings.aws_session_token,
            )

            return WorkflowRunner(
                llm=llm,
                db_client=MongoDB.get_client(),
                db_name=MongoDB.database_name,
                directive_queue=self.directive_queue,
                a2a_httpx_client=self.a2a_httpx_client,
                headers_provider=self.a2a_headers_provider,
                redis_client=self.redis_client,
                redis_key_prefix=self.settings.redis_key_prefix,
                mcp_headers_provider=self.mcp_headers_provider,
            )

        except Exception:
            logger.exception("Failed to initialize WorkflowRunner")
            raise

    @cached_property
    def workflow_control_service(self) -> WorkflowControlService:
        return WorkflowControlService(
            directive_queue=self.directive_queue,
            runner_factory=lambda: self.workflow_runner,
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
    def a2a_httpx_client(self) -> httpx.AsyncClient:
        """Shared httpx client for A2A agent invocations (workflow executors + mcpgw)."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0),
            follow_redirects=False,
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

    @cached_property
    def a2a_proxy_client_registry(self) -> A2AProxyClientRegistry:
        return A2AProxyClientRegistry(
            jwt_signing_config=self.settings.jwt_signing_config,
            jwt_subject=self.settings.registry_app_name,
            jwt_expires_in_seconds=3600,
        )

    async def startup(self) -> None:
        """Warm services that need async initialization before the app can serve traffic."""
        logger.info("Initializing services via registry container...")

        logger.info("Loading ACL role cache...")
        loaded = await load_role_cache()
        self.role_cache.clear()
        self.role_cache.update(loaded)
        logger.info("ACL role cache loaded: %d roles", len(self.role_cache))

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

        logger.info("Initializing workflow runner...")
        workflow_runner = self.workflow_runner
        logger.info("Workflow runner initialized successfully: %s", type(workflow_runner).__name__)

    async def shutdown(self) -> None:
        """Shutdown services that hold background tasks or external resources."""
        await cancel_in_flight_runs()
        await self.health_service.shutdown()
        await self.mcp_proxy_client.aclose()
        await self.a2a_httpx_client.aclose()
        await self.a2a_client_registry.close()

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
