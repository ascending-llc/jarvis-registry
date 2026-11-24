"""
Unit tests for mcp_client functions.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from registry.core.mcp_client import (
    MCPServerData,
    _drain_listing,
    _get_from_sse,
    _get_from_streamable_http,
    _is_requires_init,
    _list_optional_entities,
    get_tools_and_capabilities_from_server,
    initialize_mcp,
    perform_health_check,
)
from registry.core.mcp_config import MCPClientConfig
from registry_pkgs.oauth.discovery import DiscoveryResult
from registry_pkgs.oauth.errors import OAuthDiscoveryError


def _mock_client_cm(session):
    """Wrap a mock v2 ``mcp.Client`` session in an async context manager."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _mock_mcp_session(*, capabilities=None, tools_response=None, resources=None, prompts=None, templates=None):
    """Build a mock post-handshake ``mcp.Client`` session with v2 snake_case attributes."""
    session = AsyncMock()
    session.protocol_version = "2025-11-25"
    session.server_info = Mock(name="server", version="1.0")
    session.server_capabilities = capabilities
    session.list_tools = AsyncMock(return_value=tools_response if tools_response is not None else Mock(tools=[]))
    # next_cursor=None: a bare Mock attribute is truthy and repeats, which the pagination guard treats as a
    # misbehaving server and turns into a failed listing.
    session.list_resources = AsyncMock(return_value=Mock(resources=resources or [], next_cursor=None))
    session.list_resource_templates = AsyncMock(return_value=Mock(resource_templates=templates or [], next_cursor=None))
    session.list_prompts = AsyncMock(return_value=Mock(prompts=prompts or [], next_cursor=None))
    return session


@pytest.mark.unit
@pytest.mark.core
@pytest.mark.asyncio
async def test_rewrite_sse_request_url_strips_mount_path():
    """The v2 request hook (replacing the old httpx monkey-patch) rewrites the mount-path POST-back URL."""
    import httpx2

    from registry.core.mcp_transport import rewrite_sse_request_url

    req = httpx2.Request("POST", "http://host:8000/fininfo/messages/?session_id=abc")
    await rewrite_sse_request_url(req)
    assert str(req.url) == "http://host:8000/messages/?session_id=abc"


@pytest.mark.unit
@pytest.mark.core
@pytest.mark.asyncio
async def test_rewrite_sse_request_url_leaves_non_messages_url_untouched():
    import httpx2

    from registry.core.mcp_transport import rewrite_sse_request_url

    req = httpx2.Request("GET", "http://host:8000/sse")
    await rewrite_sse_request_url(req)
    assert str(req.url) == "http://host:8000/sse"


@pytest.mark.unit
@pytest.mark.core
@pytest.mark.asyncio
async def test_create_sse_client_factory_attaches_hook_and_optional_recorder():
    """The SSE factory attaches the mount-path rewrite hook, and the recorder only when provided."""
    from registry.core.mcp_transport import ResponseRecorder, create_sse_client_factory, rewrite_sse_request_url

    recorder = ResponseRecorder()
    client = create_sse_client_factory(recorder)(headers={"a": "b"}, auth=None, timeout=None)
    try:
        assert rewrite_sse_request_url in client.event_hooks["request"]
        assert recorder in client.event_hooks["response"]
    finally:
        await client.aclose()

    client2 = create_sse_client_factory()(headers=None, auth=None, timeout=None)
    try:
        assert rewrite_sse_request_url in client2.event_hooks["request"]
        assert recorder not in client2.event_hooks["response"]
    finally:
        await client2.aclose()


@pytest.mark.unit
@pytest.mark.core
class TestMCPClient:
    """Test suite for MCP client functions."""

    def test_failed_probe_has_unknown_initialization_requirement(self) -> None:
        result = MCPServerData(None, None, None, None, "err")

        assert result.requires_init is None

    @pytest.mark.parametrize(
        ("session_id", "expected"), [("sid-1", True), (None, False)], ids=["stateful", "stateless"]
    )
    def test_is_requires_init_detects_session(self, session_id: str | None, expected: bool) -> None:
        # v2's streamable_http_client no longer yields get_session_id; the recorder captures the session
        # id off the response headers and _is_requires_init takes it directly.
        assert _is_requires_init(session_id) is expected

    @pytest.fixture
    def mock_headers(self):
        """Create mock headers with authentication."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer test-token-123",
            "User-Agent": "MCP-Gateway-Registry/1.0",
        }

    @pytest.fixture
    def mock_tools_response(self):
        """Create mock tools response."""
        tool = Mock()
        tool.name = "test_tool"
        tool.description = "Test tool description"
        # v2 Tool model exposes input_schema (snake_case).
        tool.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}

        response = Mock()
        response.tools = [tool]
        return response

    @pytest.fixture
    def mock_capabilities(self):
        """Create mock capabilities."""
        return {"tools": {"listChanged": True}, "resources": {"subscribe": False}, "prompts": {"listChanged": False}}

    @pytest.mark.asyncio
    async def test_get_from_streamable_http_with_headers(self, mock_headers, mock_tools_response, mock_capabilities):
        """Test _get_from_streamable_http accepts pre-built headers."""
        base_url = "http://localhost:8000/mcp/"

        with (
            patch("registry.core.mcp_client.Client") as mock_client_cls,
            patch("registry.core.mcp_client.streamable_http_client"),
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            session = _mock_mcp_session(capabilities=mock_capabilities, tools_response=mock_tools_response)
            mock_client_cls.return_value = _mock_client_cm(session)

            result = await _get_from_streamable_http(
                base_url=base_url, headers=mock_headers, transport_type="streamable-http", include_capabilities=True
            )

            # Verify result is MCPServerData object
            assert result.tools is not None
            assert result.tools[0]["inputSchema"] == {"type": "object", "properties": {"query": {"type": "string"}}}
            assert result.capabilities == mock_capabilities

    @pytest.mark.asyncio
    async def test_get_from_streamable_http_without_capabilities(self, mock_headers, mock_tools_response):
        """Test _get_from_streamable_http can skip capabilities."""
        base_url = "http://localhost:8000/mcp/"

        with (
            patch("registry.core.mcp_client.Client") as mock_client_cls,
            patch("registry.core.mcp_client.streamable_http_client"),
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            session = _mock_mcp_session(tools_response=mock_tools_response)
            mock_client_cls.return_value = _mock_client_cm(session)

            result = await _get_from_streamable_http(
                base_url=base_url,
                headers=mock_headers,
                transport_type="streamable-http",
                include_capabilities=False,
            )

            assert result.tools is not None
            assert result.capabilities is None

    @pytest.mark.asyncio
    async def test_get_from_sse_with_headers(self, mock_headers, mock_tools_response, mock_capabilities):
        """Test _get_from_sse accepts pre-built headers."""
        base_url = "http://localhost:8000/sse"

        with (
            patch("registry.core.mcp_client.Client") as mock_client_cls,
            patch("registry.core.mcp_client.sse_client") as mock_sse,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            session = _mock_mcp_session(capabilities=mock_capabilities, tools_response=mock_tools_response)
            mock_client_cls.return_value = _mock_client_cm(session)

            result = await _get_from_sse(
                base_url=base_url, headers=mock_headers, transport_type="sse", include_capabilities=True
            )

            assert result.tools is not None
            assert result.capabilities == mock_capabilities
            # Regression guard: sse_client receives headers + auth, never http_client (v2 uses a factory).
            _, kwargs = mock_sse.call_args
            assert kwargs["headers"] == mock_headers
            assert "http_client" not in kwargs
            assert "httpx_client_factory" in kwargs

    @pytest.mark.asyncio
    async def test_initialize_mcp_sse_uses_headers_not_http_client(self, mock_headers):
        """Test initialize_mcp uses sse_client(headers=...) for SSE transport."""
        target_url = "https://example.com/sse"

        with (
            patch("registry.core.mcp_client.Client") as mock_client_cls,
            patch("registry.core.mcp_client.sse_client") as mock_sse_client,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=target_url)
            mock_strategy.return_value = strategy

            session = _mock_mcp_session()
            mock_client_cls.return_value = _mock_client_cm(session)

            init_result, status = await initialize_mcp(
                target_url=target_url,
                headers=mock_headers,
                transport_type="sse",
            )

            assert init_result.protocol_version == "2025-11-25"
            assert status is None
            _, kwargs = mock_sse_client.call_args
            assert kwargs["headers"] == mock_headers
            assert "http_client" not in kwargs
            assert "httpx_client_factory" in kwargs

    @pytest.mark.asyncio
    async def test_get_tools_and_capabilities_from_server_streamable_http(self, mock_headers):
        """Test main entry point with streamable-http transport."""
        base_url = "http://localhost:8000"

        with patch("registry.core.mcp_client._get_from_streamable_http") as mock_get:
            from registry.core.mcp_client import MCPServerData

            mock_get.return_value = MCPServerData(tools=["tool1"], resources=[], prompts=[], capabilities={"tools": {}})

            result = await get_tools_and_capabilities_from_server(
                base_url=base_url, headers=mock_headers, transport_type="streamable-http"
            )

            assert result.tools == ["tool1"]
            assert result.capabilities == {"tools": {}}
            # Check that the call includes all expected parameters
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert call_args[0][0] == base_url
            assert call_args[0][1] == mock_headers
            assert call_args[0][2] == "streamable-http"
            assert call_args[1]["include_capabilities"]
            assert call_args[1]["include_resources"]
            assert call_args[1]["include_prompts"]

    @pytest.mark.asyncio
    async def test_get_tools_and_capabilities_from_server_sse(self, mock_headers):
        """Test main entry point with SSE transport."""
        base_url = "http://localhost:8000"

        with patch("registry.core.mcp_client._get_from_sse") as mock_get:
            from registry.core.mcp_client import MCPServerData

            mock_get.return_value = MCPServerData(
                tools=["tool2"], resources=[], prompts=[], capabilities={"resources": {}}
            )

            result = await get_tools_and_capabilities_from_server(
                base_url=base_url, headers=mock_headers, transport_type="sse"
            )

            assert result.tools == ["tool2"]
            assert result.capabilities == {"resources": {}}
            # Check that the call includes all expected parameters
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert call_args[0][0] == base_url
            assert call_args[0][1] == mock_headers
            assert call_args[0][2] == "sse"
            assert call_args[1]["include_capabilities"]
            assert call_args[1]["include_resources"]
            assert call_args[1]["include_prompts"]

    @pytest.mark.asyncio
    async def test_get_tools_and_capabilities_auto_detect_transport(self, mock_headers):
        """Test transport auto-detection."""
        base_url = "http://localhost:8000"

        from registry.core.mcp_client import MCPServerData

        with (
            patch("registry.core.mcp_client.detect_server_transport") as mock_detect,
            patch("registry.core.mcp_client._get_from_streamable_http") as mock_get,
        ):
            mock_detect.return_value = "streamable-http"
            mock_get.return_value = MCPServerData(tools=["tool"], resources=[], prompts=[], capabilities={})

            result = await get_tools_and_capabilities_from_server(
                base_url=base_url,
                headers=mock_headers,
                transport_type=None,  # Auto-detect
            )

            mock_detect.assert_called_once_with(base_url)
            assert result.tools == ["tool"]

    @pytest.mark.asyncio
    async def test_headers_default_to_mcp_headers_if_none(self):
        """Test that None headers default to MCP base headers."""
        base_url = "http://localhost:8000/mcp/"

        with (
            patch("registry.core.mcp_client.Client") as mock_client_cls,
            patch("registry.core.mcp_client.streamable_http_client"),
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            mock_client_cls.return_value = _mock_client_cm(_mock_mcp_session())

            await _get_from_streamable_http(
                base_url=base_url,
                headers=None,  # Should use defaults
                transport_type="streamable-http",
                include_capabilities=False,
            )

            assert MCPClientConfig.DEFAULT_HEADERS["Accept"] == "application/json, text/event-stream"

    @pytest.mark.asyncio
    async def test_connection_timeout_returns_none(self, mock_headers):
        """Test timeout returns MCPServerData with None fields."""
        base_url = "http://localhost:8000/mcp/"

        with (
            patch("registry.core.mcp_client.streamable_http_client") as mock_client,
            patch("registry.core.mcp_client.get_server_strategy"),
        ):
            # Simulate timeout
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(side_effect=TimeoutError("Connection timeout"))
            mock_client.return_value = mock_context

            result = await _get_from_streamable_http(
                base_url=base_url, headers=mock_headers, transport_type="streamable-http", include_capabilities=True
            )

            assert result.tools is None
            assert result.capabilities is None
            assert result.requires_init is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self, mock_headers):
        """Test connection error returns MCPServerData with None fields."""
        base_url = "http://localhost:8000/mcp/"

        with (
            patch("registry.core.mcp_client.streamable_http_client") as mock_client,
            patch("registry.core.mcp_client.get_server_strategy"),
        ):
            # Simulate connection error
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.return_value = mock_context

            result = await _get_from_streamable_http(
                base_url=base_url, headers=mock_headers, transport_type="streamable-http", include_capabilities=True
            )

            assert result.tools is None
            assert result.capabilities is None
            assert result.error_message is not None
            assert result.requires_init is None


@pytest.mark.unit
@pytest.mark.core
class TestPerformHealthCheck:
    """Test suite for perform_health_check function."""

    @pytest.fixture
    def mock_init_result(self):
        """Create a mock v2 init snapshot with snake_case attributes."""
        mock_result = Mock()
        mock_result.protocol_version = "2025-11-25"
        mock_result.server_info = Mock()
        mock_result.server_info.name = "test-server"
        # Simulate Pydantic model that will be converted to dict
        mock_capabilities = Mock()
        mock_capabilities.model_dump = Mock(return_value={"tools": {}})
        mock_result.capabilities = mock_capabilities
        return mock_result

    @staticmethod
    def _httpx2_client_with_status(status_code: int):
        """Patch target that injects an httpx2.MockTransport returning a fixed status.

        v2 hides the HTTP status inside MCPError, so this exercises the real initialize_mcp path where
        _ResponseRecorder captures the status off the wire.
        """
        import httpx2

        real = httpx2.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(lambda request: httpx2.Response(status_code))
            return real(*args, **kwargs)

        return factory

    @pytest.mark.asyncio
    async def test_perform_health_check_success(self, mock_init_result):
        """Test successful health check."""
        url = "https://example.com/api"
        transport = "streamable-http"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=(mock_init_result, None))):
            is_healthy, status, response_time, init_result = await perform_health_check(url, transport)

            assert is_healthy is True
            assert "initialize handshake successful" in status.lower()
            assert response_time is not None
            assert init_result == mock_init_result
            # Verify capabilities were converted to dict by perform_health_check
            assert isinstance(init_result.capabilities, dict)
            assert init_result.capabilities == {"tools": {}}

    @pytest.mark.asyncio
    async def test_perform_health_check_no_url(self):
        """Test health check with no URL."""
        is_healthy, status, response_time, init_result = await perform_health_check("", "streamable-http")

        assert is_healthy is False
        assert status == "No URL provided"
        assert response_time is None
        assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_stdio_skipped(self):
        """Test health check is skipped for stdio transport."""

        is_healthy, status, response_time, init_result = await perform_health_check("/path/to/binary", "stdio")

        assert is_healthy is True
        assert "stdio transport skipped" in status.lower()
        assert response_time is None
        assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_initialization_failed(self):
        """Test health check when initialization returns None."""
        url = "https://example.com/api"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=(None, None))):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "streamable-http")

            assert is_healthy is False
            assert "initialization failed" in status.lower()
            assert response_time is not None
            assert init_result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("transport", "status_code"),
        [("streamable-http", 404), ("streamable-http", 405), ("sse", 404), ("sse", 405)],
    )
    async def test_perform_health_check_404_405_hints_transport_mismatch(self, transport, status_code):
        """A 404/405 on the initialize endpoint flags a likely config.url/config.type mismatch.

        Drives the real mcp client over an httpx2.MockTransport. v2 hides the HTTP status inside MCPError,
        so _ResponseRecorder captures the status off the wire for the health-check interpretation.
        """
        with patch("httpx2.AsyncClient", new=self._httpx2_client_with_status(status_code)):
            is_healthy, status, _, init_result = await perform_health_check("https://example.com/v1/sse", transport)

        assert is_healthy is False
        assert str(status_code) in status
        assert "config.type" in status
        assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_invalid_response(self):
        """Test health check with invalid MCP response (missing fields)."""
        from registry.core.mcp_client import perform_health_check

        # Mock result missing required fields (protocol_version and server_info)
        invalid_result = Mock(spec=["some_other_field"])

        url = "https://example.com/api"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=(invalid_result, None))):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "streamable-http")

            assert is_healthy is False
            assert "invalid initialize response" in status.lower()
            assert response_time is not None
            assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_sse_transport(self, mock_init_result):
        """Test health check with SSE transport."""
        from registry.core.mcp_client import perform_health_check

        url = "https://example.com/sse"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=(mock_init_result, None))):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "sse")

            assert is_healthy is True
            assert "initialize handshake successful" in status.lower()
            assert init_result == mock_init_result
            # Verify capabilities were converted to dict
            assert isinstance(init_result.capabilities, dict)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_perform_health_check_auth_required(self, status_code):
        """401/403 arrives as MCPError in v2; the recorder captures the status → healthy, auth-required."""
        with patch("httpx2.AsyncClient", new=self._httpx2_client_with_status(status_code)):
            is_healthy, status, response_time, init_result = await perform_health_check(
                "https://example.com/mcp", "streamable-http"
            )

        assert is_healthy is True
        assert "initialize requires authentication" in status.lower()
        assert response_time is not None
        assert init_result is None


class TestGetOAuthMetadataFromServer:
    """get_oauth_metadata_from_server is a read-only preview over discover_mcp_oauth (AS-1929)."""

    @pytest.mark.asyncio
    async def test_delegates_to_discovery_and_adds_resource(self):
        from registry.core.mcp_client import get_oauth_metadata_from_server

        result = DiscoveryResult(
            server_url="https://mcp.atlassian.com/v2/mcp",
            resource="https://mcp.atlassian.com/v2/mcp",
            authorization_server_metadata={
                "issuer": "https://auth.atlassian.com/tenant",
                "authorization_endpoint": "https://auth.atlassian.com/authorize",
                "token_endpoint": "https://auth.atlassian.com/oauth/token",
            },
            issuer="https://auth.atlassian.com/tenant",
        )
        discover = AsyncMock(return_value=result)
        with patch("registry.core.mcp_client.discover_mcp_oauth", discover):
            metadata = await get_oauth_metadata_from_server("https://mcp.atlassian.com/v2/mcp")

        assert discover.await_args.args == ("https://mcp.atlassian.com/v2/mcp",)
        assert discover.await_args.kwargs["http_client"].follow_redirects is True
        assert metadata == {**result.authorization_server_metadata, "resource": "https://mcp.atlassian.com/v2/mcp"}

    @pytest.mark.asyncio
    async def test_no_resource_in_legacy_branch(self):
        from registry.core.mcp_client import get_oauth_metadata_from_server

        result = DiscoveryResult(
            server_url="https://mcp.example.com/mcp",
            authorization_server_metadata={"issuer": "https://mcp.example.com"},
            issuer="https://mcp.example.com",
        )
        with patch("registry.core.mcp_client.discover_mcp_oauth", AsyncMock(return_value=result)):
            metadata = await get_oauth_metadata_from_server("https://mcp.example.com/mcp")

        assert metadata == {"issuer": "https://mcp.example.com"}

    @pytest.mark.asyncio
    async def test_discovery_failure_returns_none(self):
        from registry.core.mcp_client import get_oauth_metadata_from_server

        with patch("registry.core.mcp_client.discover_mcp_oauth", AsyncMock(side_effect=OAuthDiscoveryError("x"))):
            assert await get_oauth_metadata_from_server("https://mcp.example.com/mcp") is None

    @pytest.mark.asyncio
    async def test_empty_url_returns_none(self):
        from registry.core.mcp_client import get_oauth_metadata_from_server

        assert await get_oauth_metadata_from_server("") is None


def _initialize_response(request_id, *, session_id: str | None = None):
    import httpx2

    headers = {"content-type": "application/json"}
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    return httpx2.Response(
        200,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "redirected", "version": "1"},
            },
        },
    )


def _redirecting_handler(final_status: int | None = None, *, session_id: str | None = "sid-1"):
    """``/mcp`` 307-redirects to ``/mcp/``; ``/mcp/`` answers ``final_status`` or a real MCP exchange."""
    import json

    import httpx2

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/mcp":
            return httpx2.Response(307, headers={"location": "http://srv/mcp/"})
        if final_status is not None:
            return httpx2.Response(final_status)
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method == "initialize":
            return _initialize_response(body["id"], session_id=session_id)
        if method == "tools/list":
            return httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}},
            )
        return httpx2.Response(202)

    return handler


def _hanging_handler(request):
    """Answer every request with an SSE stream that only ever sends keepalive comments."""
    import httpx2

    class _KeepaliveForever(httpx2.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.05)
                yield b": keepalive\n\n"

    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=_KeepaliveForever())


def _patch_httpx2_transport(handler):
    """Route every httpx2.AsyncClient (ours and the SDK's) through a MockTransport."""
    import httpx2

    real = httpx2.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(handler)
        return real(*args, **kwargs)

    return patch("httpx2.AsyncClient", new=factory)


@pytest.mark.unit
@pytest.mark.core
class TestResponseRecorderSkipsRedirects:
    """v2 transports follow same-origin redirects themselves, so the recorder must skip the 3xx hops."""

    @pytest.mark.asyncio
    async def test_initialize_through_redirect_records_final_status(self):
        with _patch_httpx2_transport(_redirecting_handler()):
            init_result, status_code = await initialize_mcp("http://srv/mcp")

        assert init_result is not None
        assert init_result.protocol_version == "2025-11-25"
        assert status_code == 200

    @pytest.mark.asyncio
    async def test_discovery_through_redirect_detects_stateful_server(self):
        with _patch_httpx2_transport(_redirecting_handler(session_id="sid-1")):
            result = await _get_from_streamable_http("http://srv/mcp")

        assert result.requires_init is True

    @pytest.mark.asyncio
    async def test_discovery_through_redirect_detects_stateless_server(self):
        with _patch_httpx2_transport(_redirecting_handler(session_id=None)):
            result = await _get_from_streamable_http("http://srv/mcp")

        assert result.requires_init is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_health_check_auth_required_behind_redirect(self, status_code):
        with _patch_httpx2_transport(_redirecting_handler(final_status=status_code)):
            is_healthy, status, _, init_result = await perform_health_check("http://srv/mcp", "streamable-http")

        assert is_healthy is True
        assert "initialize requires authentication" in status.lower()
        assert init_result is None


@pytest.mark.unit
@pytest.mark.core
class TestInitializeHandshakeTimeout:
    """The handshake is bounded by INIT_TIMEOUT even when the server keeps the stream alive."""

    @pytest.fixture(autouse=True)
    def _short_init_timeout(self, monkeypatch):
        monkeypatch.setattr(MCPClientConfig, "INIT_TIMEOUT", 0.3)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("transport", "url"), [("streamable-http", "http://srv/mcp"), ("sse", "http://srv/sse")])
    async def test_initialize_mcp_times_out(self, transport, url, caplog):
        with _patch_httpx2_transport(_hanging_handler), caplog.at_level("ERROR", logger="registry.core.mcp_client"):
            init_result, status_code = await asyncio.wait_for(initialize_mcp(url, transport_type=transport), timeout=5)

        assert init_result is None
        assert status_code == 200
        assert any("timed out" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_streamable_http_discovery_times_out(self):
        with _patch_httpx2_transport(_hanging_handler):
            result = await asyncio.wait_for(_get_from_streamable_http("http://srv/mcp"), timeout=5)

        assert result.tools is None
        assert result.error_message == "Timeout connecting to server"

    @pytest.mark.asyncio
    async def test_sse_discovery_times_out(self):
        with _patch_httpx2_transport(_hanging_handler):
            result = await asyncio.wait_for(_get_from_sse("http://srv/sse"), timeout=5)

        assert result.tools is None
        assert result.error_message == "Timeout connecting to server"


@pytest.mark.unit
@pytest.mark.core
class TestInitializeFailureLogging:
    @pytest.mark.asyncio
    async def test_non_http_failure_logs_error(self, caplog):
        with (
            _patch_httpx2_transport(lambda request: (_ for _ in ()).throw(ConnectionRefusedError("refused"))),
            caplog.at_level("DEBUG", logger="registry.core.mcp_client"),
        ):
            init_result, status_code = await initialize_mcp("http://srv/mcp")

        assert (init_result, status_code) == (None, None)
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert errors and "MCP initialization failed" in errors[-1].getMessage()
        # The transport's ExceptionGroup wrapper is unwrapped down to the real cause.
        assert "refused" in errors[-1].getMessage()
        assert "unhandled errors in a TaskGroup" not in errors[-1].getMessage()

    @pytest.mark.asyncio
    async def test_http_error_status_is_not_logged_as_error(self, caplog):
        import httpx2

        with (
            _patch_httpx2_transport(lambda request: httpx2.Response(401)),
            caplog.at_level("DEBUG", logger="registry.core.mcp_client"),
        ):
            init_result, status_code = await initialize_mcp("http://srv/mcp")

        assert (init_result, status_code) == (None, 401)
        assert not [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.unit
@pytest.mark.core
def test_sse_client_factory_builds_client_with_hooks_and_timeout():
    import httpx2

    from registry.core.mcp_transport import ResponseRecorder, create_sse_client_factory, rewrite_sse_request_url

    recorder = ResponseRecorder()
    timeout = httpx2.Timeout(5.0, read=300.0)

    client = create_sse_client_factory(recorder)(headers={"X-Test": "1"}, timeout=timeout)

    assert client.timeout == timeout
    assert client.headers["X-Test"] == "1"
    assert client.event_hooks["request"] == [rewrite_sse_request_url, recorder.on_request]
    assert client.event_hooks["response"] == [recorder]
    assert create_sse_client_factory()().event_hooks["response"] == []


@pytest.mark.asyncio
async def test_health_check_reports_unsupported_transport_without_network_io():
    with patch("registry.core.mcp_client.initialize_mcp") as initialize:
        result = await perform_health_check("https://example.com/mcp", transport="websocket")

    assert result == (False, "unhealthy: unsupported transport 'websocket'", None, None)
    initialize.assert_not_called()


def _page(items_attr: str, items: list, next_cursor: str | None) -> SimpleNamespace:
    return SimpleNamespace(**{items_attr: items, "next_cursor": next_cursor})


def _resource(uri: str) -> SimpleNamespace:
    return SimpleNamespace(uri=uri, name=uri, description=None, mime_type="text/plain", annotations=None)


def _template(uri_template: str) -> SimpleNamespace:
    return SimpleNamespace(
        uri_template=uri_template, name="tpl", description="d", mime_type="application/json", annotations=None
    )


@pytest.mark.unit
@pytest.mark.core
class TestOptionalListings:
    """Pagination and failure semantics for resources, resource templates and prompts."""

    async def test_drain_listing_follows_cursors_and_passes_cursor_keyword(self) -> None:
        pages = {None: _page("resources", [1, 2], "c1"), "c1": _page("resources", [3], None)}
        fetch = AsyncMock(side_effect=lambda cursor: pages[cursor])

        items = await _drain_listing(fetch, "resources", "resources", "http://srv")

        assert items == [1, 2, 3]
        assert [call.args[0] for call in fetch.await_args_list] == [None, "c1"]

    async def test_drain_listing_repeated_cursor_fails_only_that_listing(self, caplog) -> None:
        fetch = AsyncMock(return_value=_page("prompts", [1], "same"))

        assert await _drain_listing(fetch, "prompts", "prompts", "http://srv") is None
        assert "repeated a pagination cursor" in caplog.text

    @pytest.mark.parametrize("items_attr", ["resources", "resource_templates", "prompts"])
    async def test_drain_listing_method_not_found_is_a_definite_empty_listing(self, items_attr: str) -> None:
        from mcp.shared.exceptions import MCPError

        fetch = AsyncMock(side_effect=MCPError(-32601, "Method not found"))

        assert await _drain_listing(fetch, items_attr, items_attr, "http://srv") == []
        fetch.assert_awaited_once_with(None)

    @pytest.mark.parametrize("items_attr", ["resources", "resource_templates", "prompts"])
    @pytest.mark.parametrize("first_page_items", [[], ["first-page-item"]])
    async def test_drain_listing_later_method_not_found_keeps_stored_listing(
        self, items_attr: str, first_page_items: list[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        from mcp.shared.exceptions import MCPError

        from registry.services.server_service import _captured_listings

        fetch = AsyncMock(
            side_effect=[
                _page(items_attr, first_page_items, "page2"),
                MCPError(-32601, "Method not found"),
            ]
        )
        config_key = "resourceTemplates" if items_attr == "resource_templates" else items_attr
        stored = {config_key: ["stored-item"], "resourceCapture": {"revision": "stored-proof"}}

        result = await _drain_listing(fetch, items_attr, items_attr, "http://srv")
        assert result is None
        # Exercise the real capture write builder: neither a failed list nor its proof may be replaced.
        captured = MCPServerData(tools=[], resources=None, prompts=None, capabilities=None, resource_templates=None)
        setattr(captured, items_attr, result)
        fields = _captured_listings(captured, stored)
        assert config_key not in fields
        assert "resourceCapture" not in fields
        assert "Failed to retrieve" in caplog.text
        assert [call.args[0] for call in fetch.await_args_list] == [None, "page2"]

    async def test_drain_listing_other_jsonrpc_error_returns_none(self) -> None:
        from mcp.shared.exceptions import MCPError

        fetch = AsyncMock(side_effect=MCPError(-32603, "Internal error"))

        assert await _drain_listing(fetch, "prompts", "prompts", "http://srv") is None

    async def test_drain_listing_timeout_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(MCPClientConfig, "TOOLS_TIMEOUT", 0.01)

        async def slow(_cursor):
            await asyncio.sleep(1)

        assert await _drain_listing(slow, "resources", "resources", "http://srv") is None

    async def test_session_methods_receive_cursor_keyword(self) -> None:
        session = _mock_mcp_session(capabilities=SimpleNamespace(resources=SimpleNamespace()))
        session.list_resources = AsyncMock(
            side_effect=[_page("resources", [_resource("a://1")], "c1"), _page("resources", [_resource("a://2")], None)]
        )

        resources, templates, prompts = await _list_optional_entities(
            session, "http://srv", include_resources=True, include_resource_templates=True, include_prompts=True
        )

        assert [r["uri"] for r in resources] == ["a://1", "a://2"]
        assert [call.kwargs for call in session.list_resources.await_args_list] == [{"cursor": None}, {"cursor": "c1"}]
        session.list_resource_templates.assert_awaited_once_with(cursor=None)
        session.list_prompts.assert_awaited_once_with(cursor=None)
        assert templates == [] and prompts == []

    async def test_templates_are_extracted(self) -> None:
        session = _mock_mcp_session(
            capabilities={"resources": {}}, templates=[_template("github://repo/{owner}/{repo}")]
        )

        _, templates, _ = await _list_optional_entities(
            session, "http://srv", include_resources=True, include_resource_templates=True, include_prompts=True
        )

        assert templates == [
            {
                "uriTemplate": "github://repo/{owner}/{repo}",
                "name": "tpl",
                "description": "d",
                "mimeType": "application/json",
                "annotations": None,
            }
        ]

    @pytest.mark.parametrize("capabilities", [None, {"tools": {}}, SimpleNamespace(resources=None)])
    async def test_templates_are_definitely_empty_when_resources_capability_not_declared(self, capabilities) -> None:
        session = _mock_mcp_session(capabilities=capabilities)

        _, templates, _ = await _list_optional_entities(
            session, "http://srv", include_resources=True, include_resource_templates=True, include_prompts=True
        )

        assert templates == []
        session.list_resource_templates.assert_not_awaited()

    async def test_not_requested_and_failed_are_none(self) -> None:
        session = _mock_mcp_session(
            capabilities={"resources": {}}, prompts=[SimpleNamespace(name="p", description=None, arguments=None)]
        )
        session.list_resource_templates = AsyncMock(side_effect=RuntimeError("Method not found"))

        resources, templates, prompts = await _list_optional_entities(
            session, "http://srv", include_resources=False, include_resource_templates=True, include_prompts=True
        )

        # Not requested: unknown, so the caller keeps what it stored.
        assert resources is None
        session.list_resources.assert_not_awaited()
        assert templates is None
        assert prompts == [{"name": "p", "description": None, "arguments": []}]

    async def test_resource_uris_are_not_logged(self, caplog) -> None:
        caplog.set_level("DEBUG", logger="registry.core.mcp_client")
        session = _mock_mcp_session(resources=[_resource("gdrive:///alice/salary.xlsx")])

        resources, _, _ = await _list_optional_entities(
            session, "http://srv", include_resources=True, include_resource_templates=False, include_prompts=False
        )

        assert resources[0]["uri"] == "gdrive:///alice/salary.xlsx"
        assert "salary" not in caplog.text


@pytest.mark.unit
@pytest.mark.core
async def test_capture_against_real_sdk_server(monkeypatch) -> None:
    """End to end through the real mcp.Client: catches listing-call signature mismatches that mocks accept."""
    import httpx2
    from mcp.server.mcpserver import MCPServer
    from tests.mcp_downstream_support import sdk_endpoint

    server = MCPServer("capture-target")

    @server.tool()
    def ping() -> str:
        return "pong"

    @server.resource("test://doc", mime_type="text/plain")
    def doc() -> str:
        return "doc"

    @server.resource("test://doc/{doc_id}", mime_type="text/plain")
    def doc_by_id(doc_id: str) -> str:
        return doc_id

    @server.resource("ui://widget/app.html", mime_type="text/html;profile=mcp-app")
    def widget() -> str:
        return "<html></html>"

    @server.prompt()
    def greet(name: str) -> str:
        return f"Hello {name}"

    async with sdk_endpoint(server_override=server) as transport:
        original = httpx2.AsyncClient
        monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))

        result = await _get_from_streamable_http("http://downstream/mcp", headers={})

    assert result.error_message is None
    assert [tool["name"] for tool in result.tools] == ["ping"]
    assert sorted(r["uri"] for r in result.resources) == ["test://doc", "ui://widget/app.html"]
    assert [t["uriTemplate"] for t in result.resource_templates] == ["test://doc/{doc_id}"]
    assert [p["name"] for p in result.prompts] == ["greet"]


@pytest.mark.unit
@pytest.mark.core
async def test_templates_not_requested_are_none() -> None:
    session = _mock_mcp_session(capabilities={"resources": {}})

    _, templates, prompts = await _list_optional_entities(
        session, "http://srv", include_resources=True, include_resource_templates=False, include_prompts=False
    )

    assert templates is None and prompts is None
    session.list_resource_templates.assert_not_awaited()
    session.list_prompts.assert_not_awaited()
