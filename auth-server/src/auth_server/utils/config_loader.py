import logging
import re
from pathlib import Path
from typing import Any

import yaml

from ..core.config import AuthSettings
from ..core.types import AllowedProvider, AuthProviderConfig, EntraConfig, OAuth2Config

logger = logging.getLogger(__name__)


class OAuth2ConfigLoader:
    """OAuth2 configuration loader with environment variable substitution."""

    def __init__(self, settings: AuthSettings):
        self._settings = settings
        # Eagerly load OAuth2 config so app can fail early and loudly on start-up if config file is off.
        self._config = self._load_config()

    def _load_config(self) -> OAuth2Config:
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

            # Coerce every provider's `enabled` to a real bool: substitution turns bool
            # settings into the string "true"/"false", and the string "false" is truthy.
            for provider_cfg in processed_config.get("providers", {}).values():
                provider_cfg["enabled"] = str(provider_cfg.get("enabled", False)).strip().lower() == "true"

            # Log loaded providers
            providers = list(processed_config.get("providers", {}).keys())
            logger.info(f"Successfully loaded OAuth2 configuration with providers: {providers}")

            return processed_config
        except Exception:
            logger.exception("Failed to load OAuth2 configuration")

            raise

    def _get_value(self, var_name: str) -> str | None:
        field_name = var_name.strip().lower()

        value = getattr(self._settings, field_name, None)

        if value is None:
            return None
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)

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
            # Support bash-style default values: ${VAR_NAME:-default_value}
            def replace_var(match):
                var_expr = match.group(1)
                # Check if it has a default value
                if ":-" in var_expr:
                    var_name, default_value = var_expr.split(":-", 1)
                    return self._get_value(var_name) or default_value.strip()
                else:
                    var_name = var_expr.strip()
                    value = self._get_value(var_name)
                    if value is not None:
                        return value

                    logger.warning(f"Setting not found for oauth2 placeholder: {var_name}")
                    return match.group(0)  # Return original if not found

            return re.sub(r"\$\{([^}]+)\}", replace_var, config)
        else:
            return config

    def get_config(self) -> OAuth2Config:
        """Get the loaded OAuth2 configuration."""

        return self._config

    def get_provider_config(
        self,
        provider: AllowedProvider,
    ) -> AuthProviderConfig | EntraConfig:
        """Get configuration for a specific provider."""

        return self.get_config()["providers"][provider]
