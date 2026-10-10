"""Base authentication provider interface."""

import logging
from abc import ABC, abstractmethod
from typing import Any

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def log_group_resolution_failure(provider: str, exc: Exception) -> None:
    """Log group outages without user identifiers, tokens or untrusted response bodies."""
    logger.error(
        "Group resolution failed for provider=%s error_type=%s; proceeding with empty groups",
        provider,
        type(exc).__name__,
    )


class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

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
