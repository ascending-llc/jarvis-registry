from unittest.mock import AsyncMock

import pytest

from registry.core.mcp_client import MCPServerData
from registry.services.federation.runtime_invoker import AgentCoreRuntimeInvoker


def _build_invoker() -> AgentCoreRuntimeInvoker:
    async def _get_runtime_client(_region: str):
        return object()

    async def _get_runtime_credentials_provider(_region: str):
        return lambda: None

    return AgentCoreRuntimeInvoker(
        default_region="us-east-1",
        get_runtime_client=_get_runtime_client,
        get_runtime_credentials_provider=_get_runtime_credentials_provider,
        extract_region_from_arn=lambda _arn, default: default,
    )


@pytest.mark.unit
class TestAgentCoreRuntimeInvoker:
    def test_next_request_id_returns_unique_strings(self):
        invoker = _build_invoker()
        request_id_1 = invoker._next_request_id()
        request_id_2 = invoker._next_request_id()

        assert isinstance(request_id_1, str)
        assert isinstance(request_id_2, str)
        assert request_id_1 != request_id_2

    def test_detect_runtime_auth_mode_defaults_to_iam(self):
        invoker = _build_invoker()
        assert invoker.detect_runtime_auth_mode(metadata={}) == "IAM"

    def test_detect_runtime_auth_mode_detects_jwt(self):
        invoker = _build_invoker()
        mode = invoker.detect_runtime_auth_mode(
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}}
        )
        assert mode == "JWT"

    def test_extract_json_payload_supports_sse_data_line(self):
        payload = 'event: message\ndata: {"jsonrpc":"2.0","result":{"ok":true}}\n\n'
        parsed = AgentCoreRuntimeInvoker._extract_json_payload(payload)
        assert parsed["result"]["ok"] is True

    @pytest.mark.asyncio
    async def test_call_with_runtime_init_retry_uses_to_thread(self, monkeypatch):
        invoker = _build_invoker()
        invoker._runtime_init_retry_attempts = 2
        invoker._runtime_init_retry_delay_seconds = 0

        calls = {"to_thread": 0, "op": 0}

        async def _fake_to_thread(op):
            calls["to_thread"] += 1
            return op()

        def _operation():
            calls["op"] += 1
            if calls["op"] == 1:
                raise ValueError("Runtime initialization time exceeded")
            return {"ok": True}

        monkeypatch.setattr("registry.services.federation.runtime_invoker.asyncio.to_thread", _fake_to_thread)

        result = await invoker._call_with_runtime_init_retry(_operation)
        assert result == {"ok": True}
        assert calls["to_thread"] == 2

    @pytest.mark.asyncio
    async def test_call_with_runtime_init_retry_async_retries(self):
        invoker = _build_invoker()
        invoker._runtime_init_retry_attempts = 2
        invoker._runtime_init_retry_delay_seconds = 0
        calls = {"n": 0}

        async def _operation():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("Runtime initialization time exceeded")
            return {"ok": True}

        result = await invoker._call_with_runtime_init_retry_async(_operation)
        assert result == {"ok": True}
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_invoke_mcp_jsonrpc_uses_to_thread(self, monkeypatch):
        invoker = _build_invoker()

        calls = {"to_thread": 0}

        async def _fake_to_thread(op, **kwargs):
            calls["to_thread"] += 1
            return op(**kwargs)

        class _FakeClient:
            def invoke_agent_runtime(self, **_kwargs):
                return {
                    "response": b'{"jsonrpc":"2.0","result":{"tools":[]}}',
                    "runtimeSessionId": "r1",
                    "mcpSessionId": "m1",
                    "mcpProtocolVersion": "2025-11-05",
                }

        monkeypatch.setattr("registry.services.federation.runtime_invoker.asyncio.to_thread", _fake_to_thread)

        result, runtime_session_id, mcp_session_id, protocol_version = await invoker._invoke_mcp_jsonrpc(
            client=_FakeClient(),
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
            method="tools/list",
            params={},
            request_id=1,
            runtime_session_id=None,
            mcp_session_id=None,
            protocol_version=None,
        )

        assert result == {"tools": []}
        assert runtime_session_id == "r1"
        assert mcp_session_id == "m1"
        assert protocol_version == "2025-11-05"
        assert calls["to_thread"] == 1

    @pytest.mark.asyncio
    async def test_initialize_mcp_session_uses_initialize_then_initialized(self, monkeypatch):
        invoker = _build_invoker()
        invoke_mock = AsyncMock(
            return_value=(
                {"protocolVersion": "2025-11-05"},
                "runtime-session-1",
                "mcp-session-1",
                None,
            )
        )
        notification_mock = AsyncMock()
        monkeypatch.setattr(invoker, "_invoke_mcp_jsonrpc", invoke_mock)
        monkeypatch.setattr(invoker, "_send_mcp_notification", notification_mock)

        runtime_session_id, mcp_session_id, protocol_version = await invoker._initialize_mcp_session(
            client=object(),
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
        )

        assert runtime_session_id == "runtime-session-1"
        assert mcp_session_id == "mcp-session-1"
        assert protocol_version == "2025-11-05"
        invoke_mock.assert_awaited_once()
        assert invoke_mock.await_args.kwargs["method"] == "initialize"
        assert invoke_mock.await_args.kwargs["params"]["clientInfo"]["name"] == "mcp-gateway-registry"
        notification_mock.assert_awaited_once()
        assert notification_mock.await_args.kwargs["method"] == "notifications/initialized"
        assert notification_mock.await_args.kwargs["mcp_session_id"] == "mcp-session-1"

    @pytest.mark.asyncio
    async def test_fetch_mcp_payloads_via_sdk_initializes_before_list_calls(self, monkeypatch):
        invoker = _build_invoker()

        class _FakeClient:
            pass

        runtime_client = _FakeClient()
        initialize_mock = AsyncMock(return_value=("runtime-session-1", "mcp-session-1", "2025-11-05"))
        invoked_methods: list[tuple[str, str | None, str | None, str | None]] = []

        async def _fake_invoke_mcp_jsonrpc(**kwargs):
            invoked_methods.append(
                (
                    kwargs["method"],
                    kwargs["runtime_session_id"],
                    kwargs["mcp_session_id"],
                    kwargs["protocol_version"],
                )
            )
            method = kwargs["method"]
            if method == "tools/list":
                return {"tools": []}, "runtime-session-1", "mcp-session-1", "2025-11-05"
            if method == "resources/list":
                return {"resources": []}, "runtime-session-1", "mcp-session-1", "2025-11-05"
            if method == "prompts/list":
                return {"prompts": []}, "runtime-session-1", "mcp-session-1", "2025-11-05"
            raise AssertionError(f"unexpected method {method}")

        monkeypatch.setattr(invoker, "_get_runtime_client", AsyncMock(return_value=runtime_client))
        monkeypatch.setattr(invoker, "_initialize_mcp_session", initialize_mock)
        monkeypatch.setattr(invoker, "_invoke_mcp_jsonrpc", _fake_invoke_mcp_jsonrpc)

        result = await invoker._fetch_mcp_payloads_via_sdk(
            metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1"},
            runtime_detail=None,
        )

        assert result.error_message is None
        initialize_mock.assert_awaited_once_with(
            client=runtime_client,
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1",
        )
        assert invoked_methods == [
            ("tools/list", "runtime-session-1", "mcp-session-1", "2025-11-05"),
            ("resources/list", "runtime-session-1", "mcp-session-1", "2025-11-05"),
            ("prompts/list", "runtime-session-1", "mcp-session-1", "2025-11-05"),
        ]

    @pytest.mark.asyncio
    async def test_fetch_mcp_payloads_iam_prefers_sdk_success(self, monkeypatch):
        invoker = _build_invoker()
        sdk = MCPServerData(tools=[], resources=[], prompts=[], capabilities={}, error_message=None)
        sdk_mock = AsyncMock(return_value=sdk)
        http_mock = AsyncMock()
        monkeypatch.setattr(invoker, "_fetch_mcp_payloads_via_sdk", sdk_mock)
        monkeypatch.setattr(invoker, "_fetch_mcp_payloads_via_http_with_retry", http_mock)

        result = await invoker.fetch_mcp_payloads(
            runtime_url="https://example.com/invocations",
            transport_type="streamable-http",
            metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:1:runtime/r"},
            runtime_detail={"authorizerConfiguration": None},
        )

        assert result is sdk
        sdk_mock.assert_awaited_once()
        http_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_mcp_payloads_iam_falls_back_to_http(self, monkeypatch):
        invoker = _build_invoker()
        sdk = MCPServerData(tools=None, resources=None, prompts=None, capabilities=None, error_message="sdk failed")
        http = MCPServerData(tools=[], resources=[], prompts=[], capabilities={}, error_message=None)
        sdk_mock = AsyncMock(return_value=sdk)
        http_mock = AsyncMock(return_value=http)
        monkeypatch.setattr(invoker, "_fetch_mcp_payloads_via_sdk", sdk_mock)
        monkeypatch.setattr(invoker, "_fetch_mcp_payloads_via_http_with_retry", http_mock)

        result = await invoker.fetch_mcp_payloads(
            runtime_url="https://example.com/invocations",
            transport_type="streamable-http",
            metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:1:runtime/r"},
            runtime_detail={"authorizerConfiguration": None},
        )

        assert result is http
        sdk_mock.assert_awaited_once()
        http_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_mcp_payloads_jwt_uses_http_only(self, monkeypatch):
        invoker = _build_invoker()
        sdk_mock = AsyncMock()
        http = MCPServerData(tools=[], resources=[], prompts=[], capabilities={}, error_message=None)
        http_mock = AsyncMock(return_value=http)
        monkeypatch.setattr(invoker, "_fetch_mcp_payloads_via_sdk", sdk_mock)
        monkeypatch.setattr(invoker, "_fetch_mcp_payloads_via_http_with_retry", http_mock)

        result = await invoker.fetch_mcp_payloads(
            runtime_url="https://example.com/invocations",
            transport_type="streamable-http",
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            runtime_detail=None,
        )

        assert result is http
        sdk_mock.assert_not_awaited()
        http_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_with_a2a_card_retry_retries_on_retryable_error(self, monkeypatch):
        invoker = _build_invoker()
        invoker._a2a_card_retry_attempts = 2
        invoker._a2a_card_retry_delay_seconds = 0

        calls = {"n": 0}

        async def _fake_to_thread(op):
            return op()

        def _operation():
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("GetAgentCard operation returned (502)")
            return {"agentCard": {"name": "ok"}}

        monkeypatch.setattr("registry.services.federation.runtime_invoker.asyncio.to_thread", _fake_to_thread)

        result = await invoker._call_with_a2a_card_retry(_operation)
        assert result["agentCard"]["name"] == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_build_runtime_http_auth_jwt_uses_bearer_header(self, monkeypatch):
        invoker = _build_invoker()
        monkeypatch.setenv("AGENTCORE_RUNTIME_JWT", "token-123")

        headers, auth = await invoker._build_runtime_http_auth(
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            runtime_detail=None,
        )

        assert headers["Authorization"] == "Bearer token-123"
        assert auth is None
