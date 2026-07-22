from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from agno.workflow import StepInput
from beanie import PydanticObjectId

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows.mcp_executor import make_mcp_executor
from registry_pkgs.workflows.types import WorkflowConfigError


def _jwt_config(**overrides) -> JwtSigningConfig:
    defaults = {
        "jwt_private_key": "fake-pem",
        "jwt_issuer": "https://jarvis.example.com",
        "jwt_self_signed_kid": "kid-v1",
        "jwt_audience": "jarvis-services",
        "registry_app_name": "jarvis-registry-client",
    }
    defaults.update(overrides)
    return JwtSigningConfig(**defaults)


def _mcp_server(name: str = "github", runtime_access: dict | None = None) -> ExtendedMCPServer:
    config = {"description": "server description", "url": f"https://{name}.example.com/mcp"}
    if runtime_access is not None:
        config["runtimeAccess"] = runtime_access
    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName=name,
        config=config,
        author=PydanticObjectId(),
    )


@pytest.mark.unit
class TestAgentCoreMcpExecutor:
    """Case 1: AgentCore-federated MCP server uses sync header_provider."""

    @pytest.mark.asyncio
    async def test_header_provider_returns_bearer_token(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(arun=AsyncMock(return_value=SimpleNamespace(content="done")))
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())
        captured_tools_kwargs: dict = {}

        def fake_mcp_tools_cls(*args, **kwargs):
            captured_tools_kwargs.update(kwargs)
            return fake_mcp_tools

        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.MCPTools", fake_mcp_tools_cls)
        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.Agent", lambda **kwargs: fake_agent_instance)

        redis_client = MagicMock()
        redis_client.get.return_value = b"cached-agentcore-token"
        access_authorizer = AsyncMock()
        auth_context = {"user_id": "user-1", "client_id": "client-1"}

        executor = make_mcp_executor(
            _mcp_server("agentcore-server", runtime_access={"mode": "jwt", "jwt": {"audiences": ["agentcore"]}}),
            llm=SimpleNamespace(),
            auth_context=auth_context,
            jwt_config=_jwt_config(),
            redis_client=redis_client,
            redis_key_prefix="test-registry",
            mcp_access_authorizer=access_authorizer,
            mcp_headers_provider=None,
        )

        output = await executor(StepInput(input="hello", previous_step_content="ctx"), {})

        assert output.success is True
        assert output.content == "done"
        assert captured_tools_kwargs.get("header_provider") is not None
        assert captured_tools_kwargs.get("refresh_connection") is False
        assert captured_tools_kwargs["server_params"].url == "https://agentcore-server.example.com/mcp"
        access_authorizer.assert_awaited_once_with(ANY, auth_context)

        # Calling header_provider directly should return the cached token.
        header_provider = captured_tools_kwargs["header_provider"]
        headers = header_provider()
        assert headers == {"Authorization": "Bearer cached-agentcore-token"}

    def test_header_provider_mints_and_caches_on_miss(self, monkeypatch: pytest.MonkeyPatch):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_key = rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        jwt_config = _jwt_config(jwt_private_key=private_key)

        fake_agent_instance = SimpleNamespace(arun=AsyncMock())
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())
        captured_tools_kwargs: dict = {}

        def fake_mcp_tools_cls(*args, **kwargs):
            captured_tools_kwargs.update(kwargs)
            return fake_mcp_tools

        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.MCPTools", fake_mcp_tools_cls)
        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.Agent", lambda **kwargs: fake_agent_instance)

        redis_client = MagicMock()
        redis_client.get.return_value = None

        make_mcp_executor(
            _mcp_server("agentcore-server", runtime_access={"mode": "jwt", "jwt": {}}),
            llm=SimpleNamespace(),
            auth_context=None,
            jwt_config=jwt_config,
            redis_client=redis_client,
            redis_key_prefix="test-registry",
            mcp_headers_provider=None,
        )

        # Trigger header_provider to mint.
        header_provider = captured_tools_kwargs["header_provider"]
        headers = header_provider()

        assert headers["Authorization"].startswith("Bearer ")
        redis_client.setex.assert_called_once()
        _, ttl, token = redis_client.setex.call_args.args
        assert ttl == 3540
        assert token == headers["Authorization"].split(" ", 1)[1]

    def test_rejects_agentcore_iam_mode(self):
        with pytest.raises(NotImplementedError, match="IAM authentication is not supported"):
            make_mcp_executor(
                _mcp_server("agentcore-server", runtime_access={"mode": "iam"}),
                llm=SimpleNamespace(),
                auth_context=None,
                jwt_config=_jwt_config(),
                redis_client=None,
                redis_key_prefix="test-registry",
                mcp_headers_provider=None,
            )


@pytest.mark.unit
class TestManualMcpExecutor:
    """Case 2: manually-registered MCP server builds headers fresh per step."""

    @pytest.mark.asyncio
    async def test_rebuilds_tools_and_headers_per_executor_call(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(arun=AsyncMock(return_value=SimpleNamespace(content="done")))
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())
        tools_calls: list[dict] = []

        def fake_mcp_tools_cls(*args, **kwargs):
            tools_calls.append(kwargs)
            return fake_mcp_tools

        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.MCPTools", fake_mcp_tools_cls)
        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.Agent", lambda **kwargs: fake_agent_instance)

        async def fake_headers_provider(server, auth_context):
            return {"Authorization": "Bearer oauth-token", "X-User-Id": auth_context["user_id"]}

        auth_context = {"user_id": "user-42"}
        executor = make_mcp_executor(
            _mcp_server("oauth-server"),
            llm=SimpleNamespace(),
            auth_context=auth_context,
            jwt_config=_jwt_config(),
            redis_client=None,
            redis_key_prefix="test-registry",
            mcp_headers_provider=fake_headers_provider,
        )

        output = await executor(StepInput(input="hello", previous_step_content="ctx"), {})

        assert output.success is True
        assert output.content == "done"
        assert len(tools_calls) == 1
        assert tools_calls[0]["server_params"].headers == {
            "Authorization": "Bearer oauth-token",
            "X-User-Id": "user-42",
        }

    @pytest.mark.asyncio
    async def test_raises_workflow_config_error_when_auth_context_missing(self):
        executor = make_mcp_executor(
            _mcp_server("oauth-server"),
            llm=SimpleNamespace(),
            auth_context=None,
            jwt_config=_jwt_config(),
            redis_client=None,
            redis_key_prefix="test-registry",
            mcp_headers_provider=lambda *args, **kwargs: {},
        )

        with pytest.raises(WorkflowConfigError, match="No auth context available"):
            await executor(StepInput(input="hello", previous_step_content="ctx"), {})

    @pytest.mark.asyncio
    async def test_does_not_call_provider_when_auth_context_missing(self):
        provider_called = False

        async def fake_headers_provider(server, auth_context):
            nonlocal provider_called
            provider_called = True
            return {}

        executor = make_mcp_executor(
            _mcp_server("oauth-server"),
            llm=SimpleNamespace(),
            auth_context=None,
            jwt_config=_jwt_config(),
            redis_client=None,
            redis_key_prefix="test-registry",
            mcp_headers_provider=fake_headers_provider,
        )

        with pytest.raises(WorkflowConfigError, match="No auth context available"):
            await executor(StepInput(input="hello", previous_step_content="ctx"), {})

        assert provider_called is False

    @pytest.mark.asyncio
    async def test_raises_workflow_config_error_when_provider_missing(self):
        executor = make_mcp_executor(
            _mcp_server("oauth-server"),
            llm=SimpleNamespace(),
            auth_context={"user_id": "user-42"},
            jwt_config=_jwt_config(),
            redis_client=None,
            redis_key_prefix="test-registry",
            mcp_headers_provider=None,
        )

        with pytest.raises(WorkflowConfigError, match="No headers provider configured"):
            await executor(StepInput(input="hello", previous_step_content="ctx"), {})

    @pytest.mark.asyncio
    async def test_reraises_agent_failures(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(arun=AsyncMock(side_effect=RuntimeError("init failed")))
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())

        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.MCPTools", lambda *args, **kwargs: fake_mcp_tools)
        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.Agent", lambda **kwargs: fake_agent_instance)

        async def fake_headers_provider(server, auth_context):
            return {"Authorization": "Bearer token"}

        executor = make_mcp_executor(
            _mcp_server("oauth-server"),
            llm=SimpleNamespace(),
            auth_context={"user_id": "user-42"},
            jwt_config=_jwt_config(),
            redis_client=None,
            redis_key_prefix="test-registry",
            mcp_headers_provider=fake_headers_provider,
        )

        with pytest.raises(RuntimeError, match="MCP executor 'oauth-server' failed: init failed"):
            await executor(StepInput(input="hello", previous_step_content="ctx"), {})

    @pytest.mark.asyncio
    async def test_raises_when_agent_returns_error_status(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(
            arun=AsyncMock(return_value=SimpleNamespace(content="Unable to locate credentials", status="error"))
        )
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())

        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.MCPTools", lambda *args, **kwargs: fake_mcp_tools)
        monkeypatch.setattr("registry_pkgs.workflows.mcp_executor.Agent", lambda **kwargs: fake_agent_instance)

        async def fake_headers_provider(server, auth_context):
            return {"Authorization": "Bearer token"}

        executor = make_mcp_executor(
            _mcp_server("oauth-server"),
            llm=SimpleNamespace(),
            auth_context={"user_id": "user-42"},
            jwt_config=_jwt_config(),
            redis_client=None,
            redis_key_prefix="test-registry",
            mcp_headers_provider=fake_headers_provider,
        )

        with pytest.raises(RuntimeError, match="Unable to locate credentials"):
            await executor(StepInput(input="hello", previous_step_content="ctx"), {})
