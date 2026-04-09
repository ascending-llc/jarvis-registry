"""
Auth Server Configuration

Centralized configuration management using Pydantic Settings.
All environment variables are loaded here and accessed through the global `settings` instance.
"""

from typing import Any

from pydantic import field_validator

from registry_pkgs.core.config import JarvisBaseSettings


class AuthSettings(JarvisBaseSettings):
    """Auth server settings with environment variable support."""

    # ==================== Core Settings ====================
    # JWT Settings
    max_token_lifetime_hours: int = 24
    default_token_lifetime_hours: int = 8

    # Rate Limiting
    max_tokens_per_user_per_hour: int = 100

    # ==================== CORS Configuration ====================
    cors_origins: str = "*"  # Comma-separated list of allowed origins, or "*" for all

    # ==================== Auth Provider ====================
    auth_provider: str = "entra"  # cognito, keycloak, entra

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

    # ==================== Metrics Settings ====================
    metrics_service_url: str = "http://localhost:8890"
    metrics_api_key: str | None = None

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

    @field_validator("auth_provider")
    @classmethod
    def validate_auth_provider(cls, v: str) -> str:
        """Validate auth provider value."""
        allowed = ["cognito", "keycloak", "entra"]
        if v.lower() not in allowed:
            raise ValueError(f"auth_provider must be one of {allowed}, got '{v}'")
        return v.lower()

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        # Set keycloak_external_url to keycloak_url if not provided
        if self.keycloak_url and not self.keycloak_external_url:
            self.keycloak_external_url = self.keycloak_url


# Global settings instance
settings = AuthSettings()
