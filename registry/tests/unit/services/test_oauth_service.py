from unittest.mock import AsyncMock, Mock, patch

import pytest
from bson import ObjectId

from registry.auth.oauth import FlowStateManager
from registry.schemas.enums import OAuthFlowStatus
from registry.schemas.oauth_schema import OAuthTokens
from registry.services.oauth.oauth_service import MCPOAuthService
from registry.services.oauth.token_service import TokenService
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer


class TestMCPOAuthService:
    """Unit tests for MCPOAuthService class"""

    @pytest.fixture
    def mock_flow_manager(self):
        """Mock FlowStateManager"""
        return Mock(spec=FlowStateManager)

    @pytest.fixture
    def mock_oauth_client(self):
        """Mock OAuthClient"""
        return Mock()

    @pytest.fixture
    def mock_token_service(self):
        return Mock(spec=TokenService)

    @pytest.fixture
    def oauth_service(self, mock_flow_manager, mock_oauth_client, mock_token_service):
        """Create MCPOAuthService instance with mocked dependencies"""
        service = MCPOAuthService(flow_manager=mock_flow_manager, token_service_instance=mock_token_service)
        service.oauth_client = mock_oauth_client
        return service

    @pytest.fixture
    def mock_server(self):
        """Mock ExtendedMCPServer"""
        server = Mock(spec=ExtendedMCPServer)
        server.id = ObjectId("507f1f77bcf86cd799439011")
        server.serverName = "test_server"
        server.path = "/test_server"
        server.config = {
            "oauth": {
                "authorization_url": "https://example.com/auth",
                "token_url": "https://example.com/token",
                "client_id": "test_client_id",
                "scope": "read write",
            },
            "requiresOAuth": True,
        }
        return server

    @pytest.fixture
    def mock_tokens(self):
        """Mock OAuthTokens"""
        tokens = Mock(spec=OAuthTokens)
        tokens.access_token = "test_access_token"
        tokens.refresh_token = "test_refresh_token"
        tokens.token_type = "Bearer"
        tokens.expires_in = 3600
        return tokens

    # Helper method to mock token_service responses
    def _mock_token_service(
        self, token_service_mock, access_exists=True, access_valid=True, refresh_exists=True, refresh_valid=True
    ):
        """Helper to mock token_service responses for status checks"""
        access_doc = Mock() if access_exists else None
        refresh_doc = Mock() if refresh_exists else None

        if access_doc:
            access_doc.token = "expired_access_token"

        if refresh_doc:
            refresh_doc.token = "valid_refresh_token"

        # Mock the async methods
        token_service_mock.get_access_token_status = AsyncMock(return_value=(access_doc, access_valid))
        token_service_mock.get_refresh_token_status = AsyncMock(return_value=(refresh_doc, refresh_valid))
        token_service_mock.get_oauth_tokens = AsyncMock(return_value=None)
        token_service_mock.store_oauth_tokens = AsyncMock()
        token_service_mock.has_refresh_token = AsyncMock(return_value=refresh_exists and refresh_valid)

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_prefers_injected_token_service(self, mock_flow_manager, mock_oauth_client):
        injected_token_service = AsyncMock()
        injected_token_service.store_oauth_client_credentials = AsyncMock()

        service = MCPOAuthService(
            flow_manager=mock_flow_manager,
            token_service_instance=injected_token_service,
        )
        service.oauth_client = mock_oauth_client

        user_id = "test_user"
        mock_server = Mock(spec=ExtendedMCPServer)
        mock_server.id = ObjectId("507f1f77bcf86cd799439011")
        mock_server.serverName = "test_server"
        mock_server.path = "/test_server"
        mock_server.config = {
            "oauth": {},
            "oauthMetadata": {
                "issuer": "https://example.com",
                "authorization_endpoint": "https://example.com/oauth/authorize",
                "token_endpoint": "https://example.com/oauth/token",
                "registration_endpoint": "https://example.com/oauth/register",
            },
        }

        from registry.schemas.oauth_schema import OAuthClientInformation

        mock_client_info = OAuthClientInformation(
            client_id="dcr_registered_client_123",
            client_secret="dcr_secret_abc",
            redirect_uris=["https://registry.example.com/api/v1/mcp/test_server/oauth/callback"],
            scope="read write",
        )
        service.oauth_client.register_client = AsyncMock(return_value=mock_client_info)
        service.oauth_client.build_authorization_url = AsyncMock(return_value="https://example.com/auth")

        mock_flow_manager.generate_flow_id = Mock(return_value="test_flow_id")
        mock_metadata = Mock()
        mock_metadata.client_info = mock_client_info
        mock_metadata.state = "test_flow_id##security_token"
        mock_flow_manager.create_flow_metadata = Mock(return_value=mock_metadata)
        mock_flow_manager.create_flow = Mock(return_value=Mock())

        flow_id, auth_url, error = await service.initiate_oauth_flow(user_id, mock_server)

        injected_token_service.store_oauth_client_credentials.assert_awaited_once()
        assert flow_id == "test_flow_id"
        assert auth_url == "https://example.com/auth"
        assert error is None

    # Tests for handle_reinitialize_auth method
    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_access_token_valid(self, oauth_service, mock_server):
        """Test handle_reinitialize_auth when access token exists and is valid"""
        user_id = "test_user"
        self._mock_token_service(
            oauth_service.token_service, access_exists=True, access_valid=True, refresh_exists=True, refresh_valid=True
        )

        needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)

        assert needs_connection
        assert response_data["success"]
        assert response_data["server_name"] == "test_server"
        assert "reinitialized successfully" in response_data["message"]

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_access_token_expired_refresh_valid(self, oauth_service, mock_server):
        """Test handle_reinitialize_auth when access token expired but refresh token is valid"""
        user_id = "test_user"
        self._mock_token_service(
            oauth_service.token_service, access_exists=True, access_valid=False, refresh_exists=True, refresh_valid=True
        )

        # Mock successful token refresh
        with patch.object(
            oauth_service,
            "_refresh_and_connect",
            AsyncMock(return_value=(True, {"success": True, "message": "Refreshed successfully"})),
        ):
            needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)
            assert needs_connection
            assert response_data["success"]

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_access_token_expired_refresh_invalid(self, oauth_service, mock_server):
        """Test handle_reinitialize_auth when access token expired and refresh token invalid"""
        user_id = "test_user"
        self._mock_token_service(
            oauth_service.token_service,
            access_exists=True,
            access_valid=False,
            refresh_exists=True,
            refresh_valid=False,
        )

        # Mock OAuth flow initiation
        with patch.object(
            oauth_service,
            "_build_oauth_required_response",
            AsyncMock(
                return_value=(
                    False,
                    {"success": True, "authorization_url": "https://example.com/auth", "requires_oauth": True},
                )
            ),
        ):
            needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)

            assert not needs_connection
            assert response_data["success"]
            assert "authorization_url" in response_data

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_no_access_token_refresh_valid(self, oauth_service, mock_server):
        """Test handle_reinitialize_auth when no access token but refresh token is valid"""
        user_id = "test_user"
        self._mock_token_service(
            oauth_service.token_service,
            access_exists=False,
            access_valid=False,
            refresh_exists=True,
            refresh_valid=True,
        )

        # Mock successful token refresh
        with patch.object(
            oauth_service,
            "_refresh_and_connect",
            AsyncMock(return_value=(True, {"success": True, "message": "Refreshed successfully"})),
        ):
            needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)

            assert needs_connection
            assert response_data["success"]

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_no_valid_tokens(self, oauth_service, mock_server):
        """Test handle_reinitialize_auth when no valid tokens exist"""
        user_id = "test_user"
        self._mock_token_service(
            oauth_service.token_service,
            access_exists=False,
            access_valid=False,
            refresh_exists=False,
            refresh_valid=False,
        )

        # Mock OAuth flow initiation
        with patch.object(
            oauth_service,
            "_build_oauth_required_response",
            AsyncMock(
                return_value=(
                    False,
                    {"success": True, "authorization_url": "https://example.com/auth", "requires_oauth": True},
                )
            ),
        ):
            needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)

            assert not needs_connection
            assert response_data["success"]
            assert "authorization_url" in response_data

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_oauth_config_missing(self, oauth_service):
        """Test handle_reinitialize_auth when OAuth configuration is missing"""
        user_id = "test_user"
        mock_server = Mock(spec=ExtendedMCPServer)
        mock_server.id = ObjectId("507f1f77bcf86cd799439011")
        mock_server.serverName = "test_server"
        mock_server.config = {}  # No OAuth config

        self._mock_token_service(
            oauth_service.token_service,
            access_exists=False,
            access_valid=False,
            refresh_exists=False,
            refresh_valid=False,
        )

        needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)

        assert not needs_connection
        assert response_data["success"]
        assert "OAuth authorization required" in response_data["message"]

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_refresh_failure(self, oauth_service, mock_server):
        """Test handle_reinitialize_auth when token refresh fails"""
        user_id = "test_user"
        self._mock_token_service(
            oauth_service.token_service, access_exists=True, access_valid=False, refresh_exists=True, refresh_valid=True
        )

        # Mock refresh failure
        with patch.object(
            oauth_service,
            "_refresh_and_connect",
            AsyncMock(return_value=(False, {"success": True, "authorization_url": "https://example.com/auth"})),
        ):
            needs_connection, response_data = await oauth_service.handle_reinitialize_auth(user_id, mock_server)

            assert not needs_connection
            assert response_data["success"]
            assert "authorization_url" in response_data

    # Tests for initiate_oauth_flow method
    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_success(self, oauth_service, mock_server):
        """Test successful initiation of OAuth flow"""
        user_id = "test_user"

        # Mock flow manager methods
        oauth_service.flow_manager.generate_flow_id = Mock(return_value="test_flow_id")

        # Create a proper metadata mock with required attributes
        mock_metadata = Mock()
        mock_metadata.client_info = Mock()
        mock_metadata.client_info.client_id = "test_client_id"
        mock_metadata.state = "test_flow_id##security_token"
        oauth_service.flow_manager.create_flow_metadata = Mock(return_value=mock_metadata)
        oauth_service.flow_manager.create_flow = Mock(return_value=Mock())

        # Mock HTTP client
        oauth_service.oauth_client.build_authorization_url = AsyncMock(
            return_value="https://example.com/auth?state=test"
        )

        # Mock crypto utils
        with patch(
            "registry.services.oauth.oauth_service.decrypt_auth_fields", return_value=mock_server.config["oauth"]
        ):
            flow_id, auth_url, error = await oauth_service.initiate_oauth_flow(user_id, mock_server)

            assert flow_id == "test_flow_id"
            assert auth_url == "https://example.com/auth?state=test"
            assert error is None

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_no_oauth_config(self, oauth_service, mock_server):
        """Test initiate_oauth_flow when OAuth configuration is missing (no DCR)"""
        user_id = "test_user"
        mock_server.config = {}  # No OAuth config and no oauthMetadata

        flow_id, auth_url, error = await oauth_service.initiate_oauth_flow(user_id, mock_server)

        assert flow_id is None
        assert auth_url is None
        assert "requires either client_id in oauth config or oauthMetadata for DCR" in error

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_dcr_success(self, oauth_service, mock_server):
        """Test successful OAuth flow initiation with DCR"""
        user_id = "test_user"

        # Mock server with DCR support (no client_id, but has oauthMetadata)
        mock_server.config = {
            "oauth": {},  # No client_id
            "oauthMetadata": {
                "issuer": "https://example.com",
                "authorization_endpoint": "https://example.com/oauth/authorize",
                "token_endpoint": "https://example.com/oauth/token",
                "registration_endpoint": "https://example.com/oauth/register",
                "scopes_supported": ["read", "write"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
            },
        }

        # Mock DCR registration
        from registry.schemas.oauth_schema import OAuthClientInformation

        mock_client_info = OAuthClientInformation(
            client_id="dcr_registered_client_123",
            client_secret="dcr_secret_abc",
            redirect_uris=["https://registry.example.com/api/v1/mcp/test_server/oauth/callback"],
            scope="read write",
        )
        oauth_service.oauth_client.register_client = AsyncMock(return_value=mock_client_info)

        # Mock token service
        oauth_service.token_service.store_oauth_client_credentials = AsyncMock()

        # Mock flow manager methods
        oauth_service.flow_manager.generate_flow_id = Mock(return_value="test_flow_id")
        mock_metadata = Mock()
        mock_metadata.client_info = mock_client_info
        mock_metadata.state = "test_flow_id##security_token"
        oauth_service.flow_manager.create_flow_metadata = Mock(return_value=mock_metadata)
        oauth_service.flow_manager.create_flow = Mock(return_value=Mock())

        # Mock authorization URL builder
        oauth_service.oauth_client.build_authorization_url = AsyncMock(
            return_value="https://example.com/auth?state=test&client_id=dcr_registered_client_123"
        )

        flow_id, auth_url, error = await oauth_service.initiate_oauth_flow(user_id, mock_server)

        # Verify DCR was called
        oauth_service.oauth_client.register_client.assert_awaited_once()

        # Verify credentials were stored
        oauth_service.token_service.store_oauth_client_credentials.assert_awaited_once()

        # Verify flow was created
        assert flow_id == "test_flow_id"
        assert auth_url is not None
        assert "dcr_registered_client_123" in auth_url
        assert error is None

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_dcr_no_registration_endpoint(self, oauth_service, mock_server):
        """Test OAuth flow fails when DCR needed but no registration_endpoint"""
        user_id = "test_user"

        # Mock server with oauthMetadata but no registration_endpoint
        mock_server.config = {
            "oauth": {},  # No client_id
            "oauthMetadata": {
                "issuer": "https://example.com",
                "authorization_endpoint": "https://example.com/oauth/authorize",
                "token_endpoint": "https://example.com/oauth/token",
                # No registration_endpoint
            },
        }

        flow_id, auth_url, error = await oauth_service.initiate_oauth_flow(user_id, mock_server)

        assert flow_id is None
        assert auth_url is None
        assert "requires client_id or dynamic client registration support" in error

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_dcr_registration_fails(self, oauth_service, mock_server):
        """Test OAuth flow fails when DCR registration fails"""
        user_id = "test_user"

        # Mock server with DCR support
        mock_server.config = {
            "oauth": {},
            "oauthMetadata": {
                "issuer": "https://example.com",
                "authorization_endpoint": "https://example.com/oauth/authorize",
                "token_endpoint": "https://example.com/oauth/token",
                "registration_endpoint": "https://example.com/oauth/register",
            },
        }

        # Mock DCR registration failure
        import httpx

        oauth_service.oauth_client.register_client = AsyncMock(
            side_effect=httpx.HTTPError("Client registration failed: 400")
        )

        flow_id, auth_url, error = await oauth_service.initiate_oauth_flow(user_id, mock_server)

        assert flow_id is None
        assert auth_url is None
        assert "Dynamic client registration failed" in error

    # Tests for complete_oauth_flow method
    @pytest.mark.asyncio
    async def test_complete_oauth_flow_success(self, oauth_service):
        """Test successful completion of OAuth flow"""
        flow_id = "test_flow_id"
        authorization_code = "test_code"
        state = "test_flow_id##security_token"

        # Mock flow manager
        mock_flow = Mock()
        mock_flow.state = state
        mock_flow.metadata = Mock()
        mock_flow.metadata.metadata = Mock(
            authorization_endpoint="https://example.com/auth",
            token_endpoint="https://example.com/token",
            issuer="example.com",
            scopes_supported=["read", "write"],
        )
        mock_flow.user_id = "test_user"
        mock_flow.server_name = "test_server"
        mock_flow.server_id = "507f1f77bcf86cd799439011"

        oauth_service.flow_manager.decode_state = Mock(
            return_value=({"flow_id": "test_flow_id", "security_token": "security_token"})
        )
        oauth_service.flow_manager.get_flow = Mock(return_value=mock_flow)
        oauth_service.flow_manager.is_flow_expired = Mock(return_value=False)
        oauth_service.flow_manager.complete_flow = Mock()

        # Mock HTTP client
        mock_tokens = Mock(spec=OAuthTokens)
        oauth_service.oauth_client.exchange_code_for_tokens = AsyncMock(return_value=mock_tokens)

        # Mock token service
        oauth_service.token_service.store_oauth_tokens = AsyncMock()

        success, error = await oauth_service.complete_oauth_flow(flow_id, authorization_code, state)

        assert success
        assert error is None
        oauth_service.token_service.store_oauth_tokens.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_invalid_state(self, oauth_service):
        """Test complete_oauth_flow with invalid state parameter"""
        flow_id = "test_flow_id"
        authorization_code = "test_code"
        state = "invalid_state"

        oauth_service.flow_manager.decode_state = Mock(side_effect=ValueError("Invalid state"))
        success, error = await oauth_service.complete_oauth_flow(flow_id, authorization_code, state)

        assert not success
        assert "Invalid state format" in error

    # Tests for get_valid_access_token method
    @pytest.mark.asyncio
    async def test_get_valid_access_token_valid_token(self, oauth_service, mock_server):
        """Test get_valid_access_token when token is valid"""
        user_id = "test_user"

        # Mock token service to return valid token
        oauth_service.token_service.is_access_token_expired = AsyncMock(return_value=False)
        mock_tokens = Mock(spec=OAuthTokens)
        mock_tokens.access_token = "valid_access_token"
        oauth_service.token_service.get_oauth_tokens = AsyncMock(return_value=mock_tokens)

        token, auth_url, error = await oauth_service.get_valid_access_token(user_id, mock_server)

        assert token == "valid_access_token"
        assert auth_url is None
        assert error is None

    @pytest.mark.asyncio
    async def test_get_valid_access_token_refresh_success(self, oauth_service, mock_server):
        """Test get_valid_access_token when token needs refresh and refresh succeeds"""
        user_id = "test_user"

        # Mock token service
        oauth_service.token_service.is_access_token_expired = AsyncMock(return_value=True)
        oauth_service.token_service.has_refresh_token = AsyncMock(return_value=True)

        # Mock successful refresh
        oauth_service.validate_and_refresh_tokens = AsyncMock(return_value=(True, None))

        mock_tokens = Mock(spec=OAuthTokens)
        mock_tokens.access_token = "refreshed_access_token"
        oauth_service.token_service.get_oauth_tokens = AsyncMock(return_value=mock_tokens)

        token, auth_url, error = await oauth_service.get_valid_access_token(user_id, mock_server)

        assert token == "refreshed_access_token"
        assert auth_url is None
        assert error is None

    # Tests for refresh_token method
    @pytest.mark.asyncio
    async def test_refresh_token_success(self, oauth_service):
        """Test successful token refresh"""
        user_id = "test_user"
        server_id = "507f1f77bcf86cd799439011"
        server_name = "test_server"
        refresh_token_value = "valid_refresh_token"
        oauth_config = {
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
            "client_id": "test_client_id",
            "scope": "read write",
            "issuer": "example.com",
        }

        # Mock HTTP client
        mock_tokens = Mock(spec=OAuthTokens)
        mock_tokens.access_token = "new_access_token"
        mock_tokens.refresh_token = "new_refresh_token"  # Simulate token rotation
        oauth_service.oauth_client.refresh_tokens = AsyncMock(return_value=mock_tokens)

        # Mock token service methods
        oauth_service.token_service.get_oauth_client_credentials = AsyncMock(return_value=(None, None))
        oauth_service.token_service.store_oauth_tokens = AsyncMock()

        success, error = await oauth_service.refresh_token(
            user_id, server_id, server_name, refresh_token_value, oauth_config
        )

        assert success
        assert error is None
        oauth_service.token_service.store_oauth_tokens.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self, oauth_service):
        """Test token refresh failure"""
        user_id = "test_user"
        server_id = "507f1f77bcf86cd799439011"
        server_name = "test_server"
        refresh_token_value = "invalid_refresh_token"
        oauth_config = {
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
            "client_id": "test_client_id",
        }

        # Mock HTTP client to return None (failure)
        oauth_service.oauth_client.refresh_tokens = AsyncMock(return_value=None)

        # Mock token service
        oauth_service.token_service.get_oauth_client_credentials = AsyncMock(return_value=(None, None))

        success, error = await oauth_service.refresh_token(
            user_id, server_id, server_name, refresh_token_value, oauth_config
        )

        assert not success
        assert "Token refresh failed" in error

    # Tests for other important methods
    @pytest.mark.asyncio
    async def test_get_tokens_success(self, oauth_service):
        """Test successful retrieval of tokens from database"""
        user_id = "test_user"
        server_name = "test_server"

        mock_tokens = Mock(spec=OAuthTokens)
        oauth_service.token_service.get_oauth_tokens = AsyncMock(return_value=mock_tokens)

        result = await oauth_service.get_tokens(user_id, server_name)

        assert result == mock_tokens
        oauth_service.token_service.get_oauth_tokens.assert_awaited_once_with(user_id, server_name)

    @pytest.mark.asyncio
    async def test_cancel_oauth_flow_success(self, oauth_service):
        """Test successful cancellation of OAuth flow"""
        user_id = "test_user"
        server_id = "507f1f77bcf86cd799439011"

        oauth_service.flow_manager.cancel_user_flow = Mock(return_value=True)
        success, error = await oauth_service.cancel_oauth_flow(user_id, server_id)

        assert success
        assert error is None

    @pytest.mark.asyncio
    async def test_has_active_flow_true(self, oauth_service):
        """Test has_active_flow returns True when active flow exists"""
        user_id = "test_user"
        server_name = "test_server"

        mock_flow = Mock()
        oauth_service.flow_manager.get_user_flows = Mock(return_value=[mock_flow])

        result = await oauth_service.has_active_flow(user_id, server_name)

        assert result

    @pytest.mark.asyncio
    async def test_has_failed_flow_true(self, oauth_service):
        """Test has_failed_flow returns True when failed flow exists"""
        user_id = "test_user"
        server_name = "test_server"

        mock_flow = Mock()
        mock_flow.status = OAuthFlowStatus.FAILED
        oauth_service.flow_manager.get_user_flows = Mock(return_value=[mock_flow])

        result = await oauth_service.has_failed_flow(user_id, server_name)
        assert result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
