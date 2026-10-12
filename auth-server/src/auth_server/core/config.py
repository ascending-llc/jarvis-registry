"""
Auth Server Configuration

Centralized configuration management using Pydantic Settings.
All environment variables are loaded here and accessed through the global `settings` instance.
"""

from functools import cached_property
from typing import Self

from pydantic import model_validator

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

    # ==================== Entra ID Settings ====================
    # entra_tenant_id / entra_client_id / entra_client_secret are inherited from JarvisBaseSettings.
    entra_token_kind: str = "id"  # "id" or "access"
    entra_enabled: str = "true"

    # ==================== Google Settings ====================
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_enabled: str = "false"

    # Provider toggles (*_enabled) feed the ${..._ENABLED} substitution in oauth2_providers.yml.
    # Kept as strings so a blank env value (e.g. `ENTRA_ENABLED=`) is valid and falls back to the
    # YAML default; config_loader coerces the substituted value to a real bool. Only entra is on
    # by default — every other provider is opt-in.

    # ==================== Metrics Settings ====================
    metrics_service_url: str = "http://localhost:8890"
    metrics_api_key: str | None = None

    # ==================== OAuth Device Flow Settings ====================
    device_code_expiry_seconds: int = 900  # 15 minutes for real IdP login and possible MFA
    device_code_poll_interval: int = 5  # Poll every 5 seconds
    oauth_access_token_expiry_seconds: int = 3600

    # ==================== Redis ====================
    redis_uri: str = "redis://registry-redis:6379/1"

    @model_validator(mode="after")
    def _validate_enabled_entra_scope_groups(self) -> Self:
        # Match ${ENTRA_ENABLED:-true} substitution and config_loader's bool coercion.
        enabled = (self.entra_enabled or "true").strip().lower() == "true"
        if enabled and self.auth_provider != "entra" and self.x_jarvis_registry_import_checks != "disabled":
            self._normalize_scope_group_object_ids()
        return self

    @cached_property
    def redis_config(self) -> RedisConfig:
        return RedisConfig(redis_uri=self.redis_uri, redis_key_prefix=self.auth_server_redis_key_prefix)


# Global settings instance
settings = AuthSettings()
