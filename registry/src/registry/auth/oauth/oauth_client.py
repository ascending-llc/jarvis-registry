"""OAuth client using Authlib for OAuth 2.0 operations with PKCE support."""

import logging
import secrets
from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from ...core.config import settings
from ...schemas.oauth_schema import (
    MCPOAuthFlowMetadata,
    OAuthClientInformation,
    OAuthMetadata,
    OAuthProtectedResourceMetadata,
    OAuthTokens,
)

logger = logging.getLogger(__name__)


class OAuthClient:
    """OAuth client using Authlib for RFC-compliant OAuth 2.0 operations."""

    def __init__(self):
        """Initialize OAuth client."""
        self._clients: dict[str, AsyncOAuth2Client] = {}

    def generate_code_verifier(self) -> str:
        """Generate PKCE code_verifier using secure random generator."""
        return secrets.token_urlsafe(32)

    def generate_code_challenge(self, code_verifier: str) -> str:
        """Generate PKCE code_challenge from code_verifier using S256 method."""
        return create_s256_code_challenge(code_verifier)

    @staticmethod
    def _select_auth_method(supported_methods: list[str] | None) -> str:
        """
        Select the token endpoint auth method from the server's advertised list.

        Prefers client_secret_post over client_secret_basic for maximum compatibility
        with third-party MCP servers (e.g. HubSpot) that only accept POST body credentials.
        Defaults to client_secret_post when no methods are advertised.
        """
        methods = supported_methods or []
        if "client_secret_post" in methods:
            return "client_secret_post"
        if "client_secret_basic" in methods:
            return "client_secret_basic"
        return "client_secret_post"  # safe default when server does not advertise methods

    def _get_client(self, flow_metadata: MCPOAuthFlowMetadata, code_verifier: str | None = None) -> AsyncOAuth2Client:
        """
        Create Authlib OAuth2 client for the flow.

        Args:
            flow_metadata: OAuth flow metadata
            code_verifier: PKCE code verifier (required for token exchange)

        Returns:
            AsyncOAuth2Client configured for the flow
        """
        client_info = flow_metadata.client_info
        metadata = flow_metadata.metadata

        supported_methods = metadata.token_endpoint_auth_methods_supported
        auth_method = self._select_auth_method(supported_methods)
        logger.debug(f"Token endpoint auth method selected: {auth_method} (supported: {supported_methods})")

        # Create Authlib client
        client = AsyncOAuth2Client(
            client_id=client_info.client_id,
            client_secret=client_info.client_secret,
            redirect_uri=client_info.redirect_uris[0] if client_info.redirect_uris else None,
            scope=client_info.scope,
            token_endpoint=metadata.token_endpoint,
            token_endpoint_auth_method=auth_method,
            code_challenge_method="S256",
            timeout=30.0,
        )

        # Store code_verifier for token exchange
        if code_verifier:
            client.code_verifier = code_verifier

        return client

    async def build_authorization_url(
        self, flow_metadata: MCPOAuthFlowMetadata, code_challenge: str, flow_id: str
    ) -> str:
        """
        Build OAuth authorization URL with PKCE.

        Args:
            flow_metadata: OAuth flow metadata
            code_challenge: PKCE code challenge
            flow_id: Flow identifier (for logging)

        Returns:
            Authorization URL with all required parameters
        """
        client_info = flow_metadata.client_info
        metadata = flow_metadata.metadata
        state = flow_metadata.state

        # Create temporary client for URL generation
        client = self._get_client(flow_metadata)

        # Prepare authorization URL parameters
        params = {
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Add additional parameters from config
        if client_info.additional_params:
            params.update(client_info.additional_params)

        # Add resource parameter for RFC 8707 Resource Indicator support
        if flow_metadata.resource_metadata and flow_metadata.resource_metadata.resource:
            params["resource"] = flow_metadata.resource_metadata.resource

        # Build authorization URL using Authlib
        authorization_url, _ = client.create_authorization_url(
            metadata.authorization_endpoint,
            **params,
        )

        logger.debug(f"Built authorization URL: {authorization_url}")
        return authorization_url

    async def exchange_code_for_tokens(
        self, flow_metadata: MCPOAuthFlowMetadata, authorization_code: str
    ) -> OAuthTokens | None:
        """
        Exchange authorization code for access tokens using PKCE.

        Args:
            flow_metadata: OAuth flow metadata with code_verifier
            authorization_code: Authorization code from OAuth provider

        Returns:
            OAuthTokens if successful, None otherwise
        """
        try:
            if not flow_metadata.metadata or not flow_metadata.client_info:
                logger.error("Missing metadata or client info for token exchange")
                return None

            token_url = flow_metadata.metadata.token_endpoint
            if not token_url:
                logger.error("No token endpoint in metadata")
                return None

            logger.debug(f"Exchanging code for tokens at {token_url}")

            # Create client with code_verifier for PKCE
            client = self._get_client(flow_metadata, flow_metadata.code_verifier)

            # Additional parameters from config
            extra_params = {}
            if flow_metadata.client_info.additional_params:
                extra_params.update(flow_metadata.client_info.additional_params)

            # Add resource parameter for RFC 8707 Resource Indicator support
            if flow_metadata.resource_metadata and flow_metadata.resource_metadata.resource:
                extra_params["resource"] = flow_metadata.resource_metadata.resource

            # Exchange code for tokens using Authlib
            # Authlib automatically handles PKCE, client authentication, and token response parsing
            token_response = await client.fetch_token(
                token_url,
                code=authorization_code,
                code_verifier=flow_metadata.code_verifier,
                **extra_params,
            )

            logger.info("Token exchange successful")

            return OAuthTokens(
                access_token=token_response.get("access_token"),
                token_type=token_response.get("token_type", "Bearer"),
                expires_in=token_response.get("expires_in"),
                refresh_token=token_response.get("refresh_token"),
                scope=token_response.get("scope"),
                expires_at=token_response.get("expires_at"),  # Authlib calculates this automatically
            )

        except Exception as e:
            logger.error(f"Failed to exchange code for tokens: {e}", exc_info=True)
            return None
        finally:
            # Close the client connection
            if "client" in locals():
                await client.aclose()

    async def refresh_tokens(self, oauth_config: dict[str, Any], refresh_token: str) -> OAuthTokens | None:
        """
        Refresh OAuth tokens using refresh token.

        Args:
            oauth_config: OAuth configuration from MongoDB
            refresh_token: Current refresh token

        Returns:
            New OAuthTokens if successful, None otherwise
        """
        try:
            token_url = oauth_config.get("token_url")
            if not token_url:
                logger.error("No token URL for refresh")
                return None

            # Determine client authentication method
            auth_methods = oauth_config.get("token_endpoint_auth_methods_supported")
            auth_method = self._select_auth_method(auth_methods)

            # Handle scope format (list or string)
            scopes = oauth_config.get("scope")
            if isinstance(scopes, list):
                scope_str = " ".join(scopes)
            else:
                scope_str = scopes

            # Create temporary client for token refresh
            client = AsyncOAuth2Client(
                client_id=oauth_config.get("client_id", ""),
                client_secret=oauth_config.get("client_secret"),
                token_endpoint=token_url,
                token_endpoint_auth_method=auth_method,
                scope=scope_str,
                timeout=30.0,
            )

            logger.debug(f"Refreshing tokens at {token_url}")

            # Refresh tokens using Authlib
            # Authlib automatically handles client authentication and token response parsing
            token_response = await client.refresh_token(token_url, refresh_token=refresh_token)

            logger.info("Token refresh successful")

            return OAuthTokens(
                access_token=token_response.get("access_token"),
                token_type=token_response.get("token_type", "Bearer"),
                expires_in=token_response.get("expires_in"),
                refresh_token=token_response.get("refresh_token", refresh_token),  # Keep old if not rotated
                scope=token_response.get("scope"),
                expires_at=token_response.get("expires_at"),  # Authlib calculates this automatically
            )

        except Exception as e:
            logger.error(f"Failed to refresh tokens: {e}", exc_info=True)
            return None
        finally:
            # Close the client connection
            if "client" in locals():
                await client.aclose()

    async def fetch_client_credentials_token(
        self,
        *,
        token_endpoint: str,
        client_id: str,
        client_secret: str | None,
        scope: str | None = None,
        audience: str | None = None,
    ) -> OAuthTokens | None:
        """
        Acquire an OAuth access token using the client_credentials grant.
        """
        try:
            client = AsyncOAuth2Client(
                client_id=client_id,
                client_secret=client_secret,
                token_endpoint=token_endpoint,
                token_endpoint_auth_method="client_secret_post" if client_secret else "none",
                scope=scope,
                timeout=30.0,
            )
            extra_params: dict[str, Any] = {}
            if scope:
                extra_params["scope"] = scope
            if audience:
                extra_params["audience"] = audience

            token_response = await client.fetch_token(
                token_endpoint,
                grant_type="client_credentials",
                **extra_params,
            )
            return OAuthTokens(
                access_token=token_response.get("access_token"),
                token_type=token_response.get("token_type", "Bearer"),
                expires_in=token_response.get("expires_in"),
                refresh_token=token_response.get("refresh_token"),
                scope=token_response.get("scope"),
                expires_at=token_response.get("expires_at"),
            )
        except Exception as e:
            logger.error(f"Failed to fetch client_credentials token: {e}", exc_info=True)
            return None
        finally:
            if "client" in locals():
                await client.aclose()

    async def close(self):
        """Close all HTTP clients."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def register_client(
        self,
        server_url: str,
        metadata: OAuthMetadata,
        resource_metadata: OAuthProtectedResourceMetadata | None = None,
        redirect_uri: str | None = None,
        token_exchange_method: str | None = None,
    ) -> OAuthClientInformation:
        """
        Dynamically register OAuth client (RFC 7591).

        Args:
            server_url: Authorization server URL
            metadata: OAuth server metadata
            resource_metadata: Protected resource metadata (optional)
            redirect_uri: Redirect URI for callbacks
            token_exchange_method: Preferred token endpoint auth method

        Returns:
            OAuthClientInformation with registered client_id and client_secret

        Raises:
            ValueError: If registration_endpoint is not available
            httpx.HTTPError: If registration request fails
        """
        if not metadata.registration_endpoint:
            raise ValueError(f"Server {server_url} does not support dynamic client registration")

        logger.info(f"[DCR] Registering OAuth client for {server_url}")

        # Build client metadata
        client_metadata = {
            "client_name": settings.registry_app_name,
            "redirect_uris": [redirect_uri] if redirect_uri else [],
            "grant_types": self._negotiate_grant_types(metadata),
            "response_types": metadata.response_types_supported or ["code"],
            "token_endpoint_auth_method": self._negotiate_auth_method(metadata, token_exchange_method),
            "scope": self._build_scope(metadata, resource_metadata),
        }

        logger.debug(f"[DCR] Client metadata: {client_metadata}")

        # Make registration request
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            response = await client.post(
                metadata.registration_endpoint,
                json=client_metadata,
                headers=headers,
            )

            if not response.is_success:
                error_detail = response.text
                logger.error(f"[DCR] Registration failed: {response.status_code} - {error_detail}")
                raise httpx.HTTPError(f"Client registration failed: {response.status_code} - {error_detail}")

        # Parse response
        registration_response = response.json()

        client_info = OAuthClientInformation(
            client_id=registration_response["client_id"],
            client_secret=registration_response.get("client_secret"),
            redirect_uris=registration_response.get("redirect_uris", [redirect_uri] if redirect_uri else []),
            scope=registration_response.get("scope", client_metadata.get("scope")),
            grant_types=registration_response.get("grant_types", client_metadata["grant_types"]),
            token_endpoint_auth_method=registration_response.get(
                "token_endpoint_auth_method", client_metadata["token_endpoint_auth_method"]
            ),
        )

        logger.info(
            f"[DCR] Client registered successfully: client_id={client_info.client_id}, "
            f"has_secret={bool(client_info.client_secret)}"
        )

        return client_info

    def _negotiate_grant_types(self, metadata: OAuthMetadata) -> list[str]:
        """
        Negotiate grant types based on server support.

        Args:
            metadata: OAuth server metadata

        Returns:
            List of grant types to request
        """
        supported_grants = metadata.grant_types_supported or ["authorization_code"]
        requested_grants = ["authorization_code"]

        if "refresh_token" in supported_grants:
            requested_grants.append("refresh_token")
            logger.debug("[DCR] Server supports refresh_token grant")

        return requested_grants

    def _negotiate_auth_method(self, metadata: OAuthMetadata, preferred_method: str | None = None) -> str:
        """
        Negotiate token endpoint authentication method.

        Priority:
        1. Use preferred_method if specified and supported
        2. Prefer client_secret_basic (most secure)
        3. Fallback to client_secret_post
        4. Last resort: none (public client)

        Args:
            metadata: OAuth server metadata
            preferred_method: Preferred authentication method

        Returns:
            Token endpoint authentication method
        """
        supported = metadata.token_endpoint_auth_methods_supported or []

        if preferred_method and preferred_method in supported:
            return preferred_method

        if "client_secret_basic" in supported:
            return "client_secret_basic"
        if "client_secret_post" in supported:
            return "client_secret_post"
        if "none" in supported:
            return "none"

        # Default if server doesn't advertise methods
        return "client_secret_basic"

    def _build_scope(
        self, metadata: OAuthMetadata, resource_metadata: OAuthProtectedResourceMetadata | None
    ) -> str | None:
        """
        Build scope string from metadata.

        Args:
            metadata: OAuth server metadata
            resource_metadata: Protected resource metadata (optional)

        Returns:
            Space-separated scope string or None
        """
        available_scopes = (
            resource_metadata.scopes_supported
            if resource_metadata and resource_metadata.scopes_supported
            else metadata.scopes_supported
        )

        if available_scopes:
            return " ".join(available_scopes)

        return None
