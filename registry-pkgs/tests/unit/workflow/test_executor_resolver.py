from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.types import AgentCard
from beanie import PydanticObjectId
from pydantic import HttpUrl

from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.workflows import executor_resolver


def _mcp_server(name: str = "github") -> ExtendedMCPServer:
    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName=name,
        config={"description": "server description"},
        author=PydanticObjectId(),
        status="active",
    )


def _a2a_agent(path: str = "/deep-intel") -> A2AAgent:
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
            url=HttpUrl("https://config.example.com/agent"),
            type="jsonrpc",
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
    @staticmethod
    def _patch_beanie_filters(monkeypatch: pytest.MonkeyPatch) -> None:
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
        monkeypatch.setattr(executor_resolver, "_make_mcp_executor", lambda *args, **kwargs: "mcp-executor")
        monkeypatch.setattr(executor_resolver, "_make_a2a_executor", lambda *args, **kwargs: "a2a-executor")

        resolved = await executor_resolver._resolve_executor(
            "github",
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        assert resolved == "mcp-executor"

    @pytest.mark.asyncio
    async def test_resolve_executor_falls_back_to_a2a_agent(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=_a2a_agent("/deep-intel")))
        captured = {}

        def fake_make_a2a_executor(*args, **kwargs):
            captured.update(kwargs)
            return "a2a-executor"

        monkeypatch.setattr(executor_resolver, "_make_a2a_executor", fake_make_a2a_executor)

        resolved = await executor_resolver._resolve_executor(
            "deep-intel",
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        assert resolved == "a2a-executor"
        assert captured == {"registry_url": "https://registry.example.com", "registry_token": "token"}

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
            )

    def test_make_mcp_executor_requires_registry_token(self):
        with pytest.raises(ValueError, match="registry_token is required"):
            executor_resolver._make_mcp_executor(
                _mcp_server("github"),
                llm=SimpleNamespace(),
                registry_url="https://registry.example.com",
                registry_token="",
            )

    @pytest.mark.asyncio
    async def test_make_mcp_executor_returns_step_output(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(arun=AsyncMock(return_value=SimpleNamespace(content="done")))

        monkeypatch.setattr(executor_resolver, "MCPTools", lambda *args, **kwargs: "mcp-tools")
        monkeypatch.setattr(executor_resolver, "Agent", lambda **kwargs: fake_agent_instance)

        executor = executor_resolver._make_mcp_executor(
            _mcp_server("github"),
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        output = await executor(SimpleNamespace(input="hello", previous_step_content="ctx"), {})

        assert output.success is True
        assert output.content == "done"
        fake_agent_instance.arun.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_make_a2a_executor_uses_registry_proxy_and_wraps_errors(self, monkeypatch: pytest.MonkeyPatch):
        a2a_send = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(executor_resolver, "_a2a_send", a2a_send)
        executor = executor_resolver._make_a2a_executor(
            _a2a_agent("/deep-intel"),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        output = await executor(SimpleNamespace(input="hello", previous_step_content=None), {})

        assert output.success is False
        assert output.error == "boom"
        assert output.content == "boom"
        assert executor.__name__ == "deep-intel_a2a_executor"
        a2a_send.assert_awaited_once_with(
            "https://registry.example.com/proxy/a2a/deep-intel",
            "hello",
            registry_token="token",
            protocol_version="0.3",
        )

    def test_make_a2a_executor_requires_registry_token(self):
        with pytest.raises(ValueError, match="registry_token is required"):
            executor_resolver._make_a2a_executor(
                _a2a_agent("/deep-intel"),
                registry_url="https://registry.example.com",
                registry_token="",
            )

    @pytest.mark.asyncio
    async def test_a2a_send_uses_agno_client_with_version_header(self, monkeypatch: pytest.MonkeyPatch):
        captured = {}

        class FakeClient:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs

            async def send_message(self, message: str, *, headers: dict | None = None):
                captured["message"] = message
                captured["headers"] = headers
                return SimpleNamespace(content="agent response")

        monkeypatch.setattr(executor_resolver, "A2AClient", FakeClient)

        text = await executor_resolver._a2a_send(
            "https://registry.example.com/proxy/a2a/deep-intel",
            "ping",
            registry_token="token",
            protocol_version="1.0",
        )

        assert text == "agent response"
        assert captured["client_kwargs"] == {
            "base_url": "https://registry.example.com/proxy/a2a/deep-intel",
            "timeout": 300,
            "protocol": "json-rpc",
        }
        assert captured["message"] == "ping"
        assert captured["headers"] == {"Authorization": "Bearer token", "A2A-Version": "1.0"}

    def test_a2a_protocol_version_uses_major_minor_from_agent_card(self):
        agent = _a2a_agent()

        assert executor_resolver._a2a_protocol_version(agent) == "0.3"

    def test_build_prompt_joins_previous_step_content_and_input(self):
        prompt = executor_resolver._build_prompt(SimpleNamespace(previous_step_content="ctx", input="hello"))
        empty_prompt = executor_resolver._build_prompt(SimpleNamespace(previous_step_content=None, input=""))

        assert prompt == "Context from previous step:\nctx\n\nhello"
        assert empty_prompt == "(no input)"
