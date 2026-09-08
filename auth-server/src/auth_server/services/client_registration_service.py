"""OAuth dynamic client registration business logic."""

import logging
import secrets
import time
from dataclasses import dataclass

from registry_pkgs.core.client_categories import ClientCategory, get_client_policy
from registry_pkgs.core.oauth_state_store import OAuthStateStoreProtocol
from registry_pkgs.core.redirect_uri import validate_registration_redirect_uri

from ..models.client_registration import ClientRegistrationRequest, ClientRegistrationResponse

logger = logging.getLogger(__name__)

# OAuth method identifier, not a credential.
CLIENT_SECRET_POST_METHOD = "client_secret_post"  # nosec B105
SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS = frozenset({"none", CLIENT_SECRET_POST_METHOD})
NON_EXPIRING_CREDENTIAL = 0


@dataclass(frozen=True)
class ClientRegistrationError(Exception):
    """Expected RFC 7591 registration failure returned as an OAuth error."""

    error: str
    description: str


def _validate_redirect_uris(redirect_uris: list[str] | None) -> list[str]:
    uris = redirect_uris or []
    if not uris:
        raise ClientRegistrationError("invalid_redirect_uri", "at least one redirect_uri is required")

    for uri in uris:
        try:
            validate_registration_redirect_uri(uri)
        except ValueError as e:
            raise ClientRegistrationError("invalid_redirect_uri", str(e)) from e
    return uris


def _resolve_grant_types(
    requested_grant_types: list[str] | None,
    allowed_grant_types: tuple[str, ...],
) -> list[str]:
    if requested_grant_types is None:
        return list(allowed_grant_types)

    grant_types = [grant_type for grant_type in allowed_grant_types if grant_type in requested_grant_types]
    if not grant_types:
        raise ClientRegistrationError(
            "invalid_client_metadata",
            "none of the requested grant_types are supported",
        )
    return grant_types


class ClientRegistrationService:
    """Register MCP and A2A OAuth clients under their category policy."""

    def __init__(self, store: OAuthStateStoreProtocol) -> None:
        self._store = store

    def register(
        self,
        registration: ClientRegistrationRequest,
        *,
        category: ClientCategory,
        default_client_name: str,
        ip_address: str,
    ) -> ClientRegistrationResponse:
        policy = get_client_policy(category)
        if policy is None or policy.client_id_prefix is None or policy.default_scope is None:
            raise RuntimeError(f"DCR policy is not configured for category {category}")

        redirect_uris = _validate_redirect_uris(registration.redirect_uris)
        grant_types = _resolve_grant_types(registration.grant_types, policy.allowed_grant_types)
        requested_scope = registration.scope or policy.default_scope
        token_endpoint_auth_method = self._resolve_auth_method(registration)
        client_secret = secrets.token_urlsafe(32) if token_endpoint_auth_method == CLIENT_SECRET_POST_METHOD else None
        client_id = f"{policy.client_id_prefix}{secrets.token_urlsafe(16)}"
        issued_at = int(time.time())
        metadata = {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": issued_at,
            "client_secret_expires_at": NON_EXPIRING_CREDENTIAL,
            "client_name": registration.client_name or default_client_name,
            "client_uri": registration.client_uri,
            "redirect_uris": redirect_uris,
            "grant_types": grant_types,
            "response_types": ["code"],
            "scope": requested_scope,
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "contacts": registration.contacts or [],
            "registered_at": issued_at,
            "ip_address": ip_address,
        }
        self._store.save_client(client_id, metadata)
        logger.info("Registered new OAuth client: client_id=%s, name=%s", client_id, metadata["client_name"])
        return ClientRegistrationResponse.model_validate(metadata)

    @staticmethod
    def _resolve_auth_method(registration: ClientRegistrationRequest) -> str:
        requested = registration.token_endpoint_auth_method
        if requested in SUPPORTED_TOKEN_ENDPOINT_AUTH_METHODS:
            return requested
        logger.warning(
            "DCR client requested unsupported token_endpoint_auth_method=%s; substituting 'none'. client_name=%s",
            requested,
            registration.client_name,
        )
        return "none"
