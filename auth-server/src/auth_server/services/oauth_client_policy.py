"""Resolve OAuth client metadata and enforce category-level client policy."""

from dataclasses import dataclass
from typing import Any

from registry_pkgs.core.client_categories import (
    STATIC_CLIENT_METADATA,
    get_client_policy,
    resolve_client_category,
)
from registry_pkgs.core.config import JwtTokenConfig
from registry_pkgs.core.oauth_state_store import OAuthStateStoreProtocol

from ..core.config import settings


@dataclass(frozen=True)
class ClientAuthorizationResult:
    """Outcome of authenticating a client and checking its requested grant."""

    credentials_valid: bool
    grant_authorized: bool


def is_registry_client(client_id: str) -> bool:
    return client_id == settings.registry_app_name


def resolve_client_metadata(client_id: str, store: OAuthStateStoreProtocol) -> dict[str, Any] | None:
    """Resolve static single-instance clients before consulting the persistent client store."""
    static = STATIC_CLIENT_METADATA.get(client_id)
    if static is not None:
        return static
    return store.get_client(client_id)


def resolve_authorized_client_metadata(
    client_id: str,
    client_secret: str | None,
    grant_type: str,
    store: OAuthStateStoreProtocol,
    config: JwtTokenConfig,
) -> ClientAuthorizationResult:
    """Authenticate a client and authorize its grant with at most one store read."""
    if is_registry_client(client_id):
        credentials_valid = client_secret == settings.registry_client_secret
        return ClientAuthorizationResult(credentials_valid, credentials_valid)

    static = STATIC_CLIENT_METADATA.get(client_id)
    if static is not None:
        credentials_valid = static.get("token_endpoint_auth_method") == "none"
        metadata = static if credentials_valid else None
    else:
        credentials_valid = store.validate_client_credentials(client_id, client_secret)
        metadata = resolve_client_metadata(client_id, store) if credentials_valid else None

    if metadata is None:
        return ClientAuthorizationResult(False, False)

    category = resolve_client_category(client_id, config)
    policy = get_client_policy(category)
    if policy is None or grant_type not in policy.allowed_grant_types:
        return ClientAuthorizationResult(True, False)

    return ClientAuthorizationResult(True, grant_type in (metadata.get("grant_types") or []))
