"""
Unit tests for server service.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, mock_open, patch

import pytest

from registry.services.server_service import ServerServiceV1


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.skip(reason="ServerServiceV1 refactored to use MongoDB - tests need rewrite")
class TestServerService:
    """Test suite for ServerService."""

    def test_init(self, server_service: ServerServiceV1):
        """Test ServerService initialization."""
        assert server_service.registered_servers == {}
        assert server_service.service_state == {}

    def test_path_to_filename(self, server_service: ServerServiceV1):
        """Test path to filename conversion."""
        assert server_service._path_to_filename("/api/v1/test") == "api_v1_test.json"
        assert server_service._path_to_filename("api/v1/test") == "api_v1_test.json"
        assert server_service._path_to_filename("/simple") == "simple.json"
        assert server_service._path_to_filename("/test.json") == "test.json"

    def test_register_server_success(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test successful server registration."""
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            result = server_service.register_server(sample_server)

            assert result is True
            assert sample_server["path"] in server_service.registered_servers
            assert server_service.registered_servers[sample_server["path"]] == sample_server
            assert server_service.service_state[sample_server["path"]] is False

    def test_register_server_duplicate_path(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test registering server with duplicate path fails."""
        # First registration
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            server_service.register_server(sample_server)

        # Second registration with same path should fail
        result = server_service.register_server(sample_server)
        assert result is False

    def test_register_server_save_failure(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test server registration fails when file save fails."""
        with patch.object(server_service, "save_server_to_file", return_value=False):
            result = server_service.register_server(sample_server)
            assert result is False
            assert sample_server["path"] not in server_service.registered_servers

    def test_update_server_success(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test successful server update."""
        # First register the server
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            server_service.register_server(sample_server)

        # Update the server
        updated_server = sample_server.copy()
        updated_server["server_name"] = "Updated Name"

        with patch.object(server_service, "save_server_to_file", return_value=True):
            result = server_service.update_server(sample_server["path"], updated_server)

            assert result is True
            assert server_service.registered_servers[sample_server["path"]]["server_name"] == "Updated Name"

    def test_update_server_not_found(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test updating non-existent server fails."""
        result = server_service.update_server("/nonexistent", sample_server)
        assert result is False

    def test_update_server_save_failure(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test server update fails when file save fails."""
        # First register the server
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            server_service.register_server(sample_server)

        # Try to update with save failure
        with patch.object(server_service, "save_server_to_file", return_value=False):
            result = server_service.update_server(sample_server["path"], sample_server)
            assert result is False

    def test_toggle_service_success(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test successful service toggle."""
        # Register server first
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            server_service.register_server(sample_server)

        # Toggle to enabled
        with patch.object(server_service, "save_service_state"):
            result = server_service.toggle_service(sample_server["path"], True)
            assert result is True
            assert server_service.service_state[sample_server["path"]] is True

        # Toggle to disabled
        with patch.object(server_service, "save_service_state"):
            result = server_service.toggle_service(sample_server["path"], False)
            assert result is True
            assert server_service.service_state[sample_server["path"]] is False

    def test_toggle_service_not_found(self, server_service: ServerServiceV1):
        """Test toggling non-existent service fails."""
        result = server_service.toggle_service("/nonexistent", True)
        assert result is False

    def test_get_server_info(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test getting server info."""
        # Test non-existent server
        assert server_service.get_server_info("/nonexistent") is None

        # Register server and test retrieval
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            server_service.register_server(sample_server)

        result = server_service.get_server_info(sample_server["path"])
        assert result == sample_server

    def test_get_all_servers(self, server_service: ServerServiceV1, sample_servers: dict[str, dict[str, Any]]):
        """Test getting all servers."""
        # Empty case
        assert server_service.get_all_servers() == {}

        # Add servers
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            for server in sample_servers.values():
                server_service.register_server(server)

        result = server_service.get_all_servers()
        assert len(result) == len(sample_servers)

    def test_is_service_enabled(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test checking if service is enabled."""
        # Non-existent service
        assert server_service.is_service_enabled("/nonexistent") is False

        # Register server (defaults to disabled)
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            server_service.register_server(sample_server)

        assert server_service.is_service_enabled(sample_server["path"]) is False

        # Enable service
        with patch.object(server_service, "save_service_state"):
            server_service.toggle_service(sample_server["path"], True)
            assert server_service.is_service_enabled(sample_server["path"]) is True

    def test_get_enabled_services(self, server_service: ServerServiceV1, sample_servers: dict[str, dict[str, Any]]):
        """Test getting enabled services."""
        # Empty case
        assert server_service.get_enabled_services() == []

        # Register servers
        with (
            patch.object(server_service, "save_server_to_file", return_value=True),
            patch.object(server_service, "save_service_state"),
        ):
            for server in sample_servers.values():
                server_service.register_server(server)

        # Enable some services
        paths = list(sample_servers.keys())
        with patch.object(server_service, "save_service_state"):
            server_service.toggle_service(paths[0], True)
            server_service.toggle_service(paths[1], True)

        enabled = server_service.get_enabled_services()
        assert len(enabled) == 2
        assert paths[0] in enabled
        assert paths[1] in enabled
        assert paths[2] not in enabled

    @patch("builtins.open", new_callable=mock_open)
    @patch("json.dump")
    def test_save_server_to_file(
        self, mock_json_dump, mock_file, server_service: ServerServiceV1, sample_server: dict[str, Any]
    ):
        """Test saving server to file."""
        result = server_service.save_server_to_file(sample_server)

        assert result is True
        mock_file.assert_called_once()
        mock_json_dump.assert_called_once_with(sample_server, mock_file.return_value, indent=2)

    @patch("builtins.open", side_effect=OSError("File error"))
    def test_save_server_to_file_failure(
        self, mock_file, server_service: ServerServiceV1, sample_server: dict[str, Any]
    ):
        """Test server file save failure."""
        result = server_service.save_server_to_file(sample_server)
        assert result is False

    @patch("builtins.open", new_callable=mock_open)
    @patch("json.dump")
    def test_save_service_state(self, mock_json_dump, mock_file, server_service: ServerServiceV1):
        """Test saving service state."""
        server_service.service_state = {"/test": True, "/test2": False}
        server_service.save_service_state()

        mock_file.assert_called_once()
        mock_json_dump.assert_called_once_with(server_service.service_state, mock_file.return_value, indent=2)

    @patch("builtins.open", side_effect=OSError("File error"))
    def test_save_service_state_failure(self, mock_file, server_service: ServerServiceV1):
        """Test saving service state failure."""
        server_service.service_state = {"/test": True}
        # Should not raise exception
        server_service.save_service_state()

    def test_load_service_state_no_file(self, server_service: ServerServiceV1, temp_dir):
        """Test loading service state when no state file exists."""
        # Set up some registered servers
        server_service.registered_servers = {"/test": {"server_name": "Test"}}

        # Call the method
        server_service._load_service_state()

        # Verify state was initialized properly
        assert server_service.service_state == {"/test": False}

    @patch("builtins.open", new_callable=mock_open, read_data='{"test": true, "test2": false}')
    @patch("json.load")
    def test_load_service_state_with_file(self, mock_json_load, mock_file, server_service: ServerServiceV1):
        """Test loading service state from existing file."""
        mock_json_load.return_value = {"/test": True, "/test2": False}

        # Set up registered servers
        server_service.registered_servers = {"/test": {"server_name": "Test"}, "/test2": {"server_name": "Test2"}}

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_settings.service_state_file.exists.return_value = True
            server_service._load_service_state()

        assert server_service.service_state == {"/test": True, "/test2": False}

    @patch("builtins.open", side_effect=OSError("File error"))
    def test_load_service_state_file_error(self, mock_file, server_service: ServerServiceV1):
        """Test loading service state with file error."""
        server_service.registered_servers = {"/test": {"server_name": "Test"}}

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_settings.service_state_file.exists.return_value = True
            server_service._load_service_state()

        # Should fall back to default state
        assert server_service.service_state == {"/test": False}

    @patch("json.load", side_effect=json.JSONDecodeError("Bad JSON", "", 0))
    @patch("builtins.open", new_callable=mock_open)
    def test_load_service_state_json_error(self, mock_file, mock_json_load, server_service: ServerServiceV1):
        """Test loading service state with JSON decode error."""
        server_service.registered_servers = {"/test": {"server_name": "Test"}}

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_settings.service_state_file.exists.return_value = True
            server_service._load_service_state()

        # Should fall back to default state
        assert server_service.service_state == {"/test": False}

    def test_load_servers_and_state_empty_directory(self, server_service: ServerServiceV1):
        """Test loading servers when directory is empty."""
        with patch("registry.services.server_service.settings") as mock_settings:
            mock_servers_dir = Mock()
            mock_servers_dir.mkdir = Mock()
            mock_servers_dir.glob.return_value = []
            mock_settings.servers_dir = mock_servers_dir

            with patch.object(server_service, "_load_service_state"):
                server_service.load_servers_and_state()

            assert server_service.registered_servers == {}

    def test_load_servers_and_state_with_servers(self, server_service: ServerServiceV1, sample_server: dict[str, Any]):
        """Test loading servers from files."""
        test_fixtures_dir = Path(__file__).parent.parent.parent / "fixtures" / "servers"

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_settings.servers_dir = test_fixtures_dir
            mock_settings.state_file_path.name = "state.json"

            with patch.object(server_service, "_load_service_state"):
                server_service.load_servers_and_state()

                # Should have loaded test_server_1.json, test_server_2.json, and currenttime.json
                assert len(server_service.registered_servers) >= 3
                assert "/test1" in server_service.registered_servers
                assert "/test2" in server_service.registered_servers
                assert "/currenttime" in server_service.registered_servers
                assert server_service.registered_servers["/test1"]["server_name"] == "Test Server 1"
                assert server_service.registered_servers["/test2"]["server_name"] == "Test Server 2"

    def test_load_servers_and_state_file_error(self, server_service: ServerServiceV1):
        """Test loading servers with file read error."""
        mock_file = Mock()
        mock_file.name = "bad.json"
        mock_file.relative_to.return_value = Path("bad.json")
        mock_files = [mock_file]

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_servers_dir = Mock()
            mock_servers_dir.mkdir = Mock()
            mock_servers_dir.glob.return_value = mock_files
            mock_settings.servers_dir = mock_servers_dir
            mock_settings.state_file_path.name = "state.json"

            with (
                patch("builtins.open", side_effect=OSError("File error")),
                patch.object(server_service, "_load_service_state"),
            ):
                server_service.load_servers_and_state()

                # Should continue loading other files and not crash
                assert server_service.registered_servers == {}

    def test_load_servers_and_state_json_error(self, server_service: ServerServiceV1):
        """Test loading servers with JSON decode error."""
        mock_file = Mock()
        mock_file.name = "invalid.json"
        mock_file.relative_to.return_value = Path("invalid.json")
        mock_files = [mock_file]

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_servers_dir = Mock()
            mock_servers_dir.mkdir = Mock()
            mock_servers_dir.glob.return_value = mock_files
            mock_settings.servers_dir = mock_servers_dir
            mock_settings.state_file_path.name = "state.json"

            with (
                patch("builtins.open", new_callable=mock_open),
                patch("json.load", side_effect=json.JSONDecodeError("Bad JSON", "", 0)),
                patch.object(server_service, "_load_service_state"),
            ):
                server_service.load_servers_and_state()

                # Should continue and not crash
                assert server_service.registered_servers == {}

    def test_load_servers_and_state_missing_path(self, server_service: ServerServiceV1):
        """Test loading servers with missing path field."""
        mock_file = Mock()
        mock_file.name = "nopath.json"
        mock_file.relative_to.return_value = Path("nopath.json")
        mock_files = [mock_file]

        with patch("registry.services.server_service.settings") as mock_settings:
            mock_servers_dir = Mock()
            mock_servers_dir.mkdir = Mock()
            mock_servers_dir.glob.return_value = mock_files
            mock_settings.servers_dir = mock_servers_dir
            mock_settings.state_file_path.name = "state.json"

            with (
                patch("builtins.open", new_callable=mock_open),
                patch("json.load") as mock_json_load,
                patch.object(server_service, "_load_service_state"),
            ):
                mock_json_load.return_value = {"server_name": "No Path Server"}

                server_service.load_servers_and_state()

                # Should skip servers without path
                assert server_service.registered_servers == {}

    def test_load_servers_and_state_directory_not_exists(self, server_service: ServerServiceV1):
        """Test loading servers when directory doesn't exist."""
        with patch("registry.services.server_service.settings") as mock_settings:
            mock_servers_dir = Mock()
            mock_servers_dir.mkdir = Mock()
            mock_servers_dir.glob.return_value = []
            mock_settings.servers_dir = mock_servers_dir

            with patch.object(server_service, "_load_service_state"):
                server_service.load_servers_and_state()

            assert server_service.registered_servers == {}


@pytest.mark.unit
@pytest.mark.servers
class TestBuildCompleteHeaders:
    """Test suite for build_complete_headers_for_server function."""

    @pytest.fixture
    def mock_oauth_server(self):
        """Create mock OAuth server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "oauth-server"
        server.config = {
            "requiresOAuth": True,
            "oauth": {
                "authorizationUrl": "https://oauth.example.com/authorize",
                "tokenUrl": "https://oauth.example.com/token",
            },
        }
        return server

    @pytest.fixture
    def mock_apikey_server(self):
        """Create mock API key server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "apikey-server"
        server.config = {"apiKey": {"key": "test-api-key-123", "authorization_type": "bearer"}}
        return server

    @pytest.fixture
    def mock_basic_auth_server(self):
        """Create mock Basic auth server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "basic-auth-server"
        server.config = {"apiKey": {"key": "username:password", "authorization_type": "basic"}}
        return server

    @pytest.fixture
    def mock_custom_auth_server(self):
        """Create mock custom auth server."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "custom-auth-server"
        server.config = {
            "apiKey": {"key": "custom-token-xyz", "authorization_type": "custom", "custom_header": "X-API-Key"}
        }
        return server

    @pytest.fixture
    def mock_custom_headers_server(self):
        """Create mock server with custom headers."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "custom-headers-server"
        server.config = {"headers": [{"X-Custom-Header": "value1"}, {"X-Another-Header": "value2"}]}
        return server

    @pytest.mark.asyncio
    async def test_oauth_server_success(self, mock_oauth_server):
        """Test OAuth server returns valid access token."""
        from unittest.mock import AsyncMock

        from registry.services.server_service import build_complete_headers_for_server

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = mock_oauth_server.config

            # Mock OAuth service
            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("access-token-123", None, None))
            mock_oauth_svc.return_value = oauth_service

            headers = await build_complete_headers_for_server(mock_oauth_server, "user-123")

            assert headers["Authorization"] == "Bearer access-token-123"
            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json"
            oauth_service.get_valid_access_token.assert_called_once_with(user_id="user-123", server=mock_oauth_server)

    @pytest.mark.asyncio
    async def test_oauth_server_missing_user_id(self, mock_oauth_server):
        """Test OAuth server raises error when user_id is missing."""
        from registry.schemas.errors import MissingUserIdError
        from registry.services.server_service import build_complete_headers_for_server

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_oauth_server.config

            with pytest.raises(MissingUserIdError) as exc_info:
                await build_complete_headers_for_server(mock_oauth_server, None)

            assert "User ID required" in str(exc_info.value)
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_oauth_server_reauth_required(self, mock_oauth_server):
        """Test OAuth server raises error when re-authentication is required."""
        from unittest.mock import AsyncMock

        from registry.schemas.errors import OAuthReAuthRequiredError
        from registry.services.server_service import build_complete_headers_for_server

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = mock_oauth_server.config

            # Mock OAuth service returns auth_url
            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(
                return_value=(None, "https://oauth.example.com/authorize", None)
            )
            mock_oauth_svc.return_value = oauth_service

            with pytest.raises(OAuthReAuthRequiredError) as exc_info:
                await build_complete_headers_for_server(mock_oauth_server, "user-123")

            assert "re-authentication required" in str(exc_info.value).lower()
            assert exc_info.value.auth_url == "https://oauth.example.com/authorize"
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_oauth_server_token_error(self, mock_oauth_server):
        """Test OAuth server raises error when token retrieval fails."""
        from unittest.mock import AsyncMock

        from registry.schemas.errors import OAuthTokenError
        from registry.services.server_service import build_complete_headers_for_server

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = mock_oauth_server.config

            # Mock OAuth service returns error
            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=(None, None, "Token refresh failed"))
            mock_oauth_svc.return_value = oauth_service

            with pytest.raises(OAuthTokenError) as exc_info:
                await build_complete_headers_for_server(mock_oauth_server, "user-123")

            assert "OAuth token error" in str(exc_info.value)
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_oauth_server_no_token(self, mock_oauth_server):
        """Test OAuth server raises error when no token available."""
        from unittest.mock import AsyncMock

        from registry.schemas.errors import OAuthTokenError
        from registry.services.server_service import build_complete_headers_for_server

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = mock_oauth_server.config

            # Mock OAuth service returns None for all
            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=(None, None, None))
            mock_oauth_svc.return_value = oauth_service

            with pytest.raises(OAuthTokenError) as exc_info:
                await build_complete_headers_for_server(mock_oauth_server, "user-123")

            assert "No valid OAuth token" in str(exc_info.value)
            assert exc_info.value.server_name == "oauth-server"

    @pytest.mark.asyncio
    async def test_apikey_bearer_auth(self, mock_apikey_server):
        """Test API key with Bearer authorization."""
        from registry.services.server_service import build_complete_headers_for_server

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_apikey_server.config

            headers = await build_complete_headers_for_server(mock_apikey_server, None)

            assert headers["Authorization"] == "Bearer test-api-key-123"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_apikey_basic_auth(self, mock_basic_auth_server):
        """Test API key with Basic authorization."""
        from registry.services.server_service import build_complete_headers_for_server

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_basic_auth_server.config

            headers = await build_complete_headers_for_server(mock_basic_auth_server, None)

            # Basic auth should be base64 encoded
            assert headers["Authorization"].startswith("Basic ")
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_apikey_custom_auth(self, mock_custom_auth_server):
        """Test API key with custom header."""
        from registry.services.server_service import build_complete_headers_for_server

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_custom_auth_server.config

            headers = await build_complete_headers_for_server(mock_custom_auth_server, None)

            assert headers["X-API-Key"] == "custom-token-xyz"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_custom_headers_only(self, mock_custom_headers_server):
        """Test server with only custom headers."""
        from registry.services.server_service import build_complete_headers_for_server

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = mock_custom_headers_server.config

            headers = await build_complete_headers_for_server(mock_custom_headers_server, None)

            assert headers["X-Custom-Header"] == "value1"
            assert headers["X-Another-Header"] == "value2"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_oauth_with_custom_headers(self, mock_oauth_server):
        """Test OAuth server also processes custom headers."""
        from unittest.mock import AsyncMock

        from registry.services.server_service import build_complete_headers_for_server

        # Add custom headers to OAuth config
        mock_oauth_server.config["headers"] = [{"X-Custom-Header": "custom-value"}]

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = mock_oauth_server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("access-token", None, None))
            mock_oauth_svc.return_value = oauth_service

            headers = await build_complete_headers_for_server(mock_oauth_server, "user-123")

            # Should have both OAuth and custom headers
            assert headers["Authorization"] == "Bearer access-token"
            assert headers["X-Custom-Header"] == "custom-value"

    @pytest.mark.asyncio
    async def test_no_auth_returns_base_headers(self):
        """Test server with no authentication returns base MCP headers."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "no-auth-server"
        server.config = {}

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = {}

            headers = await build_complete_headers_for_server(server, None)

            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json"
            # User-Agent is now set to registry_app_name (jarvis-registry-client)
            assert "User-Agent" in headers
            assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_oauth_overrides_custom_authorization_header(self):
        """Test OAuth Bearer token overrides custom Authorization header."""
        from unittest.mock import AsyncMock

        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "oauth-priority-server"
        server.config = {
            "requiresOAuth": True,
            "oauth": {
                "authorizationUrl": "https://oauth.example.com/authorize",
                "tokenUrl": "https://oauth.example.com/token",
            },
            "headers": [{"Authorization": "Bearer custom-should-be-overridden"}],
        }

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("oauth-token-wins", None, None))
            mock_oauth_svc.return_value = oauth_service

            headers = await build_complete_headers_for_server(server, "user-123")

            # OAuth token should override custom Authorization header
            assert headers["Authorization"] == "Bearer oauth-token-wins"

    @pytest.mark.asyncio
    async def test_apikey_overrides_custom_authorization_header(self):
        """Test API key overrides custom Authorization header."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "apikey-priority-server"
        server.config = {
            "apiKey": {"key": "apikey-token-wins", "authorization_type": "bearer"},
            "headers": [{"Authorization": "Bearer custom-should-be-overridden"}],
        }

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # API key should override custom Authorization header
            assert headers["Authorization"] == "Bearer apikey-token-wins"

    @pytest.mark.asyncio
    async def test_custom_headers_added_first_for_oauth(self):
        """Test custom headers are added before OAuth processing (lowest priority)."""
        from unittest.mock import AsyncMock

        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "oauth-custom-order"
        server.config = {
            "requiresOAuth": True,
            "oauth": {"authorizationUrl": "https://oauth.example.com/authorize"},
            "headers": [
                {"X-Custom-1": "value1"},
                {"X-Custom-2": "value2"},
                {"Content-Type": "application/custom"},  # Will override base header
            ],
        }

        with (
            patch("registry.services.oauth.oauth_service.get_oauth_service") as mock_oauth_svc,
            patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt,
        ):
            mock_decrypt.return_value = server.config

            oauth_service = AsyncMock()
            oauth_service.get_valid_access_token = AsyncMock(return_value=("oauth-token", None, None))
            mock_oauth_svc.return_value = oauth_service

            headers = await build_complete_headers_for_server(server, "user-123")

            # OAuth Authorization should be present
            assert headers["Authorization"] == "Bearer oauth-token"

            # Custom headers should be present
            assert headers["X-Custom-1"] == "value1"
            assert headers["X-Custom-2"] == "value2"

            # Custom Content-Type should override base header
            assert headers["Content-Type"] == "application/custom"

    @pytest.mark.asyncio
    async def test_custom_headers_added_first_for_apikey(self):
        """Test custom headers are added before API key processing (lowest priority)."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "apikey-custom-order"
        server.config = {
            "apiKey": {"key": "test-key", "authorization_type": "bearer"},
            "headers": [{"X-App-Id": "app-123"}, {"Authorization": "Bearer should-be-overridden"}],
        }

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # API key should override custom Authorization
            assert headers["Authorization"] == "Bearer test-key"

            # Custom non-auth headers should be present
            assert headers["X-App-Id"] == "app-123"

    @pytest.mark.asyncio
    async def test_custom_headers_dict_format(self):
        """Test custom headers provided as dict are supported."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "dict-headers-server"
        server.config = {
            "headers": {
                "X-Trace-Id": "trace-123",
                "Accept": ["application/json", "application/xml"],
            }
        }

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            assert headers["X-Trace-Id"] == "trace-123"
            assert headers["Accept"] == "application/json, application/xml"

    @pytest.mark.asyncio
    async def test_custom_header_with_list_values(self):
        """Test custom headers with list values are joined correctly."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "list-header-server"
        server.config = {
            "headers": [
                {"Accept": ["application/json", "application/xml"]},
                {"X-Custom-List": ["value1", "value2", "value3"]},
            ]
        }

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # List values should be joined with comma
            assert headers["Accept"] == "application/json, application/xml"
            assert headers["X-Custom-List"] == "value1, value2, value3"

    @pytest.mark.asyncio
    async def test_no_auth_with_custom_headers(self):
        """Test server with no auth but custom headers."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "no-auth-custom-headers"
        server.config = {"headers": [{"X-API-Version": "v2"}, {"X-Request-ID": "req-123"}]}

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # Base headers should be present
            assert headers["Content-Type"] == "application/json"
            assert headers["Accept"] == "application/json"

            # Custom headers should be present
            assert headers["X-API-Version"] == "v2"
            assert headers["X-Request-ID"] == "req-123"

            # No Authorization header
            assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_apikey_basic_auth_pre_encoded(self):
        """Test API key Basic auth with pre-encoded base64."""
        import base64

        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        # Pre-encoded credentials
        encoded_creds = base64.b64encode(b"user:pass").decode()

        server = Mock(spec=MCPServerDocument)
        server.serverName = "basic-auth-encoded"
        server.config = {"apiKey": {"key": encoded_creds, "authorization_type": "basic"}}

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # Should use the pre-encoded value
            assert headers["Authorization"] == f"Basic {encoded_creds}"

    @pytest.mark.asyncio
    async def test_apikey_basic_auth_not_encoded(self):
        """Test API key Basic auth with plain text credentials (auto-encoding)."""
        import base64

        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "basic-auth-plain"
        server.config = {"apiKey": {"key": "username:password", "authorization_type": "basic"}}

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # Should be auto-encoded
            expected = base64.b64encode(b"username:password").decode()
            assert headers["Authorization"] == f"Basic {expected}"

    @pytest.mark.asyncio
    async def test_apikey_custom_header_missing_custom_header_name(self):
        """Test API key custom auth without custom_header field logs warning."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "custom-auth-missing-header"
        server.config = {
            "apiKey": {
                "key": "custom-token",
                "authorization_type": "custom",
                # Missing "custom_header" field
            }
        }

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # Should not add any custom header
            assert "Authorization" not in headers
            # Only base headers should be present
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_apikey_unknown_authorization_type_defaults_to_bearer(self):
        """Test API key with unknown authorization_type defaults to Bearer."""
        from registry.services.server_service import build_complete_headers_for_server
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "unknown-auth-type"
        server.config = {"apiKey": {"key": "test-key", "authorization_type": "unknown_type"}}

        with patch("registry.utils.crypto_utils.decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = server.config

            headers = await build_complete_headers_for_server(server, None)

            # Should default to Bearer
            assert headers["Authorization"] == "Bearer test-key"


@pytest.mark.unit
@pytest.mark.servers
class TestConfigBuilders:
    """Test suite for config builder helpers."""

    def test_build_config_from_request_includes_headers(self):
        """Ensure headers from create request are stored in config."""
        from registry.schemas.server_api_schemas import ServerCreateRequest
        from registry.services.server_service import _build_config_from_request

        data = ServerCreateRequest(title="Test Server", path="/test", headers={"X-Test": "1"})

        config = _build_config_from_request(data, server_name="Test Server")

        assert config["headers"] == {"X-Test": "1"}

    def test_update_config_from_request_includes_headers(self):
        """Ensure headers from update request are stored in config."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        data = ServerUpdateRequest(headers={"X-New": "2"})

        updated = _update_config_from_request({}, data, server_name="Test Server")

        assert updated["headers"] == {"X-New": "2"}

    def test_update_oauth_to_dcr_removes_oauth_config(self):
        """Test updating from OAuth to DCR (oauth=None) removes oauth object from config."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        # Start with existing OAuth configuration
        existing_config = {
            "title": "Test Server",
            "url": "https://test.example.com",
            "oauth": {
                "authorization_url": "https://oauth.example.com/authorize",
                "token_url": "https://oauth.example.com/token",
                "client_id": "test-client",
                "scope": "read write",
            },
        }

        # Update to DCR (oauth=None)
        data = ServerUpdateRequest(oauth=None)

        updated = _update_config_from_request(existing_config, data, server_name="Test Server")

        # OAuth should be removed from config
        assert "oauth" not in updated
        # Other fields should remain unchanged
        assert updated["title"] == "Test Server"
        assert updated["url"] == "https://test.example.com"

    def test_update_oauth_to_apikey_removes_oauth_adds_apikey(self):
        """Test updating from OAuth to apiKey replaces oauth with apiKey."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        # Start with existing OAuth configuration
        existing_config = {
            "title": "Test Server",
            "url": "https://test.example.com",
            "oauth": {
                "authorization_url": "https://oauth.example.com/authorize",
                "token_url": "https://oauth.example.com/token",
            },
        }

        # Update to apiKey
        data = ServerUpdateRequest(apiKey={"key": "test-api-key", "authorization_type": "bearer"})

        updated = _update_config_from_request(existing_config, data, server_name="Test Server")

        # OAuth should be removed, apiKey should be added
        assert "oauth" not in updated
        assert "apiKey" in updated
        assert updated["apiKey"]["key"] == "test-api-key"
        assert updated["apiKey"]["authorization_type"] == "bearer"

    def test_update_apikey_to_dcr_removes_apikey(self):
        """Test updating from apiKey to DCR (apiKey=None) removes apiKey object."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        # Start with existing apiKey configuration
        existing_config = {
            "title": "Test Server",
            "url": "https://test.example.com",
            "apiKey": {"key": "existing-key", "authorization_type": "bearer"},
        }

        # Update to DCR (apiKey=None)
        data = ServerUpdateRequest(apiKey=None)

        updated = _update_config_from_request(existing_config, data, server_name="Test Server")

        # apiKey should be removed from config
        assert "apiKey" not in updated
        # Other fields should remain unchanged
        assert updated["title"] == "Test Server"
        assert updated["url"] == "https://test.example.com"

    def test_update_oauth_merge_preserves_existing_fields(self):
        """Test updating OAuth config merges with existing OAuth fields."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        # Start with existing OAuth configuration
        existing_config = {
            "oauth": {
                "authorization_url": "https://oauth.example.com/authorize",
                "token_url": "https://oauth.example.com/token",
                "client_id": "old-client-id",
                "scope": "read write",
            }
        }

        # Partial update (only client_id)
        data = ServerUpdateRequest(oauth={"client_id": "new-client-id"})

        updated = _update_config_from_request(existing_config, data, server_name="Test Server")

        # Should merge: client_id updated, other fields preserved
        assert updated["oauth"]["client_id"] == "new-client-id"
        assert updated["oauth"]["authorization_url"] == "https://oauth.example.com/authorize"
        assert updated["oauth"]["token_url"] == "https://oauth.example.com/token"
        assert updated["oauth"]["scope"] == "read write"


@pytest.mark.unit
@pytest.mark.servers
class TestValidateAndMergeOAuthMetadata:
    """Test suite for _validate_and_merge_oauth_metadata function."""

    def test_both_empty_returns_empty_dict(self):
        """Test that empty oauth_config and oauth_metadata returns empty dict."""
        from registry.services.server_service import _validate_and_merge_oauth_metadata

        result = _validate_and_merge_oauth_metadata(None, None)

        assert result == {}

    def test_no_server_metadata_returns_database_config(self):
        """Test that when no server metadata, returns database config as-is."""
        from registry.services.server_service import _validate_and_merge_oauth_metadata

        oauth_config = {
            "authorization_url": "https://oauth.example.com/authorize",
            "token_url": "https://oauth.example.com/token",
            "client_id": "client-123",
        }

        result = _validate_and_merge_oauth_metadata(oauth_config, None)

        assert result == oauth_config
        # Ensure it's a copy, not the same object
        assert result is not oauth_config

    def test_no_database_config_returns_server_metadata(self):
        """Test that when no database config, returns server metadata as-is."""
        from registry.services.server_service import _validate_and_merge_oauth_metadata

        oauth_metadata = {
            "authorization_servers": ["https://accounts.google.com"],
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "issuer": "https://accounts.google.com",
        }

        result = _validate_and_merge_oauth_metadata(None, oauth_metadata)

        assert result == oauth_metadata
        # Ensure it's a copy, not the same object
        assert result is not oauth_metadata

    def test_merge_with_database_config_taking_priority(self):
        """Test that database config overrides server metadata fields."""
        from registry.services.server_service import _validate_and_merge_oauth_metadata

        # Server metadata from .well-known endpoint
        oauth_metadata = {
            "authorization_servers": ["http://localhost:3080/"],  # WRONG
            "token_endpoint": "http://localhost:3080/oauth/token",
            "issuer": "http://localhost:3080",
            "scopes_supported": ["read", "write"],
        }

        # Database config (admin-configured, authoritative)
        oauth_config = {
            "authorization_servers": ["https://accounts.google.com"],  # CORRECT
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "client_id": "client-123",
            "client_secret": "secret-xyz",
        }

        result = _validate_and_merge_oauth_metadata(oauth_config, oauth_metadata)

        # Database config should override server metadata
        assert result["authorization_servers"] == ["https://accounts.google.com"]
        assert result["token_endpoint"] == "https://oauth2.googleapis.com/token"

        # Database-only fields should be present
        assert result["client_id"] == "client-123"
        assert result["client_secret"] == "secret-xyz"

        # Server metadata fields not in database config should remain
        assert result["issuer"] == "http://localhost:3080"
        assert result["scopes_supported"] == ["read", "write"]

    def test_merge_preserves_all_database_fields(self):
        """Test that all database config fields are preserved in merge."""
        from registry.services.server_service import _validate_and_merge_oauth_metadata

        oauth_metadata = {"issuer": "https://old-issuer.com"}

        oauth_config = {
            "authorization_url": "https://new.com/auth",
            "token_url": "https://new.com/token",
            "client_id": "new-client",
            "scope": "openid email profile",
        }

        result = _validate_and_merge_oauth_metadata(oauth_config, oauth_metadata)

        # All database config fields should be present
        assert result["authorization_url"] == "https://new.com/auth"
        assert result["token_url"] == "https://new.com/token"
        assert result["client_id"] == "new-client"
        assert result["scope"] == "openid email profile"

        # Server metadata field should still be present
        assert result["issuer"] == "https://old-issuer.com"

    def test_merge_does_not_mutate_input(self):
        """Test that merge operation doesn't mutate input dictionaries."""
        from registry.services.server_service import _validate_and_merge_oauth_metadata

        oauth_metadata = {"issuer": "https://issuer.com", "authorization_servers": ["https://old.com"]}
        oauth_metadata_copy = oauth_metadata.copy()

        oauth_config = {"authorization_servers": ["https://new.com"]}
        oauth_config_copy = oauth_config.copy()

        result = _validate_and_merge_oauth_metadata(oauth_config, oauth_metadata)

        # Inputs should not be mutated
        assert oauth_metadata == oauth_metadata_copy
        assert oauth_config == oauth_config_copy

        # Result should be a new dict
        assert result is not oauth_metadata
        assert result is not oauth_config


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.health
class TestHealthCheckEndpointUrlConstruction:
    """Test suite for health check endpoint URL construction.

    These tests verify that the health check correctly strips trailing slashes
    and uses the URL as-is without appending any path segments.
    """

    @pytest.fixture
    def mock_mcp_server(self):
        """Create a mock MCP server document."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer as MCPServerDocument

        server = Mock(spec=MCPServerDocument)
        server.serverName = "test-server"
        return server

    @pytest.fixture
    def mock_init_result(self):
        """Create a valid MCP InitializeResult for health check tests."""
        mock_result = Mock()
        mock_result.protocolVersion = "2024-11-05"
        mock_result.serverInfo = Mock()
        mock_result.serverInfo.name = "test-server"
        return mock_result
