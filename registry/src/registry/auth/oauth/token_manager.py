import asyncio
import logging
import time

from registry.schemas.oauth_sachema import OAuthTokens

logger = logging.getLogger(__name__)


class OAuthTokenManager:
    """OAuth token manager"""

    def __init__(self):
        self.tokens: dict[str, dict[str, OAuthTokens]] = {}  # user_id -> {server_id -> tokens}
        self._lock = asyncio.Lock()

    async def store_tokens(self, user_id: str, server_id: str, tokens: OAuthTokens) -> None:
        """Store tokens"""
        async with self._lock:
            if user_id not in self.tokens:
                self.tokens[user_id] = {}

            self.tokens[user_id][server_id] = tokens
            logger.debug(f"Stored tokens for {user_id}/{server_id}")

    async def get_tokens(self, user_id: str, server_id: str) -> OAuthTokens | None:
        """Get user's OAuth tokens"""
        async with self._lock:
            if user_id in self.tokens and server_id in self.tokens[user_id]:
                tokens = self.tokens[user_id][server_id]

                # Check if tokens are expired
                if self._are_tokens_expired(tokens):
                    logger.info(f"Tokens expired for {user_id}/{server_id}")
                    del self.tokens[user_id][server_id]
                    return None

                return tokens
            return None

    async def delete_tokens(self, user_id: str, server_id: str) -> bool:
        """Delete user's OAuth tokens"""
        async with self._lock:
            if user_id in self.tokens and server_id in self.tokens[user_id]:
                del self.tokens[user_id][server_id]
                logger.info(f"Deleted tokens for {user_id}/{server_id}")
                return True
            return False

    async def get_all_user_tokens(self, user_id: str) -> dict[str, OAuthTokens]:
        """Get all tokens for user"""
        async with self._lock:
            if user_id in self.tokens:
                # Filter out expired tokens
                valid_tokens = {}
                for server_id, tokens in self.tokens[user_id].items():
                    if not self._are_tokens_expired(tokens):
                        valid_tokens[server_id] = tokens
                    else:
                        logger.debug(f"Filtered expired tokens for {user_id}/{server_id}")

                # Update storage, remove expired tokens
                self.tokens[user_id] = valid_tokens
                return valid_tokens
            return {}

    async def refresh_tokens(self, user_id: str, server_id: str, new_tokens: OAuthTokens) -> bool:
        """Refresh tokens"""
        async with self._lock:
            if user_id not in self.tokens:
                self.tokens[user_id] = {}

            self.tokens[user_id][server_id] = new_tokens
            logger.info(f"Refreshed tokens for {user_id}/{server_id}")
            return True

    def _are_tokens_expired(self, tokens: OAuthTokens) -> bool:
        """Check if tokens are expired"""
        if not tokens.expires_at:
            return False

        # Add 5-second buffer to avoid edge cases
        buffer_time = 5
        return tokens.expires_at < (time.time() - buffer_time)

    async def cleanup_expired_tokens(self) -> int:
        """Cleanup all expired tokens, returns count cleaned"""
        async with self._lock:
            cleaned_count = 0
            users_to_remove = []

            for user_id, server_tokens in self.tokens.items():
                servers_to_remove = []

                for server_id, tokens in server_tokens.items():
                    if self._are_tokens_expired(tokens):
                        servers_to_remove.append(server_id)
                        cleaned_count += 1

                # Delete expired server tokens
                for server_id in servers_to_remove:
                    del server_tokens[server_id]

                # If user has no tokens, mark for removal
                if not server_tokens:
                    users_to_remove.append(user_id)

            # Delete users with no tokens
            for user_id in users_to_remove:
                del self.tokens[user_id]

            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} expired tokens")

            return cleaned_count
