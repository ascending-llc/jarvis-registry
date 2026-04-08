"""
Auth Server Configuration

Centralized configuration management using Pydantic Settings.
All environment variables are loaded here and accessed through the global `settings` instance.
"""

import logging
import secrets
from functools import cached_property
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from registry_pkgs import load_scopes_config
from registry_pkgs.core.config import MongoConfig, ScopesConfig, TelemetryConfig


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
    jwt_private_key: str = ""  # PEM-encoded RSA private key (JWT_PRIVATE_KEY env var)
    jwt_public_key: str = ""  # PEM-encoded RSA public key (JWT_PUBLIC_KEY env var)
    jwt_audience: str = "jarvis-services"
    jwt_self_signed_kid: str = "self-signed-key-v1"
    max_token_lifetime_hours: int = 24
    default_token_lifetime_hours: int = 8

    # ==================== RFC 9110 realm ====================
    # "realm" value in the WWW-Authenticate header. According to RFC 9110, it is suppose to describe the resource
    # being protected. Since we use the same value for both `registry` and `auth-server`, we use a generic value like below.
    jarvis_realm: str = "jarvis-resources"

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
    log_format: str = "%(asctime)s,p%(process)s,{%(name)s:%(lineno)d},%(levelname)s,%(message)s"

    # ==================== Metrics Settings ====================
    metrics_service_url: str = "http://localhost:8890"
    metrics_api_key: str | None = None
    otel_metrics_config_path: str = ""
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4318"
    otel_prometheus_enabled: bool = False
    otel_prometheus_port: int = 9464
    mongo_uri: str = "mongodb://127.0.0.1:27017/jarvis"
    mongodb_username: str = ""
    mongodb_password: str = ""
    scopes_config_path: str = ""

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

    @cached_property
    def scopes_config(self) -> dict:
        """Get the scopes configuration using centralized loader from registry_pkgs."""
        return load_scopes_config(self.scopes_file_config)

    @cached_property
    def scopes_file_config(self) -> ScopesConfig:
        return ScopesConfig(scopes_config_path=self.scopes_config_path)

    @cached_property
    def mongo_config(self) -> MongoConfig:
        return MongoConfig(
            mongo_uri=self.mongo_uri,
            mongodb_username=self.mongodb_username,
            mongodb_password=self.mongodb_password,
        )

    @cached_property
    def telemetry_config(self) -> TelemetryConfig:
        return TelemetryConfig(
            otel_metrics_config_path=self.otel_metrics_config_path,
            otel_exporter_otlp_endpoint=self.otel_exporter_otlp_endpoint,
            otel_prometheus_enabled=self.otel_prometheus_enabled,
            otel_prometheus_port=self.otel_prometheus_port,
        )

    def model_post_init(self, __context) -> None:
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

        We set hanlders on two named loggers `auth_server` and `registry_pkgs` only.
        This is to avoid the noises if we were to place the handler on the root logger.
        """
        # Convert string log level to numeric level
        numeric_level = getattr(logging, self.log_level.upper(), logging.INFO)

        auth_server_logger = logging.getLogger(__package__.split(".")[0])
        auth_server_logger.propagate = False
        auth_server_logger.setLevel(numeric_level)

        if len(auth_server_logger.handlers) == 0:
            handler = logging.StreamHandler()

            handler.setFormatter(logging.Formatter(self.log_format))

            auth_server_logger.addHandler(handler)

        registry_pkgs_logger = logging.getLogger("registry_pkgs")
        registry_pkgs_logger.propagate = False
        registry_pkgs_logger.setLevel(numeric_level)

        if len(registry_pkgs_logger.handlers) == 0:
            handler = logging.StreamHandler()

            handler.setFormatter(logging.Formatter(self.log_format))

            registry_pkgs_logger.addHandler(handler)

    @cached_property
    def jwt_issuer(self) -> str:
        """
        Per RFC 8414 requirement on issuer:
        - Both the "issuer" field of the response document of the well-known route(s) and the `iss` claim of the JWT
          tokens issued by our auth-server must be the URL that is the well-know URL with the well-known path portion stripped.
        - For example, our well-known routes are `https://jarvis-demo.ascendingdc.com/.well-known/openid-configuration`,
          and `https://jarvis-demo.ascendingdc.com/.well-known/oauth-authorization-server`. Therefore our "issuer"
          must be `https://jarvis-demo.ascendingdc.com`.
        """

        result = urlparse(self.auth_server_external_url)

        return f"{result.scheme}://{result.netloc}"


# Global settings instance
settings = AuthSettings()
