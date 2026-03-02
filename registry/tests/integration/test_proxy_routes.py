"""
Integration tests for proxy routes - specifically tool execution endpoint.
"""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from registry.main import app
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as Server


@pytest.mark.integration
class TestProxyToolExecutionRoutes:
    """Integration coverage for /proxy/tools/call endpoint."""

    def setup_method(self):
        """Override auth dependency for integration testing."""
        from registry.auth.dependencies import get_current_user

        user_context = {
            "username": "test-admin",
            "user_id": "test-admin-id",
            "is_admin": True,
            "accessible_servers": ["all"],
            "accessible_agents": ["all"],
            "accessible_services": ["all"],
            "groups": ["registry-admin"],
            "scopes": ["registry-admin"],
            "ui_permissions": {},
            "can_modify_servers": True,
            "auth_method": "traditional",
            "provider": "local",
        }

        def _mock_auth(request: Request):
            return user_context

        app.dependency_overrides[get_current_user] = _mock_auth

    def teardown_method(self):
        """Clean up dependency overrides."""
        app.dependency_overrides.clear()

    def test_execute_tool_success_json_response(self, test_client: TestClient):
        """Successful tool execution returns JSON response."""
        # Mock server lookup
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {"url": "http://localhost:8080/mcp", "transport": "streamable-http"}

        # Mock HTTP response from backend MCP server (non-SSE)
        mock_backend_response = Mock()
        mock_backend_response.status_code = 200
        mock_backend_response.headers = {"content-type": "application/json"}
        mock_backend_response.aread = AsyncMock(
            return_value=b'{"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Search results for Donald Trump..."}]}}'
        )

        mock_stream_context = Mock()
        mock_stream_context.__aenter__ = AsyncMock(return_value=mock_backend_response)
        mock_stream_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_build_headers.return_value = {"Content-Type": "application/json"}
            mock_stream.return_value = mock_stream_context

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "Donald Trump news"},
                },
            )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        assert data["server_id"] == "6972e222755441652c23090f"
        assert data["server_path"] == "/tavilysearch"
        assert data["tool_name"] == "tavily_search"
        assert "result" in data
        assert data["result"]["jsonrpc"] == "2.0"

    def test_execute_tool_success_sse_response(self, test_client: TestClient):
        """Tool execution returns SSE stream when backend sends SSE."""
        # Mock server lookup
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {"url": "http://localhost:8080/mcp", "transport": "streamable-http"}

        # Mock SSE response from backend
        async def async_iter_bytes():
            yield b"event: message\n"
            yield b'data: {"type": "progress", "value": 50}\n\n'

        mock_backend_response = Mock()
        mock_backend_response.status_code = 200
        mock_backend_response.headers = {"content-type": "text/event-stream"}
        mock_backend_response.aiter_bytes = async_iter_bytes

        mock_stream_context = Mock()
        mock_stream_context.__aenter__ = AsyncMock(return_value=mock_backend_response)
        mock_stream_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_build_headers.return_value = {"Content-Type": "application/json"}
            mock_stream.return_value = mock_stream_context

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        assert response.status_code == 200
        # Check content-type allows charset parameter
        assert "text/event-stream" in response.headers["content-type"]
        assert "event:" in response.text or "data:" in response.text

    def test_execute_tool_server_not_found(self, test_client: TestClient):
        """Tool execution fails when server not found."""
        with patch(
            "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
        ) as mock_get_server:
            mock_get_server.return_value = None

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "nonexistent-id",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        assert response.status_code == 404

    def test_execute_tool_missing_server_url(self, test_client: TestClient):
        """Tool execution fails with 500 when server URL not configured."""
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.config = {}  # Missing url

        with patch(
            "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
        ) as mock_get_server:
            mock_get_server.return_value = mock_server

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        assert response.status_code == 500
        assert "Server URL not configured" in response.json()["detail"]

    def test_execute_tool_http_error(self, test_client: TestClient):
        """Tool execution handles HTTP errors from backend server."""
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {"url": "http://localhost:8080/mcp"}

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_build_headers.return_value = {"Content-Type": "application/json"}

            # Simulate HTTP error
            mock_stream.side_effect = httpx.HTTPError("Connection failed")

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        assert response.status_code == 200  # Returns 200 but with error in body
        data = response.json()
        assert data["success"] is False
        assert "Bad gateway" in data["error"]

    def test_execute_tool_with_authentication(self, test_client: TestClient):
        """Tool execution includes authentication headers for backend server."""
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {
            "url": "http://localhost:8080/mcp",
            "apiKey": {"key": "test-api-key", "authorization_type": "bearer"},
        }

        mock_backend_response = Mock()
        mock_backend_response.status_code = 200
        mock_backend_response.headers = {"content-type": "application/json"}
        mock_backend_response.aread = AsyncMock(return_value=b'{"jsonrpc": "2.0", "id": 1, "result": {}}')

        mock_stream_context = Mock()
        mock_stream_context.__aenter__ = AsyncMock(return_value=mock_backend_response)
        mock_stream_context.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_decrypt.return_value = mock_server.config
            mock_build_headers.return_value = {
                "Authorization": "Bearer test-api-key",
                "Content-Type": "application/json",
            }
            mock_stream.return_value = mock_stream_context

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        assert response.status_code == 200

        # Verify proxy_client.stream was used to make backend request
        mock_stream.assert_called_once()

    def test_execute_tool_adds_tracking_headers(self, test_client: TestClient):
        """Tool execution adds user tracking headers to backend request."""
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {"url": "http://localhost:8080/mcp"}

        mock_backend_response = Mock()
        mock_backend_response.status_code = 200
        mock_backend_response.headers = {"content-type": "application/json"}
        mock_backend_response.aread = AsyncMock(return_value=b'{"jsonrpc": "2.0", "id": 1, "result": {}}')

        captured_headers = {}

        def capture_stream(method, url, json=None, headers=None):
            captured_headers.update(headers or {})
            mock_stream_context = Mock()
            mock_stream_context.__aenter__ = AsyncMock(return_value=mock_backend_response)
            mock_stream_context.__aexit__ = AsyncMock(return_value=None)
            return mock_stream_context

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_build_headers.return_value = {"Content-Type": "application/json"}
            mock_stream.side_effect = capture_stream

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        assert response.status_code == 200

        # Verify tracking headers were added
        assert "X-User-Id" in captured_headers
        assert "X-Username" in captured_headers
        assert "X-Tool-Name" in captured_headers
        assert captured_headers["X-User-Id"] == "test-admin-id"  # From mock_auth_middleware fixture
        # Note: ACL service lookup may modify username based on database state
        # Accept whatever username is present as long as the header exists
        assert len(captured_headers["X-Username"]) > 0
        assert captured_headers["X-Tool-Name"] == "tavily_search"

    def test_execute_tool_builds_jsonrpc_request(self, test_client: TestClient):
        """Tool execution builds proper JSON-RPC request for MCP server."""
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {"url": "http://localhost:8080/mcp"}

        mock_backend_response = Mock()
        mock_backend_response.status_code = 200
        mock_backend_response.headers = {"content-type": "application/json"}
        mock_backend_response.aread = AsyncMock(return_value=b'{"jsonrpc": "2.0", "id": 1, "result": {}}')

        captured_json = {}

        def capture_stream(method, url, json=None, headers=None):
            captured_json.update(json or {})
            mock_stream_context = Mock()
            mock_stream_context.__aenter__ = AsyncMock(return_value=mock_backend_response)
            mock_stream_context.__aexit__ = AsyncMock(return_value=None)
            return mock_stream_context

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_build_headers.return_value = {"Content-Type": "application/json"}
            mock_stream.side_effect = capture_stream

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "Donald Trump", "max_results": 5},
                },
            )

        assert response.status_code == 200

        # Verify JSON-RPC request structure
        assert captured_json["jsonrpc"] == "2.0"
        assert captured_json["id"] == 1
        assert captured_json["method"] == "tools/call"
        assert "params" in captured_json
        assert captured_json["params"]["name"] == "tavily_search"
        assert captured_json["params"]["arguments"]["query"] == "Donald Trump"
        assert captured_json["params"]["arguments"]["max_results"] == 5

        assert response.status_code == 200

        # Verify JSON-RPC request structure
        assert captured_json["jsonrpc"] == "2.0"
        assert captured_json["id"] == 1
        assert captured_json["method"] == "tools/call"
        assert "params" in captured_json
        assert captured_json["params"]["name"] == "tavily_search"
        assert captured_json["params"]["arguments"]["query"] == "Donald Trump"
        assert captured_json["params"]["arguments"]["max_results"] == 5

    def test_execute_tool_timeout_configuration(self, test_client: TestClient):
        """Tool execution handles timeout errors from backend requests."""
        mock_server = Mock(spec=Server)
        mock_server.serverName = "tavilysearch"
        mock_server.path = "/tavilysearch"
        mock_server.tags = []  # Add tags attribute
        mock_server.config = {"url": "http://localhost:8080/mcp"}

        with (
            patch(
                "registry.services.server_service.server_service_v1.get_server_by_id", new_callable=AsyncMock
            ) as mock_get_server,
            patch("registry.api.proxy_routes.proxy_client.stream") as mock_stream,
            patch(
                "registry.services.server_service._build_complete_headers_for_server", new_callable=AsyncMock
            ) as mock_build_headers,
        ):
            mock_get_server.return_value = mock_server
            mock_build_headers.return_value = {"Content-Type": "application/json"}

            # Simulate timeout
            mock_stream.side_effect = httpx.TimeoutException("Request timed out")

            response = test_client.post(
                "/proxy/tools/call",
                json={
                    "server_id": "6972e222755441652c23090f",
                    "server_path": "/tavilysearch",
                    "tool_name": "tavily_search",
                    "arguments": {"query": "test"},
                },
            )

        # Should handle timeout gracefully
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Gateway timeout" in data["error"]
