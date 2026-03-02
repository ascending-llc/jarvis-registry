from unittest.mock import AsyncMock, Mock, patch

import pytest
from bson import ObjectId
from fastapi import Request
from fastapi.testclient import TestClient

from registry.api.v1.mcp.connection_router import router
from registry.schemas.enums import ConnectionState
from registry.services.oauth.mcp_service import MCPService, get_mcp_service

# Valid MongoDB ObjectId for testing (24 hex characters)
TEST_SERVER_ID = "507f1f77bcf86cd799439011"
TEST_SERVER_NAME = "test_server"

# Create a mock MCP service
mock_mcp_service = Mock(spec=MCPService)

# Create a mock OAuth service and attach it to MCP service
mock_oauth_service = AsyncMock()
mock_mcp_service.oauth_service = mock_oauth_service

# Create a mock Connection service and attach it to MCP service
mock_connection_service = AsyncMock()
mock_mcp_service.connection_service = mock_connection_service

# Mock server_service_v1
mock_server_service_v1 = AsyncMock()

# Mock connection status service functions
mock_get_servers_connection_status = AsyncMock()
mock_get_single_server_connection_status = AsyncMock()


# Wrap all mock methods to make them async
def make_async(func):
    async def async_func(*args, **kwargs):
        return func(*args, **kwargs)

    return async_func


# Make all mock methods async for oauth_service
for attr in dir(mock_oauth_service):
    if attr.startswith("mock_") or attr in ["handle_reinitialize_auth", "get_tokens"]:
        continue

    method = getattr(mock_oauth_service, attr, None)
    if callable(method) and not attr.startswith("_"):
        setattr(mock_oauth_service, attr, make_async(method))

# Make all mock methods async for connection_service
for attr in dir(mock_connection_service):
    if attr.startswith("mock_") or attr in ["disconnect_user_connection", "create_user_connection"]:
        continue

    method = getattr(mock_connection_service, attr, None)
    if callable(method) and not attr.startswith("_"):
        setattr(mock_connection_service, attr, make_async(method))


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

    # Override the get_mcp_service dependency
    app.dependency_overrides[get_mcp_service] = lambda: mock_mcp_service

    # Mock server_service_v1
    with patch("registry.api.v1.mcp.connection_router.server_service_v1", mock_server_service_v1):
        # Mock connection status service functions
        with patch(
            "registry.api.v1.mcp.connection_router.get_servers_connection_status", mock_get_servers_connection_status
        ):
            with patch(
                "registry.api.v1.mcp.connection_router.get_single_server_connection_status",
                mock_get_single_server_connection_status,
            ):
                yield TestClient(app)


class TestConnectionRouter:
    """Test cases for the connection router endpoints"""

    def test_reinitialize_server_success(self, client):
        """Test successful reinitialization of server connection"""
        # Mock server config
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        # Mock server_service_v1.get_server_by_id to return the mock server
        mock_server_service_v1.get_server_by_id.return_value = mock_server

        # Mock connection service to successfully disconnect
        mock_mcp_service.connection_service.disconnect_user_connection.return_value = True

        # Mock OAuth service to return needs_connection=True and response data
        mock_mcp_service.oauth_service.handle_reinitialize_auth.return_value = (True, {"status": "success"})

        # Mock connection service to successfully create connection
        mock_mcp_service.connection_service.create_user_connection.return_value = None

        response = client.post(f"/mcp/{TEST_SERVER_ID}/reinitialize")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["status"] == "success"

    def test_reinitialize_server_not_found(self, client):
        """Test reinitialization with non-existent server"""
        # Mock server_service_v1.get_server_by_id to return None
        mock_server_service_v1.get_server_by_id.return_value = None

        response = client.post(f"/mcp/{TEST_SERVER_ID}/reinitialize")

        # Endpoint raises HTTPException 404
        assert response.status_code == 404
        assert "Server not found" in response.json()["detail"]

    def test_reinitialize_server_disconnect_failure(self, client):
        """Test reinitialization when disconnect fails but continues"""
        # Mock server config
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        # Mock server_service_v1.get_server_by_id to return the mock server
        mock_server_service_v1.get_server_by_id.return_value = mock_server

        # Mock connection service to fail disconnect
        mock_mcp_service.connection_service.disconnect_user_connection.return_value = False

        # Mock OAuth service to return needs_connection=True and response data
        mock_mcp_service.oauth_service.handle_reinitialize_auth.return_value = (True, {"status": "success"})

        # Mock connection service to successfully create connection
        mock_mcp_service.connection_service.create_user_connection.return_value = None

        response = client.post(f"/mcp/{TEST_SERVER_ID}/reinitialize")

        # Should still succeed even if disconnect fails
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["status"] == "success"

    def test_reinitialize_server_oauth_failure(self, client):
        """Test reinitialization when OAuth handling fails"""
        # Mock server config
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        # Mock server_service_v1.get_server_by_id to return the mock server
        mock_server_service_v1.get_server_by_id.return_value = mock_server

        # Mock connection service to successfully disconnect
        mock_mcp_service.connection_service.disconnect_user_connection.return_value = True

        # Mock OAuth service to raise an exception
        mock_mcp_service.oauth_service.handle_reinitialize_auth.side_effect = Exception("OAuth error")

        response = client.post(f"/mcp/{TEST_SERVER_ID}/reinitialize")

        # Should return 500 internal server error
        assert response.status_code == 500
        assert "Internal server error" in response.json()["detail"]

    def test_reinitialize_server_connection_failure(self, client):
        """Test reinitialization when connection creation fails"""
        # Mock server config
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        # Mock server_service_v1.get_server_by_id to return the mock server
        mock_server_service_v1.get_server_by_id.return_value = mock_server

        # Mock connection service to successfully disconnect
        mock_mcp_service.connection_service.disconnect_user_connection.return_value = True

        # Mock OAuth service to return needs_connection=True and response data
        mock_mcp_service.oauth_service.handle_reinitialize_auth.return_value = (True, {"status": "success"})

        # Mock connection service to fail creating connection
        mock_mcp_service.connection_service.create_user_connection.side_effect = Exception("Connection failed")

        response = client.post(f"/mcp/{TEST_SERVER_ID}/reinitialize")

        # Should return 500 internal server error
        assert response.status_code == 500
        assert "Internal server error" in response.json()["detail"]

    def test_get_all_connection_status_success(self, client):
        """Test successful retrieval of all connection statuses"""
        # Mock server_service_v1.list_servers to return a list of servers
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        mock_server_service_v1.list_servers.return_value = ([mock_server], 1)

        # Mock get_servers_connection_status to return status
        mock_get_servers_connection_status.return_value = [
            {
                "server_id": TEST_SERVER_ID,
                "server_name": TEST_SERVER_NAME,
                "connection_state": ConnectionState.CONNECTED,
                "requires_oauth": True,
            }
        ]

        response = client.get("/mcp/connection/status")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"]
        assert "connectionStatus" in response_data
        assert len(response_data["connectionStatus"]) == 1
        assert response_data["connectionStatus"][0]["server_id"] == TEST_SERVER_ID

    def test_get_all_connection_status_empty(self, client):
        """Test retrieval of all connection statuses when no servers exist"""
        # Mock server_service_v1.list_servers to return empty list
        mock_server_service_v1.list_servers.return_value = ([], 0)

        # Mock get_servers_connection_status to return empty list
        mock_get_servers_connection_status.return_value = []

        response = client.get("/mcp/connection/status")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"]
        assert "connectionStatus" in response_data
        assert len(response_data["connectionStatus"]) == 0

    def test_get_all_connection_status_failure(self, client):
        """Test failure when retrieving all connection statuses"""
        # Mock server_service_v1.list_servers to raise an exception
        mock_server_service_v1.list_servers.side_effect = Exception("Database error")

        response = client.get("/mcp/connection/status")

        assert response.status_code == 500
        assert "Failed to get connection status" in response.json()["detail"]

    def test_get_server_connection_status_success(self, client):
        """Test successful retrieval of single server connection status"""
        # Mock server config
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        # Mock server_service_v1.get_server_by_id to return the mock server
        mock_server_service_v1.get_server_by_id.return_value = mock_server

        # Mock get_single_server_connection_status to return status
        mock_get_single_server_connection_status.return_value = {
            "connection_state": ConnectionState.CONNECTED,
            "requires_oauth": True,
        }

        response = client.get(f"/mcp/connection/status/{TEST_SERVER_ID}")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"]
        assert response_data["serverName"] == TEST_SERVER_NAME
        assert response_data["connectionState"] == ConnectionState.CONNECTED
        assert response_data["requiresOAuth"]
        assert response_data["serverId"] == TEST_SERVER_ID

    def test_get_server_connection_status_not_found(self, client):
        """Test retrieval of single server connection status when server not found"""
        # Mock server_service_v1.get_server_by_id to return None
        mock_server_service_v1.get_server_by_id.return_value = None

        response = client.get(f"/mcp/connection/status/{TEST_SERVER_ID}")

        assert response.status_code == 404
        assert "Server not found" in response.json()["detail"]

    def test_get_server_connection_status_failure(self, client):
        """Test failure when retrieving single server connection status"""
        # Mock get_server_config to return a server
        mock_server = Mock()
        mock_server.id = ObjectId(TEST_SERVER_ID)
        mock_server.serverName = TEST_SERVER_NAME
        mock_server.config = {"oauth": {"provider": "github"}}

        # Mock server_service_v1.get_server_by_id to return the mock server
        mock_server_service_v1.get_server_by_id.return_value = mock_server

        mock_get_single_server_connection_status.side_effect = Exception("Status error")

        response = client.get(f"/mcp/connection/status/{TEST_SERVER_ID}")

        assert response.status_code == 500
        assert f"Failed to get connection status for {TEST_SERVER_ID}" in response.json()["detail"]

    def test_check_auth_values_with_tokens(self, client):
        """Test checking auth values when OAuth tokens exist"""
        mock_tokens = Mock()
        mock_mcp_service.oauth_service.get_tokens.return_value = mock_tokens

        response = client.get(f"/mcp/{TEST_SERVER_NAME}/auth-values")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"]
        assert response_data["server_name"] == TEST_SERVER_NAME
        assert response_data["auth_value_flags"]["oauth_tokens"]

    def test_check_auth_values_without_tokens(self, client):
        """Test checking auth values when no OAuth tokens exist"""
        mock_mcp_service.oauth_service.get_tokens.return_value = None

        response = client.get(f"/mcp/{TEST_SERVER_NAME}/auth-values")

        assert response.status_code == 200
        response_data = response.json()
        assert response_data["success"]
        assert response_data["server_name"] == TEST_SERVER_NAME
        assert not response_data["auth_value_flags"]["oauth_tokens"]

    def test_check_auth_values_failure(self, client):
        """Test failure when checking auth values"""
        mock_mcp_service.oauth_service.get_tokens.side_effect = Exception("Token error")

        response = client.get(f"/mcp/{TEST_SERVER_NAME}/auth-values")

        assert response.status_code == 500
        assert f"Failed to check auth values for {TEST_SERVER_NAME}" in response.json()["detail"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
