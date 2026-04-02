from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from registry.core.config import settings
from registry.core.mcp_client import MCPServerData
from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry.services.federation.agentcore_runtime import AgentCoreRuntimeInvoker
from registry_pkgs.models import A2AAgent


def _build_invoker() -> AgentCoreRuntimeInvoker:
    class _FakeProvider(AgentCoreClientProvider):
        async def get_runtime_client(self, _region: str, assume_role_arn: str | None = None):
            return object()

        async def get_runtime_credentials_provider(self, _region: str, assume_role_arn: str | None = None):
            return lambda: None

    return AgentCoreRuntimeInvoker(
        client_provider=_FakeProvider(),
        extract_region_from_arn=lambda _arn, default: default,
    )


def _build_fake_a2a_agent(*, runtime_arn: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        card=SimpleNamespace(
            name="demo-a2a",
            version="1",
            model_dump=lambda mode="json": {
                "name": "demo-a2a",
                "description": "demo",
                "url": "https://example.com",
                "version": "1",
            },
        ),
        path="/demo",
        author="user_demo_id",
        isEnabled=True,
        status="active",
        tags=[],
        registeredBy="tester",
        registeredAt=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": runtime_arn} if runtime_arn else {},
        wellKnown=SimpleNamespace(
            lastSyncStatus=None,
            syncError=None,
            lastSyncAt=None,
            lastSyncVersion=None,
            model_dump=lambda mode="json": {},
        ),
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

    def test_detect_agentcore_data_plane_auth_mode_defaults_to_iam(self):
        invoker = _build_invoker()
        assert invoker.detect_agentcore_data_plane_auth_mode(metadata={}) == "IAM"

    def test_detect_agentcore_data_plane_auth_mode_detects_jwt(self):
        invoker = _build_invoker()
        mode = invoker.detect_agentcore_data_plane_auth_mode(
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}}
        )
        assert mode == "JWT"

    def test_extract_json_payload_supports_sse_data_line(self):
        payload = 'event: message\ndata: {"jsonrpc":"2.0","result":{"ok":true}}\n\n'
        parsed = AgentCoreRuntimeInvoker._extract_json_payload(payload)
        assert parsed["result"]["ok"] is True

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
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/runtime-demo-1",
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
    async def test_invoke_mcp_runtime_via_sdk_initializes_before_list_calls(self, monkeypatch):
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

        result = await invoker._invoke_mcp_runtime_via_sdk(
            metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/runtime-demo-1"},
            region="us-east-1",
            runtime_detail=None,
        )

        assert result.error_message is None
        initialize_mock.assert_awaited_once_with(
            client=runtime_client,
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/runtime-demo-1",
        )
        assert invoked_methods == [
            ("tools/list", "runtime-session-1", "mcp-session-1", "2025-11-05"),
            ("resources/list", "runtime-session-1", "mcp-session-1", "2025-11-05"),
            ("prompts/list", "runtime-session-1", "mcp-session-1", "2025-11-05"),
        ]

    @pytest.mark.asyncio
    async def test_fetch_mcp_runtime_capabilities_iam_prefers_sdk_success(self, monkeypatch):
        invoker = _build_invoker()
        sdk = MCPServerData(tools=[], resources=[], prompts=[], capabilities={}, error_message=None)
        sdk_mock = AsyncMock(return_value=sdk)
        http_mock = AsyncMock()
        monkeypatch.setattr(invoker, "_invoke_mcp_runtime_via_sdk", sdk_mock)
        monkeypatch.setattr(invoker, "_fetch_mcp_runtime_capabilities_via_http_with_retry", http_mock)

        result = await invoker.fetch_mcp_runtime_capabilities(
            runtime_url="https://example.com/invocations",
            transport_type="streamable-http",
            metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/runtime-demo"},
            region="us-east-1",
            runtime_detail={"authorizerConfiguration": None},
        )

        assert result is sdk
        sdk_mock.assert_awaited_once()
        http_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_mcp_runtime_capabilities_iam_falls_back_to_http(self, monkeypatch):
        invoker = _build_invoker()
        sdk = MCPServerData(tools=None, resources=None, prompts=None, capabilities=None, error_message="sdk failed")
        http = MCPServerData(tools=[], resources=[], prompts=[], capabilities={}, error_message=None)
        sdk_mock = AsyncMock(return_value=sdk)
        http_mock = AsyncMock(return_value=http)
        monkeypatch.setattr(invoker, "_invoke_mcp_runtime_via_sdk", sdk_mock)
        monkeypatch.setattr(invoker, "_fetch_mcp_runtime_capabilities_via_http_with_retry", http_mock)

        result = await invoker.fetch_mcp_runtime_capabilities(
            runtime_url="https://example.com/invocations",
            transport_type="streamable-http",
            metadata={"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/runtime-demo"},
            region="us-east-1",
            runtime_detail={"authorizerConfiguration": None},
        )

        assert result is http
        sdk_mock.assert_awaited_once()
        http_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enrich_a2a_agent_uses_runtime_arn_from_metadata(self, monkeypatch):
        invoker = _build_invoker()
        agent = _build_fake_a2a_agent(runtime_arn="arn:aws:bedrock-agentcore:us-east-1:account-id:runtime/runtime-demo")
        monkeypatch.setattr(
            invoker, "fetch_a2a_agent_card", AsyncMock(return_value={"name": "demo-a2a", "version": "2"})
        )
        monkeypatch.setattr(
            A2AAgent,
            "from_a2a_agent_card",
            lambda **_kwargs: SimpleNamespace(card=SimpleNamespace(name="demo-a2a", version="2")),
        )

        await invoker.enrich_a2a_agent(
            agent=agent, runtime_detail=dict(agent.federationMetadata or {}), region="us-east-1"
        )

        assert agent.card.version == "2"
        assert agent.wellKnown is not None
        assert agent.wellKnown.lastSyncStatus == "success"

    @pytest.mark.asyncio
    async def test_enrich_a2a_agent_skips_when_runtime_arn_missing(self):
        invoker = _build_invoker()
        agent = _build_fake_a2a_agent(runtime_arn=None)

        await invoker.enrich_a2a_agent(agent=agent, runtime_detail={}, region="us-east-1")

        assert agent.wellKnown is not None
        assert agent.wellKnown.lastSyncStatus == "failed"
        assert agent.wellKnown.syncError == "missing runtime ARN for A2A enrichment"
        assert agent.federationMetadata is not None
        assert agent.federationMetadata["enrichmentError"] == "a2a enrichment failed: missing runtime ARN"

    @pytest.mark.asyncio
    async def test_fetch_mcp_runtime_capabilities_jwt_uses_http_only(self, monkeypatch):
        invoker = _build_invoker()
        sdk_mock = AsyncMock()
        http = MCPServerData(tools=[], resources=[], prompts=[], capabilities={}, error_message=None)
        http_mock = AsyncMock(return_value=http)
        monkeypatch.setattr(invoker, "_invoke_mcp_runtime_via_sdk", sdk_mock)
        monkeypatch.setattr(invoker, "_fetch_mcp_runtime_capabilities_via_http_with_retry", http_mock)

        result = await invoker.fetch_mcp_runtime_capabilities(
            runtime_url="https://example.com/invocations",
            transport_type="streamable-http",
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            region="us-east-1",
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

        monkeypatch.setattr("registry.services.federation.agentcore_runtime.asyncio.to_thread", _fake_to_thread)

        result = await invoker._call_with_a2a_card_retry(_operation)
        assert result["agentCard"]["name"] == "ok"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_build_runtime_http_auth_jwt_uses_bearer_header(self, monkeypatch):
        invoker = _build_invoker()
        monkeypatch.setattr(settings, "agentcore_runtime_jwt", "token-123")

        headers, auth = await invoker._build_runtime_http_auth(
            metadata={"authorizerConfiguration": {"customJWTAuthorizerConfiguration": {"discoveryUrl": "x"}}},
            region="us-east-1",
            runtime_detail=None,
        )

        assert headers["Authorization"] == "Bearer token-123"
        assert auth is None
