import logging
import re
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


def _get_settings_value(var_name: str) -> str | None:
    """Resolve an oauth2 placeholder from auth-server settings."""
    from ..core.config import settings

    field_name = var_name.strip().lower()
    value = getattr(settings, field_name, None)
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


class OAuth2ConfigLoader:
    """Singleton OAuth2 configuration loader with environment variable substitution.

    This class ensures that the OAuth2 configuration is loaded only once and
    cached for subsequent access. It supports bash-style default values in
    environment variables (e.g., ${VAR_NAME:-default_value}).
    """

    _instance: Optional["OAuth2ConfigLoader"] = None
    _lock: Lock = Lock()
    _config: dict[str, Any] | None = None

    def __new__(cls) -> "OAuth2ConfigLoader":
        """Create or return the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking pattern
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the configuration loader.

        Note: This will only execute once due to singleton pattern.
        """
        # Prevent re-initialization
        if self._config is not None:
            return

        with self._lock:
            if self._config is None:
                self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        """Load OAuth2 providers configuration from oauth2_providers.yml.

        Returns:
            Dict containing OAuth2 providers configuration with environment
            variables substituted.
        """
        try:
            oauth2_file = Path(__file__).parent.parent / "oauth2_providers.yml"
            logger.info(f"Loading OAuth2 configuration from: {oauth2_file}")

            with open(oauth2_file) as f:
                config = yaml.safe_load(f)

            # Substitute environment variables in configuration
            processed_config = self._substitute_env_vars(config)

            # Log loaded providers
            providers = list(processed_config.get("providers", {}).keys())
            logger.info(f"Successfully loaded OAuth2 configuration with providers: {providers}")

            return processed_config
        except FileNotFoundError:
            logger.error("OAuth2 configuration file not found")
            return {"providers": {}, "session": {}, "registry": {}}
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse OAuth2 configuration YAML: {e}")
            return {"providers": {}, "session": {}, "registry": {}}
        except Exception as e:
            logger.error(f"Failed to load OAuth2 configuration: {e}")
            return {"providers": {}, "session": {}, "registry": {}}

    def _substitute_env_vars(self, config: Any) -> Any:
        """Recursively substitute environment variables in configuration.

        Supports bash-style default values: ${VAR_NAME:-default_value}

        Args:
            config: Configuration value (dict, list, or str)

        Returns:
            Configuration with environment variables substituted
        """
        if isinstance(config, dict):
            return {k: self._substitute_env_vars(v) for k, v in config.items()}
        elif isinstance(config, list):
            return [self._substitute_env_vars(item) for item in config]
        elif isinstance(config, str) and "${" in config:
            # Handle special case for auto-derived Cognito domain
            if "COGNITO_DOMAIN:-auto" in config:
                cognito_domain = _get_settings_value("COGNITO_DOMAIN")
                if not cognito_domain:
                    user_pool_id = _get_settings_value("COGNITO_USER_POOL_ID") or ""
                    cognito_domain = self._auto_derive_cognito_domain(user_pool_id)
                config = config.replace("${COGNITO_DOMAIN:-auto}", cognito_domain)

            # Support bash-style default values: ${VAR_NAME:-default_value}
            def replace_var(match):
                var_expr = match.group(1)
                # Check if it has a default value
                if ":-" in var_expr:
                    var_name, default_value = var_expr.split(":-", 1)
                    return _get_settings_value(var_name) or default_value.strip()
                else:
                    var_name = var_expr.strip()
                    value = _get_settings_value(var_name)
                    if value is not None:
                        return value

                    logger.warning(f"Setting not found for oauth2 placeholder: {var_name}")
                    return match.group(0)  # Return original if not found

            return re.sub(r"\$\{([^}]+)\}", replace_var, config)
        else:
            return config

    def _auto_derive_cognito_domain(self, user_pool_id: str) -> str:
        """Auto-derive Cognito domain from User Pool ID.

        Example: us-east-1_KmP5A3La3 → us-east-1kmp5a3la3

        Args:
            user_pool_id: AWS Cognito User Pool ID

        Returns:
            Derived domain string
        """
        if not user_pool_id:
            return ""

        # Remove underscore and convert to lowercase
        domain = user_pool_id.replace("_", "").lower()
        logger.info(f"Auto-derived Cognito domain '{domain}' from user pool ID '{user_pool_id}'")
        return domain

    @property
    def config(self) -> dict[str, Any]:
        """Get the loaded OAuth2 configuration.

        Returns:
            Dictionary containing the OAuth2 configuration
        """
        if self._config is None:
            # This should never happen due to __init__, but just in case
            with self._lock:
                if self._config is None:
                    self._config = self._load_config()
        return self._config

    def reload(self) -> dict[str, Any]:
        """Force reload the configuration from file.

        This method can be used to refresh the configuration without
        restarting the application.

        Returns:
            Dictionary containing the reloaded OAuth2 configuration
        """
        with self._lock:
            logger.info("Reloading OAuth2 configuration...")
            self._config = self._load_config()
            return self._config

    def get_provider_config(self, provider_name: str) -> dict[str, Any] | None:
        """Get configuration for a specific provider.

        Args:
            provider_name: Name of the provider (e.g., 'keycloak', 'cognito', 'entra')

        Returns:
            Provider configuration dictionary or None if not found
        """
        return self.config.get("providers", {}).get(provider_name)

    def get_enabled_providers(self) -> list:
        """Get list of all enabled provider names.

        Returns:
            List of enabled provider names
        """
        enabled = []
        for provider_name, config in self.config.get("providers", {}).items():
            if config.get("enabled", False):
                enabled.append(provider_name)
        return enabled


# Global singleton instance accessor
_config_loader: OAuth2ConfigLoader | None = None


def get_oauth2_config(reload: bool = False) -> dict[str, Any]:
    """Get the OAuth2 configuration (singleton access).

    This is a convenience function that provides access to the singleton
    OAuth2ConfigLoader instance.

    Args:
        reload: If True, force reload the configuration from file

    Returns:
        Dictionary containing the OAuth2 configuration

    Example:
        >>> config = get_oauth2_config()
        >>> keycloak_config = config.get('providers', {}).get('keycloak')
    """
    global _config_loader

    if _config_loader is None:
        _config_loader = OAuth2ConfigLoader()

    if reload:
        return _config_loader.reload()

    return _config_loader.config


def get_provider_config(provider_name: str) -> dict[str, Any] | None:
    """Get configuration for a specific provider.

    Args:
        provider_name: Name of the provider (e.g., 'keycloak', 'cognito', 'entra')

    Returns:
        Provider configuration dictionary or None if not found

    Example:
        >>> entra_config = get_provider_config('entra')
        >>> if entra_config:
        ...     tenant_id = entra_config.get('tenant_id')
    """
    global _config_loader

    if _config_loader is None:
        _config_loader = OAuth2ConfigLoader()

    return _config_loader.get_provider_config(provider_name)


def get_enabled_providers() -> list:
    """Get list of all enabled provider names.

    Returns:
        List of enabled provider names

    Example:
        >>> enabled = get_enabled_providers()
        >>> print(f"Enabled providers: {enabled}")
    """
    global _config_loader

    if _config_loader is None:
        _config_loader = OAuth2ConfigLoader()

    return _config_loader.get_enabled_providers()
