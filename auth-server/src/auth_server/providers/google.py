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
    find_matching_jwk,
    get_token_kid,
)
from registry_pkgs.google.cloud_identity_client import CloudIdentityGroupsClient

from .base import AuthProvider

logger = logging.getLogger(__name__)

_VALID_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_JWKS_CACHE_TTL_SECONDS = 3600


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

    async def validate_token(self, token: str, **kwargs: Any) -> dict[str, Any]:
        """Validate a Google id_token and return a standard validation dict."""
        try:
            claims = await self._verify_id_token(token)
            email = claims.get("email")
            return {
                "valid": True,
                "username": email,
                "email": email,
                "groups": [],
                "scopes": [],
                "client_id": claims.get("aud", self.client_id),
                "method": "google",
                "data": claims,
            }
        except ExpiredSignatureError:
            logger.warning("Google token validation failed: token expired")
            raise ValueError("Token has expired")
        except InvalidTokenError as e:
            logger.warning(f"Google token validation failed: {e}")
            raise ValueError(f"Invalid token: {e}")

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

        groups = await self._cloud_identity_client.list_transitive_groups_for_member(email)

        return {
            "username": email,
            "email": email,
            "name": claims.get("name"),
            "id": claims.get("sub"),
            "groups": [g.email for g in groups],
        }

    def get_auth_url(self, redirect_uri: str, state: str, scope: str | None = None) -> str:
        """Build Google's authorization URL, narrowing the account picker via `hd`."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": scope or " ".join(self.scopes),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if self.allowed_hd:
            # UX nicety only — never a substitute for the server-side hd-claim check.
            params["hd"] = self.allowed_hd
        return f"{self.auth_url}?{urlencode(params)}"

    def get_logout_url(self, redirect_uri: str) -> str:
        """Google has no RP-initiated logout endpoint — return the redirect unchanged."""
        return redirect_uri

    def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange an authorization code for tokens via Authlib."""
        try:
            client = OAuth2Session(
                client_id=self.client_id,
                client_secret=self.client_secret,
                token_endpoint=self.token_url,
                scope=" ".join(self.scopes),
            )
            return client.fetch_token(
                self.token_url,
                code=code,
                redirect_uri=redirect_uri,
                grant_type=self.grant_type,
            )
        except Exception as e:
            logger.error(f"Failed to exchange code for token: {e}")
            raise ValueError(f"Token exchange failed: {e}")

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token using a refresh token."""
        try:
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
                return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to refresh token: {e}")
            raise ValueError(f"Token refresh failed: {e}")

    async def get_m2m_token(
        self, client_id: str | None = None, client_secret: str | None = None, scope: str | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError("Google does not support machine-to-machine authentication")

    async def validate_m2m_token(self, token: str) -> dict[str, Any]:
        raise NotImplementedError("Google does not support machine-to-machine authentication")
