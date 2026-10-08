"""Unit tests for TokenService (token_service.py)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry_pkgs.core.crypto_utils import encrypt_value
from registry_pkgs.models import Token, TokenType
from registry_pkgs.oauth.schemas import OAuthTokens
from registry_pkgs.oauth.token_service import (
    REGISTRY_TOKEN_IDENTIFIER_PREFIX,
    TokenService,
    registry_access_token_identifier,
    registry_refresh_token_identifier,
)

_TEST_ENCRYPTION_KEY = bytes.fromhex("00" * 16)  # AES-128 test key


class TestTokenServiceBasicMethods:
    """Tests for TokenService basic methods (identifiers, user lookups)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    @pytest.fixture
    def mock_user(self):
        """Mock user object"""
        user = Mock()
        user.id = PydanticObjectId("507f1f77bcf86cd799439011")
        user.email = "test@example.com"
        return user

    @pytest.mark.asyncio
    async def test_get_user(self, token_service, mock_user):
        """Test getting user by user_id"""
        mock_user_service = Mock()
        mock_user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)
        token_service.user_service = mock_user_service

        result = await token_service.get_user("test_user")

        assert result == mock_user
        mock_user_service.get_user_by_user_id.assert_awaited_once_with("test_user")

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, token_service):
        """Test get_user raises exception when user not found"""
        mock_user_service = Mock()
        mock_user_service.get_user_by_user_id = AsyncMock(return_value=None)
        token_service.user_service = mock_user_service

        with pytest.raises(Exception, match="User test_user not found"):
            await token_service.get_user("test_user")

    @pytest.mark.asyncio
    async def test_get_user_by_user_id(self, token_service, mock_user):
        """Test getting user object ID"""
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            result = await token_service.get_user_by_user_id("test_user")

            assert result == "507f1f77bcf86cd799439011"

    def test_get_access_identifier(self, token_service):
        """Registry-only access token identifier, distinct from Chat's mcp:<name>"""
        assert token_service._get_access_identifier("notion") == "registry:mcp:notion"
        assert token_service._get_access_identifier("google-drive") == "registry:mcp:google-drive"

    def test_get_refresh_identifier(self, token_service):
        """Registry-only refresh token identifier, distinct from Chat's mcp:<name>:refresh"""
        assert token_service._get_refresh_identifier("notion") == "registry:mcp:notion:refresh"
        assert token_service._get_refresh_identifier("slack") == "registry:mcp:slack:refresh"

    def test_public_identifier_builders(self):
        assert REGISTRY_TOKEN_IDENTIFIER_PREFIX == "registry:mcp"
        assert registry_access_token_identifier("atlassian") == "registry:mcp:atlassian"
        assert registry_refresh_token_identifier("atlassian") == "registry:mcp:atlassian:refresh"

    def test_client_credential_methods_removed(self, token_service):
        for name in (
            "store_oauth_client_credentials",
            "get_oauth_client_credentials",
            "_get_client_creds_identifier",
            "_get_client_identifier",
        ):
            assert not hasattr(token_service, name)


class TestTokenServiceStoreTokens:
    """Tests for token storage methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    @pytest.fixture
    def mock_user(self):
        """Mock user object"""
        user = Mock()
        user.id = PydanticObjectId("507f1f77bcf86cd799439011")
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_oauth_tokens(self):
        """Mock OAuth tokens"""
        return OAuthTokens(
            access_token="test_access_token",
            refresh_token="test_refresh_token",
            token_type="Bearer",
            expires_in=3600,
        )

    @pytest.mark.asyncio
    async def test_store_oauth_client_token_new(self, token_service, mock_user, mock_oauth_tokens):
        """Test storing new access token"""
        mock_token = Mock(spec=Token)
        mock_token.type = TokenType.MCP_OAUTH_ACCESS.value
        mock_token.identifier = "mcp:notion"
        mock_token.token = "test_access_token"
        mock_token.insert = AsyncMock()

        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry_pkgs.oauth.token_service.Token") as MockToken:
                MockToken.find_one = AsyncMock(return_value=None)
                MockToken.return_value = mock_token

                result = await token_service.store_oauth_client_token(
                    user_id="test_user",
                    service_name="notion",
                    tokens=mock_oauth_tokens,
                    metadata={"issuer": "https://example.com"},
                )

                assert result == mock_token
                mock_token.insert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_oauth_refresh_token_new(self, token_service, mock_user, mock_oauth_tokens):
        """Test storing new refresh token"""
        mock_token = Mock(spec=Token)
        mock_token.type = TokenType.MCP_OAUTH_REFRESH.value
        mock_token.identifier = "mcp:notion:refresh"
        mock_token.token = "test_refresh_token"
        mock_token.insert = AsyncMock()

        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry_pkgs.oauth.token_service.Token") as MockToken:
                MockToken.find_one = AsyncMock(return_value=None)
                MockToken.return_value = mock_token

                result = await token_service.store_oauth_refresh_token(
                    user_id="test_user",
                    service_name="notion",
                    tokens=mock_oauth_tokens,
                )

                assert result == mock_token
                mock_token.insert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_oauth_refresh_token_no_refresh_token(self, token_service, mock_user):
        """Test storing OAuth tokens when refresh token is not provided"""
        tokens_no_refresh = OAuthTokens(
            access_token="test_access_token",
            token_type="Bearer",
            expires_in=3600,
        )

        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            result = await token_service.store_oauth_refresh_token(
                user_id="test_user",
                service_name="notion",
                tokens=tokens_no_refresh,
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_store_oauth_tokens(self, token_service, mock_user, mock_oauth_tokens):
        """Test storing complete OAuth tokens (access + refresh)"""
        with patch.object(token_service, "store_oauth_access_token", AsyncMock()) as mock_access:
            with patch.object(token_service, "store_oauth_refresh_token", AsyncMock()) as mock_refresh:
                await token_service.store_oauth_tokens(
                    user_id="test_user",
                    service_name="notion",
                    tokens=mock_oauth_tokens,
                )

                mock_access.assert_awaited_once()
                mock_refresh.assert_awaited_once()


class TestTokenServiceGetTokens:
    """Tests for token retrieval methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    @pytest.fixture
    def mock_access_token(self):
        """Mock access token document"""
        token = Mock(spec=Token)
        token.token = "test_access_token"
        token.expiresAt = datetime.now(UTC) + timedelta(hours=1)
        token.type = TokenType.MCP_OAUTH_ACCESS.value
        return token

    @pytest.fixture
    def mock_refresh_token(self):
        """Mock refresh token document"""
        token = Mock(spec=Token)
        token.token = "test_refresh_token"
        token.expiresAt = datetime.now(UTC) + timedelta(days=30)
        token.type = TokenType.MCP_OAUTH_REFRESH.value
        return token

    @pytest.mark.asyncio
    async def test_get_oauth_client_token(self, token_service, mock_access_token):
        """Test retrieving access token"""
        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_access_token)):
                result = await token_service.get_oauth_client_token("test_user", "notion")

                assert result == mock_access_token

    @pytest.mark.asyncio
    async def test_get_oauth_refresh_token(self, token_service, mock_refresh_token):
        """Test retrieving refresh token"""
        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_refresh_token)):
                result = await token_service.get_oauth_refresh_token("test_user", "notion")

                assert result == mock_refresh_token

    @pytest.mark.asyncio
    async def test_get_oauth_tokens(self, token_service, mock_access_token, mock_refresh_token):
        """Test retrieving complete OAuth tokens as OAuthTokens object"""
        with patch.object(token_service, "get_oauth_access_token", AsyncMock(return_value=mock_access_token)):
            with patch.object(token_service, "get_oauth_refresh_token", AsyncMock(return_value=mock_refresh_token)):
                result = await token_service.get_oauth_tokens("test_user", "notion")

                assert isinstance(result, OAuthTokens)
                assert result.access_token == "test_access_token"
                assert result.refresh_token == "test_refresh_token"

    @pytest.mark.asyncio
    async def test_get_oauth_tokens_no_tokens(self, token_service):
        """Test get_oauth_tokens returns None when both access and refresh tokens are missing"""
        with patch.object(token_service, "get_oauth_access_token", AsyncMock(return_value=None)):
            with patch.object(token_service, "get_oauth_refresh_token", AsyncMock(return_value=None)):
                result = await token_service.get_oauth_tokens("test_user", "notion")

                assert result is None

    @pytest.mark.asyncio
    async def test_get_oauth_tokens_only_refresh_token(self, token_service, mock_refresh_token):
        """Test get_oauth_tokens returns OAuthTokens with only refresh token when access token is missing"""
        with patch.object(token_service, "get_oauth_access_token", AsyncMock(return_value=None)):
            with patch.object(token_service, "get_oauth_refresh_token", AsyncMock(return_value=mock_refresh_token)):
                result = await token_service.get_oauth_tokens("test_user", "notion")

                assert isinstance(result, OAuthTokens)
                assert result.access_token is None
                assert result.refresh_token == "test_refresh_token"
                assert result.expires_in is None
                assert result.expires_at is None


class TestTokenServiceDeleteTokens:
    """Tests for token deletion methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    @pytest.mark.asyncio
    async def test_delete_oauth_tokens_deletes_only_registry_identifiers(self, token_service):
        """Only registry:mcp:<name> and registry:mcp:<name>:refresh; never Chat's mcp:* records."""
        collection = Mock()
        collection.delete_many = AsyncMock(return_value=Mock(deleted_count=2))
        with (
            patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")),
            patch.object(Token, "get_pymongo_collection", Mock(return_value=collection)),
        ):
            result = await token_service.delete_oauth_tokens(user_id="test_user", service_name="notion")

        assert result is True
        query = collection.delete_many.await_args.args[0]
        assert query["userId"] == PydanticObjectId("507f1f77bcf86cd799439011")
        assert query["$or"] == [
            {"type": TokenType.MCP_OAUTH_ACCESS.value, "identifier": "registry:mcp:notion"},
            {"type": TokenType.MCP_OAUTH_REFRESH.value, "identifier": "registry:mcp:notion:refresh"},
        ]
        assert "mcp:notion" not in str(query).replace("registry:mcp:notion", "")

    @pytest.mark.asyncio
    async def test_delete_oauth_tokens_none_found(self, token_service):
        """Test deleting tokens when none exist"""
        collection = Mock()
        collection.delete_many = AsyncMock(return_value=Mock(deleted_count=0))
        with (
            patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")),
            patch.object(Token, "get_pymongo_collection", Mock(return_value=collection)),
        ):
            result = await token_service.delete_oauth_tokens(user_id="test_user", service_name="notion")

        assert result is False


class TestDeleteAccessTokenIfMatches:
    @pytest.fixture
    def token_service(self):
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    def _record(self, plaintext: str) -> Mock:
        record = Mock(spec=Token)
        record.id = PydanticObjectId("65f000000000000000000009")
        record.token = encrypt_value(plaintext, encryption_key=_TEST_ENCRYPTION_KEY)
        return record

    @pytest.mark.asyncio
    async def test_deletes_when_stored_token_matches(self, token_service):
        record = self._record("rejected-token")
        collection = Mock()
        collection.delete_one = AsyncMock(return_value=Mock(deleted_count=1))
        with (
            patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")),
            patch.object(Token, "find_one", AsyncMock(return_value=record)) as find_one,
            patch.object(Token, "get_pymongo_collection", Mock(return_value=collection)),
        ):
            assert await token_service.delete_access_token_if_matches("u", "atlassian", "rejected-token") is True

        assert find_one.await_args.args[0]["identifier"] == "registry:mcp:atlassian"
        # The delete filters on the ciphertext that was read, so a concurrently rotated token survives.
        collection.delete_one.assert_awaited_once_with({"_id": record.id, "token": record.token})

    @pytest.mark.asyncio
    async def test_keeps_record_holding_a_different_token(self, token_service):
        record = self._record("newer-token")
        collection = Mock()
        collection.delete_one = AsyncMock()
        with (
            patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")),
            patch.object(Token, "find_one", AsyncMock(return_value=record)),
            patch.object(Token, "get_pymongo_collection", Mock(return_value=collection)),
        ):
            assert await token_service.delete_access_token_if_matches("u", "atlassian", "rejected-token") is False

        collection.delete_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reports_lost_race_when_ciphertext_changed(self, token_service):
        record = self._record("rejected-token")
        collection = Mock()
        collection.delete_one = AsyncMock(return_value=Mock(deleted_count=0))
        with (
            patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")),
            patch.object(Token, "find_one", AsyncMock(return_value=record)),
            patch.object(Token, "get_pymongo_collection", Mock(return_value=collection)),
        ):
            assert await token_service.delete_access_token_if_matches("u", "atlassian", "rejected-token") is False


class TestTokenServiceTokenStatus:
    """Tests for token status checking methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    @pytest.mark.asyncio
    async def test_is_access_token_expired_true(self, token_service):
        """Test checking if access token is expired (expired)"""
        expired_token = Mock(spec=Token)
        expired_token.expiresAt = datetime.now(UTC) - timedelta(hours=1)

        with patch.object(token_service, "get_oauth_access_token", AsyncMock(return_value=expired_token)):
            result = await token_service.is_access_token_expired("test_user", "notion")

            assert result is True

    @pytest.mark.asyncio
    async def test_is_access_token_expired_false(self, token_service):
        """Test checking if access token is expired (valid)"""
        valid_token = Mock(spec=Token)
        valid_token.expiresAt = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(token_service, "get_oauth_access_token", AsyncMock(return_value=valid_token)):
            result = await token_service.is_access_token_expired("test_user", "notion")

            assert result is False

    @pytest.mark.asyncio
    async def test_has_refresh_token_true(self, token_service):
        """Test checking if refresh token exists and is valid"""
        valid_token = Mock(spec=Token)
        valid_token.expiresAt = datetime.now(UTC) + timedelta(days=30)

        with patch.object(token_service, "get_oauth_refresh_token", AsyncMock(return_value=valid_token)):
            result = await token_service.has_refresh_token("test_user", "notion")

            assert result is True

    @pytest.mark.asyncio
    async def test_has_refresh_token_false(self, token_service):
        """Test has_refresh_token when token doesn't exist"""
        with patch.object(token_service, "get_oauth_refresh_token", AsyncMock(return_value=None)):
            result = await token_service.has_refresh_token("test_user", "notion")

            assert result is False

    @pytest.mark.asyncio
    async def test_get_access_token_status(self, token_service):
        """Test getting access token and validity status"""
        valid_token = Mock(spec=Token)
        valid_token.token = "encrypted_iv:encrypted_token"
        valid_token.expiresAt = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=valid_token)):
                with patch("registry_pkgs.oauth.token_service.decrypt_value") as mock_decrypt:
                    mock_decrypt.return_value = "decrypted_token"

                    token, is_valid = await token_service.get_access_token_status("test_user", "notion")

                    assert token == valid_token
                    assert is_valid is True
                    mock_decrypt.assert_called_once_with(
                        "encrypted_iv:encrypted_token", encryption_key=_TEST_ENCRYPTION_KEY
                    )

    @pytest.mark.asyncio
    async def test_get_refresh_token_status(self, token_service):
        """Test getting refresh token and validity status"""
        valid_token = Mock(spec=Token)
        valid_token.token = "encrypted_iv:encrypted_refresh_token"
        valid_token.expiresAt = datetime.now(UTC) + timedelta(days=30)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=valid_token)):
                with patch("registry_pkgs.oauth.token_service.decrypt_value") as mock_decrypt:
                    mock_decrypt.return_value = "decrypted_refresh_token"

                    token, is_valid = await token_service.get_refresh_token_status("test_user", "notion")

                    assert token == valid_token
                    assert is_valid is True
                    mock_decrypt.assert_called_once_with(
                        "encrypted_iv:encrypted_refresh_token", encryption_key=_TEST_ENCRYPTION_KEY
                    )


class TestTokenServiceHelperMethods:
    """Tests for TokenService helper methods (expiration calculations)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    def test_calculate_expiration(self, token_service):
        """Test calculating expiration datetime from expires_in"""
        expires_in = 3600  # 1 hour
        result = token_service._calculate_expiration(expires_in)

        now = datetime.now(UTC)
        expected = now + timedelta(seconds=3600)

        # Allow 5 seconds difference for test execution
        assert abs((result - expected).total_seconds()) < 5

    def test_calculate_expiration_none(self, token_service):
        """Test calculating expiration with None expires_in (1 hour default)"""
        result = token_service._calculate_expiration(None)

        now = datetime.now(UTC)
        expected = now + timedelta(hours=1)

        assert abs((result - expected).total_seconds()) < 5

    def test_calculate_expires_in(self, token_service):
        """Test calculating remaining seconds from expires_at"""
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        result = token_service._calculate_expires_in(expires_at)

        # Should be approximately 3600 seconds (allow 5 second variance)
        assert 3595 <= result <= 3605

    def test_is_token_expired_true(self, token_service):
        """Test checking if token is expired (expired)"""
        token = Mock(spec=Token)
        token.expiresAt = datetime.now(UTC) - timedelta(hours=1)

        result = token_service._is_token_expired(token)

        assert result is True

    def test_is_token_expired_false(self, token_service):
        """Test checking if token is expired (valid with buffer)"""
        token = Mock(spec=Token)
        token.expiresAt = datetime.now(UTC) + timedelta(minutes=5)

        result = token_service._is_token_expired(token)

        assert result is False

    def test_is_token_expired_no_expiry(self, token_service):
        """Test checking token with no expiration date"""
        token = Mock(spec=Token)
        token.expiresAt = None

        result = token_service._is_token_expired(token)

        assert result is False


class TestTokenServiceEncryption:
    """Tests for token encryption (bug fix validation)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService(user_service=Mock(), encryption_key=_TEST_ENCRYPTION_KEY)

    @pytest.fixture
    def mock_user(self):
        """Mock user object"""
        user = Mock()
        user.id = PydanticObjectId("507f1f77bcf86cd799439011")
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_oauth_tokens(self):
        """Mock OAuth tokens"""
        return OAuthTokens(
            access_token="test_access_token_plain",
            refresh_token="test_refresh_token_plain",
            token_type="Bearer",
            expires_in=3600,
        )

    @pytest.mark.asyncio
    async def test_store_oauth_access_token_encrypts_token(self, token_service, mock_user, mock_oauth_tokens):
        """Test that store_oauth_access_token encrypts the access token"""
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry_pkgs.oauth.token_service.encrypt_value") as mock_encrypt:
                mock_encrypt.return_value = "encrypted_iv:encrypted_access_token"

                # Capture Token constructor call
                with patch("registry_pkgs.oauth.token_service.Token") as MockToken:
                    mock_token_instance = Mock(spec=Token)
                    mock_token_instance.insert = AsyncMock()
                    MockToken.return_value = mock_token_instance
                    MockToken.find_one = AsyncMock(return_value=None)

                    await token_service.store_oauth_access_token(
                        user_id="test_user",
                        service_name="test_service",
                        tokens=mock_oauth_tokens,
                    )

                    # Verify encrypt_value was called with the access token
                    mock_encrypt.assert_called_once_with("test_access_token_plain", encryption_key=_TEST_ENCRYPTION_KEY)

                    # Verify Token was constructed with encrypted token
                    MockToken.assert_called_once()
                    call_kwargs = MockToken.call_args[1]
                    assert call_kwargs["token"] == "encrypted_iv:encrypted_access_token"

    @pytest.mark.asyncio
    async def test_store_oauth_refresh_token_encrypts_token(self, token_service, mock_user, mock_oauth_tokens):
        """Test that store_oauth_refresh_token encrypts the refresh token"""
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry_pkgs.oauth.token_service.encrypt_value") as mock_encrypt:
                mock_encrypt.return_value = "encrypted_iv:encrypted_refresh_token"

                # Capture Token constructor call
                with patch("registry_pkgs.oauth.token_service.Token") as MockToken:
                    mock_token_instance = Mock(spec=Token)
                    mock_token_instance.email = None
                    mock_token_instance.insert = AsyncMock()
                    MockToken.return_value = mock_token_instance
                    MockToken.find_one = AsyncMock(return_value=None)

                    await token_service.store_oauth_refresh_token(
                        user_id="test_user",
                        service_name="test_service",
                        tokens=mock_oauth_tokens,
                    )

                    # Verify encrypt_value was called with the refresh token
                    mock_encrypt.assert_called_once_with(
                        "test_refresh_token_plain", encryption_key=_TEST_ENCRYPTION_KEY
                    )

                    # Verify Token was constructed with encrypted token
                    MockToken.assert_called_once()
                    call_kwargs = MockToken.call_args[1]
                    assert call_kwargs["token"] == "encrypted_iv:encrypted_refresh_token"

    @pytest.mark.asyncio
    async def test_get_oauth_access_token_decrypts_token(self, token_service):
        """Test that get_oauth_access_token decrypts the token"""
        # Mock encrypted token from DB
        mock_token = Mock(spec=Token)
        mock_token.token = "encrypted_iv:encrypted_access_token"
        mock_token.expiresAt = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_token)):
                with patch("registry_pkgs.oauth.token_service.decrypt_value") as mock_decrypt:
                    mock_decrypt.return_value = "decrypted_access_token"

                    result = await token_service.get_oauth_access_token("test_user", "test_service")

                    # Verify decrypt_value was called
                    mock_decrypt.assert_called_once_with(
                        "encrypted_iv:encrypted_access_token", encryption_key=_TEST_ENCRYPTION_KEY
                    )

                    # Verify the token was decrypted
                    assert result.token == "decrypted_access_token"

    @pytest.mark.asyncio
    async def test_get_oauth_refresh_token_decrypts_token(self, token_service):
        """Test that get_oauth_refresh_token decrypts the token"""
        # Mock encrypted token from DB
        mock_token = Mock(spec=Token)
        mock_token.token = "encrypted_iv:encrypted_refresh_token"
        mock_token.expiresAt = datetime.now(UTC) + timedelta(days=30)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_token)):
                with patch("registry_pkgs.oauth.token_service.decrypt_value") as mock_decrypt:
                    mock_decrypt.return_value = "decrypted_refresh_token"

                    result = await token_service.get_oauth_refresh_token("test_user", "test_service")

                    # Verify decrypt_value was called
                    mock_decrypt.assert_called_once_with(
                        "encrypted_iv:encrypted_refresh_token", encryption_key=_TEST_ENCRYPTION_KEY
                    )

                    # Verify the token was decrypted
                    assert result.token == "decrypted_refresh_token"

    @pytest.mark.asyncio
    async def test_get_access_token_status_decrypts_token(self, token_service):
        """Test that get_access_token_status decrypts the token"""
        mock_token = Mock(spec=Token)
        mock_token.token = "encrypted_iv:encrypted_access_token"
        mock_token.expiresAt = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_token)):
                with patch("registry_pkgs.oauth.token_service.decrypt_value") as mock_decrypt:
                    mock_decrypt.return_value = "decrypted_access_token"

                    token, is_valid = await token_service.get_access_token_status("test_user", "test_service")

                    # Verify decrypt_value was called
                    mock_decrypt.assert_called_once_with(
                        "encrypted_iv:encrypted_access_token", encryption_key=_TEST_ENCRYPTION_KEY
                    )

                    # Verify the token was decrypted
                    assert token.token == "decrypted_access_token"
                    assert is_valid is True

    @pytest.mark.asyncio
    async def test_get_refresh_token_status_decrypts_token(self, token_service):
        """Test that get_refresh_token_status decrypts the token"""
        mock_token = Mock(spec=Token)
        mock_token.token = "encrypted_iv:encrypted_refresh_token"
        mock_token.expiresAt = datetime.now(UTC) + timedelta(days=30)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_token)):
                with patch("registry_pkgs.oauth.token_service.decrypt_value") as mock_decrypt:
                    mock_decrypt.return_value = "decrypted_refresh_token"

                    token, is_valid = await token_service.get_refresh_token_status("test_user", "test_service")

                    # Verify decrypt_value was called
                    mock_decrypt.assert_called_once_with(
                        "encrypted_iv:encrypted_refresh_token", encryption_key=_TEST_ENCRYPTION_KEY
                    )

                    # Verify the token was decrypted
                    assert token.token == "decrypted_refresh_token"
                    assert is_valid is True

    @pytest.mark.asyncio
    async def test_encrypted_token_format_has_colon_separator(self, token_service, mock_user, mock_oauth_tokens):
        """Test that encrypted tokens have the expected format with colon separator"""
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            # Use real encrypt_value function
            with patch("registry_pkgs.oauth.token_service.Token") as MockToken:
                mock_token_instance = Mock(spec=Token)
                mock_token_instance.insert = AsyncMock()
                MockToken.return_value = mock_token_instance
                MockToken.find_one = AsyncMock(return_value=None)

                # Let encrypt_value run normally
                await token_service.store_oauth_access_token(
                    user_id="test_user",
                    service_name="test_service",
                    tokens=mock_oauth_tokens,
                )

                # Verify the token has the encrypted format (contains colon)
                MockToken.assert_called_once()
                call_kwargs = MockToken.call_args[1]
                stored_token = call_kwargs["token"]
                assert ":" in stored_token, "Encrypted token should contain colon separator"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
