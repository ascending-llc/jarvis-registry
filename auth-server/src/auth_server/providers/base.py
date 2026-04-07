"""Base authentication provider interface."""

import logging
from abc import ABC, abstractmethod
from typing import Any

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

    @abstractmethod
    async def validate_token(self, token: str, **kwargs: Any) -> dict[str, Any]:
        """Validate an access token and return user info.

        Args:
            token: The access token to validate
            **kwargs: Additional provider-specific arguments

        Returns:
            Dictionary containing:
                - valid: Boolean indicating if token is valid
                - username: User's username
                - email: User's email address
                - groups: List of group memberships
                - scopes: List of token scopes
                - client_id: Client ID that issued the token
                - method: Authentication method used
                - data: Raw token claims/data

        Raises:
            ValueError: If token validation fails
        """
        pass

    @abstractmethod
    async def get_jwks(self) -> dict[str, Any]:
        """Get JSON Web Key Set for token validation.

        Returns:
            Dictionary containing the JWKS data

        Raises:
            ValueError: If JWKS cannot be retrieved
        """
        pass

    @abstractmethod
    def exchange_code_for_token(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from OAuth2 flow
            redirect_uri: Redirect URI used in the authorization request

        Returns:
            Dictionary containing token response:
                - access_token: The access token
                - id_token: The ID token (if available)
                - refresh_token: The refresh token (if available)
                - token_type: Type of token (usually "Bearer")
                - expires_in: Token expiration time in seconds

        Raises:
            ValueError: If code exchange fails
        """
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
        """Get user information from access token.

        Args:
            access_token: OAuth2 access token (required for Graph API calls)
            id_token: Optional ID token (preferred for user identity extraction)

        Returns:
            Dictionary containing user information:
                - username: User's username
                - email: User's email
                - groups: User's group memberships
                - Additional provider-specific fields

        Raises:
            ValueError: If user info cannot be retrieved
        """
        pass

    @abstractmethod
    def get_auth_url(self, redirect_uri: str, state: str, scope: str | None = None) -> str:
        """Get authorization URL for OAuth2 flow.

        Args:
            redirect_uri: URI to redirect to after authorization
            state: State parameter for CSRF protection
            scope: Optional scope parameter (defaults to provider's default)

        Returns:
            Full authorization URL
        """
        pass

    @abstractmethod
    def get_logout_url(self, redirect_uri: str) -> str:
        """Get logout URL.

        Args:
            redirect_uri: URI to redirect to after logout

        Returns:
            Full logout URL
        """
        pass

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token using a refresh token.

        Args:
            refresh_token: The refresh token

        Returns:
            Dictionary containing new token response

        Raises:
            ValueError: If token refresh fails
        """
        pass

    @abstractmethod
    async def validate_m2m_token(self, token: str) -> dict[str, Any]:
        """Validate a machine-to-machine token.

        Args:
            token: The M2M access token to validate

        Returns:
            Dictionary containing validation result

        Raises:
            ValueError: If token validation fails
        """
        pass

    @abstractmethod
    async def get_m2m_token(
        self, client_id: str | None = None, client_secret: str | None = None, scope: str | None = None
    ) -> dict[str, Any]:
        """Get a machine-to-machine token using client credentials.

        Args:
            client_id: Optional client ID (uses default if not provided)
            client_secret: Optional client secret (uses default if not provided)
            scope: Optional scope for the token

        Returns:
            Dictionary containing token response

        Raises:
            ValueError: If token generation fails
        """
        pass
