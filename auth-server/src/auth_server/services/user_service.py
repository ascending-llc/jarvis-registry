"""
User service for auth server - handles user lookups from MongoDB.
"""

import logging

from registry_pkgs.models import IUser

logger = logging.getLogger(__name__)


class UserService:
    """Service for user-related operations in auth server."""

    async def resolve_user_id(self, user_info: dict) -> str | None:
        """
        Resolve user_id from MongoDB by looking up user by username first, then email.

        Args:
            user_info: Dictionary containing user information with 'username' and/or 'email' fields

        Returns:
            user_id as string if found, None otherwise
        """
        try:
            username = user_info.get("username")
            email = user_info.get("email")

            if not username and not email:
                logger.warning("No username or email found in user_info")
                return None

            # Try username first
            if username:
                user = await IUser.find_one({"username": username})
                if user:
                    logger.debug(f"✓ Resolved user_id from MongoDB by username: {user.id} for username: {username}")
                    return str(user.id)
                logger.debug(f"User not found by username: {username}, trying email...")

            # Then try email
            if email:
                user = await IUser.find_one({"email": email})
                if user:
                    logger.debug(f"✓ Resolved user_id from MongoDB by email: {user.id} for email: {email}")
                    return str(user.id)
                logger.warning(f"User not found in MongoDB for username: {username}, email: {email}")

            return None
        except Exception as e:
            logger.error(f"Error resolving user_id from MongoDB: {type(e).__name__}: {e}")
            return None
