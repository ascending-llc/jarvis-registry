"""Base authentication provider interface."""

import logging
from abc import ABC, abstractmethod
from typing import Any

# Get logger - logging is configured centrally in server.py via settings.configure_logging()
logger = logging.getLogger(__name__)


def log_group_resolution_failure(provider: str, exc: Exception) -> None:
    """Log an IdP group-source-of-truth outage with one consistent, greppable signature."""
    logger.error(
        "Group resolution failed for provider=%s; proceeding with empty groups: %s",
        provider,
        exc,
        exc_info=exc,
    )


class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

    @abstractmethod
    async def get_user_info(self, access_token: str, id_token: str | None = None) -> dict[str, Any]:
        """Return verified identity information and abstract scope groups.

        Args:
            access_token: OAuth2 access token (required for Graph API calls)
            id_token: ID token required for provider identity verification

        Returns:
            Dictionary containing user information:
                - username: Display/login name; never an identity lookup key
                - email: Normalized email, or None
                - name: Display name
                - id: Upstream identifier (Entra oid or Google sub)
                - groups: Resolved abstract scope groups
                - issuer: Verified identity issuer
                - subject: Verified identity subject
                - object_id: Entra oid, or None for Google
                - email_trusted: Whether provider-specific email trust checks passed

        Raises:
            ValueError: If user info cannot be retrieved
        """
        pass
