from functools import cached_property
from pathlib import Path
from typing import Self

from pydantic import model_validator

from registry_pkgs.core.config import (
    ChunkingConfig,
    JarvisBaseSettings,
    RedisConfig,
    VectorConfig,
)
from registry_pkgs.vector.config import BackendConfig

MCP_CLIENT_INFO = {
    "name": "jarvis-registry",
    "version": "1.0.0",
}


class Settings(JarvisBaseSettings):
    """Registry application settings loaded via pydantic-settings."""

    # ==================== MCP Client Info ====================
    mcp_client_info: dict[str, str] = MCP_CLIENT_INFO

    # ==================== Session ====================
    session_cookie_name: str = "jarvis_registry_session"
    refresh_cookie_name: str = "jarvis_registry_refresh"
    session_max_age_seconds: int = 60 * 60 * 8
    session_cookie_secure: bool = True
    session_cookie_domain: str | None = None

    # ==================== Service URLs ====================
    registry_client_url: str = "http://localhost:5173"

    # ==================== Headers ====================
    auth_egress_header: str = "Authorization"
    internal_auth_header: str = "X-Jarvis-Auth"

    # ==================== API ====================
    api_version: str = "v1"

    # ==================== Anthropic ====================
    anthropic_api_version: str = "v0.1"
    anthropic_server_namespace: str = "io.mcpgateway"
    anthropic_api_default_limit: int = 100
    anthropic_api_max_limit: int = 1000

    # ==================== Local Embeddings ====================
    local_embeddings_model_name: str = "all-MiniLM-L6-v2"
    local_embeddings_model_dimensions: int = 384

    # ==================== Search Defaults ====================
    tool_discovery_mode: str = "external"
    external_vector_search_url: str = "http://localhost:8000/mcp"

    # ==================== Health ====================
    health_check_interval_seconds: int = 300
    health_check_timeout_seconds: int = 2

    # ==================== WebSocket ====================
    max_websocket_connections: int = 100
    websocket_send_timeout_seconds: float = 2.0
    websocket_broadcast_interval_ms: int = 10
    websocket_max_batch_size: int = 20
    websocket_cache_ttl_seconds: int = 1

    # ==================== Well-Known ====================
    enable_wellknown_discovery: bool = True
    wellknown_cache_ttl: int = 300

    # ==================== Gateway Security ====================
    mcpgw_enable_dns_rebinding_protection: bool = True
    mcpgw_allowed_hosts: str = "jarvis-demo.ascendingdc.com,jarvis-demo.ascendingdc.com:*"
    mcpgw_allowed_origins: str = "https://jarvis-demo.ascendingdc.com,https://jarvis-demo.ascendingdc.com:*"

    # ==================== Server Security Scanning ====================
    security_scan_enabled: bool = True
    security_scan_on_registration: bool = True
    security_block_unsafe_servers: bool = True
    security_analyzers: str = "yara"
    security_scan_timeout: int = 60
    security_add_pending_tag: bool = True
    mcp_scanner_llm_api_key: str | None = None

    # ==================== Agent Security Scanning ====================
    agent_security_scan_enabled: bool = True
    agent_security_scan_on_registration: bool = True
    agent_security_block_unsafe_agents: bool = True
    agent_security_analyzers: str = "yara,spec"
    agent_security_scan_timeout: int = 60
    agent_security_add_pending_tag: bool = True
    a2a_scanner_llm_api_key: str | None = None

    # ==================== Container Paths ====================
    container_registry_dir: Path = Path("/app/registry")

    # ==================== Redis ====================
    redis_uri: str = "redis://registry-redis:6379/1"
    redis_key_prefix: str = "jarvis-registry"

    # ==================== Chunking ====================
    max_chunk_size: int = 2048
    chunk_overlap: int = 200

    # ==================== Vector Store ====================
    vector_store_type: str = "weaviate"
    embedding_provider: str = "aws_bedrock"
    weaviate_host: str = "127.0.0.1"
    weaviate_port: int = 8080
    weaviate_api_key: str = ""
    weaviate_collection_prefix: str = ""
    openai_api_key: str | None = None
    openai_model: str = "text-embedding-3-small"

    # ==================== AWS ====================
    aws_region: str = "us-east-1"
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    # Global fallback only. Federation-specific assumeRoleArn in providerConfig
    # should take precedence for federated sync workflows.
    agentcore_assume_role_arn: str | None = None
    agentcore_runtime_jwt: str | None = None
    # AgentCore data-plane settings:
    # - InvokeAgentRuntime: MCP tools/resources/prompts discovery
    # - GetAgentCard: A2A agent-card discovery via AWS SDK
    # - JWT HTTP timeout: JWT/HTTP fallback only
    agentcore_invoke_runtime_retry_attempts: int = 4
    agentcore_invoke_runtime_retry_delay_seconds: float = 5.0
    agentcore_get_agent_card_retry_attempts: int = 2
    agentcore_get_agent_card_retry_delay_seconds: float = 5.0
    agentcore_jwt_http_timeout_seconds: float = 20.0

    # ==================== Azure OpenAI ====================
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-06-01"
    azure_openai_resource_name: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_openai_llm_deployment: str = ""
    llm_model: str = "gpt-4"

    # ==================== Encryption ====================
    creds_key: str = ""

    # ==================== Keycloak Integration ====================
    keycloak_url: str = "http://keycloak:8080"
    keycloak_realm: str = "mcp-gateway"
    keycloak_admin: str = "admin"
    keycloak_admin_password: str | None = None
    keycloak_m2m_client_id: str = "mcp-gateway-m2m"
    keycloak_m2m_client_secret: str | None = None

    # ==================== Federation ====================
    federation_config_path: str = "/app/config/federation.json"
    asor_access_token: str | None = None
    asor_client_credentials: str | None = None

    # ==================== Build Metadata ====================
    build_version: str = "1.0.0"

    # ==================== Model Validation ====================

    @model_validator(mode="after")
    def _validate_tool_discovery_mode(self) -> Self:
        if self.x_jarvis_registry_import_checks:
            return self

        if self.tool_discovery_mode not in ("embedded", "external"):
            raise ValueError(
                f"Invalid tool_discovery_mode: '{self.tool_discovery_mode}'. Must be 'embedded' or 'external'."
            )

        return self

    @model_validator(mode="after")
    def _validate_creds_key(self) -> Self:
        if self.x_jarvis_registry_import_checks:
            return self

        if self.creds_key == "":
            raise ValueError("CREDS_KEY must be set for encryption/decryption.")

        try:
            bytes.fromhex(self.creds_key)
        except ValueError as exc:
            raise ValueError(f"CREDS_KEY must be a valid hex string, but it is {self.creds_key}") from exc

        return self

    @cached_property
    def encryption_key(self) -> bytes:
        return bytes.fromhex(self.creds_key)

    @cached_property
    def is_local_dev(self) -> bool:
        return not Path("/app").exists()

    @cached_property
    def use_external_discovery(self) -> bool:
        return self.tool_discovery_mode == "external"

    @cached_property
    def servers_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "registry" / "servers"
        return self.container_registry_dir / "servers"

    @cached_property
    def static_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "registry" / "static"
        return self.container_registry_dir / "static"

    @cached_property
    def templates_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "registry" / "templates"
        return self.container_registry_dir / "templates"

    @cached_property
    def local_embeddings_model_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "registry" / "models" / self.local_embeddings_model_name
        return self.container_registry_dir / "models" / self.local_embeddings_model_name

    @cached_property
    def log_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "logs"
        return Path("/app/logs")

    @cached_property
    def log_file_path(self) -> Path:
        return self.log_dir / "registry.log"

    @cached_property
    def faiss_index_path(self) -> Path:
        return self.servers_dir / "service_index.faiss"

    @cached_property
    def faiss_metadata_path(self) -> Path:
        return self.servers_dir / "service_index_metadata.json"

    @cached_property
    def dotenv_path(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / ".env"
        return self.container_registry_dir / ".env"

    @cached_property
    def agents_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "registry" / "agents"
        return self.container_registry_dir / "agents"

    @cached_property
    def agent_state_file_path(self) -> Path:
        return self.agents_dir / "agent_state.json"

    @cached_property
    def redis_config(self) -> RedisConfig:
        return RedisConfig(redis_uri=self.redis_uri, redis_key_prefix=self.redis_key_prefix)

    @cached_property
    def chunking_config(self) -> ChunkingConfig:
        return ChunkingConfig(max_chunk_size=self.max_chunk_size, chunk_overlap=self.chunk_overlap)

    @cached_property
    def vector_config(self) -> VectorConfig:
        return VectorConfig(
            vector_store_type=self.vector_store_type,
            embedding_provider=self.embedding_provider,
            weaviate_host=self.weaviate_host,
            weaviate_port=self.weaviate_port,
            weaviate_api_key=self.weaviate_api_key,
            weaviate_collection_prefix=self.weaviate_collection_prefix,
            openai_api_key=self.openai_api_key,
            openai_model=self.openai_model,
            aws_region=self.aws_region,
            embedding_model=self.embedding_model,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            aws_session_token=self.aws_session_token,
            azure_openai_api_key=self.azure_openai_api_key,
            azure_openai_endpoint=self.azure_openai_endpoint,
            azure_openai_api_version=self.azure_openai_api_version,
            azure_openai_resource_name=self.azure_openai_resource_name,
            azure_openai_embedding_deployment=self.azure_openai_embedding_deployment,
            azure_openai_llm_deployment=self.azure_openai_llm_deployment,
        )

    @cached_property
    def vector_backend_config(self) -> BackendConfig:
        return BackendConfig.from_vector_config(self.vector_config)


settings = Settings()
