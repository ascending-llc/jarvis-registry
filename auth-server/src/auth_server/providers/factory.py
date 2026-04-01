"""Factory for creating authentication provider instances."""

import logging

from ..core.config import settings
from .base import AuthProvider
from .cognito import CognitoProvider
from .entra import EntraIdProvider
from .keycloak import KeycloakProvider

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def get_auth_provider(
    provider_type: str | None = None,
    *,
    settings_override=None,
    oauth2_config: dict | None = None,
) -> AuthProvider:
    """Factory function to get the appropriate auth provider.

    Args:
        settings_override:
        oauth2_config:
        provider_type: Type of provider to create ('cognito', 'keycloak', or 'entra').
                      If None, uses auth-server settings.

    Returns:
        AuthProvider instance configured for the specified provider

    Raises:
        ValueError: If provider type is unknown or required config is missing
    """
    resolved_settings = settings_override or settings
    provider_type = provider_type or resolved_settings.auth_provider

    logger.info(f"Creating authentication provider: {provider_type}")

    if provider_type == "keycloak":
        return _create_keycloak_provider(resolved_settings)
    elif provider_type == "cognito":
        return _create_cognito_provider(resolved_settings)
    elif provider_type == "entra":
        return _create_entra_provider(resolved_settings, oauth2_config or {})
    else:
        raise ValueError(f"Unknown auth provider: {provider_type}")


def _create_keycloak_provider(resolved_settings) -> KeycloakProvider:
    """Create and configure Keycloak provider."""
    # Get configuration from settings
    keycloak_url = resolved_settings.keycloak_url
    keycloak_external_url = resolved_settings.keycloak_external_url or keycloak_url
    realm = resolved_settings.keycloak_realm
    client_id = resolved_settings.keycloak_client_id
    client_secret = resolved_settings.keycloak_client_secret

    # Optional M2M configuration
    m2m_client_id = resolved_settings.keycloak_m2m_client_id
    m2m_client_secret = resolved_settings.keycloak_m2m_client_secret

    # Validate required configuration
    missing_vars = []
    if not keycloak_url:
        missing_vars.append("KEYCLOAK_URL")
    if not client_id:
        missing_vars.append("KEYCLOAK_CLIENT_ID")
    if not client_secret:
        missing_vars.append("KEYCLOAK_CLIENT_SECRET")

    if missing_vars:
        raise ValueError(
            f"Missing required Keycloak configuration: {', '.join(missing_vars)}. "
            "Please set these environment variables."
        )

    logger.info(
        f"Initializing Keycloak provider for realm '{realm}' at {keycloak_url} (external: {keycloak_external_url})"
    )

    return KeycloakProvider(
        keycloak_url=keycloak_url,
        keycloak_external_url=keycloak_external_url,
        realm=realm,
        client_id=client_id,
        client_secret=client_secret,
        m2m_client_id=m2m_client_id,
        m2m_client_secret=m2m_client_secret,
    )


def _create_cognito_provider(resolved_settings) -> CognitoProvider:
    """Create and configure Cognito provider."""
    # Required configuration
    user_pool_id = resolved_settings.cognito_user_pool_id
    client_id = resolved_settings.cognito_client_id
    client_secret = resolved_settings.cognito_client_secret
    region = resolved_settings.aws_region

    # Optional configuration
    domain = resolved_settings.cognito_domain

    # Validate required configuration
    missing_vars = []
    if not user_pool_id:
        missing_vars.append("COGNITO_USER_POOL_ID")
    if not client_id:
        missing_vars.append("COGNITO_CLIENT_ID")
    if not client_secret:
        missing_vars.append("COGNITO_CLIENT_SECRET")

    if missing_vars:
        raise ValueError(
            f"Missing required Cognito configuration: {', '.join(missing_vars)}. "
            "Please set these environment variables."
        )

    logger.info(f"Initializing Cognito provider for user pool '{user_pool_id}' in region '{region}'")

    return CognitoProvider(
        user_pool_id=user_pool_id, client_id=client_id, client_secret=client_secret, region=region, domain=domain
    )


def _create_entra_provider(resolved_settings, oauth2_config: dict) -> EntraIdProvider:
    """Create and configure Microsoft Entra ID provider."""
    # Load OAuth2 configuration using shared loader
    entra_config = oauth2_config.get("providers", {}).get("entra", {})

    # Endpoint URLs from oauth2_providers.yml (already have environment variable substitution)
    tenant_id = entra_config.get("tenant_id")
    client_id = entra_config.get("client_id")
    client_secret = entra_config.get("client_secret")

    auth_url = entra_config.get("auth_url")
    token_url = entra_config.get("token_url")
    jwks_url = entra_config.get("jwks_url")
    logout_url = entra_config.get("logout_url")
    userinfo_url = entra_config.get("user_info_url")

    # Optional configuration from oauth2_providers.yml
    graph_url = entra_config.get("graph_url")
    m2m_scope = entra_config.get("m2m_scope")

    # OAuth2 configuration from oauth2_providers.yml with fallbacks
    scopes = entra_config.get("scopes")
    grant_type = entra_config.get("grant_type")

    # Optional claim mappings from oauth2_providers.yml
    username_claim = entra_config.get("username_claim")
    groups_claim = entra_config.get("groups_claim")
    email_claim = entra_config.get("email_claim")
    name_claim = entra_config.get("name_claim")

    # Validate required configuration
    missing_vars = []
    if not tenant_id:
        missing_vars.append("ENTRA_TENANT_ID")
    if not client_id:
        missing_vars.append("ENTRA_CLIENT_ID")
    if not client_secret:
        missing_vars.append("ENTRA_CLIENT_SECRET")
    if not auth_url:
        missing_vars.append("auth_url in oauth2_providers.yml")
    if not token_url:
        missing_vars.append("token_url in oauth2_providers.yml")
    if not jwks_url:
        missing_vars.append("jwks_url in oauth2_providers.yml")
    if not logout_url:
        missing_vars.append("logout_url in oauth2_providers.yml")
    if not userinfo_url:
        missing_vars.append("user_info_url in oauth2_providers.yml")

    if missing_vars:
        raise ValueError(
            f"Missing required Entra ID configuration: {', '.join(missing_vars)}. "
            "Please set the required environment variables or check oauth2_providers.yml."
        )

    logger.info(
        f"Initializing Entra ID provider for tenant '{tenant_id}' with scopes={scopes}, grant_type={grant_type}"
    )

    return EntraIdProvider(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        auth_url=auth_url,
        token_url=token_url,
        jwks_url=jwks_url,
        logout_url=logout_url,
        userinfo_url=userinfo_url,
        graph_url=graph_url,
        m2m_scope=m2m_scope,
        scopes=scopes,
        grant_type=grant_type,
        username_claim=username_claim,
        groups_claim=groups_claim,
        email_claim=email_claim,
        name_claim=name_claim,
    )


async def _get_provider_health_info() -> dict:
    """Get health information for the current provider."""
    try:
        provider = get_auth_provider()
        if hasattr(provider, "get_provider_info"):
            return await provider.get_provider_info()
        else:
            return {"provider_type": settings.auth_provider, "status": "unknown"}
    except Exception as e:
        logger.error(f"Failed to get provider health info: {e}")
        return {"provider_type": settings.auth_provider, "status": "error", "error": str(e)}
