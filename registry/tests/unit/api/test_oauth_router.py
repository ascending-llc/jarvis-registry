from unittest.mock import AsyncMock, Mock, patch

import pytest
from bson import ObjectId
from fastapi import Request
from fastapi.testclient import TestClient

from registry.api.v1.mcp.oauth_router import router
from registry.services.oauth.mcp_service import MCPService

# Valid MongoDB ObjectId for testing (24 hex characters)
TEST_SERVER_ID = "507f1f77bcf86cd799439011"

# Create a mock MCP service
mock_mcp_service = Mock(spec=MCPService)

# Create a mock OAuth service and attach it to MCP service
mock_oauth_service = Mock()
mock_mcp_service.oauth_service = mock_oauth_service

# Create a mock Connection service and attach it to MCP service
mock_connection_service = Mock()
mock_mcp_service.connection_service = mock_connection_service


# Wrap all mock methods to make them async
def make_async(func):
    async def async_func(*args, **kwargs):
        return func(*args, **kwargs)

    return async_func


# Make all mock methods async for oauth_service
for attr in dir(mock_oauth_service):
    if attr.startswith("mock_") or attr in [
        "initiate_oauth_flow",
        "get_tokens_by_flow_id",
        "get_flow_status",
        "cancel_oauth_flow",
        "refresh_tokens",
        "complete_oauth_flow",
        "flow_manager",
    ]:
        continue

    method = getattr(mock_oauth_service, attr, None)
    if callable(method) and not attr.startswith("_"):
        setattr(mock_oauth_service, attr, make_async(method))

mock_connection_service.update_connection_state = AsyncMock()
mock_connection_service.get_connection = AsyncMock()
mock_connection_service.create_user_connection = AsyncMock()
mock_connection_service.disconnect_user_connection = AsyncMock(return_value=True)

# Also mock the flow_manager methods
mock_flow_manager = Mock()
mock_oauth_service.flow_manager = mock_flow_manager

# Make flow_manager methods async if any
for attr in dir(mock_flow_manager):
    method = getattr(mock_flow_manager, attr, None)
    if callable(method) and not attr.startswith("_"):
        setattr(mock_flow_manager, attr, make_async(method))


# Set up the TestClient with the necessary dependencies overridden
@pytest.fixture
def client():
    from fastapi import FastAPI

    app = FastAPI()

    app.include_router(router)

    @app.middleware("http")
    async def mock_authenticated_middleware(request: Request, call_next):
        # Set up authenticated user context
        request.state.user = {
            "username": "test_user",
            "user_id": "test_user",
            "id": "test_user_id",
            "groups": ["registry-admin"],
            "scopes": ["registry-admin"],
            "is_admin": True,
        }
        request.state.is_authenticated = True
        response = await call_next(request)
        return response

    # Note: We need to override the actual get_mcp_service function from the module
    from registry.services.oauth.mcp_service import get_mcp_service

    # Override the get_mcp_service dependency
    app.dependency_overrides[get_mcp_service] = lambda: mock_mcp_service

    # Mock server config
    mock_server = Mock()
    mock_server.id = ObjectId(TEST_SERVER_ID)
    mock_server.serverName = "test_server"
    mock_server.path = "/test_server"  # Path stored in DB includes leading slash
    mock_server.config = {"oauth": {"provider": "github", "client_id": "test_client_id"}}

    with patch("registry.api.v1.mcp.oauth_router.server_service_v1") as mock_server_service:
        mock_server_service.get_server_by_id = AsyncMock(return_value=mock_server)
        yield TestClient(app)


class TestOAuthRouter:
    """Test cases for the OAuth router endpoints"""

    def test_initiate_oauth_flow_success(self, client):
        """Test successful initiation of OAuth flow"""
        # Mock the MCP service to return a valid response
        mock_mcp_service.oauth_service.initiate_oauth_flow = make_async(
            lambda *args, **kwargs: ("test_user-1234567890", "https://example.com/auth", None)
        )

        response = client.get(f"/mcp/{TEST_SERVER_ID}/oauth/initiate")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["flow_id"] == "test_user-1234567890"
        assert response_data["authorization_url"] == "https://example.com/auth"
        assert response_data["server_id"] == TEST_SERVER_ID
        assert response_data["user_id"] == "test_user"

    def test_initiate_oauth_flow_failure(self, client):
        """Test initiation of OAuth flow with error"""
        # Mock the MCP service to return an error
        mock_mcp_service.oauth_service.initiate_oauth_flow = make_async(
            lambda *args, **kwargs: (None, None, "Failed to initiate OAuth flow")
        )

        response = client.get(f"/mcp/{TEST_SERVER_ID}/oauth/initiate")

        assert response.status_code == 400
        assert "Failed to initiate OAuth flow" in response.json()["detail"]

    def test_get_oauth_tokens_success(self, client):
        """Test successful retrieval of OAuth tokens"""
        # Mock token response
        mock_token = Mock()
        mock_token.dict.return_value = {
            "access_token": "test_access_token",
            "refresh_token": "test_refresh_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        mock_mcp_service.oauth_service.get_tokens_by_flow_id = make_async(lambda *args, **kwargs: mock_token)

        # The flow_id should start with the user_id to pass the validation
        flow_id = "test_user-1234567890"

        response = client.get(f"/mcp/oauth/tokens/{flow_id}")

        assert response.status_code == 200
        response_data = response.json()
        assert "tokens" in response_data
        assert response_data["tokens"]["access_token"] == "test_access_token"

    def test_get_oauth_tokens_unauthorized(self, client):
        """Test retrieval of OAuth tokens for a different user"""
        # Flow ID doesn't match the current user
        flow_id = "other_user-1234567890"

        response = client.get(f"/mcp/oauth/tokens/{flow_id}")

        assert response.status_code == 403

    def test_get_oauth_status_success(self, client):
        """Test successful retrieval of OAuth status"""
        # Mock status response
        mock_status = {"flow_id": "test_user-1234567890", "status": "completed", "server_id": TEST_SERVER_ID}
        mock_mcp_service.oauth_service.get_flow_status = make_async(lambda *args, **kwargs: mock_status)

        flow_id = "test_user-1234567890"

        response = client.get(f"/mcp/oauth/status/{flow_id}")

        assert response.status_code == 200
        assert response.json() == mock_status

    def test_cancel_oauth_flow_success(self, client):
        """Test successful cancellation of OAuth flow"""
        # Mock successful cancellation
        mock_mcp_service.oauth_service.cancel_oauth_flow = make_async(lambda *args, **kwargs: (True, None))

        mock_mcp_service.connection_service.update_connection_state = AsyncMock(return_value=None)
        response = client.post(f"/mcp/oauth/cancel/{TEST_SERVER_ID}")

        assert response.status_code == 200
        assert response.json()["success"]
        assert response.json()["server_id"] == TEST_SERVER_ID
        assert "cancelled successfully" in response.json()["message"]

    def test_cancel_oauth_flow_failure(self, client):
        """Test failed cancellation of OAuth flow"""
        # Mock failed cancellation
        mock_mcp_service.oauth_service.cancel_oauth_flow = make_async(
            lambda *args, **kwargs: (False, "Failed to cancel flow")
        )

        response = client.post(f"/mcp/oauth/cancel/{TEST_SERVER_ID}")

        assert response.status_code == 400
        assert "Failed to cancel flow" in response.json()["detail"]

    def test_refresh_oauth_tokens_success(self, client):
        """Test successful refresh of OAuth tokens"""
        # Mock successful validation and refresh
        mock_mcp_service.oauth_service.validate_and_refresh_tokens = make_async(lambda *args, **kwargs: (True, None))

        mock_connection = Mock()
        mock_mcp_service.connection_service.get_connection = AsyncMock(return_value=mock_connection)
        mock_mcp_service.connection_service.update_connection_state = AsyncMock(return_value=None)

        response = client.post(f"/mcp/oauth/refresh/{TEST_SERVER_ID}")

        assert response.status_code == 200
        assert response.json()["success"]
        assert response.json()["server_id"] == TEST_SERVER_ID
        assert "refreshed successfully" in response.json()["message"]

    def test_refresh_oauth_tokens_failure(self, client):
        """Test failed refresh of OAuth tokens"""
        # Mock failed validation and refresh
        mock_mcp_service.oauth_service.validate_and_refresh_tokens = make_async(
            lambda *args, **kwargs: (False, "Failed to refresh tokens")
        )

        response = client.post(f"/mcp/oauth/refresh/{TEST_SERVER_ID}")

        assert response.status_code == 400
        assert "Failed to refresh tokens" in response.json()["detail"]

    def test_oauth_callback_success(self, client):
        """Test successful OAuth callback"""

        # Mock the flow_manager methods - note: decode_state is NOT async in the actual code
        mock_mcp_service.oauth_service.flow_manager.decode_state = lambda state: ("test_user-flow123", "security_token")

        # Mock get_flow to return a flow with user_id and server_id - note: get_flow is NOT async in the actual code
        mock_flow = Mock()
        mock_flow.user_id = "test_user"
        mock_flow.server_id = TEST_SERVER_ID
        mock_flow.status = "pending"
        mock_mcp_service.oauth_service.flow_manager.get_flow = lambda flow_id: mock_flow

        # Mock successful completion
        mock_mcp_service.oauth_service.complete_oauth_flow = make_async(lambda *args, **kwargs: (True, None))

        # Mock connection service
        mock_mcp_service.connection_service.create_user_connection = AsyncMock(return_value=None)

        # Make the request with code and state, disable redirect following
        # Note: The router path is /{server_path}/oauth/callback where server_path is a path segment
        # So the URL /mcp/test_server/oauth/callback has server_path="test_server"
        response = client.get(
            "/mcp/test_server/oauth/callback?code=auth_code&state=test_user-flow123##security_token",
            follow_redirects=False,
        )

        # Should redirect to success page
        assert response.status_code == 307
        assert "oauth-callback?type=success" in response.headers["location"]
        # The serverPath query parameter should contain the server_path from the URL
        assert "serverPath=test_server" in response.headers["location"]

    def test_oauth_callback_missing_code(self, client):
        """Test OAuth callback with missing code parameter"""
        response = client.get("/mcp/test_server/oauth/callback?state=some_state", follow_redirects=False)

        assert response.status_code == 307
        assert "oauth-callback?type=error" in response.headers["location"]
        assert "serverPath=test_server" in response.headers["location"]
        assert "error=missing_code" in response.headers["location"]

    def test_oauth_callback_missing_state(self, client):
        """Test OAuth callback with missing state parameter"""
        response = client.get("/mcp/test_server/oauth/callback?code=auth_code", follow_redirects=False)

        assert response.status_code == 307
        assert "oauth-callback?type=error" in response.headers["location"]
        assert "serverPath=test_server" in response.headers["location"]
        assert "error=missing_state" in response.headers["location"]

    def test_oauth_callback_provider_error(self, client):
        """Test OAuth callback with error from provider"""
        response = client.get("/mcp/test_server/oauth/callback?error=access_denied", follow_redirects=False)

        assert response.status_code == 307
        assert "oauth-callback?type=error" in response.headers["location"]
        assert "serverPath=test_server" in response.headers["location"]
        assert "error=access_denied" in response.headers["location"]

    def test_oauth_callback_invalid_state_format(self, client):
        """Test OAuth callback with invalid state format"""
        # Mock decode_state to raise ValueError - note: decode_state is NOT async in the actual code
        mock_mcp_service.oauth_service.flow_manager.decode_state = Mock(side_effect=ValueError("Invalid state format"))
        response = client.get(
            "/mcp/test_server/oauth/callback?code=auth_code&state=invalid_state", follow_redirects=False
        )

        assert response.status_code == 307
        assert "oauth-callback?type=error" in response.headers["location"]
        assert "serverPath=test_server" in response.headers["location"]
        assert "error=invalid_state_format" in response.headers["location"]

    def test_oauth_callback_already_completed(self, client):
        """Test OAuth callback when flow is already completed"""
        from registry.schemas.enums import OAuthFlowStatus

        # Mock decode_state - note: decode_state is NOT async in the actual code
        mock_mcp_service.oauth_service.flow_manager.decode_state = lambda state: ("test_user-flow123", "security_token")

        # Mock get_flow to return completed flow - note: get_flow is NOT async in the actual code
        mock_flow = Mock()
        mock_flow.status = OAuthFlowStatus.COMPLETED
        mock_mcp_service.oauth_service.flow_manager.get_flow = lambda flow_id: mock_flow

        response = client.get(
            "/mcp/test_server/oauth/callback?code=auth_code&state=test_user-flow123##security_token",
            follow_redirects=False,
        )

        # Should still redirect to success page but not exchange tokens again
        assert response.status_code == 307
        assert "oauth-callback?type=success" in response.headers["location"]
        assert "serverPath=test_server" in response.headers["location"]

    def test_delete_oauth_tokens_success(self, client):
        """Test successful deletion of OAuth tokens"""

        mock_mcp_service.connection_service.disconnect_user_connection = AsyncMock(return_value=True)

        with patch("registry.api.v1.mcp.oauth_router.token_service") as mock_token_service:
            mock_token_service.delete_oauth_tokens = AsyncMock(return_value=True)

            response = client.delete(f"/mcp/oauth/token/{TEST_SERVER_ID}")

            assert response.status_code == 200
            response_data = response.json()
            assert response_data["success"]
            assert response_data["server_id"] == TEST_SERVER_ID
            assert response_data["user_id"] == "test_user"
            assert "oauth delete successfully" in response_data["message"]

    def test_delete_oauth_tokens_failure(self, client):
        """Test failed deletion of OAuth tokens"""

        mock_mcp_service.connection_service.disconnect_user_connection = AsyncMock(return_value=False)

        with patch("registry.api.v1.mcp.oauth_router.token_service") as mock_token_service:
            mock_token_service.delete_oauth_tokens = AsyncMock(return_value=False)

            response = client.delete(f"/mcp/oauth/token/{TEST_SERVER_ID}")

            assert response.status_code == 200  # Note: The endpoint returns 200 even when delete returns False
            response_data = response.json()
            assert not response_data["success"]
            assert response_data["server_id"] == TEST_SERVER_ID
            assert response_data["user_id"] == "test_user"
            assert "oauth delete failed" in response_data["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
