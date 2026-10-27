import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure

from registry_pkgs.models import Token, TokenType, User

from ..core.crypto_utils import decrypt_value, encrypt_value
from .errors import OAuthCoordinationError
from .schemas import OAuthTokens
from .user_service import UserService

logger = logging.getLogger(__name__)

# Registry-only token identifiers. Jarvis Chat owns `mcp:<serverName>*` in the shared `tokens`
# collection and queries by (type, identifier), so a distinct prefix guarantees neither app reads
# or overwrites the other's records.
REGISTRY_TOKEN_IDENTIFIER_PREFIX = "registry:mcp"


def registry_access_token_identifier(service_name: str) -> str:
    return f"{REGISTRY_TOKEN_IDENTIFIER_PREFIX}:{service_name}"


def registry_refresh_token_identifier(service_name: str) -> str:
    return f"{REGISTRY_TOKEN_IDENTIFIER_PREFIX}:{service_name}:refresh"


class TokenService:
    def __init__(self, user_service: UserService, *, encryption_key: bytes):
        """
        Args:
            user_service: User lookup service
            encryption_key: AES key bytes for token encryption/decryption
                (caller-supplied; e.g. settings.encryption_key)
        """
        self.user_service = user_service
        self._encryption_key = encryption_key

    async def get_authorization_status(
        self, user_id: str, service_names: list[str]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Read only status metadata for this page; never fetch/decrypt credential values."""
        if not service_names:
            return {}
        user_obj_id = await self.get_user_by_user_id(user_id)
        identifiers = {
            (self._get_access_identifier(name), TokenType.MCP_OAUTH_ACCESS.value): (name, "access")
            for name in service_names
        }
        identifiers.update(
            {
                (self._get_refresh_identifier(name), TokenType.MCP_OAUTH_REFRESH.value): (name, "refresh")
                for name in service_names
            }
        )
        query = {
            "userId": PydanticObjectId(user_obj_id),
            "identifier": {"$in": list({identifier for identifier, _ in identifiers})},
            "type": {"$in": [TokenType.MCP_OAUTH_ACCESS.value, TokenType.MCP_OAUTH_REFRESH.value]},
            "token": {"$exists": True, "$nin": [None, ""]},
            "$or": [{"expiresAt": None}, {"expiresAt": {"$gt": datetime.now(UTC) + timedelta(seconds=3)}}],
        }
        rows = await Token.get_pymongo_collection().find(query, {"identifier": 1, "type": 1, "metadata": 1}).to_list()
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            match = identifiers.get((row["identifier"], row["type"]))
            if match is not None:
                name, kind = match
                result.setdefault(name, {})[kind] = row.get("metadata") or {}
        return result

    async def get_user(self, user_id: str) -> User | None:
        user = await self.user_service.get_user_by_user_id(user_id)
        if not user:
            raise Exception(f"OAuth operation failed: User {user_id} not found")
        return user

    async def get_user_by_user_id(self, user_id: str) -> str:
        user = await self.get_user(user_id)
        return str(user.id)

    def _get_access_identifier(self, service_name: str) -> str:
        """Build access token identifier"""
        return registry_access_token_identifier(service_name)

    def _get_refresh_identifier(self, service_name: str) -> str:
        """Build refresh token identifier"""
        return registry_refresh_token_identifier(service_name)

    async def store_oauth_access_token(
        self, user_id: str, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any] | None = None
    ) -> Token:
        """
        Store OAuth access token (encrypted)

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

        # Encrypt the access token
        encrypted_token = encrypt_value(tokens.access_token, encryption_key=self._encryption_key)

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
            existing_token.token = encrypted_token
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
                token=encrypted_token,
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
        Store OAuth refresh token (encrypted)

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

        # Encrypt the refresh token
        encrypted_token = encrypt_value(tokens.refresh_token, encryption_key=self._encryption_key)

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
            existing_token.token = encrypted_token
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
                token=encrypted_token,
                expiresAt=expires_at,
                metadata=metadata or {},
            )
            token_doc.email = user.email
            await token_doc.insert()
            logger.info(f"Created OAuth refresh token for user={user_id}, service={service_name}")
            return token_doc

    async def token_write_snapshot(self, user_id: str, service_name: str) -> dict[str, Any]:
        user = await self.get_user(user_id)
        identifiers = [self._get_access_identifier(service_name), self._get_refresh_identifier(service_name)]
        collection = Token.get_pymongo_collection()
        rows = await collection.find(
            {
                "userId": user.id,
                "$or": [
                    {"identifier": identifiers[0], "type": TokenType.MCP_OAUTH_ACCESS.value},
                    {"identifier": identifiers[1], "type": TokenType.MCP_OAUTH_REFRESH.value},
                ],
            }
        ).to_list()
        if len({r["identifier"] for r in rows}) != len(rows):
            raise OAuthCoordinationError("Duplicate OAuth credential records require repair")
        return {"user": user, "rows": {r["identifier"]: r for r in rows}}

    def snapshot_matches_refresh(self, snapshot: dict[str, Any], service_name: str, token: str) -> bool:
        previous = snapshot["rows"].get(self._get_refresh_identifier(service_name))
        return previous is not None and decrypt_value(previous["token"], encryption_key=self._encryption_key) == token

    async def commit_oauth_tokens(
        self, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any], snapshot: dict[str, Any]
    ) -> None:
        """CAS both credential records in one transaction; no IdP call is retried here.

        Existing records keep their IDs. Deterministic IDs for absent records make
        concurrent first-login inserts conflict even without a new unique index.
        MongoDB replica-set transactions are required (also used by workflows).
        """
        if not tokens.access_token:
            raise ValueError("OAuth response has no access token")
        user = snapshot["user"]
        collection = Token.get_pymongo_collection()
        now = datetime.now(UTC)
        values = [
            (
                self._get_access_identifier(service_name),
                TokenType.MCP_OAUTH_ACCESS.value,
                tokens.access_token,
                self._calculate_expiration(tokens.expires_in),
            ),
            (
                self._get_refresh_identifier(service_name),
                TokenType.MCP_OAUTH_REFRESH.value,
                tokens.refresh_token,
                now + timedelta(days=365),
            ),
        ]
        try:
            async with collection.database.client.start_session() as session:
                async with await session.start_transaction():
                    for identifier, token_type, value, expiry in values:
                        previous = snapshot["rows"].get(identifier)
                        if previous is not None:
                            # Include the previous binding as well as randomized ciphertext.
                            condition = {
                                "_id": previous["_id"],
                                "token": previous["token"],
                                "metadata": previous.get("metadata"),
                            }
                            if value is None:
                                result = await collection.delete_one(condition, session=session)
                                matched = result.deleted_count
                            else:
                                result = await collection.update_one(
                                    condition,
                                    {
                                        "$set": {
                                            "token": encrypt_value(value, encryption_key=self._encryption_key),
                                            "expiresAt": expiry,
                                            "metadata": metadata,
                                        }
                                    },
                                    session=session,
                                )
                                matched = result.matched_count
                            if matched != 1:
                                raise OAuthCoordinationError("OAuth credentials changed during refresh; retry")
                        elif value is not None:
                            document_id = PydanticObjectId(
                                hashlib.sha256(f"{user.id}:{token_type}:{identifier}".encode()).hexdigest()[:24]
                            )
                            await collection.insert_one(
                                {
                                    "_id": document_id,
                                    "userId": user.id,
                                    "email": user.email,
                                    "type": token_type,
                                    "identifier": identifier,
                                    "token": encrypt_value(value, encryption_key=self._encryption_key),
                                    "createdAt": now,
                                    "expiresAt": expiry,
                                    "metadata": metadata,
                                },
                                session=session,
                            )
        except (DuplicateKeyError, OperationFailure) as exc:
            raise OAuthCoordinationError("OAuth credential commit failed; retry") from exc

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
        Get OAuth access token (decrypted)

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Token document with decrypted token value, or None
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

        # Decrypt the token value before returning
        if token and token.token:
            token.token = decrypt_value(token.token, encryption_key=self._encryption_key)

        return token

    async def get_oauth_client_token(self, user_id: str, service_name: str) -> Token | None:
        """
        Deprecated: Use get_oauth_access_token instead.
        This method is kept for backward compatibility.
        """
        return await self.get_oauth_access_token(user_id, service_name)

    async def get_oauth_refresh_token(self, user_id: str, service_name: str) -> Token | None:
        """
        Get OAuth refresh token (decrypted)

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Token document with decrypted token value, or None
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

        # Decrypt the token value before returning
        if token and token.token:
            token.token = decrypt_value(token.token, encryption_key=self._encryption_key)

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
        Delete the user's Registry access and refresh tokens for a service.

        Args:
            user_id: User ID
            service_name: Service name

        Returns:
            Whether anything was deleted
        """
        user_obj_id = await self.get_user_by_user_id(user_id)
        result = await Token.get_pymongo_collection().delete_many(
            {
                "userId": PydanticObjectId(user_obj_id),
                "$or": [
                    {
                        "type": TokenType.MCP_OAUTH_ACCESS.value,
                        "identifier": self._get_access_identifier(service_name),
                    },
                    {
                        "type": TokenType.MCP_OAUTH_REFRESH.value,
                        "identifier": self._get_refresh_identifier(service_name),
                    },
                ],
            }
        )

        if result.deleted_count > 0:
            logger.info(f"Deleted {result.deleted_count} tokens for user={user_id}, service={service_name}")
            return True

        return False

    async def delete_access_token_if_matches(self, user_id: str, service_name: str, access_token: str) -> bool:
        """
        Delete the user's Registry access token only if it still holds ``access_token``.

        The delete filters on the stored ciphertext, so a token another pod rotated in between the
        read and the delete is never removed.

        Returns:
            Whether the record was deleted
        """
        user_obj_id = await self.get_user_by_user_id(user_id)
        record = await Token.find_one(
            {
                "userId": PydanticObjectId(user_obj_id),
                "type": TokenType.MCP_OAUTH_ACCESS.value,
                "identifier": self._get_access_identifier(service_name),
            }
        )
        if record is None or not record.token:
            return False

        stored_ciphertext = record.token
        if decrypt_value(stored_ciphertext, encryption_key=self._encryption_key) != access_token:
            return False

        result = await Token.get_pymongo_collection().delete_one({"_id": record.id, "token": stored_ciphertext})
        deleted = result.deleted_count == 1
        if deleted:
            logger.info(f"Deleted rejected access token for user={user_id}, service={service_name}")
        return deleted

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
        Get access token and its validity status (decrypted)

        Returns:
            tuple: (token_doc, is_valid)
                - token_doc: Token document with decrypted token value, or None if not exists
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

        # Decrypt the token value before returning
        if token.token:
            token.token = decrypt_value(token.token, encryption_key=self._encryption_key)

        is_valid = not self._is_token_expired(token)
        return token, is_valid

    async def get_refresh_token_status(self, user_id: str, service_name: str) -> tuple[Token | None, bool]:
        """
        Get refresh token and its validity status (decrypted)

        Returns:
            tuple: (token_doc, is_valid)
                - token_doc: Token document with decrypted token value, or None if not exists
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

        # Decrypt the token value before returning
        if token.token:
            token.token = decrypt_value(token.token, encryption_key=self._encryption_key)

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
