"""
Unit tests for health monitoring routes.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from registry.health.routes import health_status_http, router, websocket_endpoint


@pytest.mark.unit
@pytest.mark.health
class TestHealthRoutes:
    """Test suite for health monitoring routes."""

    @pytest.fixture
    def mock_session_cookie(self):
        """Create a valid session cookie for testing WebSocket (uses old itsdangerous format).

        TODO: Update WebSocket authentication to support JWT tokens, then update this fixture.
        WebSocket route currently uses itsdangerous signer, not JWT validation.
        """
        from registry.auth.dependencies import signer
        from registry.core.config import settings

        # WebSocket authentication still uses itsdangerous signer
        session_data = {
            "username": settings.admin_user,
            "auth_method": "traditional",
            "provider": "local",
            "groups": ["registry-admin"],
        }

        return signer.dumps(session_data)

    @pytest.fixture
    def mock_websocket(self, mock_session_cookie):
        """Create a mock WebSocket."""
        from registry.core.config import settings

        websocket = Mock(spec=WebSocket)
        websocket.client = "127.0.0.1:12345"
        websocket.accept = AsyncMock()
        websocket.receive_text = AsyncMock()
        websocket.send_json = AsyncMock()
        websocket.close = AsyncMock()
        websocket.ping = AsyncMock()
        # Add mock cookies and headers for authentication with actual valid session
        websocket.cookies = {settings.session_cookie_name: mock_session_cookie}
        websocket.headers = {"cookie": f"{settings.session_cookie_name}={mock_session_cookie}"}
        websocket.query_params = {}
        return websocket

    @pytest.fixture
    def mock_health_service(self):
        """Mock health service."""
        with patch("registry.health.routes.health_service") as mock_service:
            mock_service.add_websocket_connection = AsyncMock(return_value=True)
            mock_service.remove_websocket_connection = AsyncMock()
            mock_service.get_all_health_status.return_value = {
                "service1": {"status": "healthy", "last_check": "2023-01-01T00:00:00Z"},
                "service2": {"status": "unhealthy", "last_check": "2023-01-01T00:00:00Z"},
            }
            yield mock_service

    @pytest.fixture
    def mock_signer(self):
        """Mock session signer - not needed anymore since we use real session cookies."""
        # No longer mocking signer since we're using real create_session_cookie
        pass

    @pytest.mark.asyncio
    async def test_websocket_endpoint_normal_operation(self, mock_websocket, mock_health_service, mock_signer):
        """Test normal WebSocket operation."""
        # Setup receive_text to raise WebSocketDisconnect after one call
        mock_websocket.receive_text.side_effect = [
            "ping",  # First call succeeds
            WebSocketDisconnect(),  # Second call disconnects
        ]

        await websocket_endpoint(mock_websocket)

        # Verify connection was added and removed
        mock_health_service.add_websocket_connection.assert_called_once_with(mock_websocket)
        mock_health_service.remove_websocket_connection.assert_called_once_with(mock_websocket)

        # Verify receive_text was called
        assert mock_websocket.receive_text.call_count >= 1

    @pytest.mark.asyncio
    async def test_websocket_endpoint_disconnect(self, mock_websocket, mock_health_service, mock_signer):
        """Test WebSocket disconnection handling."""
        # Setup immediate disconnect
        mock_websocket.receive_text.side_effect = WebSocketDisconnect()

        await websocket_endpoint(mock_websocket)

        # Verify connection was added and removed
        mock_health_service.add_websocket_connection.assert_called_once_with(mock_websocket)
        mock_health_service.remove_websocket_connection.assert_called_once_with(mock_websocket)

    @pytest.mark.asyncio
    async def test_websocket_endpoint_exception(self, mock_websocket, mock_health_service, mock_signer):
        """Test WebSocket exception handling."""
        # Setup exception during operation
        mock_websocket.receive_text.side_effect = Exception("Connection error")

        await websocket_endpoint(mock_websocket)

        # Verify connection was added and removed even with exception
        mock_health_service.add_websocket_connection.assert_called_once_with(mock_websocket)
        mock_health_service.remove_websocket_connection.assert_called_once_with(mock_websocket)

    @pytest.mark.asyncio
    async def test_websocket_endpoint_add_connection_failure(self, mock_websocket, mock_health_service, mock_signer):
        """Test handling of failure when adding WebSocket connection."""
        # Setup add_websocket_connection to return False (connection rejected)
        mock_health_service.add_websocket_connection.return_value = False

        await websocket_endpoint(mock_websocket)

        # Verify add was called but remove was not (connection was rejected)
        mock_health_service.add_websocket_connection.assert_called_once_with(mock_websocket)
        mock_health_service.remove_websocket_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_websocket_endpoint_remove_connection_failure(self, mock_websocket, mock_health_service, mock_signer):
        """Test handling of failure when removing WebSocket connection."""
        # Setup normal operation but remove fails
        mock_websocket.receive_text.side_effect = WebSocketDisconnect()
        mock_health_service.remove_websocket_connection.side_effect = Exception("Remove failed")

        # Should not raise exception - the finally block will still execute but may raise
        try:
            await websocket_endpoint(mock_websocket)
        except Exception:
            pass  # Expected since remove_websocket_connection raises

        mock_health_service.add_websocket_connection.assert_called_once_with(mock_websocket)
        mock_health_service.remove_websocket_connection.assert_called_once_with(mock_websocket)

    @pytest.mark.asyncio
    async def test_health_status_http_success(self, mock_health_service):
        """Test successful HTTP health status retrieval."""
        expected_status = {
            "service1": {"status": "healthy", "last_check": "2023-01-01T00:00:00Z"},
            "service2": {"status": "unhealthy", "last_check": "2023-01-01T00:00:00Z"},
        }

        result = await health_status_http()

        assert result == expected_status
        mock_health_service.get_all_health_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_status_http_empty_status(self, mock_health_service):
        """Test HTTP health status when no services are monitored."""
        mock_health_service.get_all_health_status.return_value = {}

        result = await health_status_http()

        assert result == {}
        mock_health_service.get_all_health_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_status_http_service_exception(self, mock_health_service):
        """Test HTTP health status when service raises exception."""
        mock_health_service.get_all_health_status.side_effect = Exception("Service error")

        # Should propagate the exception
        with pytest.raises(Exception, match="Service error"):
            await health_status_http()

    def test_router_configuration(self):
        """Test that the router is properly configured."""
        assert router is not None

        # Check that routes are registered
        routes = router.routes
        assert len(routes) >= 2  # WebSocket and HTTP endpoints

        # Find WebSocket route
        websocket_routes = [r for r in routes if hasattr(r, "path") and r.path == "/ws/health_status"]
        assert len(websocket_routes) >= 1

        # Check if both WebSocket and HTTP routes exist for same path
        route_paths = [r.path for r in routes if hasattr(r, "path")]
        assert "/ws/health_status" in route_paths

    @pytest.mark.asyncio
    async def test_websocket_multiple_messages(self, mock_websocket, mock_health_service, mock_signer):
        """Test WebSocket handling multiple messages before disconnect."""
        # Setup multiple messages then disconnect
        mock_websocket.receive_text.side_effect = ["ping", "heartbeat", "status", WebSocketDisconnect()]

        await websocket_endpoint(mock_websocket)

        # Verify connection was added and removed
        mock_health_service.add_websocket_connection.assert_called_once_with(mock_websocket)
        mock_health_service.remove_websocket_connection.assert_called_once_with(mock_websocket)

        # Verify multiple receive_text calls
        assert mock_websocket.receive_text.call_count == 4

    @pytest.mark.asyncio
    async def test_websocket_endpoint_no_auth(self, mock_health_service):
        """Test WebSocket connection without authentication."""
        # Create websocket without session cookie
        websocket = Mock(spec=WebSocket)
        websocket.client = "127.0.0.1:12345"
        websocket.close = AsyncMock()
        websocket.cookies = {}
        websocket.headers = {}
        websocket.query_params = {}

        await websocket_endpoint(websocket)

        # Verify connection was closed due to missing auth
        websocket.close.assert_called_once()
        # Verify service methods were not called
        mock_health_service.add_websocket_connection.assert_not_called()
        mock_health_service.remove_websocket_connection.assert_not_called()
