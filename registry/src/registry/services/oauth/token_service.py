import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models import IUser, Token

from ...schemas.enums import TokenType
from ...schemas.oauth_schema import OAuthClientInformation, OAuthTokens
from ...services.user_service import UserService
from ...utils.crypto_utils import decrypt_auth_fields, encrypt_value

logger = logging.getLogger(__name__)


class TokenService:
    def __init__(self, user_service: UserService):
        self.user_service = user_service

    async def get_user(self, user_id: str) -> IUser | None:
        user = await self.user_service.get_user_by_user_id(user_id)
        if not user:
            raise Exception(f"OAuth operation failed: User {user_id} not found")
        return user

    async def get_user_by_user_id(self, user_id: str) -> str:
        user = await self.get_user(user_id)
        return str(user.id)

    def _get_access_identifier(self, service_name: str) -> str:
        """Build access token identifier"""
        return f"mcp:{service_name}"

    def _get_client_identifier(self, service_name: str) -> str:
        """
        Deprecated: Use _get_access_identifier instead.
        This method is kept for backward compatibility.
        """
        return self._get_access_identifier(service_name)

    def _get_refresh_identifier(self, service_name: str) -> str:
        """Build refresh token identifier"""
        return f"mcp:{service_name}:refresh"

    def _get_client_creds_identifier(self, service_name: str) -> str:
        """Build client credentials identifier"""
        return f"mcp:{service_name}:client"

    async def store_oauth_access_token(
        self, user_id: str, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any] | None = None
    ) -> Token:
        """
        Store OAuth access token

        Args:
            user_id: User ID
            service_name: Service name (e.g., notion, github, etc.)
            tokens: OAuth tokens object
            metadata: Additional metadata (e.g., OAuth configuration)

        Returns:
            Created or updated Token document

        Raises:
            ValueError: If tokens.access_token is None
        """
        if not tokens.access_token:
            raise ValueError(
                f"Cannot store access token: access_token is missing for user={user_id}, service={service_name}"
            )

        identifier = self._get_access_identifier(service_name)
        user = await self.get_user(user_id)
        user_obj_id = str(user.id)

        # Calculate expiration time
        expires_at = self._calculate_expiration(tokens.expires_in)

        # Check if token exists
        existing_token = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_ACCESS.value,
                "identifier": identifier,
            }
        )

        if existing_token:
            # Update existing token
            existing_token.token = tokens.access_token
            existing_token.expiresAt = expires_at
            if metadata:
                existing_token.metadata = metadata
            existing_token.email = user.email
            await existing_token.save()
            logger.info(f"Updated OAuth access token for user={user_id}, service={service_name}")
            return existing_token
        else:
            # Create new token
            token_doc = Token(
                userId=PydanticObjectId(user_obj_id),
                type=TokenType.MCP_OAUTH_ACCESS.value,
                identifier=identifier,
                token=tokens.access_token,
                expiresAt=expires_at,
                metadata=metadata or {},
                email=user.email,
            )
            await token_doc.insert()
            logger.info(f"Created OAuth access token for user={user_id}, service={service_name}")
            return token_doc

    async def store_oauth_client_token(
        self, user_id: str, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any] | None = None
    ) -> Token:
        """
        Deprecated: Use store_oauth_access_token instead.
        This method is kept for backward compatibility.
        """
        return await self.store_oauth_access_token(user_id, service_name, tokens, metadata)

    async def store_oauth_refresh_token(
        self, user_id: str, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any] | None = None
    ) -> Token | None:
        """
        Store OAuth refresh token

        Args:
            user_id: User ID
            service_name: Service name
            tokens: OAuth tokens object
            metadata: Additional metadata
        """
        if not tokens.refresh_token:
            logger.debug(f"No refresh token provided for user={user_id}, service={service_name}")
            return None

        identifier = self._get_refresh_identifier(service_name)
        user = await self.get_user(user_id)
        user_obj_id = str(user.id)

        # Refresh tokens typically have a longer expiration time, set to 1 year here
        # Or set according to OAuth provider configuration
        expires_at = datetime.now(UTC) + timedelta(days=365)

        # Check if token exists
        existing_token = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_REFRESH.value,
                "identifier": identifier,
            }
        )

        if existing_token:
            # Update existing token
            existing_token.token = tokens.refresh_token
            existing_token.expiresAt = expires_at
            if metadata:
                existing_token.metadata = metadata
            existing_token.email = user.email
            await existing_token.save()
            logger.info(f"Updated OAuth refresh token for user={user_id}, service={service_name}")
            return existing_token
        else:
            # Create new token
            token_doc = Token(
                userId=PydanticObjectId(user_obj_id),
                type=TokenType.MCP_OAUTH_REFRESH.value,
                identifier=identifier,
                token=tokens.refresh_token,
                expiresAt=expires_at,
                metadata=metadata or {},
            )
            token_doc.email = user.email
            await token_doc.insert()
            logger.info(f"Created OAuth refresh token for user={user_id}, service={service_name}")
            return token_doc

    async def store_oauth_tokens(
        self, user_id: str, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any] | None = None
    ) -> dict[str, Token | None]:
        """
        Store complete OAuth tokens (access + refresh)

        This is the main storage method, which stores both access token and refresh token

        Args:
            user_id: User ID
            service_name: Service name
            tokens: OAuth tokens object
            metadata: Additional metadata (e.g., OAuth configuration)

        Returns:
            Dictionary containing access and refresh tokens
        """
        try:
            # Store access token
            access_token = await self.store_oauth_access_token(
                user_id=user_id, service_name=service_name, tokens=tokens, metadata=metadata
            )

            # Store refresh token (if exists)
            refresh_token = await self.store_oauth_refresh_token(
                user_id=user_id, service_name=service_name, tokens=tokens, metadata=metadata
            )

            return {"access": access_token, "refresh": refresh_token}

        except Exception as e:
            logger.error(f"Failed to store OAuth tokens: {e}", exc_info=True)
            raise

    async def get_oauth_access_token(self, user_id: str, service_name: str) -> Token | None:
        """
        Get OAuth access token

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Token document or None
        """
        identifier = self._get_access_identifier(service_name)
        user_obj_id = await self.get_user_by_user_id(user_id)

        token = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_ACCESS.value,
                "identifier": identifier,
            }
        )
        logger.debug(f"OAuth access token for user={user_id}, service={service_name}")

        # Check if token is expired
        if token and self._is_token_expired(token):
            logger.info(f"Token expired for user={user_id}, service={service_name}")
            return None

        return token

    async def get_oauth_client_token(self, user_id: str, service_name: str) -> Token | None:
        """
        Deprecated: Use get_oauth_access_token instead.
        This method is kept for backward compatibility.
        """
        return await self.get_oauth_access_token(user_id, service_name)

    async def get_oauth_refresh_token(self, user_id: str, service_name: str) -> Token | None:
        """
        Get OAuth refresh token

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Token document or None
        """
        identifier = self._get_refresh_identifier(service_name)
        user_obj_id = await self.get_user_by_user_id(user_id)

        token = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_REFRESH.value,
                "identifier": identifier,
            }
        )

        # Check if token is expired
        if token and self._is_token_expired(token):
            logger.info(f"Refresh token expired for user={user_id}, service={service_name}")
            return None

        return token

    async def get_oauth_tokens(self, user_id: str, service_name: str) -> OAuthTokens | None:
        """
        Get complete OAuth tokens and convert to OAuthTokens object

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            OAuthTokens object or None (only if both access and refresh tokens are missing)
        """
        access_token = await self.get_oauth_access_token(user_id, service_name)
        refresh_token = await self.get_oauth_refresh_token(user_id, service_name)

        # Return None only if both tokens are missing
        if not access_token and not refresh_token:
            return None

        # Convert to OAuthTokens object
        # Even if access token is missing, we return the refresh token so it can be used to get a new access token
        return OAuthTokens(  # nosec B106 - "Bearer" is token type, not token value
            access_token=access_token.token if access_token else None,
            refresh_token=refresh_token.token if refresh_token else None,
            token_type="Bearer",
            expires_in=self._calculate_expires_in(access_token.expiresAt) if access_token else None,
            expires_at=int(access_token.expiresAt.timestamp()) if access_token and access_token.expiresAt else None,
        )

    async def delete_oauth_tokens(self, user_id: str, service_name: str) -> bool:
        """
        Delete user's OAuth tokens (access, refresh, and client credentials)

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Whether deletion was successful
        """
        user_obj_id = await self.get_user_by_user_id(user_id)

        # Delete access token (mcp_oauth_access)
        access_identifier = self._get_access_identifier(service_name)
        client_result = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_ACCESS.value,
                "identifier": access_identifier,
            }
        )

        # Delete refresh token (mcp_oauth_refresh)
        refresh_identifier = self._get_refresh_identifier(service_name)
        refresh_result = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_REFRESH.value,
                "identifier": refresh_identifier,
            }
        )

        # Delete client credentials (mcp_oauth_client)
        creds_identifier = self._get_client_creds_identifier(service_name)
        creds_result = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_CLIENT.value,
                "identifier": creds_identifier,
            }
        )

        deleted_count = 0
        if client_result:
            await client_result.delete()
            deleted_count += 1

        if refresh_result:
            await refresh_result.delete()
            deleted_count += 1

        if creds_result:
            await creds_result.delete()
            deleted_count += 1

        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} tokens for user={user_id}, service={service_name}")
            return True

        return False

    async def get_user_tokens(self, user_id: str, token_type: TokenType | None = None) -> list[Token]:
        """
        Get all user tokens

        Args:
            user_id: User ID
            token_type: Optional token type filter (TokenType.MCP_OAUTH_CLIENT or TokenType.MCP_OAUTH_REFRESH)

        Returns:
            List of Token documents
        """
        user_obj_id = await self.get_user_by_user_id(user_id)

        query = {"userId": user_obj_id}
        if token_type:
            query["type"] = token_type.value

        tokens = await Token.find(query).to_list()

        # Filter out expired tokens
        valid_tokens = [token for token in tokens if not self._is_token_expired(token)]

        return valid_tokens

    async def cleanup_expired_tokens(self) -> int:
        """
        Clean up all expired tokens

        Returns:
            Number of tokens cleaned up
        """
        now = datetime.now(UTC)

        # Find all expired tokens
        expired_tokens = await Token.find({"expiresAt": {"$lt": now}}).to_list()

        count = len(expired_tokens)

        # Delete expired tokens
        for token in expired_tokens:
            await token.delete()

        if count > 0:
            logger.info(f"Cleaned up {count} expired tokens")

        return count

    async def is_access_token_expired(self, user_id: str, service_name: str) -> bool:
        """
        Check if access token is expired or missing

        Returns:
            True if expired/missing, False if valid
        """
        access_token = await self.get_oauth_access_token(user_id, service_name)

        if not access_token:
            return True

        return self._is_token_expired(access_token)

    async def has_refresh_token(self, user_id: str, service_name: str) -> bool:
        """
        Check if user has a valid refresh token

        Returns:
            True if refresh token exists and not expired
        """
        refresh_token = await self.get_oauth_refresh_token(user_id, service_name)

        if not refresh_token:
            return False

        return not self._is_token_expired(refresh_token)

    async def get_access_token_status(self, user_id: str, service_name: str) -> tuple[Token | None, bool]:
        """
        Get access token and its validity status

        Returns:
            tuple: (token_doc, is_valid)
                - token_doc: Token document or None if not exists
                - is_valid: True if token exists and not expired, False otherwise
        """
        identifier = self._get_access_identifier(service_name)
        user_obj_id = await self.get_user_by_user_id(user_id)

        token = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_ACCESS.value,
                "identifier": identifier,
            }
        )

        if not token:
            return None, False

        is_valid = not self._is_token_expired(token)
        return token, is_valid

    async def get_refresh_token_status(self, user_id: str, service_name: str) -> tuple[Token | None, bool]:
        """
        Get refresh token and its validity status

        Returns:
            tuple: (token_doc, is_valid)
                - token_doc: Token document or None if not exists
                - is_valid: True if token exists and not expired, False otherwise
        """
        identifier = self._get_refresh_identifier(service_name)
        user_obj_id = await self.get_user_by_user_id(user_id)

        token = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_REFRESH.value,
                "identifier": identifier,
            }
        )

        if not token:
            return None, False

        is_valid = not self._is_token_expired(token)
        return token, is_valid

    def _calculate_expiration(self, expires_in: int | None) -> datetime:
        """
        Calculate token expiration time

        Args:
            expires_in: Expiration time (in seconds)

        Returns:
            datetime object of expiration time
        """
        if not expires_in:
            # Default to 1 hour
            expires_in = 3600

        return datetime.now(UTC) + timedelta(seconds=expires_in)

    def _calculate_expires_in(self, expires_at: datetime) -> int:
        """
        Calculate remaining valid time

        Args:
            expires_at: Expiration time

        Returns:
            Remaining seconds
        """
        now = datetime.now(UTC)

        # Ensure expires_at is timezone-aware
        if expires_at.tzinfo is None:
            # If timezone-naive, assume UTC
            expires_at = expires_at.replace(tzinfo=UTC)

        delta = expires_at - now
        return max(0, int(delta.total_seconds()))

    def _is_token_expired(self, token: Token) -> bool:
        """
        Check if token is expired

        Args:
            token: Token document

        Returns:
            Whether expired
        """
        if not token.expiresAt:
            return False
        now = datetime.now(UTC)

        # If expiresAt is timezone-naive, assume it's UTC and add timezone info
        if token.expiresAt.tzinfo is None:
            expires_at = token.expiresAt.replace(tzinfo=UTC)
        else:
            expires_at = token.expiresAt

        return expires_at <= (now + timedelta(seconds=3))

    async def store_oauth_client_credentials(
        self,
        user_id: str,
        service_name: str,
        client_info: OAuthClientInformation,
        metadata: dict[str, Any],
    ) -> Token:
        """
        Store OAuth client credentials (client_id, client_secret, etc.)

        Args:
            user_id: User ID
            service_name: Service name
            client_info: OAuth client information (client_id, client_secret, etc.)
            metadata: OAuth server metadata (endpoints, issuer, etc.)

        Returns:
            Token document
        """
        identifier = self._get_client_creds_identifier(service_name)
        user = await self.get_user(user_id)
        user_obj_id = str(user.id)

        # 1. Serialize client_info to JSON
        client_info_json = json.dumps(client_info.dict())

        # 2. Encrypt the JSON directly (not using encrypt_auth_fields which only handles specific fields)
        encrypted = encrypt_value(client_info_json)

        # 3. Set 1 year expiry
        expires_at = datetime.now(UTC) + timedelta(days=365)

        # 4. Check if exists
        existing = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_CLIENT.value,
                "identifier": identifier,
            }
        )

        if existing:
            # Update
            existing.token = encrypted
            existing.expiresAt = expires_at
            existing.metadata = metadata
            existing.email = user.email
            await existing.save()
            logger.info(f"Updated OAuth client credentials for user={user_id}, service={service_name}")
            return existing
        else:
            # Create new
            token_doc = Token(
                userId=PydanticObjectId(user_obj_id),
                type=TokenType.MCP_OAUTH_CLIENT.value,
                identifier=identifier,
                token=encrypted,
                expiresAt=expires_at,
                metadata=metadata,
                email=user.email,
            )
            await token_doc.insert()
            logger.info(f"Created OAuth client credentials for user={user_id}, service={service_name}")
            return token_doc

    async def get_oauth_client_credentials(
        self, user_id: str, service_name: str
    ) -> tuple[OAuthClientInformation | None, dict[str, Any] | None]:
        """
        Retrieve OAuth client credentials from MongoDB

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Tuple of (client_info, metadata) or (None, None) if not found
        """
        identifier = self._get_client_creds_identifier(service_name)
        user = await self.get_user(user_id)
        user_obj_id = str(user.id)

        token_doc = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_CLIENT.value,
                "identifier": identifier,
            }
        )

        if not token_doc:
            logger.debug(f"No client credentials found for user={user_id}, service={service_name}")
            return None, None

        # Decrypt
        decrypted = decrypt_auth_fields({"token": token_doc.token})["token"]

        # Parse JSON to OAuthClientInformation
        client_info_dict = json.loads(decrypted)
        client_info = OAuthClientInformation(**client_info_dict)

        logger.debug(f"Retrieved client credentials for user={user_id}, service={service_name}")
        return client_info, token_doc.metadata
