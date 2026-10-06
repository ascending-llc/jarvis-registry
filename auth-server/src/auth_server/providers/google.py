import logging
import time
from typing import Any

import httpx

from registry_pkgs.core.jwt_utils import (
    decode_jwt_with_jwk,
    find_matching_jwk,
    get_token_kid,
)
from registry_pkgs.google.cloud_identity_client import CloudIdentityGroupsClient

from .base import AuthProvider, log_group_resolution_failure

logger = logging.getLogger(__name__)

_VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_JWKS_CACHE_TTL_SECONDS = 3600


def _group_local_part(group_email: str) -> str:
    """Cloud Identity groups are always email-addressed (``groupKey.id``), but scopes.yml's
    group_mappings keys are bare, provider-agnostic role names. Take the local part before
    "@" so a Workspace group like "jarvis-registry-admin@example.com" matches the role name
    "jarvis-registry-admin" regardless of which Workspace domain it was created in.
    """
    return group_email.split("@", 1)[0]


class GoogleEmailNotVerifiedError(ValueError):
    """Raised when a Google id_token's email is not verified."""


class GoogleDomainNotAllowedError(ValueError):
    """Raised when a Google id_token's `hd` claim is outside the allowed domain."""


class GoogleProvider(AuthProvider):
    """Google Workspace OIDC provider (human SSO only — no M2M)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        cloud_identity_client: CloudIdentityGroupsClient,
        auth_url: str,
        token_url: str,
        jwks_url: str,
        allowed_hd: str = "",
        scopes: list[str] | None = None,
        grant_type: str = "authorization_code",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._cloud_identity_client = cloud_identity_client
        self.allowed_hd = allowed_hd
        self.scopes = scopes or ["openid", "email", "profile"]
        self.grant_type = grant_type

        self.auth_url = auth_url
        self.token_url = token_url
        self.jwks_url = jwks_url
        self.valid_issuers = _VALID_ISSUERS

        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_time: float = 0
        self._jwks_cache_ttl: int = _JWKS_CACHE_TTL_SECONDS

        logger.debug(f"Initialized Google provider with scopes={self.scopes}, allowed_hd={allowed_hd or '(any)'}")

    async def get_jwks(self) -> dict[str, Any]:
        """Fetch Google's JWKS with a 1-hour TTL cache (mirrors EntraIdProvider)."""
        current_time = time.time()
        if self._jwks_cache and (current_time - self._jwks_cache_time) < self._jwks_cache_ttl:
            logger.debug("Using cached JWKS")
            return self._jwks_cache

        try:
            logger.debug(f"Fetching JWKS from {self.jwks_url}")
            async with httpx.AsyncClient() as client:
                response = await client.get(self.jwks_url, timeout=10)
                response.raise_for_status()
                self._jwks_cache = response.json()
                self._jwks_cache_time = current_time
            return self._jwks_cache
        except Exception as e:
            logger.error(f"Failed to retrieve JWKS from Google: {e}")
            raise ValueError(f"Cannot retrieve JWKS: {e}")

    async def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Cryptographically verify a Google id_token and return its claims."""
        jwks = await self.get_jwks()
        matching_key = find_matching_jwk(jwks, get_token_kid(id_token))
        return decode_jwt_with_jwk(
            id_token,
            matching_key,
            algorithms=["RS256"],
            issuer=list(self.valid_issuers),
            audience=self.client_id,
        )

    async def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
        """Verify the id_token, enforce the login gate, then resolve groups.

        Returns the same shape as ``EntraIdProvider.get_user_info``:
        ``{"username", "email", "name", "id", "groups"}`` — with ``groups`` set to the
        list of Cloud Identity group **email addresses**.
        """
        if not id_token:
            raise ValueError("Google login requires an id_token")

        claims = await self._verify_id_token(id_token)
        email = claims.get("email")

        # Login gate — enforced before any group lookup.
        if claims.get("email_verified") is not True:
            raise GoogleEmailNotVerifiedError(f"Email not verified for {email}")

        if self.allowed_hd and claims.get("hd") != self.allowed_hd:
            raise GoogleDomainNotAllowedError(
                f"Domain '{claims.get('hd')}' is not the allowed domain '{self.allowed_hd}'"
            )

        try:
            groups = await self._cloud_identity_client.list_transitive_groups_for_member(email)
        except Exception as exc:
            log_group_resolution_failure("google", email, exc)
            groups = []

        return {
            "username": email,
            "email": email,
            "name": claims.get("name"),
            "id": claims.get("sub"),
            "groups": [_group_local_part(g.email) for g in groups],
        }
