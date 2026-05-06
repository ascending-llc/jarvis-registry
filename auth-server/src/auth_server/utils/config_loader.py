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
            # Handle special case for auto-derived Cognito domain
            if "COGNITO_DOMAIN:-auto" in config:
                cognito_domain = self._get_value("COGNITO_DOMAIN")
                if not cognito_domain:
                    user_pool_id = self._get_value("COGNITO_USER_POOL_ID") or ""
                    cognito_domain = self._auto_derive_cognito_domain(user_pool_id)
                config = config.replace("${COGNITO_DOMAIN:-auto}", cognito_domain)

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

    def get_config(self) -> OAuth2Config:
        """Get the loaded OAuth2 configuration."""

        return self._config

    def get_provider_config(
        self,
        provider: AllowedProvider,
    ) -> AuthProviderConfig | EntraConfig:
        """Get configuration for a specific provider."""

        return self.get_config()["providers"][provider]
