import logging
import time
from typing import Any

import httpx

from registry_pkgs.core.jwt_utils import (
    InvalidTokenError,
    decode_jwt_unverified,
    decode_jwt_with_jwk,
    find_matching_jwk,
    get_token_kid,
)

from ..core.config import settings
from .base import AuthProvider, log_group_resolution_failure

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


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
        graph_url: str | None = None,
        m2m_scope: str | None = None,
        scopes: list | None = None,
        grant_type: str = "authorization_code",
        username_claim: str = "preferred_username",
        groups_claim: str = "groups",
        email_claim: str = "email",
        name_claim: str = "name",
    ):
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
            graph_url: Microsoft Graph API base URL (default: 'https://graph.microsoft.com')
            m2m_scope: Default scope for M2M authentication (default: 'https://graph.microsoft.com/.default')
            scopes: List of OAuth2 scopes (default: ['openid', 'profile', 'email', 'User.Read'])
            grant_type: OAuth2 grant type (default: 'authorization_code')
            username_claim: Claim to use for username (default: 'preferred_username')
            groups_claim: Claim to use for groups (default: 'groups')
            email_claim: Claim to use for email (default: 'email')
            name_claim: Claim to use for display name (default: 'name')
        """
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

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
        self.email_claim = email_claim
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
            f"claims: username={username_claim}, email={email_claim}, groups={groups_claim}, name={name_claim}"
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

    def _extract_user_info_from_token(self, verified_claims: dict[str, Any], token_type: str) -> dict[str, Any] | None:
        """Extract user information from verified JWT claims.

        Args:
            verified_claims: Claims returned by a successful JWT verification.
            token_type: Type of token ('id' or 'access')

        Returns:
            Dict with user info or None if extraction fails
        """
        try:
            logger.debug(f"Extracting user info from {token_type} token")
            logger.debug(f"Verified token claims available: {list(verified_claims.keys())}")

            # Extract username with fallback chain
            username = (
                verified_claims.get(self.username_claim)
                or verified_claims.get("preferred_username")
                or verified_claims.get("upn")
                or verified_claims.get("unique_name")
            )
            # Extract email
            email = verified_claims.get(self.email_claim) or verified_claims.get("upn")
            # Extract name
            name = (
                verified_claims.get(self.name_claim)
                or verified_claims.get("displayName")
                or verified_claims.get("given_name")
            )
            user_info = {
                "username": username,
                "email": email,
                "name": name,
                "id": verified_claims.get("oid") or verified_claims.get("sub"),
                "groups": [],
            }
            logger.info(f"User info extracted from {token_type} token: {username}")
            return user_info

        except Exception as e:
            logger.warning(f"Failed to extract user info from {token_type} token: {e}")
            return None

    async def _fetch_user_info_from_graph(self, access_token: str) -> dict[str, Any]:
        """Fetch user information from Microsoft Graph API.

        Args:
            access_token: OAuth2 access token

        Returns:
            Dict containing user information

        Raises:
            ValueError: If Graph API request fails
        """
        try:
            logger.debug("Fetching user info from Microsoft Graph API")
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient() as client:
                response = await client.get(self.userinfo_url, headers=headers, timeout=10)
                response.raise_for_status()
                graph_data = response.json()
            logger.info(f"User info fetched from Microsoft Graph API: {graph_data}")

            # Map Microsoft Graph response to standard format
            username = graph_data.get(self.username_claim)
            email = graph_data.get(self.email_claim)

            name = graph_data.get(self.name_claim) or graph_data.get("DisplayName")
            user_info = {
                "username": username,
                "email": email,
                "name": name,
                "given_name": graph_data.get("givenName"),
                "family_name": graph_data.get("surname"),
                "id": graph_data.get("id"),
                "job_title": graph_data.get("jobTitle"),
                "office_location": graph_data.get("officeLocation"),
                "groups": [],
            }
            logger.info(f"User info fetched from Microsoft Graph API: {user_info}")
            return user_info

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch user info from Graph API: {e}")
            raise ValueError(f"Graph API request failed: {e}")

    async def get_user_groups(self, access_token: str, identifier: str) -> list:
        """Get user's group memberships from Microsoft Graph API.

        Args:
            access_token: OAuth2 access token
            identifier: Email/username of the user, used only to identify the user in logs
                when the group lookup fails.

        Returns:
            List of group display names
        """
        try:
            logger.debug("Fetching user groups from Graph API")
            headers = {"Authorization": f"Bearer {access_token}"}
            groups_url = (
                f"{self.graph_url}/v1.0/me/transitiveMemberOf/microsoft.graph.group?$count=true&$select=id,displayName"
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(groups_url, headers=headers, timeout=10)
                response.raise_for_status()
                groups_data = response.json()

            # Extract group display names
            groups = [group.get("displayName") for group in groups_data.get("value", [])]
            logger.info(f"Retrieved {groups} groups for user")
            return groups

        except Exception as exc:
            log_group_resolution_failure("entra", identifier, exc)
            return []

    async def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
        """Get user information from token or Microsoft Graph API.

        This method supports flexible user info extraction:
        1. Extract from id_token (preferred) or access_token based on auth-server settings
        2. Fallback to Microsoft Graph API if token extraction fails
        3. Groups are automatically included (fetched from Graph API using access_token)

        Args:
            access_token: OAuth2 access token (required for Graph API calls)
            id_token: Optional ID token (preferred for user identity extraction)

        Returns:
            Dict containing user information with keys:
            - username: User's principal name or email
            - email: User's email address
            - name: User's display name
            - id: User's unique identifier
            - groups: List of group display names (from Graph API)
            - Additional fields from Graph API (if fallback used)
        """
        try:
            token_kind = settings.entra_token_kind.lower()
            user_info = None

            if token_kind == "id" and id_token:
                # Use ID token for user identity
                logger.debug("Extracting user info from ID token")
                verified_claims = await self._verify_user_info_token(id_token)
                user_info = self._extract_user_info_from_token(verified_claims, "id")
            elif token_kind == "access" and access_token:
                #  Use access token
                logger.debug("Extracting user info from access token")
                verified_claims = await self._verify_user_info_token(access_token)
                user_info = self._extract_user_info_from_token(verified_claims, "access")
            else:
                logger.warning(f"Token kind '{token_kind}' not available or token missing, falling back to Graph API")

            # Fallback to Microsoft Graph API if token extraction failed
            if not user_info:
                logger.info("Token extraction failed, using Graph API fallback")
                user_info = await self._fetch_user_info_from_graph(access_token)

            # Get user groups separately using access_token (required for Graph API)
            groups = await self.get_user_groups(
                access_token, user_info.get("email") or user_info.get("username") or "unknown"
            )
            user_info["groups"] = groups

            logger.info(f"User info retrieved: {user_info.get('username')} with {len(groups)} groups")
            return user_info

        except InvalidTokenError:
            raise
        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            raise ValueError(f"User info retrieval failed: {e}")
