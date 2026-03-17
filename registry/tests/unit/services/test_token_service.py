"""Unit tests for TokenService (token_service.py)."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry.schemas.enums import TokenType
from registry.schemas.oauth_sachema import OAuthClientInformation, OAuthTokens
from registry.services.oauth.token_service import TokenService
from registry_pkgs.models._generated.token import Token


class TestTokenServiceBasicMethods:
    """Tests for TokenService basic methods (identifiers, user lookups)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

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
        with patch("registry.services.oauth.token_service.user_service") as mock_user_service:
            mock_user_service.get_user_by_user_id = AsyncMock(return_value=mock_user)

            result = await token_service.get_user("test_user")

            assert result == mock_user
            mock_user_service.get_user_by_user_id.assert_awaited_once_with("test_user")

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, token_service):
        """Test get_user raises exception when user not found"""
        with patch("registry.services.oauth.token_service.user_service") as mock_user_service:
            mock_user_service.get_user_by_user_id = AsyncMock(return_value=None)

            with pytest.raises(Exception, match="User test_user not found"):
                await token_service.get_user("test_user")

    @pytest.mark.asyncio
    async def test_get_user_by_user_id(self, token_service, mock_user):
        """Test getting user object ID"""
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            result = await token_service.get_user_by_user_id("test_user")

            assert result == "507f1f77bcf86cd799439011"

    def test_get_client_identifier(self, token_service):
        """Test access token identifier format"""
        result = token_service._get_client_identifier("notion")
        assert result == "mcp:notion"

        result = token_service._get_client_identifier("google-drive")
        assert result == "mcp:google-drive"

    def test_get_refresh_identifier(self, token_service):
        """Test refresh token identifier format"""
        result = token_service._get_refresh_identifier("notion")
        assert result == "mcp:notion:refresh"

        result = token_service._get_refresh_identifier("slack")
        assert result == "mcp:slack:refresh"

    def test_get_client_creds_identifier(self, token_service):
        """Test client credentials identifier format"""
        result = token_service._get_client_creds_identifier("notion")
        assert result == "mcp:notion:client"

        result = token_service._get_client_creds_identifier("github")
        assert result == "mcp:github:client"


class TestTokenServiceStoreTokens:
    """Tests for token storage methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

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
        mock_token.type = TokenType.MCP_OAUTH.value
        mock_token.identifier = "mcp:notion"
        mock_token.token = "test_access_token"
        mock_token.insert = AsyncMock()

        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry.services.oauth.token_service.Token") as MockToken:
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
            with patch("registry.services.oauth.token_service.Token") as MockToken:
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
        with patch.object(token_service, "store_oauth_client_token", AsyncMock()) as mock_client:
            with patch.object(token_service, "store_oauth_refresh_token", AsyncMock()) as mock_refresh:
                await token_service.store_oauth_tokens(
                    user_id="test_user",
                    service_name="notion",
                    tokens=mock_oauth_tokens,
                )

                mock_client.assert_awaited_once()
                mock_refresh.assert_awaited_once()


class TestTokenServiceGetTokens:
    """Tests for token retrieval methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

    @pytest.fixture
    def mock_access_token(self):
        """Mock access token document"""
        token = Mock(spec=Token)
        token.token = "test_access_token"
        token.expiresAt = datetime.now(UTC) + timedelta(hours=1)
        token.type = TokenType.MCP_OAUTH.value
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
        with patch.object(token_service, "get_oauth_client_token", AsyncMock(return_value=mock_access_token)):
            with patch.object(token_service, "get_oauth_refresh_token", AsyncMock(return_value=mock_refresh_token)):
                result = await token_service.get_oauth_tokens("test_user", "notion")

                assert isinstance(result, OAuthTokens)
                assert result.access_token == "test_access_token"
                assert result.refresh_token == "test_refresh_token"

    @pytest.mark.asyncio
    async def test_get_oauth_tokens_no_access_token(self, token_service):
        """Test get_oauth_tokens returns None when no access token"""
        with patch.object(token_service, "get_oauth_client_token", AsyncMock(return_value=None)):
            result = await token_service.get_oauth_tokens("test_user", "notion")

            assert result is None


class TestTokenServiceDeleteTokens:
    """Tests for token deletion methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

    @pytest.mark.asyncio
    async def test_delete_oauth_tokens_all_three_types(self, token_service):
        """Test deleting all OAuth tokens (access, refresh, client credentials)"""
        access_token = Mock(spec=Token)
        access_token.delete = AsyncMock()
        refresh_token = Mock(spec=Token)
        refresh_token.delete = AsyncMock()
        client_creds = Mock(spec=Token)
        client_creds.delete = AsyncMock()

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):

            async def mock_find_one(query):
                token_type = query.get("type")
                if token_type == TokenType.MCP_OAUTH.value:
                    return access_token
                elif token_type == TokenType.MCP_OAUTH_REFRESH.value:
                    return refresh_token
                elif token_type == TokenType.MCP_OAUTH_CLIENT.value:
                    return client_creds
                return None

            with patch.object(Token, "find_one", side_effect=mock_find_one):
                result = await token_service.delete_oauth_tokens(user_id="test_user", service_name="notion")

                assert result is True
                access_token.delete.assert_awaited_once()
                refresh_token.delete.assert_awaited_once()
                client_creds.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_oauth_tokens_none_found(self, token_service):
        """Test deleting tokens when none exist"""
        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=None)):
                result = await token_service.delete_oauth_tokens(user_id="test_user", service_name="notion")

                assert result is False


class TestTokenServiceTokenStatus:
    """Tests for token status checking methods"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

    @pytest.mark.asyncio
    async def test_is_access_token_expired_true(self, token_service):
        """Test checking if access token is expired (expired)"""
        expired_token = Mock(spec=Token)
        expired_token.expiresAt = datetime.now(UTC) - timedelta(hours=1)

        with patch.object(token_service, "get_oauth_client_token", AsyncMock(return_value=expired_token)):
            result = await token_service.is_access_token_expired("test_user", "notion")

            assert result is True

    @pytest.mark.asyncio
    async def test_is_access_token_expired_false(self, token_service):
        """Test checking if access token is expired (valid)"""
        valid_token = Mock(spec=Token)
        valid_token.expiresAt = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(token_service, "get_oauth_client_token", AsyncMock(return_value=valid_token)):
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
        valid_token.expiresAt = datetime.now(UTC) + timedelta(hours=1)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=valid_token)):
                token, is_valid = await token_service.get_access_token_status("test_user", "notion")

                assert token == valid_token
                assert is_valid is True

    @pytest.mark.asyncio
    async def test_get_refresh_token_status(self, token_service):
        """Test getting refresh token and validity status"""
        valid_token = Mock(spec=Token)
        valid_token.expiresAt = datetime.now(UTC) + timedelta(days=30)

        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value="507f1f77bcf86cd799439011")):
            with patch.object(Token, "find_one", AsyncMock(return_value=valid_token)):
                token, is_valid = await token_service.get_refresh_token_status("test_user", "notion")

                assert token == valid_token
                assert is_valid is True


class TestTokenServiceHelperMethods:
    """Tests for TokenService helper methods (expiration calculations)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

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


class TestTokenServiceClientCredentials:
    """Tests for OAuth client credentials storage (DCR support)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

    @pytest.fixture
    def mock_user(self):
        """Mock user object"""
        user = Mock()
        user.id = PydanticObjectId("507f1f77bcf86cd799439011")
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def mock_client_info(self):
        """Mock OAuth client information"""
        return OAuthClientInformation(
            client_id="test_client_123",
            client_secret="test_secret_abc",
            redirect_uris=["https://registry.example.com/callback"],
            scope="read write",
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="client_secret_basic",
        )

    @pytest.fixture
    def mock_metadata(self):
        """Mock OAuth metadata"""
        return {
            "issuer": "https://example.com",
            "authorization_endpoint": "https://example.com/oauth/authorize",
            "token_endpoint": "https://example.com/oauth/token",
            "registration_endpoint": "https://example.com/oauth/register",
            "scopes_supported": ["read", "write"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
        }

    @pytest.mark.asyncio
    async def test_store_oauth_client_credentials_new(self, token_service, mock_user, mock_client_info, mock_metadata):
        """Test storing new OAuth client credentials"""
        user_id = "test_user"
        service_name = "test_service"

        mock_token = Mock(spec=Token)
        mock_token.token = "encrypted_iv:encrypted_ciphertext"
        mock_token.type = TokenType.MCP_OAUTH_CLIENT.value
        mock_token.identifier = "mcp:test_service:client"
        mock_token.insert = AsyncMock()

        # Mock user service
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            # Mock Token constructor and find_one
            with patch("registry.services.oauth.token_service.Token") as MockToken:
                MockToken.find_one = AsyncMock(return_value=None)
                MockToken.return_value = mock_token

                # Mock encrypt_value
                with patch("registry.services.oauth.token_service.encrypt_value") as mock_encrypt:
                    mock_encrypt.return_value = "encrypted_iv:encrypted_ciphertext"

                    result = await token_service.store_oauth_client_credentials(
                        user_id=user_id,
                        service_name=service_name,
                        client_info=mock_client_info,
                        metadata=mock_metadata,
                    )

                    # Verify encryption was called
                    mock_encrypt.assert_called_once()
                    encrypted_arg = mock_encrypt.call_args[0][0]

                    # Verify the encrypted data is JSON string
                    assert isinstance(encrypted_arg, str)
                    decrypted_data = json.loads(encrypted_arg)
                    assert decrypted_data["client_id"] == "test_client_123"
                    assert decrypted_data["client_secret"] == "test_secret_abc"

                    # Verify token document was created
                    assert result.token == "encrypted_iv:encrypted_ciphertext"
                    assert result.type == TokenType.MCP_OAUTH_CLIENT.value
                    assert result.identifier == "mcp:test_service:client"

    @pytest.mark.asyncio
    async def test_store_oauth_client_credentials_update_existing(
        self, token_service, mock_user, mock_client_info, mock_metadata
    ):
        """Test updating existing OAuth client credentials"""
        user_id = "test_user"
        service_name = "test_service"

        # Mock existing token
        existing_token = Mock(spec=Token)
        existing_token.save = AsyncMock()

        # Mock user service
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            # Mock Token.find_one to return existing token
            with patch.object(Token, "find_one", AsyncMock(return_value=existing_token)):
                # Mock encrypt_value
                with patch("registry.services.oauth.token_service.encrypt_value") as mock_encrypt:
                    mock_encrypt.return_value = "updated_encrypted_iv:updated_ciphertext"

                    result = await token_service.store_oauth_client_credentials(
                        user_id=user_id,
                        service_name=service_name,
                        client_info=mock_client_info,
                        metadata=mock_metadata,
                    )

                    # Verify existing token was updated
                    assert result == existing_token
                    assert existing_token.token == "updated_encrypted_iv:updated_ciphertext"
                    assert existing_token.metadata == mock_metadata
                    existing_token.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_oauth_client_credentials_success(
        self, token_service, mock_user, mock_client_info, mock_metadata
    ):
        """Test retrieving OAuth client credentials"""
        user_id = "test_user"
        service_name = "test_service"

        # Mock stored token with encrypted data
        client_info_json = json.dumps(mock_client_info.dict())

        mock_token = Mock(spec=Token)
        mock_token.token = "encrypted_iv:encrypted_ciphertext"
        mock_token.metadata = mock_metadata

        # Mock user service
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            # Mock Token.find_one
            with patch.object(Token, "find_one", AsyncMock(return_value=mock_token)):
                # Mock decrypt_auth_fields to return decrypted JSON
                with patch("registry.services.oauth.token_service.decrypt_auth_fields") as mock_decrypt:
                    mock_decrypt.return_value = {"token": client_info_json}

                    client_info, metadata = await token_service.get_oauth_client_credentials(
                        user_id=user_id, service_name=service_name
                    )

                    # Verify decryption was called
                    mock_decrypt.assert_called_once_with({"token": "encrypted_iv:encrypted_ciphertext"})

                    # Verify result
                    assert isinstance(client_info, OAuthClientInformation)
                    assert client_info.client_id == "test_client_123"
                    assert client_info.client_secret == "test_secret_abc"
                    assert metadata == mock_metadata

    @pytest.mark.asyncio
    async def test_get_oauth_client_credentials_not_found(self, token_service, mock_user):
        """Test retrieving OAuth client credentials when none exist"""
        user_id = "test_user"
        service_name = "test_service"

        # Mock user service
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            # Mock Token.find_one to return None
            with patch.object(Token, "find_one", AsyncMock(return_value=None)):
                client_info, metadata = await token_service.get_oauth_client_credentials(
                    user_id=user_id, service_name=service_name
                )

                # Verify None returned
                assert client_info is None
                assert metadata is None

    @pytest.mark.asyncio
    async def test_client_credentials_identifier_format(self, token_service):
        """Test client credentials identifier format"""
        result = token_service._get_client_creds_identifier("notion")
        assert result == "mcp:notion:client"

        result = token_service._get_client_creds_identifier("google-drive")
        assert result == "mcp:google-drive:client"

    @pytest.mark.asyncio
    async def test_delete_oauth_tokens_includes_client_credentials(self, token_service, mock_user):
        """Test deleting OAuth tokens also deletes client credentials"""
        user_id = "test_user"
        service_name = "test_service"

        # Mock tokens
        access_token = Mock(spec=Token)
        access_token.delete = AsyncMock()
        refresh_token = Mock(spec=Token)
        refresh_token.delete = AsyncMock()
        client_creds = Mock(spec=Token)
        client_creds.delete = AsyncMock()

        # Mock user service
        with patch.object(token_service, "get_user_by_user_id", AsyncMock(return_value=str(mock_user.id))):
            # Mock Token.find_one to return all three token types
            async def mock_find_one(query):
                token_type = query.get("type")
                if token_type == TokenType.MCP_OAUTH.value:
                    return access_token
                elif token_type == TokenType.MCP_OAUTH_REFRESH.value:
                    return refresh_token
                elif token_type == TokenType.MCP_OAUTH_CLIENT.value:
                    return client_creds
                return None

            with patch.object(Token, "find_one", side_effect=mock_find_one):
                result = await token_service.delete_oauth_tokens(user_id=user_id, service_name=service_name)

                # Verify all three were deleted
                assert result is True
                access_token.delete.assert_awaited_once()
                refresh_token.delete.assert_awaited_once()
                client_creds.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_client_credentials_expiry_one_year(
        self, token_service, mock_user, mock_client_info, mock_metadata
    ):
        """Test client credentials are stored with 1 year expiry"""
        user_id = "test_user"
        service_name = "test_service"

        # Capture the Token constructor args
        captured_args = {}

        def capture_token_creation(**kwargs):
            captured_args.update(kwargs)
            mock_token = Mock(spec=Token)
            mock_token.insert = AsyncMock()
            for key, value in kwargs.items():
                setattr(mock_token, key, value)
            return mock_token

        # Mock user service
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry.services.oauth.token_service.Token", side_effect=capture_token_creation) as MockToken:
                MockToken.find_one = AsyncMock(return_value=None)

                with patch("registry.services.oauth.token_service.encrypt_value", return_value="encrypted"):
                    await token_service.store_oauth_client_credentials(
                        user_id=user_id,
                        service_name=service_name,
                        client_info=mock_client_info,
                        metadata=mock_metadata,
                    )

                    # Verify expiry is approximately 1 year from now
                    assert "expiresAt" in captured_args
                    now = datetime.now(UTC)
                    one_year_later = now + timedelta(days=365)

                    # Allow 5 seconds difference for test execution time
                    assert abs((captured_args["expiresAt"] - one_year_later).total_seconds()) < 5


class TestTokenServiceEncryption:
    """Tests for token encryption (bug fix validation)"""

    @pytest.fixture
    def token_service(self):
        """Create TokenService instance"""
        return TokenService()

    @pytest.mark.asyncio
    async def test_encrypt_uses_encrypt_value_not_encrypt_auth_fields(self, token_service):
        """Test that store_oauth_client_credentials uses encrypt_value for encryption"""
        mock_user = Mock()
        mock_user.id = PydanticObjectId("507f1f77bcf86cd799439011")
        mock_user.email = "test@example.com"

        client_info = OAuthClientInformation(
            client_id="test_client",
            client_secret="test_secret",
        )

        # Mock Token constructor to avoid Beanie initialization
        mock_token = Mock(spec=Token)
        mock_token.insert = AsyncMock()

        # Mock encrypt_value to verify it's called
        with patch.object(token_service, "get_user", AsyncMock(return_value=mock_user)):
            with patch("registry.services.oauth.token_service.Token") as MockToken:
                MockToken.find_one = AsyncMock(return_value=None)
                MockToken.return_value = mock_token

                with patch("registry.services.oauth.token_service.encrypt_value") as mock_encrypt_value:
                    mock_encrypt_value.return_value = "encrypted_value_result"

                    await token_service.store_oauth_client_credentials(
                        user_id="test_user",
                        service_name="test_service",
                        client_info=client_info,
                        metadata={},
                    )

                    # Verify encrypt_value was called (correct function)
                    mock_encrypt_value.assert_called_once()

                    # Verify the argument is a JSON string containing credentials
                    call_args = mock_encrypt_value.call_args[0][0]
                    assert isinstance(call_args, str)
                    assert "client_id" in call_args
                    assert client_info.client_id in call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
