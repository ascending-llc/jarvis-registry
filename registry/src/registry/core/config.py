import logging
import secrets
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from registry_pkgs import load_scopes_config


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables
    )

    # Auth settings
    secret_key: str = ""
    admin_user: str = "admin"
    admin_password: str = "password"
    session_cookie_name: str = "jarvis_registry_session"
    refresh_cookie_name: str = "jarvis_registry_refresh"
    session_max_age_seconds: int = 60 * 60 * 8  # 8 hours
    session_cookie_secure: bool = True  # Set to False for local development without HTTPS
    session_cookie_domain: str | None = None  # e.g., ".example.com" for cross-subdomain sharing
    auth_server_url: str = "http://localhost:8888"
    auth_server_external_url: str = "http://localhost:8888"  # External URL for OAuth redirects
    auth_server_api_prefix: str = ""  # API prefix for auth server routes (e.g., "/auth")
    auth_egress_header: str = "Authorization"  # RFC 6750: OAuth access token for MCP server resource access
    internal_auth_header: str = "X-Jarvis-Auth"  # Internal JWT for gateway-to-MCP-server authentication
    registry_client_url: str = "http://localhost:5173"  # Registry URL for OAuth protected resource metadata
    registry_url: str = "http://localhost:7860"
    registry_app_name: str = "jarvis-registry-client"  # OAuth client ID for registry web app

    # Embeddings settings
    embeddings_model_name: str = "all-MiniLM-L6-v2"
    embeddings_model_dimensions: int = 384

    # Health check settings
    health_check_interval_seconds: int = 300  # 5 minutes for automatic background checks (configurable via env var)
    health_check_timeout_seconds: int = 2  # Very fast timeout for user-driven actions

    # WebSocket performance settings
    max_websocket_connections: int = 100  # Reasonable limit for development/testing
    websocket_send_timeout_seconds: float = 2.0  # Allow slightly more time per connection
    websocket_broadcast_interval_ms: int = 10  # Very responsive - 10ms minimum between broadcasts
    websocket_max_batch_size: int = 20  # Smaller batches for faster updates
    websocket_cache_ttl_seconds: int = 1  # 1 second cache for near real-time user feedback

    # Well-known discovery settings
    enable_wellknown_discovery: bool = True
    wellknown_cache_ttl: int = 300  # 5 minutes

    # Vector search / tool discovery settings
    tool_discovery_mode: str = "external"  # "embedded" (FAISS+transformers) or "external" (MCP service)
    external_vector_search_url: str = "http://localhost:8000/mcp"  # Used when tool_discovery_mode=external

    # Security scanning settings (MCP Servers)
    security_scan_enabled: bool = True
    security_scan_on_registration: bool = True
    security_block_unsafe_servers: bool = True
    security_analyzers: str = "yara"  # Comma-separated: yara, llm, or yara,llm
    security_scan_timeout: int = 60  # 1 minutes
    security_add_pending_tag: bool = True
    mcp_scanner_llm_api_key: str = ""  # Optional LLM API key for advanced analysis

    # Agent security scanning settings (A2A Agents)
    agent_security_scan_enabled: bool = True
    agent_security_scan_on_registration: bool = True
    agent_security_block_unsafe_agents: bool = True
    agent_security_analyzers: str = "yara,spec"  # Comma-separated: yara, spec, heuristic, llm, endpoint
    agent_security_scan_timeout: int = 60  # 1 minute
    agent_security_add_pending_tag: bool = True
    a2a_scanner_llm_api_key: str = ""  # Optional Azure OpenAI API key for LLM-based analysis

    # Container paths - adjust for local development
    container_app_dir: Path = Path("/app")
    container_registry_dir: Path = Path("/app/registry")
    container_log_dir: Path = Path("/app/logs")

    # Note:  It will be overwritten from the .env file.
    JWT_ISSUER: str = "jarvis-auth-server"
    JWT_AUDIENCE: str = "jarvis-services"
    JWT_SELF_SIGNED_KID: str = "self-signed-key-v1"
    API_VERSION: str = "v1"
    log_level: str = (
        "INFO"  # Default to INFO, can be overridden by LOG_LEVEL env var (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    )
    log_format: str = "%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s"

    # Encryption key for sensitive data (client secrets, API keys, etc.)
    # Hex-encoded AES key for encrypting OAuth client secrets and API keys
    CREDS_KEY: str | None = None

    # Local development mode detection
    @property
    def is_local_dev(self) -> bool:
        """Check if running in local development mode."""
        return not Path("/app").exists()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Generate secret key if not provided
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)

        # Automatically append API prefix to auth server URLs if configured
        if self.auth_server_api_prefix:
            prefix = self.auth_server_api_prefix.rstrip("/")
            if not self.auth_server_url.endswith(prefix):
                self.auth_server_url = f"{self.auth_server_url.rstrip('/')}{prefix}"
            if not self.auth_server_external_url.endswith(prefix):
                self.auth_server_external_url = f"{self.auth_server_external_url.rstrip('/')}{prefix}"

        # Validate tool_discovery_mode
        if self.tool_discovery_mode not in ["embedded", "external"]:
            raise ValueError(
                f"Invalid tool_discovery_mode: {self.tool_discovery_mode}. Must be 'embedded' or 'external'"
            )

    @property
    def use_external_discovery(self) -> bool:
        """Check if using external vector search service."""
        return self.tool_discovery_mode == "external"

    @property
    def embeddings_model_dir(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "registry" / "models" / self.embeddings_model_name
        return self.container_registry_dir / "models" / self.embeddings_model_name

    @property
    def log_dir(self) -> Path:
        """Get log directory based on environment."""
        if self.is_local_dev:
            return Path.cwd() / "logs"
        return self.container_log_dir

    @property
    def log_file_path(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / "logs" / "registry.log"
        return self.container_log_dir / "registry.log"

    @property
    def faiss_index_path(self) -> Path:
        return self.servers_dir / "service_index.faiss"

    @property
    def faiss_metadata_path(self) -> Path:
        return self.servers_dir / "service_index_metadata.json"

    @property
    def dotenv_path(self) -> Path:
        if self.is_local_dev:
            return Path.cwd() / ".env"
        return self.container_registry_dir / ".env"

    @property
    def agents_dir(self) -> Path:
        """Directory for agent card storage."""
        if self.is_local_dev:
            return Path.cwd() / "registry" / "agents"
        return self.container_registry_dir / "agents"

    @property
    def agent_state_file_path(self) -> Path:
        """Path to agent state file (enabled/disabled tracking)."""
        return self.agents_dir / "agent_state.json"

    def configure_logging(self) -> None:
        """Configure application-wide logging with consistent format and level.

        This should be called once at application startup to initialize logging
        for all modules. Individual modules can then use logging.getLogger(__name__)
        without needing to call basicConfig again.
        """
        # Convert string log level to numeric level
        numeric_level = getattr(logging, self.log_level.upper(), logging.INFO)

        logging.basicConfig(
            level=numeric_level,
            format=self.log_format,
            force=True,  # Override any existing configuration
        )

    @property
    def scopes_config(self) -> dict[str, Any]:
        """Get scopes configuration using centralized loader from registry_pkgs.

        Returns:
            dict: Parsed scopes configuration from YAML file
        """
        return load_scopes_config()


# Global settings instance
settings = Settings()
