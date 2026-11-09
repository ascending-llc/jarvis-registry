"""
Unit tests for server service.
"""

import asyncio as _asyncio
import json
import logging as _logging
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from beanie import PydanticObjectId

from registry.core.mcp_client import MCPServerData
from registry.schemas.server_api_schemas import ServerCreateRequest, ServerUpdateRequest
from registry.services.background_task_tracker import BackgroundTaskTracker
from registry.services.background_task_tracker import TrackerShutdownError as _TrackerShutdownError
from registry.services.server_service import ServerServiceV1
from registry.utils.crypto_utils import encrypt_auth_fields
from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer


def _record_config_at_save(server: Mock) -> list[dict[str, object]]:
    saved: list[dict[str, object]] = []

    async def save() -> None:
        saved.append(dict(server.config))

    server.save = AsyncMock(side_effect=save)
    return saved


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestFetchRegistrationCapabilities:
    @pytest.fixture
    def reindex_active(self) -> list[bool]:
        return [False]

    @pytest.fixture
    def service(self, reindex_active: list[bool]) -> ServerServiceV1:
        return ServerServiceV1(
            user_service=Mock(),
            token_service=Mock(),
            oauth_service=Mock(),
            mcp_server_repo=Mock(),
            embedding_maintenance_watcher=SimpleNamespace(is_active=lambda: reindex_active[0]),
        )

    @pytest.fixture
    def server(self) -> ExtendedMCPServer:
        return ExtendedMCPServer.model_construct(
            id=PydanticObjectId(),
            serverName="registration-test",
            tags=["test"],
            numTools=0,
            config={
                "url": "https://mcp.example.com/mcp",
                "type": "streamable-http",
                "apiKey": {"source": "system", "authorization_type": "bearer", "key": "test-only"},
                "capabilities": "{}",
                "toolFunctions": {},
                "tools": "",
                "enabled": False,
            },
        )

    @pytest.fixture
    def update(self) -> Iterator[AsyncMock]:
        """Patch the targeted update and fail if the test falls back to a whole-document save."""
        update = AsyncMock(return_value=Mock(matched_count=1))
        with (
            patch.object(ExtendedMCPServer, "find_one", Mock(return_value=Mock(update=update))) as find_one,
            patch.object(ExtendedMCPServer, "save", AsyncMock(side_effect=AssertionError("save() must not be used"))),
        ):
            update.find_one = find_one
            yield update

    @staticmethod
    def _set_fields(update: AsyncMock) -> dict[str, object]:
        (modifications,) = update.await_args.args
        assert set(modifications) == {"$set"}
        return modifications["$set"]

    @pytest.mark.parametrize(
        ("capabilities", "resources", "prompts"),
        [
            ({"tools": {"listChanged": True}}, [{"uri": "test://resource"}], [{"name": "summary"}]),
            ({}, None, None),
        ],
    )
    async def test_sets_only_fetched_fields(
        self,
        service: ServerServiceV1,
        server: ExtendedMCPServer,
        update: AsyncMock,
        capabilities: dict[str, object],
        resources: list[dict[str, str]] | None,
        prompts: list[dict[str, str]] | None,
    ) -> None:
        def retrieve(current_server: ExtendedMCPServer, **_: object) -> tuple[object, ...]:
            current_server.config["requiresInit"] = False
            return [{"name": "ignored-tool"}], resources, prompts, capabilities, None

        with patch.object(service, "retrieve_from_server", AsyncMock(side_effect=retrieve)) as fetch:
            await service.fetch_registration_capabilities(server, "user-1")

        fetch.assert_awaited_once_with(
            server,
            include_capabilities=True,
            include_resources=True,
            include_prompts=True,
            user_id="user-1",
        )
        update.find_one.assert_called_once_with({"_id": server.id})
        update.assert_awaited_once()
        assert self._set_fields(update) == {
            "config.capabilities": json.dumps(capabilities),
            "config.resources": resources or [],
            "config.prompts": prompts or [],
            "config.requiresInit": False,
            "updatedAt": ANY,
        }
        assert isinstance(self._set_fields(update)["updatedAt"], datetime)

    async def test_omits_requires_init_when_upstream_did_not_report_it(
        self, service: ServerServiceV1, server: ExtendedMCPServer, update: AsyncMock
    ) -> None:
        with patch.object(service, "retrieve_from_server", AsyncMock(return_value=([], [], [], {"tools": {}}, None))):
            await service.fetch_registration_capabilities(server, "user-1")

        assert "config.requiresInit" not in self._set_fields(update)

    @pytest.mark.parametrize("transport_type", ["sse", "streamable-http"])
    async def test_builds_real_auth_headers_from_encrypted_config(
        self,
        service: ServerServiceV1,
        server: ExtendedMCPServer,
        update: AsyncMock,
        transport_type: str,
    ) -> None:
        server.config.update(type=transport_type, headers={"X-Test": "custom-value"})
        server.config = encrypt_auth_fields(server.config)
        encrypted_key = server.config["apiKey"]["key"]
        assert encrypted_key != "test-only"
        result = MCPServerData(
            tools=[{"name": "ignored-tool"}],
            resources=[{"uri": "test://resource"}],
            prompts=[{"name": "summary"}],
            capabilities={"tools": {}},
            requires_init=False,
        )

        with patch(
            "registry.core.mcp_client.get_tools_and_capabilities_from_server", AsyncMock(return_value=result)
        ) as fetch:
            await service.fetch_registration_capabilities(server, "user-1")

        fetch.assert_awaited_once_with(
            server.config["url"],
            headers=ANY,
            transport_type=transport_type,
            include_resources=True,
            include_prompts=True,
        )
        headers = fetch.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-only"
        assert headers["X-Test"] == "custom-value"
        assert all(isinstance(value, str) for value in headers.values())
        assert set(headers).isdisjoint({"tags", "type", "apiKey", "headers"})
        fields = self._set_fields(update)
        assert json.loads(fields["config.capabilities"]) == result.capabilities
        assert fields["config.resources"] == result.resources
        assert fields["config.prompts"] == result.prompts
        assert fields["config.requiresInit"] is False

    @pytest.mark.parametrize(
        "config", [{}, {"oauth": {}}, {"requiresOAuth": True}], ids=["no-url", "oauth", "requires-oauth"]
    )
    async def test_skips_missing_url_and_oauth(
        self,
        service: ServerServiceV1,
        server: ExtendedMCPServer,
        update: AsyncMock,
        config: dict[str, object],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        server.config = {"url": "https://mcp.example.com/mcp", **config} if config else {}
        with (
            patch.object(service, "retrieve_from_server", new_callable=AsyncMock) as fetch,
            caplog.at_level(_logging.INFO, logger="registry.services.server_service"),
        ):
            await service.fetch_registration_capabilities(server, "user-1")

        fetch.assert_not_awaited()
        update.assert_not_awaited()
        if config:
            assert "Skipping registration capabilities fetch for OAuth server" in caplog.text

    @pytest.mark.parametrize("failure", ["error-tuple", "retrieve", "timeout", "update"])
    async def test_failure_returns_normally(
        self,
        service: ServerServiceV1,
        server: ExtendedMCPServer,
        update: AsyncMock,
        failure: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        if failure == "update":
            update.side_effect = RuntimeError("update failed")

        async def retrieve(_: ExtendedMCPServer, **__: object) -> tuple[object, ...]:
            if failure == "error-tuple":
                return None, None, None, None, "boom"
            if failure == "retrieve":
                raise RuntimeError("retrieve failed")
            if failure == "timeout":
                raise TimeoutError("upstream timed out")
            return [], [{"uri": "test://resource"}], [{"name": "summary"}], {"tools": {}}, None

        with (
            patch.object(service, "retrieve_from_server", AsyncMock(side_effect=retrieve)),
            caplog.at_level(_logging.WARNING, logger="registry.services.server_service"),
        ):
            await service.fetch_registration_capabilities(server, "user-1")

        assert "Registration capabilities fetch failed" in caplog.text
        if failure == "update":
            update.assert_awaited_once()
        else:
            update.assert_not_awaited()

    async def test_skips_write_when_reindex_starts_during_fetch(
        self,
        service: ServerServiceV1,
        server: ExtendedMCPServer,
        update: AsyncMock,
        reindex_active: list[bool],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def retrieve(_: ExtendedMCPServer, **__: object) -> tuple[object, ...]:
            reindex_active[0] = True
            return [], [], [], {"tools": {}}, None

        with (
            patch.object(service, "retrieve_from_server", AsyncMock(side_effect=retrieve)),
            caplog.at_level(_logging.INFO, logger="registry.services.server_service"),
        ):
            await service.fetch_registration_capabilities(server, "user-1")

        update.assert_not_awaited()
        assert "during an embedding reindex" in caplog.text

    async def test_deleted_server_is_not_reinserted(
        self,
        service: ServerServiceV1,
        server: ExtendedMCPServer,
        update: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        update.return_value = Mock(matched_count=0)

        with (
            patch.object(service, "retrieve_from_server", AsyncMock(return_value=([], [], [], {"tools": {}}, None))),
            caplog.at_level(_logging.INFO, logger="registry.services.server_service"),
        ):
            await service.fetch_registration_capabilities(server, "user-1")

        update.assert_awaited_once()
        assert "upsert" not in update.await_args.kwargs
        assert "was deleted before its registration capabilities were stored" in caplog.text


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestScheduleRegistrationCapabilities:
    @pytest.fixture
    def server(self) -> ExtendedMCPServer:
        return ExtendedMCPServer.model_construct(
            id=PydanticObjectId(),
            serverName="registration-test",
            config={"url": "https://mcp.example.com/mcp", "capabilities": "{}"},
        )

    async def test_spawns_fetch_on_a_copy(self, server: ExtendedMCPServer) -> None:
        tracker = _RunningTracker()
        service = _delete_service(Mock(), tracker)
        fetched: list[ExtendedMCPServer] = []

        async def fetch(current_server: ExtendedMCPServer, user_id: str) -> None:
            assert user_id == "user-1"
            current_server.config["requiresInit"] = False
            fetched.append(current_server)

        with patch.object(service, "fetch_registration_capabilities", side_effect=fetch):
            service.schedule_registration_capabilities(server, "user-1")
            await tracker.task

        assert tracker.names == [f"registration-capabilities-server-{server.id}"]
        assert fetched[0] is not server
        assert fetched[0].id == server.id
        assert "requiresInit" not in server.config

    @pytest.mark.parametrize("tracker", [None, "shutting-down"])
    async def test_refused_spawn_is_tolerated(
        self, server: ExtendedMCPServer, tracker: str | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        refusing = BackgroundTaskTracker()
        await refusing.shutdown()
        service = _delete_service(Mock(), refusing if tracker else None)

        with (
            patch.object(service, "retrieve_from_server", new_callable=AsyncMock) as fetch,
            caplog.at_level(_logging.INFO, logger="registry.services.server_service"),
        ):
            service.schedule_registration_capabilities(server, "user-1")

        fetch.assert_not_awaited()
        assert "refresh the server to fetch its capabilities" in caplog.text


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestRefreshServerCapabilities:
    """Test suite for refresh_server_capabilities service method."""

    @pytest.fixture
    def mock_server(self):
        """Create a mock MCP server document."""
        from datetime import UTC, datetime

        server = Mock(spec=ExtendedMCPServer)
        server.id = Mock()
        server.serverName = "test-server"
        server.config = {"title": "Test Server"}
        server.lastError = None
        server.errorMessage = None
        server.lastConnected = None
        server.updatedAt = datetime.now(UTC)
        server.vectorSyncPendingAt = None
        server.vectorContentHash = "old-hash"
        server.numTools = 0
        server.registryDisabledTools = []
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
                # Prevent the real vector sync (scheduled post-commit)
                with patch.object(server_service, "schedule_vector_sync"):
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
                # Prevent the real vector sync (scheduled post-commit)
                with patch.object(server_service, "schedule_vector_sync"):
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

    @pytest.mark.parametrize("previous_requires_init", [True, False, None], ids=["stateful", "stateless", "unknown"])
    async def test_failed_probe_preserves_requires_init_when_saved(
        self, server_service: ServerServiceV1, mock_server: Mock, previous_requires_init: bool | None
    ) -> None:
        mock_server.config["url"] = "https://mcp.example.com/mcp"
        if previous_requires_init is not None:
            mock_server.config["requiresInit"] = previous_requires_init
        original_config = dict(mock_server.config)
        saved = _record_config_at_save(mock_server)
        probe_result = MCPServerData(None, None, None, None, "Timeout connecting to server")

        with (
            patch.object(server_service, "get_server_by_id", return_value=mock_server),
            patch("registry.services.server_service.build_complete_headers_for_server", AsyncMock(return_value={})),
            patch("registry.core.mcp_client.get_tools_and_capabilities_from_server", return_value=probe_result),
            patch.object(server_service, "schedule_vector_sync") as sync,
        ):
            result = await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

        assert result["status"] == "failed"
        assert saved == [original_config]
        assert mock_server.config == original_config
        mock_server.save.assert_awaited_once()
        sync.assert_not_called()

    @pytest.mark.parametrize("requires_init", [True, False], ids=["stateful", "stateless"])
    async def test_successful_probe_updates_requires_init_when_saved(
        self, server_service: ServerServiceV1, mock_server: Mock, requires_init: bool
    ) -> None:
        mock_server.config.update({"url": "https://mcp.example.com/mcp", "requiresInit": not requires_init})
        saved = _record_config_at_save(mock_server)
        probe_result = MCPServerData([], [], [], {}, requires_init=requires_init)

        with (
            patch.object(server_service, "get_server_by_id", return_value=mock_server),
            patch("registry.services.server_service.build_complete_headers_for_server", AsyncMock(return_value={})),
            patch("registry.core.mcp_client.get_tools_and_capabilities_from_server", return_value=probe_result),
            patch.object(server_service, "schedule_vector_sync"),
        ):
            result = await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

        assert result["status"] == "success"
        assert [config["requiresInit"] for config in saved] == [requires_init]
        assert mock_server.config["requiresInit"] is requires_init
        mock_server.save.assert_awaited_once()

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
                # Mock schedule_vector_sync 并验证调用
                with patch.object(server_service, "schedule_vector_sync") as mock_sync:
                    await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

                    # 验证 Weaviate 同步被触发，且携带该 server 的 plan
                    mock_sync.assert_called_once()
                    plan = mock_sync.call_args.args[0]
                    assert plan.doc is mock_server
                    assert mock_server.vectorSyncPendingAt is not None

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
                # Prevent the real vector sync (scheduled post-commit)
                with patch.object(server_service, "schedule_vector_sync"):
                    await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

                    # 验证 numTools 被更新
                    assert mock_server.numTools == 3

                    # 验证工具信息在 config 中
                    assert "toolFunctions" in mock_server.config
                    assert len(mock_server.config["toolFunctions"]) == 3

    async def test_refresh_server_capabilities_zero_tools_clears_stale_state(self, server_service, mock_server):
        """A successful refresh with tool_list=[] (server now serves no tools) must still clear
        stale toolFunctions/numTools/tools and prune registryDisabledTools to [] — tool_list=[] is
        a legitimate success result, distinct from the tool_list is None failure case."""
        from unittest.mock import patch

        mock_server.config = {
            "title": "Test Server",
            "toolFunctions": {"old_tool_mcp_test": {"mcpToolName": "old_tool"}},
            "tools": "old_tool",
        }
        mock_server.numTools = 1
        mock_server.registryDisabledTools = ["old_tool"]

        with patch.object(server_service, "get_server_by_id", return_value=mock_server):
            with patch.object(
                server_service,
                "retrieve_tools_and_capabilities_from_server",
                return_value=([], [], [], {}, None),
            ):
                with patch.object(server_service, "schedule_vector_sync"):
                    result = await server_service.refresh_server_capabilities(server_id="test-id", user_id="user-123")

        assert result["status"] == "success"
        assert mock_server.config["toolFunctions"] == {}
        assert mock_server.config["tools"] == ""
        assert mock_server.numTools == 0
        assert mock_server.registryDisabledTools == []


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

    def test_build_config_converts_apikey_to_snake_case(self):
        """Create stores apiKey sub-fields in snake_case, with no camelCase keys left."""
        from registry.schemas.server_api_schemas import ServerCreateRequest
        from registry.services.server_service import _build_config_from_request

        data = ServerCreateRequest(
            title="Test Server",
            path="/test",
            apiKey={
                "source": "admin",
                "authorizationType": "custom",
                "customHeader": "Authorization",
                "key": "ApiKey x",
            },
        )

        config = _build_config_from_request(data, server_name="Test Server")

        assert config["apiKey"] == {
            "source": "admin",
            "authorization_type": "custom",
            "custom_header": "Authorization",
            "key": "ApiKey x",
        }
        assert "authorizationType" not in config["apiKey"]
        assert "customHeader" not in config["apiKey"]

    def test_update_apikey_converts_camel_case_before_merge(self):
        """Update converts incoming camelCase before merging, overwriting stale snake_case keys."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        # Stored record is snake_case with an encrypted key and no key in the incoming update.
        existing_config = {
            "apiKey": {"source": "admin", "authorization_type": "bearer", "key": "enc-key"},
        }

        data = ServerUpdateRequest(
            apiKey={"source": "admin", "authorizationType": "custom", "customHeader": "Authorization"}
        )

        updated = _update_config_from_request(existing_config, data, server_name="Test Server")

        assert updated["apiKey"] == {
            "source": "admin",
            "authorization_type": "custom",
            "custom_header": "Authorization",
            "key": "enc-key",
        }
        assert "authorizationType" not in updated["apiKey"]
        assert "customHeader" not in updated["apiKey"]

    def test_update_apikey_drops_legacy_camelcase_from_stored_record(self):
        """A legacy camelCase stored record leaves no camelCase leftover after update."""
        from registry.schemas.server_api_schemas import ServerUpdateRequest
        from registry.services.server_service import _update_config_from_request

        # Stored record is still in legacy camelCase (never migrated).
        existing_config = {
            "apiKey": {"source": "admin", "authorizationType": "bearer", "key": "enc-key"},
        }

        data = ServerUpdateRequest(apiKey={"authorizationType": "custom", "customHeader": "Authorization"})

        updated = _update_config_from_request(existing_config, data, server_name="Test Server")

        assert updated["apiKey"] == {
            "source": "admin",
            "authorization_type": "custom",
            "custom_header": "Authorization",
            "key": "enc-key",
        }
        # No camelCase key survives the merge.
        assert "authorizationType" not in updated["apiKey"]
        assert "customHeader" not in updated["apiKey"]

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


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestCreateServerNormalizedServerName:
    """create_server must compute and persist normalizedServerName (AS-1855)."""

    def _make_service(self):
        from beanie import PydanticObjectId

        service = ServerServiceV1(
            user_service=Mock(),
            token_service=Mock(),
            oauth_service=Mock(),
            mcp_server_repo=Mock(),
        )
        service.user_service.get_user_by_user_id = AsyncMock(return_value=Mock(id=PydanticObjectId()))
        return service

    async def test_sets_normalized_server_name_on_construction(self):
        from registry.schemas.server_api_schemas import ServerCreateRequest

        service = self._make_service()
        data = ServerCreateRequest(title="Example Server!!", path="/example")

        with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
            MockServer.find.return_value.to_list = AsyncMock(return_value=[])
            MockServer.find_one = AsyncMock(return_value=None)
            MockServer.return_value.insert = AsyncMock()

            await service.create_server(data=data, user_id="user-1")

            _, kwargs = MockServer.call_args
            assert kwargs["serverName"] == "example-server"
            assert kwargs["normalizedServerName"] == "example-server"

    async def test_duplicate_check_matches_on_either_server_name_or_normalized_name(self):
        """The lookup query must OR serverName and normalizedServerName together."""
        from registry.schemas.server_api_schemas import ServerCreateRequest

        service = self._make_service()
        data = ServerCreateRequest(title="Example Server", path="/example")

        with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
            MockServer.find.return_value.to_list = AsyncMock(return_value=[])
            MockServer.find_one = AsyncMock(return_value=None)
            MockServer.return_value.insert = AsyncMock()

            await service.create_server(data=data, user_id="user-1")

            query_filter = MockServer.find_one.call_args.args[0]
            assert query_filter == {
                "$or": [{"serverName": "example-server"}, {"normalizedServerName": "example-server"}]
            }

    async def test_rejects_normalized_name_collision_even_when_raw_server_names_differ(self):
        """A new slug-based server_name must be rejected if it collides on normalizedServerName
        with an existing document whose raw serverName differs (e.g. it predates normalization
        or used unsafe characters that normalize to the new slug)."""
        from registry.schemas.server_api_schemas import ServerCreateRequest

        service = self._make_service()
        data = ServerCreateRequest(title="Weird Server", path="/weird")

        existing_conflict = Mock(serverName="Weird/Server", normalizedServerName="weird-server")

        with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
            MockServer.find.return_value.to_list = AsyncMock(return_value=[])
            MockServer.find_one = AsyncMock(return_value=existing_conflict)

            with pytest.raises(ValueError, match="already exists"):
                await service.create_server(data=data, user_id="user-1")

    @pytest.mark.parametrize("save_fails", [False, True])
    async def test_post_registration_save_does_not_fetch_or_delete(self, save_fails: bool) -> None:
        service = self._make_service()
        data = ServerCreateRequest(title="Example Server", path="/example", url="https://mcp.example.com/mcp")
        session = Mock()
        error = RuntimeError("save failed")

        with (
            patch("registry.services.server_service.ExtendedMCPServer") as MockServer,
            patch("registry.core.mcp_client.perform_health_check", AsyncMock(return_value=(True, "healthy", 1, None))),
            patch("registry.core.mcp_client.get_tools_and_capabilities_from_server", new_callable=AsyncMock) as fetch,
        ):
            MockServer.find.return_value.to_list = AsyncMock(return_value=[])
            MockServer.find_one = AsyncMock(return_value=None)
            server = MockServer.return_value
            server.config = {"url": data.url, "capabilities": "{}"}
            server.insert = AsyncMock()
            server.save = AsyncMock(side_effect=error if save_fails else None)
            server.delete = AsyncMock()

            if save_fails:
                with pytest.raises(RuntimeError) as exc_info:
                    await service.create_server(data=data, user_id="user-1", session=session)
                assert exc_info.value is error
            else:
                result = await service.create_server(data=data, user_id="user-1", session=session)
                assert result is server
                assert server.config["capabilities"] == "{}"
                assert server.config["toolFunctions"] == {}
                assert server.config["tools"] == ""
                assert server.numTools == 0

            server.save.assert_awaited_once_with(session=session)
            server.delete.assert_not_awaited()
            fetch.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestReindexGate:
    """create_server/update_server must refuse to write while a reindex is active."""

    def _make_service(self, *, reindex_active: bool):
        from types import SimpleNamespace

        return ServerServiceV1(
            user_service=Mock(),
            token_service=Mock(),
            oauth_service=Mock(),
            mcp_server_repo=Mock(),
            embedding_maintenance_watcher=SimpleNamespace(is_active=lambda: reindex_active),
        )

    async def test_create_server_raises_before_any_mongo_read_or_write(self):

        service = self._make_service(reindex_active=True)
        data = ServerCreateRequest(title="Gated Server", path="/gated")

        with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
            with pytest.raises(EmbeddingReindexInProgressException):
                await service.create_server(data=data, user_id="user-1")
            MockServer.find.assert_not_called()

    async def test_update_server_raises_before_lookup(self):

        service = self._make_service(reindex_active=True)
        service.get_server_by_id = AsyncMock()

        with pytest.raises(EmbeddingReindexInProgressException):
            await service.update_server(server_id="srv-gated", data=ServerUpdateRequest())
        service.get_server_by_id.assert_not_called()

    async def test_delete_server_raises_before_lookup(self):
        service = self._make_service(reindex_active=True)

        with patch("registry.services.server_service.ExtendedMCPServer") as MockServer:
            with pytest.raises(EmbeddingReindexInProgressException):
                await service.delete_server(server_id="srv-gated")
            MockServer.get.assert_not_called()

    async def test_toggle_server_raises_before_lookup(self):
        service = self._make_service(reindex_active=True)
        service.get_server_by_id = AsyncMock()

        with pytest.raises(EmbeddingReindexInProgressException):
            await service.toggle_server_status(server_id="srv-gated", enabled=False)
        service.get_server_by_id.assert_not_called()

    async def test_refresh_server_raises_before_lookup(self):
        service = self._make_service(reindex_active=True)
        service.get_server_by_id = AsyncMock()

        with pytest.raises(EmbeddingReindexInProgressException):
            await service.refresh_server_capabilities(server_id="srv-gated")
        service.get_server_by_id.assert_not_called()

    async def test_update_disabled_tools_raises_before_lookup(self):
        service = self._make_service(reindex_active=True)
        service.get_server_by_id = AsyncMock()

        with pytest.raises(EmbeddingReindexInProgressException):
            await service.update_disabled_tools(server_id="srv-gated", disabled_tools=["t1"])
        service.get_server_by_id.assert_not_called()

    async def test_inactive_gate_does_not_block(self):

        service = self._make_service(reindex_active=False)
        service.get_server_by_id = AsyncMock(return_value=None)

        # Gate off -> proceeds to the normal "not found" path instead of raising the gate error.
        with pytest.raises(ValueError, match="not found"):
            await service.update_server(server_id="srv-gated", data=ServerUpdateRequest())


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestUpdateDisabledTools:
    """Test suite for update_disabled_tools and refresh-time pruning."""

    def _make_service(self):
        mock_repo = Mock()
        mock_repo.sync_tools_enabled = AsyncMock()
        service = ServerServiceV1(
            user_service=Mock(),
            token_service=Mock(),
            oauth_service=Mock(),
            mcp_server_repo=mock_repo,
        )
        return service, mock_repo

    def _make_server(self, disabled=None):
        from datetime import UTC, datetime

        server = Mock(spec=ExtendedMCPServer)
        server.id = "srv-1"
        server.serverName = "test-server"
        server.config = {
            "toolFunctions": {
                "read_file_mcp_test": {"mcpToolName": "read_file"},
                "write_file_mcp_test": {"mcpToolName": "write_file"},
                "delete_file_mcp_test": {"mcpToolName": "delete_file"},
            }
        }
        server.vectorSyncPendingAt = None
        server.registryDisabledTools = disabled if disabled is not None else []
        server.updatedAt = datetime.now(UTC)
        server.save = AsyncMock()
        return server

    async def test_drops_unknown_tool_names(self):
        """Submitted names that are not current tools are silently dropped, not persisted."""
        service, _ = self._make_service()
        server = self._make_server()

        with patch.object(service, "get_server_by_id", return_value=server):
            with patch.object(service, "_schedule_tool_enabled_sync") as mock_schedule:
                result = await service.update_disabled_tools("srv-1", ["read_file", "ghost_tool"])

        assert result.registryDisabledTools == ["read_file"]  # ghost_tool dropped, sorted
        server.save.assert_awaited_once()
        # Only the real newly-disabled tool is scheduled for the Weaviate push; the marker is set first.
        mock_schedule.assert_called_once_with(ANY, {"read_file"}, set())
        assert mock_schedule.call_args.args[0].full_resync is False
        assert server.vectorSyncPendingAt is not None

    async def test_schedules_flip_sets_for_weaviate_push(self):
        """update_disabled_tools schedules the flipped new/previous sets, not the server's whole tool set."""
        service, _ = self._make_service()
        server = self._make_server(disabled=["read_file"])

        with patch.object(service, "get_server_by_id", return_value=server):
            with patch.object(service, "_schedule_tool_enabled_sync") as mock_schedule:
                # read_file re-enabled (removed), write_file newly disabled.
                await service.update_disabled_tools("srv-1", ["write_file"])

        assert server.registryDisabledTools == ["write_file"]
        mock_schedule.assert_called_once_with(ANY, {"write_file"}, {"read_file"})

    async def test_noop_when_unchanged_skips_save_and_weaviate(self):
        """Submitting the same effective list is an idempotent no-op."""
        service, _ = self._make_service()
        server = self._make_server(disabled=["read_file"])

        with patch.object(service, "get_server_by_id", return_value=server):
            with patch.object(service, "_schedule_tool_enabled_sync") as mock_schedule:
                await service.update_disabled_tools("srv-1", ["read_file"])

        server.save.assert_not_awaited()
        mock_schedule.assert_not_called()

    @pytest.mark.parametrize("pending", [False, True])
    async def test_tool_delta_preserves_prior_pending_work(self, pending):
        service, _ = self._make_service()
        server = self._make_server(disabled=["read_file"])
        server.vectorSyncPendingAt = datetime.now(UTC) if pending else None
        server.vectorSyncAttempts = 4
        with (
            patch.object(service, "get_server_by_id", return_value=server),
            patch.object(service, "schedule_vector_sync") as schedule,
        ):
            await service.update_disabled_tools("srv-1", ["write_file"])
        plan = schedule.call_args.args[0]
        assert plan.full_resync is pending
        assert plan.tool_updates == ((("write_file",), False), (("read_file",), True))
        assert server.vectorSyncAttempts == 0
        assert plan.marker == server.vectorSyncPendingAt

    async def test_server_not_found_raises(self):
        service, _ = self._make_service()
        with patch.object(service, "get_server_by_id", return_value=None):
            with pytest.raises(ValueError, match="not found"):
                await service.update_disabled_tools("missing", ["x"])

    async def test_refresh_prunes_stale_disabled_names(self):
        """A capabilities refresh drops disabled names that no longer exist downstream."""
        from datetime import UTC, datetime

        service, _ = self._make_service()
        server = Mock(spec=ExtendedMCPServer)
        server.id = "srv-1"
        server.serverName = "test-server"
        server.config = {}
        server.registryDisabledTools = ["read_file", "delete_file"]  # delete_file removed downstream
        server.lastError = None
        server.errorMessage = None
        server.lastConnected = None
        server.updatedAt = datetime.now(UTC)
        server.vectorSyncPendingAt = None
        server.vectorContentHash = "old-hash"
        server.numTools = 0
        server.save = AsyncMock()

        with patch.object(service, "get_server_by_id", return_value=server):
            with patch.object(
                service,
                "retrieve_tools_and_capabilities_from_server",
                return_value=([{"name": "read_file"}], [], [], {"sampling": {}}, None),
            ):
                with patch.object(service, "schedule_vector_sync"):
                    result = await service.refresh_server_capabilities(server_id="srv-1", user_id="u1")

        assert result["status"] == "success"
        assert server.registryDisabledTools == ["read_file"]  # delete_file pruned


class TestOAuthReAuthRequired:
    """Enable/refresh must not orphan an OAuth flow and must surface a typed re-auth error."""

    def _make_service(self) -> ServerServiceV1:
        return ServerServiceV1(
            user_service=Mock(),
            token_service=Mock(),
            oauth_service=Mock(),
            mcp_server_repo=Mock(),
        )

    def _oauth_server(self) -> Mock:

        server = Mock(spec=ExtendedMCPServer)
        server.id = "srv-1"
        server.serverName = "google-workspace"
        server.config = {"url": "https://mcp.example.com/mcp", "oauth": {"client_id": "abc"}, "enabled": False}
        server.lastError = None
        server.errorMessage = None
        server.save = AsyncMock()
        return server

    async def test_retrieve_from_server_runs_non_interactively_and_raises(self):
        from registry_pkgs.oauth.errors import OAuthReAuthRequiredError

        service = self._make_service()
        server = self._oauth_server()
        build = AsyncMock(side_effect=OAuthReAuthRequiredError("re-auth", server_name="google-workspace"))

        with (
            patch("registry.services.server_service.build_complete_headers_for_server", build),
            patch("registry.core.mcp_client.get_tools_and_capabilities_from_server") as fetch,
        ):
            with pytest.raises(OAuthReAuthRequiredError) as exc_info:
                await service.retrieve_from_server(server, user_id="user-1")

        assert exc_info.value.auth_url is None
        assert build.await_args.kwargs["interactive"] is False
        fetch.assert_not_called()

    async def test_toggle_enable_rolls_back_and_reraises(self):
        from registry_pkgs.oauth.errors import OAuthReAuthRequiredError

        service = self._make_service()
        server = self._oauth_server()
        service.get_server_by_id = AsyncMock(return_value=server)
        error = OAuthReAuthRequiredError("re-auth", server_name="google-workspace")

        with (
            patch.object(service, "retrieve_from_server", AsyncMock(side_effect=error)),
            patch.object(service, "schedule_vector_sync") as sync,
        ):
            with pytest.raises(OAuthReAuthRequiredError) as exc_info:
                await service.toggle_server_status(server_id="srv-1", enabled=True, user_id="user-1")

        assert exc_info.value is error
        assert server.config["enabled"] is False
        server.save.assert_awaited_once()
        sync.assert_not_called()

    async def test_toggle_enable_other_fetch_failure_keeps_value_error(self) -> None:
        service = self._make_service()
        server = self._oauth_server()
        server.config["requiresInit"] = True
        original_config = dict(server.config)
        saved = _record_config_at_save(server)
        service.get_server_by_id = AsyncMock(return_value=server)
        probe_result = MCPServerData(None, None, None, None, "Connection refused")

        with (
            patch("registry.services.server_service.build_complete_headers_for_server", AsyncMock(return_value={})),
            patch("registry.core.mcp_client.get_tools_and_capabilities_from_server", return_value=probe_result),
        ):
            with pytest.raises(ValueError, match="Failed to fetch tools from server. Server remains disabled."):
                await service.toggle_server_status(server_id="srv-1", enabled=True, user_id="user-1")

        assert server.config["enabled"] is False
        assert saved == [original_config]
        server.save.assert_awaited_once()

    async def test_refresh_leaves_server_error_fields_untouched_and_does_not_save(self):
        from registry_pkgs.oauth.errors import OAuthReAuthRequiredError

        service = self._make_service()
        server = self._oauth_server()
        server.lastError = "previous"
        server.errorMessage = "previous message"
        service.get_server_by_id = AsyncMock(return_value=server)
        build = AsyncMock(side_effect=OAuthReAuthRequiredError("re-auth", server_name="google-workspace"))

        with (
            patch("registry.services.server_service.build_complete_headers_for_server", build),
            patch.object(service, "schedule_vector_sync") as sync,
        ):
            with pytest.raises(OAuthReAuthRequiredError):
                await service.refresh_server_capabilities(server_id="srv-1", user_id="user-1")

        assert server.lastError == "previous"
        assert server.errorMessage == "previous message"
        server.save.assert_not_called()
        sync.assert_not_called()


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
class TestRegistryOAuthLifecycleOnUpdate:
    """Server create/update no longer run OAuth discovery; a URL change or OAuth removal unsets
    registryOAuth, which save() can't touch (AS-1929)."""

    def _make_service(self):
        from types import SimpleNamespace

        return ServerServiceV1(
            user_service=Mock(),
            token_service=Mock(),
            oauth_service=Mock(),
            mcp_server_repo=Mock(),
            embedding_maintenance_watcher=SimpleNamespace(is_active=lambda: False),
        )

    def _make_server(self, config):
        from beanie import PydanticObjectId

        return ExtendedMCPServer.model_construct(
            id=PydanticObjectId("65f000000000000000000001"),
            serverName="atlassian",
            path="/atlassian",
            tags=[],
            config=config,
            vectorContentHash=None,
            numTools=0,
        )

    async def _update(
        self, server: ExtendedMCPServer, data: ServerUpdateRequest
    ) -> tuple[AsyncMock, list[dict[str, object]]]:
        service = self._make_service()
        service.get_server_by_id = AsyncMock(return_value=server)
        service.schedule_vector_sync = Mock()  # update_server returns a plan; route schedules it
        collection = Mock()
        collection.update_one = AsyncMock()
        saved_configs: list[dict[str, object]] = []

        async def record_config(**_: object) -> None:
            saved_configs.append(dict(server.config))

        with (
            patch.object(ExtendedMCPServer, "save", AsyncMock(side_effect=record_config)),
            patch.object(ExtendedMCPServer, "get_pymongo_collection", Mock(return_value=collection)),
        ):
            await service.update_server(server_id=str(server.id), data=data)
        return collection.update_one, saved_configs

    async def test_url_change_unsets_registry_oauth(self):
        server = self._make_server({"url": "https://mcp.atlassian.com/v1/sse", "requiresOAuth": True})

        update_one, _ = await self._update(server, ServerUpdateRequest(url="https://mcp.atlassian.com/v2/mcp"))

        update_one.assert_awaited_once_with({"_id": server.id}, {"$unset": {"registryOAuth": ""}}, session=None)
        assert "oauthMetadata" not in server.config

    async def test_removing_oauth_unsets_registry_oauth(self):
        server = self._make_server({"url": "https://mcp.example.com/mcp", "requiresOAuth": True})

        update_one, _ = await self._update(server, ServerUpdateRequest(requiresOauth=False))

        update_one.assert_awaited_once()

    async def test_unrelated_edit_keeps_registry_oauth(self):
        server = self._make_server({"url": "https://mcp.example.com/mcp", "requiresOAuth": True})

        update_one, _ = await self._update(server, ServerUpdateRequest(title="Renamed"))

        update_one.assert_not_awaited()

    @pytest.mark.parametrize(
        "data",
        [ServerUpdateRequest(url="https://other.example.com/mcp"), ServerUpdateRequest(type="sse")],
        ids=["url", "type"],
    )
    async def test_endpoint_change_clears_requires_init(self, data: ServerUpdateRequest) -> None:
        server = self._make_server(
            {"url": "https://mcp.example.com/mcp", "type": "streamable-http", "requiresInit": False}
        )

        _, saved_configs = await self._update(server, data)

        (saved_config,) = saved_configs
        assert "requiresInit" not in saved_config
        assert "requiresInit" not in server.config

    @pytest.mark.parametrize(
        "data",
        [
            ServerUpdateRequest(title="Renamed"),
            ServerUpdateRequest(apiKey={"key": "test-api-key", "authorizationType": "bearer"}),
            ServerUpdateRequest(headers={"X-Test": "updated"}),
            ServerUpdateRequest(oauth={"clientId": "updated-client"}),
            ServerUpdateRequest(url="https://mcp.example.com/mcp", type="streamable-http"),
        ],
        ids=["title", "api-key", "headers", "oauth", "same-endpoint"],
    )
    async def test_unchanged_endpoint_keeps_requires_init(self, data: ServerUpdateRequest) -> None:
        server = self._make_server(
            {"url": "https://mcp.example.com/mcp", "type": "streamable-http", "requiresInit": False}
        )

        _, saved_configs = await self._update(server, data)

        (saved_config,) = saved_configs
        assert saved_config["requiresInit"] is False
        assert server.config["requiresInit"] is False

    async def test_explicit_default_type_keeps_requires_init(self) -> None:
        server = self._make_server({"url": "https://mcp.example.com/mcp", "requiresInit": False})

        _, saved_configs = await self._update(server, ServerUpdateRequest(type="streamable-http"))

        (saved_config,) = saved_configs
        assert saved_config["requiresInit"] is False
        assert server.config["requiresInit"] is False

    async def test_create_and_update_no_longer_run_discovery(self):
        import registry.services.server_service as server_service_module

        assert not hasattr(server_service_module, "get_oauth_metadata_from_server")


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
async def test_toggle_enable_marks_pending_and_forces_full_resync():
    """Enabling a server marks it pending in the same save and forces a full resync even when the
    content hash is unchanged."""
    service = ServerServiceV1(user_service=Mock(), token_service=Mock(), oauth_service=Mock(), mcp_server_repo=Mock())
    server = Mock()
    server.id = "srv-1"
    server.serverName = "s"
    server.config = {"enabled": False}
    server.vectorSyncPendingAt = None
    server.vectorContentHash = "h"
    server.save = AsyncMock()

    with (
        patch.object(service, "get_server_by_id", AsyncMock(return_value=server)),
        patch.object(service, "_fetch_and_update_tools", AsyncMock(return_value=True)),
        patch.object(service, "schedule_vector_sync") as sched,
    ):
        await service.toggle_server_status(server_id="srv-1", enabled=True, user_id="u")

    assert server.vectorSyncPendingAt is not None
    plan = sched.call_args.args[0]
    assert plan.doc is server
    assert plan.full_resync is True
    assert plan.marker == server.vectorSyncPendingAt


def _delete_service(repo, tracker=None):
    return ServerServiceV1(
        user_service=Mock(),
        token_service=Mock(),
        oauth_service=Mock(),
        mcp_server_repo=repo,
        background_task_tracker=tracker,
    )


class _RunningTracker:
    """A tracker whose spawn runs the coroutine inline (and records the name)."""

    def __init__(self):
        self.names = []
        self.task = None

    def spawn(self, coro, name):
        self.names.append(name)
        self.task = _asyncio.ensure_future(coro)


@pytest.mark.asyncio
async def test_delete_server_no_longer_deletes_vectors():
    from beanie import PydanticObjectId

    repo = Mock()
    repo.delete_by_server_id = AsyncMock()
    service = _delete_service(repo)
    fake_server = Mock()
    fake_server.id = PydanticObjectId()
    fake_server.serverName = "srv-x"
    fake_server.delete = AsyncMock()

    with (
        patch("registry.services.server_service.ExtendedMCPServer") as MockServer,
    ):
        MockServer.get = AsyncMock(return_value=fake_server)
        name = await service.delete_server(server_id=str(fake_server.id))

    assert name == "srv-x"
    repo.delete_by_server_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_vector_delete_goes_through_tracker():
    repo = Mock()
    repo.delete_by_server_id = AsyncMock(return_value=1)
    tracker = _RunningTracker()
    service = _delete_service(repo, tracker)

    service.schedule_vector_delete("srv-1", "Srv One")
    await tracker.task

    assert tracker.names == ["vector-delete-server-srv-1"]
    repo.delete_by_server_id.assert_awaited_once_with("srv-1", "Srv One")


@pytest.mark.asyncio
async def test_schedule_vector_delete_logs_warning_on_repo_failure(caplog):
    repo = Mock()
    repo.delete_by_server_id = AsyncMock(side_effect=RuntimeError("weaviate down"))
    tracker = _RunningTracker()
    service = _delete_service(repo, tracker)

    with caplog.at_level(_logging.WARNING):
        service.schedule_vector_delete("srv-1", "Srv One")
        await tracker.task  # must not raise

    assert any("Vector delete failed for server srv-1" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_schedule_vector_delete_returns_when_tracker_refuses(caplog):
    repo = Mock()
    repo.delete_by_server_id = AsyncMock()

    def _refuse(coro, name):
        coro.close()  # the real tracker closes the coro before refusing
        raise _TrackerShutdownError()

    tracker = Mock()
    tracker.spawn = _refuse
    service = _delete_service(repo, tracker)

    # Returns normally; the Mongo delete already committed, the sweep heals the orphan.
    with caplog.at_level(_logging.INFO, logger="registry.services.server_service"):
        service.schedule_vector_delete("srv-1", "Srv One")

    # The log names the orphan sweep, not the reconciler (which never heals deletes).
    assert "the orphan sweep will remove its vectors" in caplog.text
    assert "reconciler" not in caplog.text


def _record_marker_at_save(doc) -> list[tuple]:
    """Make ``doc.save`` record the marker fields as they are at save time (i.e. what commits)."""
    saved: list[tuple] = []

    async def save(*args, **kwargs):
        saved.append((doc.vectorSyncPendingAt, doc.vectorSyncAttempts))

    doc.save = AsyncMock(side_effect=save)
    return saved


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
async def test_update_server_persists_marker_in_same_save_and_resets_attempts():
    """update_server stamps the marker before its save (so it commits with the change), resets the
    backoff counter, and returns a plan carrying that exact marker."""
    from types import SimpleNamespace

    from beanie import PydanticObjectId

    service = ServerServiceV1(
        user_service=Mock(),
        token_service=Mock(),
        oauth_service=Mock(),
        mcp_server_repo=Mock(),
        embedding_maintenance_watcher=SimpleNamespace(is_active=lambda: False),
    )
    server = ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName="srv",
        path="/srv",
        tags=[],
        config={"url": "https://mcp.example.com/mcp"},
        vectorContentHash=None,
        vectorSyncPendingAt=None,
        vectorSyncAttempts=5,
        numTools=0,
    )
    service.get_server_by_id = AsyncMock(return_value=server)
    collection = Mock(update_one=AsyncMock())
    with (
        patch.object(ExtendedMCPServer, "save", autospec=True) as save,
        patch.object(ExtendedMCPServer, "get_pymongo_collection", Mock(return_value=collection)),
    ):
        saved: list[tuple] = []
        save.side_effect = lambda doc, **kwargs: saved.append((doc.vectorSyncPendingAt, doc.vectorSyncAttempts))
        _server, plan = await service.update_server(server_id=str(server.id), data=ServerUpdateRequest(title="New"))

    assert saved == [(plan.marker, 0)]
    assert plan.doc is server


@pytest.mark.unit
@pytest.mark.servers
@pytest.mark.asyncio
async def test_refresh_capabilities_persists_marker_in_same_save_and_resets_attempts():
    service = ServerServiceV1(user_service=Mock(), token_service=Mock(), oauth_service=Mock(), mcp_server_repo=Mock())
    server = Mock(spec=ExtendedMCPServer)
    server.id = "srv-1"
    server.serverName = "test-server"
    server.config = {}
    server.registryDisabledTools = []
    server.lastError = None
    server.errorMessage = None
    server.lastConnected = None
    server.updatedAt = datetime.now(UTC)
    server.vectorSyncPendingAt = None
    server.vectorSyncAttempts = 5
    server.vectorContentHash = "old-hash"
    server.numTools = 0
    saved = _record_marker_at_save(server)

    with (
        patch.object(service, "get_server_by_id", return_value=server),
        patch.object(
            service,
            "retrieve_tools_and_capabilities_from_server",
            return_value=([{"name": "tool1"}], [], [], {}, None),
        ),
        patch.object(service, "schedule_vector_sync") as schedule,
    ):
        await service.refresh_server_capabilities(server_id="srv-1", user_id="user-1")

    plan = schedule.call_args.args[0]
    assert saved == [(plan.marker, 0)]
