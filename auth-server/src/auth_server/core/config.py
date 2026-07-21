"""
Auth Server Configuration

Centralized configuration management using Pydantic Settings.
All environment variables are loaded here and accessed through the global `settings` instance.
"""

from functools import cached_property
from typing import Any

from registry_pkgs.core.config import JarvisBaseSettings, RedisConfig


class AuthSettings(JarvisBaseSettings):
    """Auth server settings with environment variable support."""

    # ==================== Cookies ====================
    oauth2_temp_session_cookie_name: str = "oauth2_temp_session"
    oauth2_consent_nonce_cookie_name: str = "oauth2_consent_nonce"

    # ==================== Core Settings ====================
    # JWT Settings
    max_token_lifetime_hours: int = 24
    default_token_lifetime_hours: int = 8

    # Rate Limiting
    max_tokens_per_user_per_hour: int = 100

    # ==================== CORS Configuration ====================
    cors_origins: str = "*"  # Comma-separated list of allowed origins, or "*" for all

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
    # entra_tenant_id / entra_client_id / entra_client_secret are inherited from JarvisBaseSettings.
    entra_token_kind: str = "id"  # "id" or "access"

    # ==================== Metrics Settings ====================
    metrics_service_url: str = "http://localhost:8890"
    metrics_api_key: str | None = None

    # ==================== OAuth Device Flow Settings ====================
    device_code_expiry_seconds: int = 900  # 15 minutes for real IdP login and possible MFA
    device_code_poll_interval: int = 5  # Poll every 5 seconds
    oauth_access_token_expiry_seconds: int = 3600

    # ==================== Redis ====================
    redis_uri: str = "redis://registry-redis:6379/1"
    redis_key_prefix: str = "jarvis-auth-server"

    @cached_property
    def redis_config(self) -> RedisConfig:
        return RedisConfig(redis_uri=self.redis_uri, redis_key_prefix=self.redis_key_prefix)

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        # Set keycloak_external_url to keycloak_url if not provided
        if self.keycloak_url and not self.keycloak_external_url:
            self.keycloak_external_url = self.keycloak_url


# Global settings instance
settings = AuthSettings()
