from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.types import AgentCard
from beanie import PydanticObjectId
from pydantic import HttpUrl

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows import a2a_executor as a2a_exec
from registry_pkgs.workflows import executor_resolver
from registry_pkgs.workflows import mcp_executor as mcp_exec
from registry_pkgs.workflows.helpers import build_prompt


def _jwt_config(**overrides) -> JwtSigningConfig:
    defaults = {
        "jwt_private_key": "fake-pem",
        "jwt_issuer": "https://jarvis.example.com",
        "jwt_self_signed_kid": "kid-v1",
        "jwt_audience": "jarvis-services",
    }
    defaults.update(overrides)
    return JwtSigningConfig(**defaults)


def _mcp_server(name: str = "github") -> ExtendedMCPServer:
    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName=name,
        config={"description": "server description"},
        author=PydanticObjectId(),
        status="active",
    )


def _a2a_agent(
    path: str = "/deep-intel",
    transport: str = "jsonrpc",
    config_url: str = "https://config.example.com/agent",
) -> A2AAgent:
    return A2AAgent.model_construct(
        id=PydanticObjectId(),
        path=path,
        card=AgentCard.model_construct(
            name="Deep Intel",
            description="desc",
            url="https://card.example.com/agent",
            version="1.0.0",
            protocol_version="0.3.0",
            capabilities={},
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            skills=[],
        ),
        config=AgentConfig(
            title="Configured Agent",
            description="desc",
            url=HttpUrl(config_url),
            type=transport,
        ),
        author=PydanticObjectId(),
        status="active",
    )


class _FieldExpr:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return (self.name, "==", other)


@pytest.mark.unit
class TestExecutorResolver:
    """Tests for the orchestration layer"""

    @staticmethod
    def _patch_beanie_filters(monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch Beanie field expressions used in find_one() calls."""
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "serverName", _FieldExpr("serverName"), raising=False)
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "status", _FieldExpr("status"), raising=False)
        monkeypatch.setattr(executor_resolver.A2AAgent, "path", _FieldExpr("path"), raising=False)
        monkeypatch.setattr(executor_resolver.A2AAgent, "status", _FieldExpr("status"), raising=False)

    @pytest.mark.asyncio
    async def test_build_executor_registry_deduplicates_keys(self, monkeypatch: pytest.MonkeyPatch):
        seen: list[str] = []

        async def fake_resolve(key: str, **kwargs):
            seen.append(key)
            return f"executor:{key}"

        monkeypatch.setattr(executor_resolver, "_resolve_executor", fake_resolve)

        registry = await executor_resolver.build_executor_registry(
            ["alpha", "beta", "alpha"],
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
        )

        assert seen == ["alpha", "beta"]
        assert registry == {"alpha": "executor:alpha", "beta": "executor:beta"}

    @pytest.mark.asyncio
    async def test_resolve_executor_prefers_active_mcp_server(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        monkeypatch.setattr(
            executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=_mcp_server("github"))
        )
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=_a2a_agent("/github")))
        monkeypatch.setattr(executor_resolver, "make_mcp_executor", lambda *args, **kwargs: "mcp-executor")
        monkeypatch.setattr(executor_resolver, "make_a2a_executor", lambda *args, **kwargs: "a2a-executor")

        resolved = await executor_resolver._resolve_executor(
            "github",
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
        )

        assert resolved == "mcp-executor"

    @pytest.mark.asyncio
    async def test_resolve_executor_falls_back_to_a2a_agent(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=_a2a_agent("/deep-intel")))
        captured_agents: list = []

        def fake_make_a2a_executor(agent, *, jwt_config):
            captured_agents.append(agent)
            return "a2a-executor"

        monkeypatch.setattr(executor_resolver, "make_a2a_executor", fake_make_a2a_executor)

        resolved = await executor_resolver._resolve_executor(
            "deep-intel",
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
        )

        assert resolved == "a2a-executor"
        assert len(captured_agents) == 1
        assert captured_agents[0].path == "/deep-intel"

    @pytest.mark.asyncio
    async def test_resolve_executor_raises_when_key_is_unknown(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=None))

        with pytest.raises(KeyError, match="executor_key 'unknown' not resolved"):
            await executor_resolver._resolve_executor(
                "unknown",
                llm=SimpleNamespace(),
                registry_url="https://registry.example.com",
                registry_token="token",
                jwt_config=_jwt_config(),
                accessible_agent_ids=None,
            )

    @pytest.mark.asyncio
    async def test_resolve_executor_raises_permission_error_when_agent_not_accessible(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        self._patch_beanie_filters(monkeypatch)
        agent = _a2a_agent("/deep-intel")
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=agent))
        monkeypatch.setattr(executor_resolver, "make_a2a_executor", lambda *args, **kwargs: "a2a-executor")

        with pytest.raises(PermissionError, match="user lacks access"):
            await executor_resolver._resolve_executor(
                "deep-intel",
                llm=SimpleNamespace(),
                registry_url="https://registry.example.com",
                registry_token="token",
                jwt_config=_jwt_config(),
                accessible_agent_ids=set(),  # explicitly empty: no access
            )

    @pytest.mark.asyncio
    async def test_resolve_executor_allows_accessible_a2a_agent(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        agent = _a2a_agent("/deep-intel")
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=agent))
        monkeypatch.setattr(executor_resolver, "make_a2a_executor", lambda *args, **kwargs: "a2a-executor")

        resolved = await executor_resolver._resolve_executor(
            "deep-intel",
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
            jwt_config=_jwt_config(),
            accessible_agent_ids={str(agent.id)},
        )

        assert resolved == "a2a-executor"


@pytest.mark.unit
class TestMcpExecutor:
    """Tests for mcp_executor.make_mcp_executor."""

    def test_make_mcp_executor_requires_registry_token(self):
        with pytest.raises(ValueError, match="registry_token is required"):
            mcp_exec.make_mcp_executor(
                _mcp_server("github"),
                llm=SimpleNamespace(),
                registry_url="https://registry.example.com",
                registry_token="",
            )

    @pytest.mark.asyncio
    async def test_make_mcp_executor_returns_step_output(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(arun=AsyncMock(return_value=SimpleNamespace(content="done")))

        monkeypatch.setattr(mcp_exec, "MCPTools", lambda *args, **kwargs: "mcp-tools")
        monkeypatch.setattr(mcp_exec, "Agent", lambda **kwargs: fake_agent_instance)

        executor = mcp_exec.make_mcp_executor(
            _mcp_server("github"),
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        output = await executor(SimpleNamespace(input="hello", previous_step_content="ctx"), {})

        assert output.success is True
        assert output.content == "done"
        fake_agent_instance.arun.assert_awaited_once()


@pytest.mark.unit
class TestA2AExecutor:
    """Tests for a2a_executor.make_a2a_executor and helpers."""

    def test_make_agent_jwt_calls_encode_with_correct_claims(self, monkeypatch: pytest.MonkeyPatch):
        built_payloads: list[dict] = []
        encoded_calls: list[tuple] = []

        def fake_build_payload(subject, issuer, audience, expires_in_seconds):
            built_payloads.append({"sub": subject, "iss": issuer, "aud": audience, "exp": expires_in_seconds})
            return {"sub": subject, "iss": issuer, "aud": audience}

        def fake_encode(payload, key, kid):
            encoded_calls.append((payload, key, kid))
            return "signed-jwt"

        monkeypatch.setattr(a2a_exec, "build_jwt_payload", fake_build_payload)
        monkeypatch.setattr(a2a_exec, "encode_jwt", fake_encode)

        token = a2a_exec.make_agent_jwt(
            agent_url="https://agent.example.com",
            jwt_config=_jwt_config(),
            expires_in_seconds=120,
        )

        assert token == "signed-jwt"
        assert built_payloads[0]["sub"] == "jarvis-workflow"
        assert built_payloads[0]["iss"] == "https://jarvis.example.com"
        assert built_payloads[0]["aud"] == "https://agent.example.com"
        assert built_payloads[0]["exp"] == 120
        _, key_used, kid_used = encoded_calls[0]
        assert key_used == "fake-pem"
        assert kid_used == "kid-v1"


@pytest.mark.unit
class TestHelpers:
    """Tests for shared workflow helper utilities."""

    def test_build_prompt_joins_previous_step_content_and_input(self):
        prompt = build_prompt(SimpleNamespace(previous_step_content="ctx", input="hello"))
        empty_prompt = build_prompt(SimpleNamespace(previous_step_content=None, input=""))

        assert prompt == "Context from previous step:\nctx\n\nhello"
        assert empty_prompt == "(no input)"
