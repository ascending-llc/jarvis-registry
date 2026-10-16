import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, StrictStr

from registry_pkgs.core.jwt_utils import (
    decode_jwt_unverified,
    decode_jwt_with_jwk,
    find_matching_jwk,
    get_token_kid,
)
from registry_pkgs.core.scope_groups import scope_groups_for_entra_ids

from .base import AuthProvider, log_group_resolution_failure

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


class _GroupMembershipResponse(BaseModel):
    value: list[StrictStr]


class EntraIdProvider(AuthProvider):
    """Microsoft Entra ID authentication provider implementation."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        auth_url: str,
        token_url: str,
        jwks_url: str,
        logout_url: str,
        userinfo_url: str,
        scope_group_ids: dict[str, str],
        graph_url: str | None = None,
        m2m_scope: str | None = None,
        scopes: list | None = None,
        grant_type: str = "authorization_code",
        username_claim: str = "preferred_username",
        groups_claim: str = "groups",
        name_claim: str = "name",
    ) -> None:
        """Initialize Entra ID provider.

        Args:
            tenant_id: Azure AD tenant ID (or 'common' for multi-tenant)
            client_id: Azure AD application (client) ID
            client_secret: Azure AD client secret
            auth_url: Authorization endpoint URL
            token_url: Token endpoint URL
            jwks_url: JWKS endpoint URL
            logout_url: Logout endpoint URL
            userinfo_url: User info endpoint URL
            scope_group_ids: Configured group object IDs mapped to abstract scope groups.
            graph_url: Microsoft Graph API base URL (default: 'https://graph.microsoft.com')
            m2m_scope: Default scope for M2M authentication (default: 'https://graph.microsoft.com/.default')
            scopes: List of OAuth2 scopes (default: ['openid', 'profile', 'email', 'User.Read'])
            grant_type: OAuth2 grant type (default: 'authorization_code')
            username_claim: Claim to use for username (default: 'preferred_username')
            groups_claim: Claim to use for groups (default: 'groups')
            name_claim: Claim to use for display name (default: 'name')
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope_group_ids = dict(scope_group_ids)

        # Cache for JWKS
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_time: float = 0
        self._jwks_cache_ttl: int = 3600  # 1 hour

        # Microsoft Entra ID endpoints - from configuration
        base_url = f"https://login.microsoftonline.com/{tenant_id}"
        self.auth_url = auth_url
        self.token_url = token_url
        self.jwks_url = jwks_url
        self.logout_url = logout_url
        self.userinfo_url = userinfo_url
        self.graph_url = graph_url or "https://graph.microsoft.com"
        self.m2m_scope = m2m_scope or f"{self.graph_url}/.default"
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"

        # OAuth2 configuration - injected via constructor
        self.scopes = scopes or ["openid", "profile", "email", "User.Read"]
        self.grant_type = grant_type

        # Claim mappings configuration
        self.username_claim = username_claim
        self.groups_claim = groups_claim
        self.name_claim = name_claim

        # Entra ID supports two issuer formats:
        # v2.0 endpoint: https://login.microsoftonline.com/{tenant}/v2.0
        # v1.0/M2M endpoint: https://sts.windows.net/{tenant}/
        self.issuer_v2 = f"{base_url}/v2.0"
        self.issuer_v1 = f"https://sts.windows.net/{tenant_id}/"
        self.valid_issuers = [self.issuer_v2, self.issuer_v1]

        logger.debug(
            f"Initialized Entra ID provider for tenant '{tenant_id}' with "
            f"scopes={self.scopes}, grant_type={self.grant_type}, graph_url={self.graph_url}, "
            f"claims: username={username_claim}, groups={groups_claim}, name={name_claim}"
        )

    async def get_jwks(self) -> dict[str, Any]:
        """Get JSON Web Key Set from Entra ID with caching."""
        current_time = time.time()
        # Check if cache is still valid
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

            logger.debug("JWKS fetched and cached successfully")
            return self._jwks_cache

        except Exception as e:
            logger.error(f"Failed to retrieve JWKS from Entra ID: {e}")
            raise ValueError(f"Cannot retrieve JWKS: {e}")

    async def _verify_user_info_token(self, token: str) -> dict[str, Any]:
        """Verify a JWT before using it as an identity source."""
        jwks = await self.get_jwks()
        matching_key = find_matching_jwk(jwks, get_token_kid(token))

        # Unverified issuer inspection is only used to select the expected issuer
        # before the cryptographic verification below.
        unverified_issuer = decode_jwt_unverified(token).get("iss")
        issuer = unverified_issuer if unverified_issuer in self.valid_issuers else self.issuer

        return decode_jwt_with_jwk(
            token,
            matching_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=[self.client_id, f"api://{self.client_id}"],
        )

    def _extract_user_info_from_token(self, claims: dict[str, Any]) -> dict[str, Any]:
        """Extract stable identity fields only from successfully verified ID-token claims."""
        for field in ("iss", "sub", "oid"):
            value = claims.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Entra ID token requires a non-empty {field}")

        email = claims.get("email")
        if email is not None and not isinstance(email, str):
            raise ValueError("Entra ID token email must be a string")
        if email is not None:
            email = email.strip().lower() or None
        username = (
            claims.get(self.username_claim)
            or claims.get("preferred_username")
            or claims.get("upn")
            or claims.get("unique_name")
        )
        return {
            "username": username,
            "email": email,
            "name": claims.get(self.name_claim) or claims.get("displayName") or claims.get("given_name"),
            "id": claims["oid"],
            "groups": [],
            "issuer": claims["iss"],
            "subject": claims["sub"],
            "object_id": claims["oid"],
            "email_trusted": claims.get("xms_edov") is True,
        }

    async def get_scope_groups(self, access_token: str) -> list[str]:
        """Check transitive membership of the configured IDs, never trusting display names."""
        try:
            logger.debug("Checking configured scope-group membership with Graph API")
            headers = {"Authorization": f"Bearer {access_token}"}
            groups_url = f"{self.graph_url}/v1.0/me/checkMemberGroups"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    groups_url, headers=headers, json={"groupIds": list(self.scope_group_ids)}, timeout=10
                )
                response.raise_for_status()
                membership = _GroupMembershipResponse.model_validate(response.json())
            return scope_groups_for_entra_ids(membership.value, self.scope_group_ids)

        except Exception as exc:  # Fail closed here so callback's generic userinfo fallback cannot grant scopes.
            log_group_resolution_failure("entra", exc)
            return []

    async def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
        """Verify the ID-token identity, then resolve scope groups using the Graph access token."""
        if not id_token:
            raise ValueError("Entra login requires an id_token")

        claims = await self._verify_user_info_token(id_token)
        user_info = self._extract_user_info_from_token(claims)
        user_info["groups"] = await self.get_scope_groups(access_token)
        return user_info
