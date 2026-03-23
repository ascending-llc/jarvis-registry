import logging
import time
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from authlib.integrations.requests_client import OAuth2Session

from ..core.config import settings
from .base import AuthProvider

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

    def validate_token(self, token: str, **kwargs: Any) -> dict[str, Any]:
        """Validate Entra ID JWT token.

        Args:
            token: The JWT access token to validate
            **kwargs: Additional provider-specific arguments

        Returns:
            Dictionary containing:
                - valid: True if token is valid
                - username: User's preferred_username or sub claim
                - email: User's email address
                - groups: List of Azure AD group Object IDs
                - scopes: List of token scopes
                - client_id: Client ID that issued the token
                - method: 'entra'
                - data: Raw token claims

        Raises:
            ValueError: If token validation fails
        """
        try:
            logger.debug("Validating Entra ID JWT token")

            # Get JWKS for validation
            jwks = self.get_jwks()

            # Decode token header to get key ID
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get("kid")

            if not kid:
                raise ValueError("Token missing 'kid' in header")

            # Find matching key
            signing_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    from jwt import PyJWK

                    signing_key = PyJWK(key).key
                    break

            if not signing_key:
                raise ValueError(f"No matching key found for kid: {kid}")

            # First, decode without validation to check issuer
            unverified_claims = jwt.decode(token, options={"verify_signature": False})
            token_issuer = unverified_claims.get("iss")

            # Check if issuer is valid (v1.0 or v2.0)
            if token_issuer not in self.valid_issuers:
                raise ValueError(f"Invalid issuer: {token_issuer}. Expected one of: {self.valid_issuers}")

            # Validate and decode token with the correct issuer
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=[self.client_id, f"api://{self.client_id}"],
                options={"verify_exp": True, "verify_iat": True, "verify_aud": True},
            )

            # Extract user info from claims using configured claim mappings
            username = claims.get(self.username_claim) or claims.get("sub")  # Fallback to 'sub' as last resort
            email = claims.get(self.email_claim)

            # Extract groups - handle both string and list claims
            groups_raw = claims.get(self.groups_claim, [])
            groups = groups_raw if isinstance(groups_raw, list) else []

            logger.debug(f"Token validation successful for user: {username}")

            return {
                "valid": True,
                "username": username,
                "email": email,
                "groups": groups,
                "scopes": claims.get("scp", "").split() if claims.get("scp") else [],
                "client_id": claims.get("azp", claims.get("appid", self.client_id)),
                "method": "entra",
                "data": claims,
            }

        except jwt.ExpiredSignatureError:
            logger.warning("Token validation failed: Token has expired")
            raise ValueError("Token has expired")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Token validation failed: Invalid token - {e}")
            raise ValueError(f"Invalid token: {e}")
        except Exception as e:
            logger.error(f"Entra ID token validation error: {e}")
            raise ValueError(f"Token validation failed: {e}")

    def get_jwks(self) -> dict[str, Any]:
        """Get JSON Web Key Set from Entra ID with caching."""
        current_time = time.time()
        # Check if cache is still valid
        if self._jwks_cache and (current_time - self._jwks_cache_time) < self._jwks_cache_ttl:
            logger.debug("Using cached JWKS")
            return self._jwks_cache

        try:
            logger.debug(f"Fetching JWKS from {self.jwks_url}")
            response = requests.get(self.jwks_url, timeout=10)
            response.raise_for_status()

            self._jwks_cache = response.json()
            self._jwks_cache_time = current_time

            logger.debug("JWKS fetched and cached successfully")
            return self._jwks_cache

        except Exception as e:
            logger.error(f"Failed to retrieve JWKS from Entra ID: {e}")
            raise ValueError(f"Cannot retrieve JWKS: {e}")

    def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token using Authlib."""
        try:
            logger.debug("Exchanging authorization code for token")

            client = OAuth2Session(
                client_id=self.client_id,
                client_secret=self.client_secret,
                token_endpoint=self.token_url,
                scope=" ".join(self.scopes),
            )

            token_data = client.fetch_token(
                self.token_url,
                code=code,
                redirect_uri=redirect_uri,
                grant_type=self.grant_type,
            )

            logger.debug("Token exchange successful")
            return token_data

        except Exception as e:
            logger.error(f"Failed to exchange code for token: {e}")
            raise ValueError(f"Token exchange failed: {e}")

    def _extract_user_info_from_token(self, token: str, token_type: str) -> dict[str, Any] | None:
        """Extract user information from JWT token.

        Args:
            token: JWT token string
            token_type: Type of token ('id' or 'access')

        Returns:
            Dict with user info or None if extraction fails
        """
        try:
            logger.debug(f"Extracting user info from {token_type} token")
            token_claims = jwt.decode(token, options={"verify_signature": False})
            logger.debug(f"Token claims extracted: {list(token_claims.keys())}")

            # Extract username with fallback chain
            username = (
                token_claims.get(self.username_claim)
                or token_claims.get("preferred_username")
                or token_claims.get("upn")
                or token_claims.get("unique_name")
            )
            # Extract email
            email = token_claims.get(self.email_claim) or token_claims.get("upn")
            # Extract name
            name = (
                token_claims.get(self.name_claim) or token_claims.get("displayName") or token_claims.get("given_name")
            )
            user_info = {
                "username": username,
                "email": email,
                "name": name,
                "id": token_claims.get("oid") or token_claims.get("sub"),
                "groups": [],
            }
            logger.info(f"User info extracted from {token_type} token: {username}")
            return user_info

        except Exception as e:
            logger.warning(f"Failed to extract user info from {token_type} token: {e}")
            return None

    def _fetch_user_info_from_graph(self, access_token: str) -> dict[str, Any]:
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
            response = requests.get(self.userinfo_url, headers=headers, timeout=10)
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

        except requests.RequestException as e:
            logger.error(f"Failed to fetch user info from Graph API: {e}")
            raise ValueError(f"Graph API request failed: {e}")

    def get_user_groups(self, access_token: str) -> list:
        """Get user's group memberships from Microsoft Graph API.

        Args:
            access_token: OAuth2 access token

        Returns:
            List of group display names
        """
        try:
            logger.debug("Fetching user groups from Graph API")
            headers = {"Authorization": f"Bearer {access_token}"}
            groups_url = (
                f"{self.graph_url}/v1.0/me/transitiveMemberOf/microsoft.graph.group?$count=true&$select=id,displayName"
            )
            response = requests.get(groups_url, headers=headers, timeout=10)
            response.raise_for_status()
            groups_data = response.json()

            # Extract group display names
            groups = [group.get("displayName") for group in groups_data.get("value", [])]
            logger.info(f"Retrieved {groups} groups for user")
            return groups

        except Exception as e:
            logger.warning(f"Failed to fetch user groups: {e}")
            return []

    def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
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
                user_info = self._extract_user_info_from_token(id_token, "id")
            elif token_kind == "access" and access_token:
                #  Use access token
                logger.debug("Extracting user info from access token")
                user_info = self._extract_user_info_from_token(access_token, "access")
            else:
                logger.warning(f"Token kind '{token_kind}' not available or token missing, falling back to Graph API")

            # Fallback to Microsoft Graph API if token extraction failed
            if not user_info:
                logger.info("Token extraction failed, using Graph API fallback")
                user_info = self._fetch_user_info_from_graph(access_token)

            # Get user groups separately using access_token (required for Graph API)
            groups = self.get_user_groups(access_token)
            user_info["groups"] = groups

            logger.info(f"User info retrieved: {user_info.get('username')} with {len(groups)} groups")
            return user_info

        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            raise ValueError(f"User info retrieval failed: {e}")

    def get_auth_url(self, redirect_uri: str, state: str, scope: str | None = None) -> str:
        """Get Entra ID authorization URL.

        Args:
            redirect_uri: URI to redirect to after authorization
            state: State parameter for CSRF protection
            scope: Optional scope parameter (defaults to openid email profile)

        Returns:
            Full authorization URL
        """
        logger.debug(f"Generating auth URL with redirect_uri: {redirect_uri}")

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": scope or " ".join(self.scopes),
            "redirect_uri": redirect_uri,
            "state": state,
        }

        auth_url = f"{self.auth_url}?{urlencode(params)}"
        logger.debug(f"Generated auth URL: {auth_url}")

        return auth_url

    def get_logout_url(self, redirect_uri: str) -> str:
        """Get Entra ID logout URL.

        Args:
            redirect_uri: URI to redirect to after logout

        Returns:
            Full logout URL
        """
        logger.debug(f"Generating logout URL with redirect_uri: {redirect_uri}")

        params = {"client_id": self.client_id, "post_logout_redirect_uri": redirect_uri}

        logout_url = f"{self.logout_url}?{urlencode(params)}"
        logger.debug(f"Generated logout URL: {logout_url}")

        return logout_url

    def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token using a refresh token.

        Args:
            refresh_token: The refresh token

        Returns:
            Dictionary containing new token response

        Raises:
            ValueError: If token refresh fails
        """
        try:
            logger.debug("Refreshing access token")

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": " ".join(self.scopes),
            }

            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            response = requests.post(self.token_url, data=data, headers=headers, timeout=10)
            response.raise_for_status()

            token_data = response.json()
            logger.debug("Token refresh successful")

            return token_data

        except requests.RequestException as e:
            logger.error(f"Failed to refresh token: {e}")
            raise ValueError(f"Token refresh failed: {e}")

    def validate_m2m_token(self, token: str) -> dict[str, Any]:
        """Validate a machine-to-machine token.

        Args:
            token: The M2M access token to validate

        Returns:
            Dictionary containing validation result

        Raises:
            ValueError: If token validation fails
        """
        return self.validate_token(token)

    def get_m2m_token(
        self, client_id: str | None = None, client_secret: str | None = None, scope: str | None = None
    ) -> dict[str, Any]:
        """Get machine-to-machine token using client credentials.

        This method is used for AI agent authentication using Azure AD service principals.
        Each AI agent should have its own service principal (app registration) in Azure AD.

        Args:
            client_id: Optional client ID (uses default if not provided)
            client_secret: Optional client secret (uses default if not provided)
            scope: Optional scope for the token (defaults to .default)

        Returns:
            Dictionary containing token response:
                - access_token: The M2M access token
                - token_type: "Bearer"
                - expires_in: Token expiration time in seconds

        Raises:
            ValueError: If token generation fails
        """
        try:
            logger.debug("Requesting M2M token using client credentials")

            # Default scope for Entra ID M2M tokens
            if not scope:
                scope = f"api://{client_id or self.client_id}/.default"

            data = {
                "grant_type": "client_credentials",
                "client_id": client_id or self.client_id,
                "client_secret": client_secret or self.client_secret,
                "scope": scope or self.m2m_scope,
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = requests.post(self.token_url, data=data, headers=headers, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            logger.debug("M2M token generation successful")
            return token_data

        except requests.RequestException as e:
            logger.error(f"Failed to get M2M token: {e}")
            raise ValueError(f"M2M token generation failed: {e}")

    def get_provider_info(self) -> dict[str, Any]:
        """Get provider-specific information.

        Returns:
            Dictionary containing provider configuration and endpoints
        """
        return {
            "provider_type": "entra",
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "endpoints": {
                "auth": self.auth_url,
                "token": self.token_url,
                "userinfo": self.userinfo_url,
                "jwks": self.jwks_url,
                "logout": self.logout_url,
            },
            "issuers": {"v2": self.issuer_v2, "v1": self.issuer_v1},
        }
