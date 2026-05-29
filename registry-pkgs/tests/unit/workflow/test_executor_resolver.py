from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.types import AgentCard
from beanie import PydanticObjectId
from pydantic import HttpUrl

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.federation import AgentCoreRuntimeAccessConfig, AgentCoreRuntimeJwtConfig
from registry_pkgs.workflows import a2a_client, executor_resolver
from registry_pkgs.workflows import a2a_executor as a2a_exec
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
    path: str = "deep-intel",  # Updated: path is now in slug format (no slashes)
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
        monkeypatch.setattr(executor_resolver.A2AAgent, "path", _FieldExpr("path"), raising=False)

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
            user_id=None,
        )

        assert seen == ["alpha", "beta"]
        assert registry == {"alpha": "executor:alpha", "beta": "executor:beta"}

    @pytest.mark.asyncio
    async def test_resolve_executor_prefers_active_mcp_server(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        monkeypatch.setattr(
            executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=_mcp_server("github"))
        )
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=_a2a_agent("github")))
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
    async def test_resolve_executor_supports_builtin_echo_without_db_lookup(self, monkeypatch: pytest.MonkeyPatch):
        mcp_find_one = AsyncMock()
        a2a_find_one = AsyncMock()
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", mcp_find_one)
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", a2a_find_one)

        resolved = await executor_resolver._resolve_executor(
            "echo",
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
        )

        output = await resolved(SimpleNamespace(input="hello", previous_step_content="ctx"), {"echo_count": 0})

        assert output.success is True
        assert output.content == "Context from previous step:\nctx\n\nhello"
        mcp_find_one.assert_not_awaited()
        a2a_find_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolve_executor_falls_back_to_a2a_agent(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        find_one = AsyncMock(return_value=_a2a_agent("deep-intel"))
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", find_one)
        captured_agents: list = []

        def fake_make_a2a_executor(agent, *, jwt_config, httpx_client=None):
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
        assert captured_agents[0].path == "deep-intel"  # Path is now normalized (no slashes)
        find_one.assert_awaited_once_with(("path", "==", "deep-intel"), {"isEnabled": True})

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
        agent = _a2a_agent("deep-intel")
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
        agent = _a2a_agent("deep-intel")
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
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())

        monkeypatch.setattr(mcp_exec, "MCPTools", lambda *args, **kwargs: fake_mcp_tools)
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
        fake_mcp_tools.connect.assert_awaited_once_with(force=False)
        fake_agent_instance.arun.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_make_mcp_executor_reraises_agent_failures(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(arun=AsyncMock(side_effect=RuntimeError("init failed")))
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())

        monkeypatch.setattr(mcp_exec, "MCPTools", lambda *args, **kwargs: fake_mcp_tools)
        monkeypatch.setattr(mcp_exec, "Agent", lambda **kwargs: fake_agent_instance)

        executor = mcp_exec.make_mcp_executor(
            _mcp_server("github"),
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        with pytest.raises(RuntimeError, match="MCP executor 'github' failed: init failed"):
            await executor(SimpleNamespace(input="hello", previous_step_content="ctx"), {})

    @pytest.mark.asyncio
    async def test_make_mcp_executor_raises_when_agent_returns_error_status(self, monkeypatch: pytest.MonkeyPatch):
        fake_agent_instance = SimpleNamespace(
            arun=AsyncMock(return_value=SimpleNamespace(content="Unable to locate credentials", status="error"))
        )
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())

        monkeypatch.setattr(mcp_exec, "MCPTools", lambda *args, **kwargs: fake_mcp_tools)
        monkeypatch.setattr(mcp_exec, "Agent", lambda **kwargs: fake_agent_instance)

        executor = mcp_exec.make_mcp_executor(
            _mcp_server("github"),
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        with pytest.raises(RuntimeError, match="Unable to locate credentials"):
            await executor(SimpleNamespace(input="hello", previous_step_content="ctx"), {})

    @pytest.mark.asyncio
    async def test_make_mcp_executor_raises_when_agent_returns_credential_error_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        fake_agent_instance = SimpleNamespace(
            arun=AsyncMock(return_value=SimpleNamespace(content="Unable to locate credentials", status="completed"))
        )
        fake_mcp_tools = SimpleNamespace(initialized=True, connect=AsyncMock())

        monkeypatch.setattr(mcp_exec, "MCPTools", lambda *args, **kwargs: fake_mcp_tools)
        monkeypatch.setattr(mcp_exec, "Agent", lambda **kwargs: fake_agent_instance)

        executor = mcp_exec.make_mcp_executor(
            _mcp_server("github"),
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
        )

        with pytest.raises(RuntimeError, match="Unable to locate credentials"):
            await executor(SimpleNamespace(input="hello", previous_step_content="ctx"), {})


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

        monkeypatch.setattr(a2a_client, "build_jwt_payload", fake_build_payload)
        monkeypatch.setattr(a2a_client, "encode_jwt", fake_encode)

        token = a2a_client.make_agent_jwt(
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

    def test_make_agentcore_jwt_strips_whitespace_in_config_claims(self, monkeypatch: pytest.MonkeyPatch):
        built_payloads: list[dict] = []

        def fake_build_payload(subject, issuer, audience, expires_in_seconds, extra_claims=None):
            built_payloads.append(
                {
                    "sub": subject,
                    "iss": issuer,
                    "aud": audience,
                    "exp": expires_in_seconds,
                    "extra_claims": extra_claims,
                }
            )
            return {"sub": subject}

        monkeypatch.setattr(a2a_client, "build_jwt_payload", fake_build_payload)
        monkeypatch.setattr(a2a_client, "encode_jwt", lambda *args, **kwargs: "signed-jwt")

        agent = _a2a_agent()
        agent.config.runtimeAccess = AgentCoreRuntimeAccessConfig(
            mode=AgentCoreRuntimeAccessMode.JWT,
            jwt=AgentCoreRuntimeJwtConfig(
                discoveryUrl="https://issuer.example.com/.well-known/openid-configuration",
                audiences=[" jarvis-agentcore "],
                allowedClients=[" Deep Intel "],
                allowedScopes=[" workflows.read ", " workflows.write "],
                customClaims={"agent_name": " Deep Intel "},
            ),
        )

        token = a2a_client._make_agentcore_jwt(agent, jwt_config=_jwt_config(), expires_in_seconds=90)

        assert token == "signed-jwt"
        payload = built_payloads[0]
        assert payload["iss"] == "https://issuer.example.com"
        assert payload["aud"] == "jarvis-agentcore"
        assert payload["exp"] == 90
        assert payload["extra_claims"] == {
            "client_id": "Deep Intel",
            "scope": "workflows.read workflows.write",
            "agent_name": "Deep Intel",
        }

    @pytest.mark.asyncio
    async def test_make_a2a_executor_passes_httpx_client_to_call_a2a(self):
        """The closure returned by make_a2a_executor must forward its captured
        httpx_client to call_a2a so the workflow step reuses the shared pool."""
        from unittest.mock import patch

        import httpx

        from registry_pkgs.workflows.a2a_client import A2ACallResult
        from registry_pkgs.workflows.a2a_executor import make_a2a_executor

        agent = _a2a_agent()
        shared = httpx.AsyncClient()
        try:
            executor = make_a2a_executor(
                agent,
                jwt_config=_jwt_config(),
                httpx_client=shared,
            )
            captured_kwargs: dict = {}

            async def fake_call_a2a(agent_obj, text, **kwargs):
                captured_kwargs.update(kwargs)
                return A2ACallResult(success=True)

            step_input = SimpleNamespace(previous_step_content=None, input="hello")
            with patch("registry_pkgs.workflows.a2a_executor.call_a2a", side_effect=fake_call_a2a):
                await executor(step_input)

            assert captured_kwargs.get("httpx_client") is shared
        finally:
            await shared.aclose()


@pytest.mark.unit
class TestHelpers:
    """Tests for shared workflow helper utilities."""

    def test_build_prompt_joins_previous_step_content_and_input(self):
        prompt = build_prompt(SimpleNamespace(previous_step_content="ctx", input="hello"))
        empty_prompt = build_prompt(SimpleNamespace(previous_step_content=None, input=""))

        assert prompt == "Context from previous step:\nctx\n\nhello"
        assert empty_prompt == "(no input)"


@pytest.mark.unit
class TestLoadAccessibleAgentIds:
    """Tests for _load_accessible_agent_ids ACL helper."""

    @pytest.mark.asyncio
    async def test_returns_agent_ids_with_view_permission(self, monkeypatch: pytest.MonkeyPatch):
        from beanie import PydanticObjectId

        from registry_pkgs.models.enums import PermissionBits
        from registry_pkgs.models.extended_acl_entry import ExtendedAclEntry

        rid1 = PydanticObjectId()
        rid2 = PydanticObjectId()
        rid3 = PydanticObjectId()

        entry1 = SimpleNamespace(permBits=PermissionBits.VIEW, resourceId=rid1)
        entry2 = SimpleNamespace(permBits=PermissionBits.EDIT, resourceId=rid2)  # no VIEW
        entry3 = SimpleNamespace(permBits=PermissionBits.VIEW, resourceId=rid3)

        def fake_find(query):
            class FakeQuery:
                async def to_list(self):
                    return [entry1, entry2, entry3]

            return FakeQuery()

        monkeypatch.setattr(ExtendedAclEntry, "find", fake_find)

        result = await executor_resolver._load_accessible_agent_ids(str(PydanticObjectId()))
        assert result == {str(rid1), str(rid3)}

    @pytest.mark.asyncio
    async def test_returns_empty_set_when_no_entries(self, monkeypatch: pytest.MonkeyPatch):
        from registry_pkgs.models.extended_acl_entry import ExtendedAclEntry

        def fake_find(query):
            class FakeQuery:
                async def to_list(self):
                    return []

            return FakeQuery()

        monkeypatch.setattr(ExtendedAclEntry, "find", fake_find)

        result = await executor_resolver._load_accessible_agent_ids(str(PydanticObjectId()))
        assert result == set()

    @pytest.mark.asyncio
    async def test_build_executor_registry_passes_acl_set_to_resolver(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_resolve(key: str, **kwargs):
            return kwargs.get("accessible_agent_ids")

        monkeypatch.setattr(executor_resolver, "_resolve_executor", fake_resolve)

        async def fake_load_acl(user_id: str) -> set[str]:
            return {"agent-1"}

        monkeypatch.setattr(executor_resolver, "_load_accessible_agent_ids", fake_load_acl)

        registry = await executor_resolver.build_executor_registry(
            ["alpha"],
            llm=SimpleNamespace(),
            registry_url="https://registry.example.com",
            registry_token="token",
            jwt_config=_jwt_config(),
            user_id="user-123",
        )

        assert registry == {"alpha": {"agent-1"}}


@pytest.mark.unit
class TestA2APoolExecutorQueries:
    """Ensure make_a2a_pool_executor queries use isEnabled, not status."""

    @pytest.mark.asyncio
    async def test_pool_initial_selection_queries_by_is_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """Initial pool query must filter on isEnabled=True, not status='active'."""
        captured_queries: list = []

        async def fake_to_list():
            return []

        class FakeFind:
            def __init__(self, query):
                captured_queries.append(query)

            def to_list(self):
                return fake_to_list()

        # Patch Agent so make_a2a_pool_executor doesn't validate the model arg
        monkeypatch.setattr(a2a_exec, "Agent", lambda **kwargs: SimpleNamespace())
        monkeypatch.setattr(a2a_exec.A2AAgent, "find", FakeFind)

        executor = a2a_exec.make_a2a_pool_executor(
            node_name="test-pool",
            pool_keys=["agent-a", "agent-b"],
            selector_llm=SimpleNamespace(),
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
        )

        result = await executor(SimpleNamespace(input="hello", previous_step_content=None), {})

        assert result.success is False  # no agents found → pool resolution failed
        assert len(captured_queries) == 1, "expected exactly one find() call"
        query = captured_queries[0]
        assert "status" not in query, f"status filter found in pool query: {query}"
        assert query.get("isEnabled") is True, f"isEnabled=True not in pool query: {query}"
        assert query.get("path") == {"$in": ["agent-a", "agent-b"]}

    @pytest.mark.asyncio
    async def test_pool_retry_path_queries_by_is_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """Retry path (selected_path already cached) must filter on isEnabled=True."""
        captured_args: list = []

        async def fake_find_one(*args, **kwargs):
            captured_args.extend(args)
            return None  # agent gone → triggers error StepOutput

        # Patch Agent so make_a2a_pool_executor doesn't validate the model arg
        monkeypatch.setattr(a2a_exec, "Agent", lambda **kwargs: SimpleNamespace())
        monkeypatch.setattr(a2a_exec.A2AAgent, "find_one", fake_find_one)

        executor = a2a_exec.make_a2a_pool_executor(
            node_name="retry-pool",
            pool_keys=["agent-a"],
            selector_llm=SimpleNamespace(),
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
        )

        # Pre-fill cache to simulate retry path
        state = {"a2a_target_retry-pool": "agent-a"}
        result = await executor(SimpleNamespace(input="hello", previous_step_content=None), state)

        assert result.success is False
        assert "not found or disabled" in result.error
        assert len(captured_args) >= 1
        args_str = str(captured_args)
        assert "status" not in args_str, f"status filter found in retry query: {captured_args}"
        assert "isEnabled" in args_str, f"isEnabled not in retry query: {captured_args}"
        assert captured_args[0] == {"path": "agent-a", "isEnabled": True}
