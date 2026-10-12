"""Factory for creating authentication provider instances."""

# Builtin
import logging

from registry_pkgs.core.config import JarvisBaseSettings
from registry_pkgs.core.scope_groups import entra_scope_group_ids
from registry_pkgs.google.cloud_identity_client import CloudIdentityGroupsClient

from ..core.types import AllowedProvider, GoogleConfig
from ..utils.config_loader import EntraConfig, OAuth2Config
from .base import AuthProvider
from .entra import EntraIdProvider
from .google import GoogleProvider

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def get_auth_provider(
    provider_type: AllowedProvider,
    oauth2_config: OAuth2Config,
    cloud_identity_client: CloudIdentityGroupsClient,
    settings: JarvisBaseSettings,
) -> AuthProvider:
    """Factory function to get the appropriate auth provider.

    Args:
        provider_type: Type of provider to create ('entra' or 'google').
        oauth2_config:
        cloud_identity_client:
        settings: Shared settings containing the Entra scope-group bindings.

    Returns:
        AuthProvider instance configured for the specified provider

    Raises:
        ValueError: If provider type is unknown or required config is missing
    """
    logger.info(f"Creating authentication provider: {provider_type}")

    if provider_type == "entra":
        return _create_entra_provider(oauth2_config["providers"]["entra"], settings)
    elif provider_type == "google":
        return _create_google_provider(oauth2_config["providers"]["google"], cloud_identity_client)
    else:
        raise ValueError(f"Unknown auth provider: {provider_type}")


def _create_entra_provider(entra_config: EntraConfig, settings: JarvisBaseSettings) -> EntraIdProvider:
    """Create and configure Microsoft Entra ID provider."""

    scope_group_ids = entra_scope_group_ids(settings)
    if not scope_group_ids:
        raise ValueError("Entra scope groups are not configured; set JARVIS_REGISTRY_*_GROUP_OBJECT_ID")

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
        scope_group_ids=scope_group_ids,
    )


def _create_google_provider(
    google_config: GoogleConfig,
    cloud_identity_client: CloudIdentityGroupsClient,
) -> GoogleProvider:
    """Create and configure the Google Workspace provider."""
    client_id = google_config.get("client_id")
    client_secret = google_config.get("client_secret")
    auth_url = google_config.get("auth_url")
    token_url = google_config.get("token_url")
    jwks_url = google_config.get("jwks_url")

    missing_vars = []
    if not client_id:
        missing_vars.append("GOOGLE_CLIENT_ID")
    if not client_secret:
        missing_vars.append("GOOGLE_CLIENT_SECRET")
    if not auth_url:
        missing_vars.append("auth_url in oauth2_providers.yml")
    if not token_url:
        missing_vars.append("token_url in oauth2_providers.yml")
    if not jwks_url:
        missing_vars.append("jwks_url in oauth2_providers.yml")

    if missing_vars:
        raise ValueError(
            f"Missing required Google configuration: {', '.join(missing_vars)}. Please set these environment variables."
        )

    logger.info("Initializing Google provider")

    return GoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        cloud_identity_client=cloud_identity_client,
        auth_url=auth_url,
        token_url=token_url,
        jwks_url=jwks_url,
        allowed_hd=google_config.get("allowed_hd", ""),
        scopes=google_config.get("scopes"),
        grant_type=google_config.get("grant_type", "authorization_code"),
    )
