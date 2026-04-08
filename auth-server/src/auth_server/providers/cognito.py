"""AWS Cognito authentication provider implementation."""

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.integrations.requests_client import OAuth2Session

from registry_pkgs.core.jwt_utils import (
    ExpiredSignatureError,
    InvalidTokenError,
    decode_jwt_with_jwk,
    get_token_unverified_header,
)

from .base import AuthProvider

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


class CognitoProvider(AuthProvider):
    """AWS Cognito authentication provider implementation."""

    def __init__(self, user_pool_id: str, client_id: str, client_secret: str, region: str, domain: str | None = None):
        """Initialize Cognito provider.

        Args:
            user_pool_id: AWS Cognito User Pool ID
            client_id: OAuth2 client ID
            client_secret: OAuth2 client secret
            region: AWS region
            domain: Optional custom domain name
        """
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.domain = domain

        # Cache for JWKS
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_time: float = 0
        self._jwks_cache_ttl: int = 3600  # 1 hour

        # Cognito endpoints
        if domain:
            self.cognito_domain = f"https://{domain}.auth.{region}.amazoncognito.com"
        else:
            user_pool_id_clean = user_pool_id.replace("_", "")
            self.cognito_domain = f"https://{user_pool_id_clean}.auth.{region}.amazoncognito.com"

        self.token_url = f"{self.cognito_domain}/oauth2/token"
        self.auth_url = f"{self.cognito_domain}/oauth2/authorize"
        self.userinfo_url = f"{self.cognito_domain}/oauth2/userInfo"
        self.jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
        self.logout_url = f"{self.cognito_domain}/logout"
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

        logger.debug(f"Initialized Cognito provider for user pool '{user_pool_id}' in region '{region}'")

    async def validate_token(self, token: str, **kwargs: Any) -> dict[str, Any]:
        """Validate Cognito JWT token."""
        try:
            logger.debug("Validating Cognito JWT token")

            # Get JWKS for validation
            jwks = await self.get_jwks()

            # Decode token header to get key ID
            unverified_header = get_token_unverified_header(token)
            kid = unverified_header.get("kid")

            if not kid:
                raise ValueError("Token missing 'kid' in header")

            # Find matching key
            matching_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    matching_key = key
                    break

            if not matching_key:
                raise ValueError(f"No matching key found for kid: {kid}")

            # Validate and decode token
            claims = decode_jwt_with_jwk(
                token,
                matching_key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.client_id,
            )

            logger.debug(f"Token validation successful for user: {claims.get('username', 'unknown')}")

            # Extract user info from claims
            return {
                "valid": True,
                "username": claims.get("username", claims.get("sub")),
                "email": claims.get("email"),
                "groups": claims.get("cognito:groups", []),
                "scopes": claims.get("scope", "").split() if claims.get("scope") else [],
                "client_id": claims.get("client_id", self.client_id),
                "method": "cognito",
                "data": claims,
            }

        except ExpiredSignatureError:
            logger.warning("Token validation failed: Token has expired")
            raise ValueError("Token has expired")
        except InvalidTokenError as e:
            logger.warning(f"Token validation failed: Invalid token - {e}")
            raise ValueError(f"Invalid token: {e}")
        except Exception as e:
            logger.error(f"Cognito token validation error: {e}")
            raise ValueError(f"Token validation failed: {e}")

    async def get_jwks(self) -> dict[str, Any]:
        """Get JSON Web Key Set from Cognito with caching."""
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
            logger.error(f"Failed to retrieve JWKS from Cognito: {e}")
            raise ValueError(f"Cannot retrieve JWKS: {e}")

    def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token using Authlib."""
        try:
            logger.debug("Exchanging authorization code for token")

            client = OAuth2Session(
                client_id=self.client_id,
                client_secret=self.client_secret,
                token_endpoint=self.token_url,
            )

            token_data = client.fetch_token(
                self.token_url,
                code=code,
                redirect_uri=redirect_uri,
            )

            logger.debug("Token exchange successful")
            return token_data

        except Exception as e:
            logger.error(f"Failed to exchange code for token: {e}")
            raise ValueError(f"Token exchange failed: {e}")

    async def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
        """Get user information from Cognito."""
        try:
            logger.debug("Fetching user info from Cognito")

            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient() as client:
                response = await client.get(self.userinfo_url, headers=headers, timeout=10)
                response.raise_for_status()
                user_info = response.json()

            logger.debug(f"User info retrieved for: {user_info.get('username', 'unknown')}")
            return user_info

        except httpx.HTTPError as e:
            logger.error(f"Failed to get user info: {e}")
            raise ValueError(f"User info retrieval failed: {e}")

    def get_auth_url(self, redirect_uri: str, state: str, scope: str | None = None) -> str:
        """Get Cognito authorization URL."""
        logger.debug(f"Generating auth URL with redirect_uri: {redirect_uri}")

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": scope or "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
        }

        auth_url = f"{self.auth_url}?{urlencode(params)}"
        logger.debug(f"Generated auth URL: {auth_url}")

        return auth_url

    def get_logout_url(self, redirect_uri: str) -> str:
        """Get Cognito logout URL."""
        logger.debug(f"Generating logout URL with redirect_uri: {redirect_uri}")

        params = {"client_id": self.client_id, "logout_uri": redirect_uri}

        logout_url = f"{self.logout_url}?{urlencode(params)}"
        logger.debug(f"Generated logout URL: {logout_url}")

        return logout_url

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token using a refresh token."""
        try:
            logger.debug("Refreshing access token")

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }

            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            async with httpx.AsyncClient() as client:
                response = await client.post(self.token_url, data=data, headers=headers, timeout=10)
                response.raise_for_status()
                token_data = response.json()

            logger.debug("Token refresh successful")
            return token_data

        except httpx.HTTPError as e:
            logger.error(f"Failed to refresh token: {e}")
            raise ValueError(f"Token refresh failed: {e}")

    async def validate_m2m_token(self, token: str) -> dict[str, Any]:
        """Validate a machine-to-machine token."""
        # M2M tokens use the same validation as regular tokens in Cognito
        return await self.validate_token(token)

    async def get_m2m_token(
        self, client_id: str | None = None, client_secret: str | None = None, scope: str | None = None
    ) -> dict[str, Any]:
        """Get machine-to-machine token using client credentials."""
        try:
            logger.debug("Requesting M2M token using client credentials")

            data = {
                "grant_type": "client_credentials",
                "client_id": client_id or self.client_id,
                "client_secret": client_secret or self.client_secret,
            }

            if scope:
                data["scope"] = scope

            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            async with httpx.AsyncClient() as client:
                response = await client.post(self.token_url, data=data, headers=headers, timeout=10)
                response.raise_for_status()
                token_data = response.json()

            logger.debug("M2M token generation successful")
            return token_data

        except httpx.HTTPError as e:
            logger.error(f"Failed to get M2M token: {e}")
            raise ValueError(f"M2M token generation failed: {e}")

    async def get_provider_info(self) -> dict[str, Any]:
        """Get provider-specific information."""
        return {
            "provider_type": "cognito",
            "user_pool_id": self.user_pool_id,
            "region": self.region,
            "client_id": self.client_id,
            "endpoints": {
                "auth": self.auth_url,
                "token": self.token_url,
                "userinfo": self.userinfo_url,
                "jwks": self.jwks_url,
                "logout": self.logout_url,
            },
            "issuer": self.issuer,
        }
