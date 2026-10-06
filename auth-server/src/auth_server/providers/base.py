"""Base authentication provider interface."""

import logging
from abc import ABC, abstractmethod
from typing import Any

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def log_group_resolution_failure(provider: str, identifier: str, exc: Exception) -> None:
    """Log an IdP group-source-of-truth outage with one consistent, greppable signature."""
    logger.error(
        "Group resolution failed for provider=%s identifier=%s; proceeding with empty groups: %s",
        provider,
        identifier,
        exc,
        exc_info=True,
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
