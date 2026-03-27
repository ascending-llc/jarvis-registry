"""
Unit tests for mcp_client functions.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from registry.core.exceptions import MisimplementedSpecException
from registry.core.mcp_client import (
    _get_from_sse,
    _get_from_streamable_http,
    call_tool_via_sse_ephemeral,
    get_tools_and_capabilities_from_server,
    initialize_mcp,
)
from registry.core.mcp_config import MCPClientConfig


@pytest.mark.unit
@pytest.mark.core
class TestMCPClient:
    """Test suite for MCP client functions."""

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_waits_for_initialize_response(self, mock_headers):
        """Test ephemeral SSE waits for initialize response before continuing handshake."""

        class MockEvent:
            def __init__(self, event_type, data="", payload=None):
                self.event = event_type
                self.data = data
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("No JSON payload")
                return self._payload

        class MockResponse:
            def __init__(self, is_success=True, status_code=200, headers=None, body=b""):
                self.is_success = is_success
                self.status_code = status_code
                self.headers = headers or {}
                self._body = body

            async def aread(self):
                return self._body

        class MockStreamContext:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self._response

            async def __aexit__(self, exc_type, exc, tb):
                return None

        init_post_started = asyncio.Event()
        call_order: list[str] = []
        notification_handler = AsyncMock()

        class MockHttpClient:
            def stream(self, method, url, **kwargs):
                if method == "GET":
                    call_order.append("get_sse")
                    return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

                if method == "POST" and kwargs["json"].get("method") == "initialize":
                    call_order.append("post_initialize")
                    init_post_started.set()
                    return MockStreamContext(MockResponse())

                if method == "POST" and kwargs["json"].get("method") == "notifications/initialized":
                    call_order.append("post_initialized")
                    return MockStreamContext(MockResponse())

                if method == "POST" and kwargs["json"].get("method") == "tools/call":
                    call_order.append("post_tool_call")
                    return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

                raise AssertionError(f"Unexpected stream call: {method} {url} {kwargs}")

        class MockEventSource:
            def __init__(self, _response):
                self._response = _response

            async def aiter_sse(self):
                yield MockEvent("endpoint", "/messages")
                await init_post_started.wait()
                call_order.append("initialize_response")
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "1", "result": {"protocolVersion": "2024-11-05"}},
                )
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "method": "notifications/message", "params": {"value": "ping"}},
                )
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "tool-123", "result": {"content": []}},
                )

        request_body = {
            "jsonrpc": "2.0",
            "id": "tool-123",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with patch("registry.core.mcp_client.EventSource", MockEventSource):
            result = await call_tool_via_sse_ephemeral(
                http_client=MockHttpClient(),
                sse_url="https://example.com/sse",
                headers=mock_headers,
                request_body=request_body,
                on_notification=notification_handler,
            )

        assert result == {"jsonrpc": "2.0", "id": "tool-123", "result": {"content": []}}
        assert call_order == [
            "get_sse",
            "post_initialize",
            "initialize_response",
            "post_initialized",
            "post_tool_call",
        ]
        notification_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_uses_endpoint_event_messages_url(self, mock_headers):
        """Test ephemeral SSE posts handshake and tool calls to the endpoint event URL."""

        class MockEvent:
            def __init__(self, event_type, data="", payload=None):
                self.event = event_type
                self.data = data
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("No JSON payload")
                return self._payload

        class MockResponse:
            def __init__(self, is_success=True, status_code=200, headers=None, body=b""):
                self.is_success = is_success
                self.status_code = status_code
                self.headers = headers or {}
                self._body = body

            async def aread(self):
                return self._body

        class MockStreamContext:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self._response

            async def __aexit__(self, exc_type, exc, tb):
                return None

        init_post_started = asyncio.Event()
        post_calls: list[tuple[str, str]] = []

        class MockHttpClient:
            def stream(self, method, url, **kwargs):
                if method == "GET":
                    return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

                request_method = kwargs["json"].get("method")
                post_calls.append((request_method, url))
                if request_method == "initialize":
                    init_post_started.set()

                response_headers = {"content-type": "text/event-stream"} if request_method == "tools/call" else {}
                return MockStreamContext(MockResponse(headers=response_headers))

        class MockEventSource:
            def __init__(self, _response):
                self._response = _response

            async def aiter_sse(self):
                yield MockEvent("endpoint", "/messages?session_id=session-123")
                await init_post_started.wait()
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "1", "result": {"protocolVersion": "2024-11-05"}},
                )
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "tool-123", "result": {"content": []}},
                )
                await asyncio.sleep(1)

        request_body = {
            "jsonrpc": "2.0",
            "id": "tool-123",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with patch("registry.core.mcp_client.EventSource", MockEventSource):
            result = await call_tool_via_sse_ephemeral(
                http_client=MockHttpClient(),
                sse_url="https://example.com/sse",
                headers=mock_headers,
                request_body=request_body,
            )

        assert result == {"jsonrpc": "2.0", "id": "tool-123", "result": {"content": []}}
        assert post_calls == [
            ("initialize", "https://example.com/messages?session_id=session-123"),
            ("notifications/initialized", "https://example.com/messages?session_id=session-123"),
            ("tools/call", "https://example.com/messages?session_id=session-123"),
        ]

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_forwards_notifications(self, mock_headers):
        """Test ephemeral SSE forwards JSON-RPC notifications observed on the SSE stream."""

        class MockEvent:
            def __init__(self, event_type, data="", payload=None):
                self.event = event_type
                self.data = data
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("No JSON payload")
                return self._payload

        class MockResponse:
            def __init__(self, is_success=True, status_code=200, headers=None, body=b""):
                self.is_success = is_success
                self.status_code = status_code
                self.headers = headers or {}
                self._body = body

            async def aread(self):
                return self._body

        class MockStreamContext:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self._response

            async def __aexit__(self, exc_type, exc, tb):
                return None

        init_post_started = asyncio.Event()
        notification_handler = AsyncMock()

        class MockHttpClient:
            def stream(self, method, _url, **kwargs):
                if method == "GET":
                    return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

                if kwargs["json"].get("method") == "initialize":
                    init_post_started.set()

                response_headers = (
                    {"content-type": "text/event-stream"} if kwargs["json"].get("method") == "tools/call" else {}
                )
                return MockStreamContext(MockResponse(headers=response_headers))

        notification_payloads = [
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 25}},
            {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}},
        ]

        class MockEventSource:
            def __init__(self, _response):
                self._response = _response

            async def aiter_sse(self):
                yield MockEvent("endpoint", "/messages")
                await init_post_started.wait()
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "1", "result": {"protocolVersion": "2024-11-05"}},
                )
                for payload in notification_payloads:
                    yield MockEvent("message", payload=payload)
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "tool-123", "result": {"content": []}},
                )
                await asyncio.sleep(1)

        request_body = {
            "jsonrpc": "2.0",
            "id": "tool-123",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with patch("registry.core.mcp_client.EventSource", MockEventSource):
            await call_tool_via_sse_ephemeral(
                http_client=MockHttpClient(),
                sse_url="https://example.com/sse",
                headers=mock_headers,
                request_body=request_body,
                on_notification=notification_handler,
            )

        assert notification_handler.await_count == 2
        assert [call.args[0] for call in notification_handler.await_args_list] == notification_payloads

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_times_out_waiting_for_endpoint_event(self, mock_headers):
        """Test ephemeral SSE raises a timeout when the endpoint event never arrives."""

        class MockEvent:
            def __init__(self, event_type, data="", payload=None):
                self.event = event_type
                self.data = data
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("No JSON payload")
                return self._payload

        class MockResponse:
            def __init__(self, is_success=True, status_code=200, headers=None, body=b""):
                self.is_success = is_success
                self.status_code = status_code
                self.headers = headers or {}
                self._body = body

            async def aread(self):
                return self._body

        class MockStreamContext:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self._response

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class MockHttpClient:
            def stream(self, method, _url, **_kwargs):
                assert method == "GET"
                return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

        class MockEventSource:
            def __init__(self, _response):
                self._response = _response

            async def aiter_sse(self):
                yield MockEvent("comment", data="keep-alive")
                await asyncio.sleep(1)

        request_body = {
            "jsonrpc": "2.0",
            "id": "tool-123",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with patch("registry.core.mcp_client.EventSource", MockEventSource):
            with pytest.raises(asyncio.TimeoutError):
                await call_tool_via_sse_ephemeral(
                    http_client=MockHttpClient(),
                    sse_url="https://example.com/sse",
                    headers=mock_headers,
                    request_body=request_body,
                    timeout_seconds=0.01,
                )

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_raises_on_initialize_rpc_error(self, mock_headers):
        """Test ephemeral SSE aborts when the initialize response on the SSE stream is an error."""

        class MockEvent:
            def __init__(self, event_type, data="", payload=None):
                self.event = event_type
                self.data = data
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("No JSON payload")
                return self._payload

        class MockResponse:
            def __init__(self, is_success=True, status_code=200, headers=None, body=b""):
                self.is_success = is_success
                self.status_code = status_code
                self.headers = headers or {}
                self._body = body

            async def aread(self):
                return self._body

        class MockStreamContext:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self._response

            async def __aexit__(self, exc_type, exc, tb):
                return None

        init_post_started = asyncio.Event()
        post_methods: list[str] = []

        class MockHttpClient:
            def stream(self, method, _url, **kwargs):
                if method == "GET":
                    return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

                request_method = kwargs["json"].get("method")
                post_methods.append(request_method)
                if request_method == "initialize":
                    init_post_started.set()
                    return MockStreamContext(MockResponse())

                raise AssertionError(f"Unexpected POST after initialize failure: {request_method}")

        class MockEventSource:
            def __init__(self, _response):
                self._response = _response

            async def aiter_sse(self):
                yield MockEvent("endpoint", "/messages")
                await init_post_started.wait()
                yield MockEvent(
                    "message",
                    payload={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "error": {"code": -32000, "message": "initialize rejected"},
                    },
                )
                await asyncio.sleep(1)

        request_body = {
            "jsonrpc": "2.0",
            "id": "tool-123",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with patch("registry.core.mcp_client.EventSource", MockEventSource):
            with pytest.raises(ConnectionError, match="initialize rejected"):
                await call_tool_via_sse_ephemeral(
                    http_client=MockHttpClient(),
                    sse_url="https://example.com/sse",
                    headers=mock_headers,
                    request_body=request_body,
                )

        assert post_methods == ["initialize"]

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_rejects_missing_request_id(self, mock_headers):
        """Test ephemeral SSE rejects tool requests without a JSON-RPC id."""

        request_body = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with pytest.raises(MisimplementedSpecException, match="must include a JSON-RPC id"):
            await call_tool_via_sse_ephemeral(
                http_client=Mock(),
                sse_url="https://example.com/sse",
                headers=mock_headers,
                request_body=request_body,
            )

    @pytest.mark.asyncio
    async def test_call_tool_via_sse_ephemeral_rejects_direct_json_post_response(self, mock_headers):
        """Test ephemeral SSE rejects direct JSON POST responses instead of treating them as valid."""

        class MockEvent:
            def __init__(self, event_type, data="", payload=None):
                self.event = event_type
                self.data = data
                self._payload = payload

            def json(self):
                if self._payload is None:
                    raise ValueError("No JSON payload")
                return self._payload

        class MockResponse:
            def __init__(self, is_success=True, status_code=200, headers=None, body=b""):
                self.is_success = is_success
                self.status_code = status_code
                self.headers = headers or {}
                self._body = body

            async def aread(self):
                return self._body

        class MockStreamContext:
            def __init__(self, response):
                self._response = response

            async def __aenter__(self):
                return self._response

            async def __aexit__(self, exc_type, exc, tb):
                return None

        init_post_started = asyncio.Event()

        class MockHttpClient:
            def stream(self, method, _url, **kwargs):
                if method == "GET":
                    return MockStreamContext(MockResponse(headers={"content-type": "text/event-stream"}))

                request_method = kwargs["json"].get("method")
                if request_method == "initialize":
                    init_post_started.set()
                    return MockStreamContext(MockResponse())

                if request_method == "notifications/initialized":
                    return MockStreamContext(MockResponse())

                if request_method == "tools/call":
                    return MockStreamContext(MockResponse(headers={"content-type": "application/json"}))

                raise AssertionError(f"Unexpected stream call: {method} {kwargs}")

        class MockEventSource:
            def __init__(self, _response):
                self._response = _response

            async def aiter_sse(self):
                yield MockEvent("endpoint", "/messages")
                await init_post_started.wait()
                yield MockEvent(
                    "message",
                    payload={"jsonrpc": "2.0", "id": "1", "result": {"protocolVersion": "2024-11-05"}},
                )
                await asyncio.sleep(1)

        request_body = {
            "jsonrpc": "2.0",
            "id": "tool-123",
            "method": "tools/call",
            "params": {"name": "demo", "arguments": {}},
        }

        with patch("registry.core.mcp_client.EventSource", MockEventSource):
            with pytest.raises(MisimplementedSpecException, match="application/json"):
                await call_tool_via_sse_ephemeral(
                    http_client=MockHttpClient(),
                    sse_url="https://example.com/sse",
                    headers=mock_headers,
                    request_body=request_body,
                )

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
        tool.inputSchema = {"type": "object", "properties": {"query": {"type": "string"}}}

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
            patch("registry.core.mcp_client.streamable_http_client") as mock_client,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            # Mock strategy
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            # Mock MCP session
            mock_init_result = Mock()
            mock_init_result.capabilities = mock_capabilities

            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock(return_value=mock_init_result)
            mock_session.list_tools = AsyncMock(return_value=mock_tools_response)
            mock_session.list_resources = AsyncMock(return_value=Mock(resources=[]))
            mock_session.list_prompts = AsyncMock(return_value=Mock(prompts=[]))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            # Mock streamable_http_client context manager
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=(Mock(), Mock(), Mock()))
            mock_client.return_value = mock_context

            with patch("registry.core.mcp_client.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

                result = await _get_from_streamable_http(
                    base_url=base_url, headers=mock_headers, transport_type="streamable-http", include_capabilities=True
                )

            # Verify result is MCPServerData object
            assert result.tools is not None
            assert result.capabilities == mock_capabilities

    @pytest.mark.asyncio
    async def test_get_from_streamable_http_without_capabilities(self, mock_headers, mock_tools_response):
        """Test _get_from_streamable_http can skip capabilities."""
        base_url = "http://localhost:8000/mcp/"

        with (
            patch("registry.core.mcp_client.streamable_http_client") as mock_client,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            mock_session = AsyncMock()
            mock_session.list_tools = AsyncMock(return_value=mock_tools_response)
            mock_session.list_resources = AsyncMock(return_value=Mock(resources=[]))
            mock_session.list_prompts = AsyncMock(return_value=Mock(prompts=[]))

            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=(Mock(), Mock(), Mock()))
            mock_client.return_value = mock_context

            with patch("registry.core.mcp_client.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

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
            patch("registry.core.mcp_client.sse_client") as mock_client,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            mock_init_result = Mock()
            mock_init_result.capabilities = mock_capabilities

            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock(return_value=mock_init_result)
            mock_session.list_tools = AsyncMock(return_value=mock_tools_response)
            mock_session.list_resources = AsyncMock(return_value=Mock(resources=[]))
            mock_session.list_prompts = AsyncMock(return_value=Mock(prompts=[]))
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            # Mock sse_client context
            mock_sse_context = AsyncMock()
            mock_sse_context.__aenter__ = AsyncMock(return_value=(Mock(), Mock()))
            mock_client.return_value = mock_sse_context

            with patch("registry.core.mcp_client.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

                result = await _get_from_sse(
                    base_url=base_url, headers=mock_headers, transport_type="sse", include_capabilities=True
                )

            assert result.tools is not None
            assert result.capabilities == mock_capabilities
            # Regression guard: sse_client should receive headers and never http_client.
            _, kwargs = mock_client.call_args
            assert kwargs["headers"] == mock_headers
            assert "http_client" not in kwargs

    @pytest.mark.asyncio
    async def test_initialize_mcp_sse_uses_headers_not_http_client(self, mock_headers):
        """Test initialize_mcp uses sse_client(headers=...) for SSE transport."""
        target_url = "https://example.com/sse"

        with (
            patch("registry.core.mcp_client.sse_client") as mock_sse_client,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=target_url)
            mock_strategy.return_value = strategy

            mock_init_result = Mock()

            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock(return_value=mock_init_result)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_sse_context = AsyncMock()
            mock_sse_context.__aenter__ = AsyncMock(return_value=(Mock(), Mock()))
            mock_sse_client.return_value = mock_sse_context

            with patch("registry.core.mcp_client.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

                result = await initialize_mcp(
                    target_url=target_url,
                    headers=mock_headers,
                    transport_type="sse",
                )

            assert result == mock_init_result
            _, kwargs = mock_sse_client.call_args
            assert kwargs["headers"] == mock_headers
            assert "http_client" not in kwargs

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
            patch("registry.core.mcp_client.streamable_http_client") as mock_client,
            patch("registry.core.mcp_client.get_server_strategy") as mock_strategy,
        ):
            strategy = Mock()
            strategy.modify_url = Mock(return_value=base_url)
            mock_strategy.return_value = strategy

            mock_session = AsyncMock()
            mock_session.list_tools = AsyncMock(return_value=Mock(tools=[]))
            mock_session.list_resources = AsyncMock(return_value=Mock(resources=[]))
            mock_session.list_prompts = AsyncMock(return_value=Mock(prompts=[]))

            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=(Mock(), Mock(), Mock()))
            mock_client.return_value = mock_context

            with patch("registry.core.mcp_client.ClientSession") as mock_session_cls:
                mock_session_cls.return_value = mock_session

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


@pytest.mark.unit
@pytest.mark.core
class TestPerformHealthCheck:
    """Test suite for perform_health_check function."""

    @pytest.fixture
    def mock_init_result(self):
        """Create mock MCP initialize result."""
        mock_result = Mock()
        mock_result.protocolVersion = "2024-11-05"
        mock_result.serverInfo = Mock()
        mock_result.serverInfo.name = "test-server"
        # Simulate Pydantic model that will be converted to dict
        mock_capabilities = Mock()
        mock_capabilities.model_dump = Mock(return_value={"tools": {}})
        mock_result.capabilities = mock_capabilities
        return mock_result

    @pytest.mark.asyncio
    async def test_perform_health_check_success(self, mock_init_result):
        """Test successful health check."""
        from registry.core.mcp_client import perform_health_check

        url = "https://example.com/api"
        transport = "streamable-http"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=mock_init_result)):
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
        from registry.core.mcp_client import perform_health_check

        is_healthy, status, response_time, init_result = await perform_health_check("", "streamable-http")

        assert is_healthy is False
        assert status == "No URL provided"
        assert response_time is None
        assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_stdio_skipped(self):
        """Test health check is skipped for stdio transport."""
        from registry.core.mcp_client import perform_health_check

        is_healthy, status, response_time, init_result = await perform_health_check("/path/to/binary", "stdio")

        assert is_healthy is True
        assert "stdio transport skipped" in status.lower()
        assert response_time is None
        assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_initialization_failed(self):
        """Test health check when initialization returns None."""
        from registry.core.mcp_client import perform_health_check

        url = "https://example.com/api"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=None)):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "streamable-http")

            assert is_healthy is False
            assert "initialization failed" in status.lower()
            assert response_time is not None
            assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_invalid_response(self):
        """Test health check with invalid MCP response (missing fields)."""
        from registry.core.mcp_client import perform_health_check

        # Mock result missing required fields (protocolVersion and serverInfo)
        invalid_result = Mock(spec=["some_other_field"])

        url = "https://example.com/api"

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=invalid_result)):
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

        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(return_value=mock_init_result)):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "sse")

            assert is_healthy is True
            assert "initialize handshake successful" in status.lower()
            assert init_result == mock_init_result
            # Verify capabilities were converted to dict
            assert isinstance(init_result.capabilities, dict)

    @pytest.mark.asyncio
    async def test_perform_health_check_401_response(self):
        """Test health check when server returns 401 (Unauthorized) - should still be considered healthy."""
        import httpx

        from registry.core.mcp_client import perform_health_check

        url = "https://example.com/api"

        # Create a mock 401 response
        mock_response = Mock()
        mock_response.status_code = 401

        # Create HTTPStatusError with 401
        error_401 = httpx.HTTPStatusError("401 Unauthorized", request=Mock(), response=mock_response)

        # Mock initialize_mcp to raise HTTPStatusError directly (already unwrapped)
        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(side_effect=error_401)):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "streamable-http")

            assert is_healthy is True
            assert "initialize requires authentication" in status.lower()
            assert response_time is not None
            assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_401_direct_exception(self):
        """Test health check with direct HTTPStatusError (without ExceptionGroup wrapper)."""
        import httpx

        from registry.core.mcp_client import perform_health_check

        url = "https://example.com/api"

        # Create a mock 401 response
        mock_response = Mock()
        mock_response.status_code = 401

        # Create HTTPStatusError with 401 (direct, not wrapped)
        error_401 = httpx.HTTPStatusError("401 Unauthorized", request=Mock(), response=mock_response)

        # Mock initialize_mcp to raise direct HTTPStatusError
        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(side_effect=error_401)):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "streamable-http")

            assert is_healthy is True
            assert "initialize requires authentication" in status.lower()
            assert response_time is not None
            assert init_result is None

    @pytest.mark.asyncio
    async def test_perform_health_check_403_response(self):
        """Test health check when server returns 403 (Forbidden) - should still be considered healthy."""
        import httpx

        from registry.core.mcp_client import perform_health_check

        url = "https://example.com/api"

        # Create a mock 403 response
        mock_response = Mock()
        mock_response.status_code = 403

        # Create HTTPStatusError with 403
        error_403 = httpx.HTTPStatusError("403 Forbidden", request=Mock(), response=mock_response)

        # Mock initialize_mcp to raise HTTPStatusError
        with patch("registry.core.mcp_client.initialize_mcp", new=AsyncMock(side_effect=error_403)):
            is_healthy, status, response_time, init_result = await perform_health_check(url, "streamable-http")

            assert is_healthy is True
            assert "initialize requires authentication" in status.lower()
            assert response_time is not None
            assert init_result is None

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_rfc8414_success(self):
        """Test successful OAuth metadata discovery from RFC 8414 endpoint."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        base_url = "https://mcp.example.com/api"
        expected_metadata = {
            "issuer": "https://mcp.example.com",
            "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
            "token_endpoint": "https://mcp.example.com/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
        }

        # Mock httpx response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value=expected_metadata)

        # Mock httpx client
        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_metadata_cls:
                # Mock validation
                mock_metadata = Mock()
                mock_metadata.validate = Mock()
                mock_metadata_cls.return_value = mock_metadata

                result = await get_oauth_metadata_from_server(base_url)

        assert result == expected_metadata
        # Verify RFC 8414 endpoint was tried first
        mock_http_client.get.assert_called()
        call_args = mock_http_client.get.call_args[0][0]
        assert "/.well-known/oauth-authorization-server" in call_args

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_oidc_fallback(self):
        """Test OAuth metadata discovery falls back to OIDC endpoint."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        base_url = "https://mcp.example.com"
        expected_metadata = {
            "issuer": "https://mcp.example.com",
            "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
            "token_endpoint": "https://mcp.example.com/oauth/token",
        }

        # Mock httpx responses - RFC 8414 fails, OIDC succeeds
        mock_response_404 = Mock()
        mock_response_404.status_code = 404

        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.json = Mock(return_value=expected_metadata)

        # Mock httpx client
        mock_http_client = AsyncMock()
        # Call order: (1) Protected Resource endpoint 404, (2) RFC 8414 endpoint 404, (3) OIDC endpoint 200
        mock_http_client.get = AsyncMock(side_effect=[mock_response_404, mock_response_404, mock_response_200])
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_metadata_cls:
                mock_metadata = Mock()
                mock_metadata.validate = Mock()
                mock_metadata_cls.return_value = mock_metadata

                result = await get_oauth_metadata_from_server(base_url)

        assert result == expected_metadata
        # Verify all three endpoints were tried (protected resource + RFC 8414 + OIDC)
        assert mock_http_client.get.call_count == 3

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_no_metadata(self):
        """Test OAuth metadata discovery when no endpoints are available."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        base_url = "https://mcp.example.com"

        # Mock httpx responses - all fail with 404
        mock_response_404 = Mock()
        mock_response_404.status_code = 404

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response_404)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await get_oauth_metadata_from_server(base_url)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_validation_failure(self):
        """Test OAuth metadata discovery with validation failure (still returns metadata)."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        base_url = "https://mcp.example.com"
        metadata_dict = {
            "issuer": "https://mcp.example.com",
            "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value=metadata_dict)

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_metadata_cls:
                # Mock validation to raise exception
                mock_metadata = Mock()
                mock_metadata.validate = Mock(side_effect=ValueError("Invalid metadata"))
                mock_metadata_cls.return_value = mock_metadata

                result = await get_oauth_metadata_from_server(base_url)

        # Should still return metadata even if validation fails
        assert result == metadata_dict

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_empty_url(self):
        """Test OAuth metadata discovery with empty URL."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        result = await get_oauth_metadata_from_server("")
        assert result is None

        result = await get_oauth_metadata_from_server(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_network_error(self):
        """Test OAuth metadata discovery with network error."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        base_url = "https://mcp.example.com"

        # Mock httpx client to raise network error
        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=Exception("Network error"))
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            result = await get_oauth_metadata_from_server(base_url)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_oauth_metadata_from_server_url_parsing(self):
        """Test OAuth metadata discovery correctly extracts base domain from URLs."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        # Test with path segments - should strip to base domain
        base_url = "https://mcp.atlassian.com/api/v1/servers"
        expected_metadata = {"issuer": "https://mcp.atlassian.com"}

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value=expected_metadata)

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_metadata_cls:
                mock_metadata = Mock()
                mock_metadata.validate = Mock()
                mock_metadata_cls.return_value = mock_metadata

                result = await get_oauth_metadata_from_server(base_url)

        assert result == expected_metadata
        # Verify the well-known URL uses base domain, not the full path
        call_args = mock_http_client.get.call_args[0][0]
        assert call_args == "https://mcp.atlassian.com/.well-known/oauth-authorization-server"


class TestGetOAuthMetadataProtectedResourceDiscovery:
    """Tests for RFC 9728 protected resource metadata discovery in get_oauth_metadata_from_server."""

    def _make_http_client_mock(self, side_effects):
        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=side_effects)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)
        return mock_http_client

    def _make_response(self, status_code, json_body=None):
        r = Mock()
        r.status_code = status_code
        if json_body is not None:
            r.json = Mock(return_value=json_body)
        return r

    @pytest.mark.asyncio
    async def test_merges_resource_from_protected_resource_endpoint(self):
        """Resource URL from /.well-known/oauth-protected-resource is merged into oauthMetadata."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        pr_doc = {"resource": "https://mcp.hubspot.com", "authorization_servers": ["https://mcp.hubspot.com"]}
        as_metadata = {
            "issuer": "https://mcp.hubspot.com",
            "authorization_endpoint": "https://mcp.hubspot.com/oauth/authorize/user",
            "token_endpoint": "https://mcp.hubspot.com/oauth/v3/token",
        }
        # Call order: PR (200), RFC 8414 (200)
        mock_http_client = self._make_http_client_mock(
            [
                self._make_response(200, pr_doc),
                self._make_response(200, as_metadata),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_as_cls:
                mock_as_cls.return_value = Mock(validate=Mock())
                result = await get_oauth_metadata_from_server("https://mcp.hubspot.com")

        assert result is not None
        assert result["resource"] == "https://mcp.hubspot.com"
        # Ensure it came from PR doc, not issuer fallback
        assert result["issuer"] == "https://mcp.hubspot.com"

    @pytest.mark.asyncio
    async def test_no_resource_when_protected_resource_doc_unavailable(self):
        """When the PR endpoint returns 404, resource is NOT set — no issuer fallback."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        as_metadata = {
            "issuer": "https://mcp.example.com",
            "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
            "token_endpoint": "https://mcp.example.com/oauth/token",
        }
        # Call order: PR (404), RFC 8414 (200)
        mock_http_client = self._make_http_client_mock(
            [
                self._make_response(404),
                self._make_response(200, as_metadata),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_as_cls:
                mock_as_cls.return_value = Mock(validate=Mock())
                result = await get_oauth_metadata_from_server("https://mcp.example.com")

        assert result is not None
        # No PR document → resource must not be set (issuer is NOT used as fallback)
        assert "resource" not in result

    @pytest.mark.asyncio
    async def test_resource_in_as_metadata_preserved_when_pr_unavailable(self):
        """A resource field already present in AS metadata is kept when the PR endpoint returns 404."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        as_metadata = {
            "issuer": "https://mcp.example.com",
            "resource": "https://api.mcp.example.com",  # explicitly set
            "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
            "token_endpoint": "https://mcp.example.com/oauth/token",
        }
        mock_http_client = self._make_http_client_mock(
            [
                self._make_response(404),  # PR → 404
                self._make_response(200, as_metadata),  # RFC 8414 → 200
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_as_cls:
                mock_as_cls.return_value = Mock(validate=Mock())
                result = await get_oauth_metadata_from_server("https://mcp.example.com")

        # resource from AS metadata is preserved unchanged
        assert result["resource"] == "https://api.mcp.example.com"

    @pytest.mark.asyncio
    async def test_pr_resource_takes_precedence_over_issuer(self):
        """Resource from PR document takes precedence over issuer even when AS metadata has issuer."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        pr_doc = {"resource": "https://resource.mcp.example.com"}
        as_metadata = {
            "issuer": "https://auth.mcp.example.com",  # different from resource
            "authorization_endpoint": "https://auth.mcp.example.com/authorize",
            "token_endpoint": "https://auth.mcp.example.com/token",
        }
        mock_http_client = self._make_http_client_mock(
            [
                self._make_response(200, pr_doc),
                self._make_response(200, as_metadata),
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_as_cls:
                mock_as_cls.return_value = Mock(validate=Mock())
                result = await get_oauth_metadata_from_server("https://mcp.example.com")

        assert result["resource"] == "https://resource.mcp.example.com"

    @pytest.mark.asyncio
    async def test_pr_endpoint_failure_does_not_block_as_discovery(self):
        """A network error on the PR endpoint is caught and AS discovery continues normally."""
        from registry.core.mcp_client import get_oauth_metadata_from_server

        as_metadata = {
            "issuer": "https://mcp.example.com",
            "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
            "token_endpoint": "https://mcp.example.com/oauth/token",
        }
        mock_http_client = self._make_http_client_mock(
            [
                Exception("Connection refused"),  # PR → network error
                self._make_response(200, as_metadata),  # RFC 8414 → 200
            ]
        )

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("registry.core.mcp_client.AuthorizationServerMetadata") as mock_as_cls:
                mock_as_cls.return_value = Mock(validate=Mock())
                result = await get_oauth_metadata_from_server("https://mcp.example.com")

        assert result is not None
        assert result["token_endpoint"] == "https://mcp.example.com/oauth/token"
