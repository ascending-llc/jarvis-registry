"""
Unit tests for server service.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from registry.services.server_service import ServerServiceV1


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestRefreshServerCapabilities:
    """Test suite for refresh_server_capabilities service method."""

    @pytest.fixture
    def mock_server(self):
        """Create a mock MCP server document."""
        from datetime import UTC, datetime

        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
        server.id = Mock()
        server.serverName = "test-server"
        server.config = {"title": "Test Server"}
        server.lastError = None
        server.errorMessage = None
        server.lastConnected = None
        server.updatedAt = datetime.now(UTC)
        server.vectorContentHash = "old-hash"
        server.numTools = 0
        server.save = AsyncMock()
        return server

    @pytest.fixture
    def server_service(self):
        """Create a ServerServiceV1 instance with mocked dependencies."""
        from unittest.mock import Mock

        from registry.services.server_service import ServerServiceV1

        # Mock 所有必需的依赖
        mock_user_service = Mock()
        mock_token_service = Mock()
        mock_oauth_service = Mock()
        mock_mcp_server_repo = Mock()

        service = ServerServiceV1(
            user_service=mock_user_service,
            token_service=mock_token_service,
            oauth_service=mock_oauth_service,
            mcp_server_repo=mock_mcp_server_repo,
        )
        return service

    async def test_refresh_server_capabilities_success(self, server_service, mock_server):
        """测试成功获取服务器能力"""
        from unittest.mock import patch

        # Mock get_server_by_id
        with patch.object(server_service, "get_server_by_id", return_value=mock_server):
            # Mock retrieve_tools_and_capabilities_from_server - 成功返回
            mock_tools = [{"name": "tool1"}, {"name": "tool2"}]
            mock_resources = [{"uri": "file://test.txt"}]
            mock_prompts = [{"name": "prompt1"}]
            mock_capabilities = {"sampling": {}}

            with patch.object(
                server_service,
                "retrieve_tools_and_capabilities_from_server",
                return_value=(mock_tools, mock_resources, mock_prompts, mock_capabilities, None),
            ):
                # Mock _schedule_vector_sync
                with patch.object(server_service, "_schedule_vector_sync"):
                    result = await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

                    # 验证返回结果
                    assert result["status"] == "success"
                    assert result["server"] == mock_server
                    assert "Successfully refreshed capabilities" in result["status_message"]
                    assert result["last_checked"] is not None
                    assert result["response_time_ms"] is None

                    # 验证 lastConnected 被设置
                    assert mock_server.lastConnected is not None

                    # 验证 lastError 和 errorMessage 被清除
                    assert mock_server.lastError is None
                    assert mock_server.errorMessage is None

                    # 验证 save 被调用
                    mock_server.save.assert_called_once()

    async def test_refresh_server_capabilities_mcp_unreachable(self, server_service, mock_server):
        """测试 MCP 服务器不可达时的失败路径"""
        from unittest.mock import patch

        # 保存初始的 lastConnected 值
        initial_last_connected = mock_server.lastConnected

        # Mock get_server_by_id
        with patch.object(server_service, "get_server_by_id", return_value=mock_server):
            # Mock retrieve_tools_and_capabilities_from_server - 失败返回
            error_message = "Connection timeout: Failed to connect to MCP server"

            with patch.object(
                server_service,
                "retrieve_tools_and_capabilities_from_server",
                return_value=(None, None, None, None, error_message),
            ):
                # Mock _schedule_vector_sync
                with patch.object(server_service, "_schedule_vector_sync"):
                    result = await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

                    # 验证返回结果
                    assert result["status"] == "failed"
                    assert result["server"] == mock_server
                    assert error_message in result["status_message"]
                    assert result["last_checked"] is not None

                    # 验证 lastConnected 不被更新（保持初始值）
                    assert mock_server.lastConnected == initial_last_connected

                    # 验证 lastError 被设置
                    assert mock_server.lastError is not None

                    # 验证 errorMessage 被设置
                    assert mock_server.errorMessage == error_message

                    # 验证 save 被调用
                    mock_server.save.assert_called_once()

    async def test_refresh_server_capabilities_invalid_server_id(self, server_service):
        """测试无效的 server_id 抛出 ValueError"""
        from unittest.mock import patch

        # Mock get_server_by_id 返回 None
        with patch.object(server_service, "get_server_by_id", return_value=None):
            with pytest.raises(ValueError, match="Server not found"):
                await server_service.refresh_server_capabilities(server_id="invalid-id", user_id="user-123")

    async def test_refresh_server_capabilities_triggers_weaviate_sync(self, server_service, mock_server):
        """测试成功刷新时触发 Weaviate 同步"""
        from unittest.mock import patch

        # Mock get_server_by_id
        with patch.object(server_service, "get_server_by_id", return_value=mock_server):
            # Mock retrieve_tools_and_capabilities_from_server - 成功返回
            mock_tools = [{"name": "tool1"}]

            with patch.object(
                server_service,
                "retrieve_tools_and_capabilities_from_server",
                return_value=(mock_tools, [], [], {}, None),
            ):
                # Mock _schedule_vector_sync 并验证调用
                with patch.object(server_service, "_schedule_vector_sync") as mock_sync:
                    await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

                    # 验证 Weaviate 同步被触发
                    mock_sync.assert_called_once_with(mock_server, "old-hash")

    async def test_refresh_server_capabilities_updates_tool_count(self, server_service, mock_server):
        """测试刷新时更新工具数量"""
        from unittest.mock import patch

        # Mock get_server_by_id
        with patch.object(server_service, "get_server_by_id", return_value=mock_server):
            # Mock retrieve_tools_and_capabilities_from_server - 返回3个工具
            mock_tools = [{"name": "tool1"}, {"name": "tool2"}, {"name": "tool3"}]

            with patch.object(
                server_service,
                "retrieve_tools_and_capabilities_from_server",
                return_value=(mock_tools, [], [], {}, None),
            ):
                # Mock _schedule_vector_sync
                with patch.object(server_service, "_schedule_vector_sync"):
                    await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

                    # 验证 numTools 被更新
                    assert mock_server.numTools == 3

                    # 验证工具信息在 config 中
                    assert "toolFunctions" in mock_server.config
                    assert len(mock_server.config["toolFunctions"]) == 3


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
@pytest.mark.health
class TestHealthCheckEndpointUrlConstruction:
    """Test suite for health check endpoint URL construction.

    These tests verify that the health check correctly strips trailing slashes
    and uses the URL as-is without appending any path segments.
    """

    @pytest.fixture
    def mock_mcp_server(self):
        """Create a mock MCP server document."""
        from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer

        server = Mock(spec=ExtendedMCPServer)
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


@pytest.mark.asyncio
async def test_list_servers_enabled_only_filters_config_enabled():
    """enabled_only=True must filter on {config.enabled: True} (config.enabled is the source of truth)."""
    service = ServerServiceV1(
        user_service=Mock(),
        token_service=Mock(),
        oauth_service=Mock(),
        mcp_server_repo=Mock(),
    )
    with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
        MockServer.find.return_value.count = AsyncMock(return_value=0)
        MockServer.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(
            return_value=[]
        )

        await service.list_servers(enabled_only=True)

        query_filter = MockServer.find.call_args.args[0]
        assert query_filter == {"config.enabled": True}


@pytest.mark.asyncio
async def test_list_servers_without_enabled_only_has_no_status_filter():
    """Default (enabled_only=False) must not constrain on status or enabled."""
    service = ServerServiceV1(
        user_service=Mock(),
        token_service=Mock(),
        oauth_service=Mock(),
        mcp_server_repo=Mock(),
    )
    with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
        MockServer.find.return_value.count = AsyncMock(return_value=0)
        MockServer.find.return_value.sort.return_value.skip.return_value.limit.return_value.to_list = AsyncMock(
            return_value=[]
        )

        await service.list_servers()

        query_filter = MockServer.find.call_args.args[0]
        assert query_filter == {}
