"""
Auth Server Configuration

Centralized configuration management using Pydantic Settings.
All environment variables are loaded here and accessed through the global `settings` instance.
"""

import logging
import secrets

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from registry_pkgs import load_scopes_config

from ..utils.config_loader import get_oauth2_config


class AuthSettings(BaseSettings):
    """Auth server settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",  # Ignore extra environment variables
    )

    # ==================== Core Settings ====================
    secret_key: str = ""
    admin_user: str = "admin"
    admin_password: str = "admin123"

    # JWT Settings
    jwt_issuer: str = "jarvis-auth-server"
    jwt_audience: str = "jarvis-services"
    jwt_self_signed_kid: str = "self-signed-key-v1"
    max_token_lifetime_hours: int = 24
    default_token_lifetime_hours: int = 8

    # Rate Limiting
    max_tokens_per_user_per_hour: int = 100

    # ==================== Server URLs ====================
    auth_server_url: str = "http://localhost:8888"
    auth_server_external_url: str = "http://localhost:8888"
    registry_url: str = "http://localhost:7860"
    registry_app_name: str = "jarvis-registry-client"

    # API Prefix (e.g., "/auth", "/gateway", or empty string for no prefix)
    auth_server_api_prefix: str = ""

    # ==================== CORS Configuration ====================
    cors_origins: str = "*"  # Comma-separated list of allowed origins, or "*" for all

    # ==================== Auth Provider ====================
    auth_provider: str = "keycloak"  # cognito, keycloak, entra

    # ==================== Keycloak Settings ====================
    keycloak_url: str | None = None
    keycloak_external_url: str | None = None
    keycloak_realm: str = "mcp-gateway"
    keycloak_client_id: str | None = None
    keycloak_client_secret: str | None = None
    keycloak_m2m_client_id: str | None = None
    keycloak_m2m_client_secret: str | None = None

    # ==================== Cognito Settings ====================
    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_client_secret: str | None = None
    cognito_domain: str | None = None
    aws_region: str = "us-east-1"

    # ==================== Entra ID Settings ====================
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: str | None = None
    entra_token_kind: str = "id"  # "id" or "access"

    # ==================== Logging Settings ====================
    log_level: str = (
        "INFO"  # Default to INFO, can be overridden by LOG_LEVEL env var (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    )
    log_format: str = "%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s"

    # ==================== Metrics Settings ====================
    metrics_service_url: str = "http://localhost:8890"
    metrics_api_key: str = ""

    # ==================== OAuth Device Flow Settings ====================
    device_code_expiry_seconds: int = 600  # 10 minutes
    device_code_poll_interval: int = 5  # Poll every 5 seconds

    # ==================== OAuth Session Settings ====================
    oauth_session_ttl_seconds: int = 600  # 10 minutes for OAuth2 flow (default)
    # Note: This is the maximum time between initiating OAuth flow and completing the callback.
    # For security (CSRF protection), this should not be too long.
    # If Claude Desktop reconnection receives "session_expired", the OAuth session has expired and
    # Claude Desktop will automatically re-initiate the OAuth flow (the user may be prompted again
    # by the provider, but no manual restart of the flow is required).

    # ==================== Configuration Properties ====================

    @property
    def scopes_config(self) -> dict:
        """Get the scopes configuration using centralized loader from registry_pkgs."""
        return load_scopes_config()

    @property
    def oauth2_config(self) -> dict:
        """Get the OAuth2 configuration from oauth2_providers.yml file."""
        return get_oauth2_config()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Generate secret key if not provided
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)

        # Set keycloak_external_url to keycloak_url if not provided
        if self.keycloak_url and not self.keycloak_external_url:
            self.keycloak_external_url = self.keycloak_url

        # Automatically append API prefix to auth server URLs if configured
        # This allows setting AUTH_SERVER_URL=http://localhost:8888 and AUTH_SERVER_API_PREFIX=/auth
        # to automatically get http://localhost:8888/auth
        if self.auth_server_api_prefix:
            prefix = self.auth_server_api_prefix.rstrip("/")
            if not self.auth_server_url.endswith(prefix):
                self.auth_server_url = f"{self.auth_server_url.rstrip('/')}{prefix}"
            if not self.auth_server_external_url.endswith(prefix):
                self.auth_server_external_url = f"{self.auth_server_external_url.rstrip('/')}{prefix}"

    @field_validator("auth_provider")
    @classmethod
    def validate_auth_provider(cls, v: str) -> str:
        """Validate auth provider value."""
        allowed = ["cognito", "keycloak", "entra"]
        if v.lower() not in allowed:
            raise ValueError(f"auth_provider must be one of {allowed}, got '{v}'")
        return v.lower()

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


# Global settings instance
settings = AuthSettings()
