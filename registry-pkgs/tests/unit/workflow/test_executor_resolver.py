from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2a.types import AgentCard
from agno.workflow import StepInput
from beanie import PydanticObjectId
from pydantic import HttpUrl

from registry_pkgs.core import agentcore_jwt
from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode, PermissionBits
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.federation import AgentCoreRuntimeAccessConfig, AgentCoreRuntimeJwtConfig
from registry_pkgs.workflows import a2a_client, executor_resolver
from registry_pkgs.workflows import a2a_executor as a2a_exec
from registry_pkgs.workflows.helpers import build_prompt


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
            enabled=True,
            type=transport,
        ),
        author=PydanticObjectId(),
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
            auth_context=None,
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
            auth_context=None,
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
            accessible_mcp_server_ids=None,
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
            auth_context=None,
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
            accessible_mcp_server_ids=None,
        )

        output = await resolved(StepInput(input="hello", previous_step_content="ctx"), {"echo_count": 0})

        assert output.success is True
        assert output.content == "hello"
        mcp_find_one.assert_not_awaited()
        a2a_find_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolve_executor_falls_back_to_a2a_agent(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        find_one = AsyncMock(return_value=_a2a_agent("deep-intel"))
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", find_one)
        captured_agents: list = []

        def fake_make_a2a_executor(agent, *, jwt_config, httpx_client=None, headers_provider=None):
            captured_agents.append(agent)
            return "a2a-executor"

        monkeypatch.setattr(executor_resolver, "make_a2a_executor", fake_make_a2a_executor)

        resolved = await executor_resolver._resolve_executor(
            "deep-intel",
            llm=SimpleNamespace(),
            auth_context=None,
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
            accessible_mcp_server_ids=None,
        )

        assert resolved == "a2a-executor"
        assert len(captured_agents) == 1
        assert captured_agents[0].path == "deep-intel"  # Path is now normalized (no slashes)
        find_one.assert_awaited_once_with(("path", "==", "deep-intel"), {"config.enabled": True})

    @pytest.mark.asyncio
    async def test_resolve_executor_raises_when_key_is_unknown(self, monkeypatch: pytest.MonkeyPatch):
        self._patch_beanie_filters(monkeypatch)
        monkeypatch.setattr(executor_resolver.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
        monkeypatch.setattr(executor_resolver.A2AAgent, "find_one", AsyncMock(return_value=None))

        with pytest.raises(KeyError, match="executor_key 'unknown' not resolved"):
            await executor_resolver._resolve_executor(
                "unknown",
                llm=SimpleNamespace(),
                auth_context=None,
                jwt_config=_jwt_config(),
                accessible_agent_ids=None,
                accessible_mcp_server_ids=None,
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
                auth_context=None,
                jwt_config=_jwt_config(),
                accessible_agent_ids=set(),  # explicitly empty: no access
                accessible_mcp_server_ids=None,
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
            auth_context=None,
            jwt_config=_jwt_config(),
            accessible_agent_ids={str(agent.id)},
            accessible_mcp_server_ids=None,
        )

        assert resolved == "a2a-executor"


@pytest.mark.unit
class TestMcpAcl:
    """ACL checks for MCP servers in executor_resolver."""

    @pytest.mark.asyncio
    async def test_resolve_executor_raises_permission_error_when_mcp_server_not_accessible(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from registry_pkgs.workflows import executor_resolver as er

        TestExecutorResolver._patch_beanie_filters(monkeypatch)
        server = _mcp_server("github")
        monkeypatch.setattr(er.ExtendedMCPServer, "find_one", AsyncMock(return_value=server))
        monkeypatch.setattr(er, "make_mcp_executor", lambda *args, **kwargs: "mcp-executor")

        with pytest.raises(PermissionError, match="user lacks access"):
            await er._resolve_executor(
                "github",
                llm=SimpleNamespace(),
                auth_context=None,
                jwt_config=_jwt_config(),
                accessible_agent_ids=None,
                accessible_mcp_server_ids=set(),
            )

    @pytest.mark.asyncio
    async def test_resolve_executor_allows_accessible_mcp_server(self, monkeypatch: pytest.MonkeyPatch):
        from registry_pkgs.workflows import executor_resolver as er

        TestExecutorResolver._patch_beanie_filters(monkeypatch)
        server = _mcp_server("github")
        monkeypatch.setattr(er.ExtendedMCPServer, "find_one", AsyncMock(return_value=server))
        monkeypatch.setattr(er, "make_mcp_executor", lambda *args, **kwargs: "mcp-executor")

        resolved = await er._resolve_executor(
            "github",
            llm=SimpleNamespace(),
            auth_context=None,
            jwt_config=_jwt_config(),
            accessible_agent_ids=None,
            accessible_mcp_server_ids={str(server.id)},
        )

        assert resolved == "mcp-executor"


@pytest.mark.unit
class TestA2AExecutor:
    """Tests for a2a_executor.make_a2a_executor and helpers."""

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

        monkeypatch.setattr(agentcore_jwt, "build_jwt_payload", fake_build_payload)
        monkeypatch.setattr(agentcore_jwt, "encode_jwt", lambda *args, **kwargs: "signed-jwt")

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

            step_input = StepInput(input="hello")
            with patch("registry_pkgs.workflows.a2a_executor.call_a2a", side_effect=fake_call_a2a):
                await executor(step_input)

            assert captured_kwargs.get("httpx_client") is shared
        finally:
            await shared.aclose()

    @pytest.mark.asyncio
    async def test_make_a2a_executor_preserves_message_files_and_data(self, monkeypatch: pytest.MonkeyPatch):
        from a2a.types import DataPart, FilePart, FileWithBytes, Message, Part, Role, TextPart

        from registry_pkgs.workflows.a2a_client import A2ACallResult
        from registry_pkgs.workflows.a2a_executor import make_a2a_executor

        msg = Message(
            kind="message",
            role=Role.agent,
            message_id="m",
            parts=[
                Part(root=TextPart(kind="text", text="done")),
                Part(
                    root=FilePart(
                        kind="file",
                        file=FileWithBytes(
                            bytes="aW1hZ2U=",
                            mimeType="image/jpeg",
                            name="result.jpg",
                        ),
                    )
                ),
                Part(
                    root=FilePart(
                        kind="file",
                        file=FileWithBytes(
                            bytes="dmlkZW8=",
                            mimeType="video/mp4",
                            name="clip.mp4",
                        ),
                    )
                ),
                Part(
                    root=FilePart(
                        kind="file",
                        file=FileWithBytes(
                            bytes="YXVkaW8=",
                            mimeType="audio/mpeg",
                            name="sound.mp3",
                        ),
                    )
                ),
                Part(root=DataPart(kind="data", data={"title": "Result"})),
            ],
        )

        async def fake_call_a2a(*args, **kwargs):
            return A2ACallResult(message=msg, success=True)

        monkeypatch.setattr(a2a_exec, "call_a2a", fake_call_a2a)

        output = await make_a2a_executor(_a2a_agent(), jwt_config=_jwt_config())(StepInput(input="hello"))

        assert output.content == "done"
        assert output.images and output.images[0].mime_type == "image/jpeg"
        assert output.videos and output.videos[0].mime_type == "video/mp4"
        assert output.audio and output.audio[0].mime_type == "audio/mpeg"
        assert output.files and output.files[0].mime_type == "application/json"
        assert '"title": "Result"' in output.files[0].content

    @pytest.mark.asyncio
    async def test_make_a2a_executor_preserves_task_artifact_files_and_data(self, monkeypatch: pytest.MonkeyPatch):
        from a2a.types import Artifact, DataPart, FilePart, FileWithBytes, Part, Task, TaskState, TaskStatus

        from registry_pkgs.workflows.a2a_client import A2ACallResult
        from registry_pkgs.workflows.a2a_executor import make_a2a_executor

        task = Task(
            id="task",
            context_id="ctx",
            kind="task",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="a1",
                    name="report",
                    parts=[
                        Part(root=DataPart(kind="data", data={"rows": 2})),
                        Part(
                            root=FilePart(
                                kind="file",
                                file=FileWithBytes(
                                    bytes="eyJvayI6IHRydWV9",
                                    mimeType="application/json",
                                    name="report.json",
                                ),
                            )
                        ),
                    ],
                )
            ],
        )

        async def fake_call_a2a(*args, **kwargs):
            return A2ACallResult(task=task, success=True)

        monkeypatch.setattr(a2a_exec, "call_a2a", fake_call_a2a)

        output = await make_a2a_executor(_a2a_agent(), jwt_config=_jwt_config())(StepInput(input="hello"))

        assert output.files and len(output.files) == 2
        data_file = next(f for f in output.files if f.filename == "report-data-1.json")
        assert '"rows": 2' in data_file.content
        report_file = next(f for f in output.files if f.filename == "report.json")
        assert report_file.mime_type == "application/json"

    @pytest.mark.asyncio
    async def test_make_a2a_executor_keeps_unsupported_file_mime_metadata(self, monkeypatch: pytest.MonkeyPatch):
        from a2a.types import FilePart, FileWithBytes, Message, Part, Role

        from registry_pkgs.workflows.a2a_client import A2ACallResult
        from registry_pkgs.workflows.a2a_executor import make_a2a_executor

        msg = Message(
            kind="message",
            role=Role.agent,
            message_id="m",
            parts=[
                Part(
                    root=FilePart(
                        kind="file",
                        file=FileWithBytes(
                            bytes="emlw",
                            mimeType="application/zip",
                            name="bundle.zip",
                        ),
                    )
                ),
            ],
        )

        async def fake_call_a2a(*args, **kwargs):
            return A2ACallResult(message=msg, success=True)

        monkeypatch.setattr(a2a_exec, "call_a2a", fake_call_a2a)

        output = await make_a2a_executor(_a2a_agent(), jwt_config=_jwt_config())(StepInput(input="hello"))

        assert output.files and output.files[0].filename == "bundle.zip"
        assert output.files[0].mime_type is None
        assert output.files[0].file_type == "application/zip"


@pytest.mark.unit
class TestHelpers:
    """Tests for shared workflow helper utilities."""

    def test_build_prompt_falls_back_to_raw_input_without_intention_data(self):
        prompt = build_prompt(StepInput(previous_step_content="ctx", input="hello"))
        empty_prompt = build_prompt(StepInput(previous_step_content=None, input=""))

        assert prompt == "hello"
        assert empty_prompt == "(no input)"


@pytest.mark.unit
class TestLoadAccessibleIds:
    """Tests for ACL loading helpers."""

    @pytest.mark.asyncio
    async def test_load_accessible_agent_ids_filters_by_view_permission(self, monkeypatch: pytest.MonkeyPatch):
        from beanie import PydanticObjectId

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

        monkeypatch.setattr(RegistryAclEntry, "find", fake_find)
        monkeypatch.setattr(executor_resolver, "resolve_group_ids_for_user", AsyncMock(return_value=[]))

        result = await executor_resolver._load_accessible_agent_ids(str(PydanticObjectId()))
        assert result == {str(rid1), str(rid3)}

    @pytest.mark.asyncio
    async def test_load_accessible_mcp_server_ids_filters_by_view_permission(self, monkeypatch: pytest.MonkeyPatch):
        public_entry = RegistryAclEntry.model_construct(
            resourceType="mcpServer",
            principalType="public",
            principalId=None,
            resourceId=PydanticObjectId(),
            permBits=PermissionBits.VIEW,
        )
        no_view_entry = RegistryAclEntry.model_construct(
            resourceType="mcpServer",
            principalType="user",
            principalId=PydanticObjectId("666666666666666666666666"),
            resourceId=PydanticObjectId(),
            permBits=0,
        )

        find_query = SimpleNamespace(to_list=AsyncMock(return_value=[public_entry, no_view_entry]))
        monkeypatch.setattr(executor_resolver.RegistryAclEntry, "find", lambda *args, **kwargs: find_query)
        monkeypatch.setattr(executor_resolver, "resolve_group_ids_for_user", AsyncMock(return_value=[]))

        result = await executor_resolver._load_accessible_mcp_server_ids("666666666666666666666666")
        assert result == {str(public_entry.resourceId)}

    @pytest.mark.asyncio
    async def test_load_accessible_ids_includes_current_group_principals(self, monkeypatch: pytest.MonkeyPatch):
        group_id = PydanticObjectId()
        resource_id = PydanticObjectId()
        entry = SimpleNamespace(permBits=PermissionBits.VIEW, resourceId=resource_id)
        captured_query: dict = {}

        monkeypatch.setattr(executor_resolver, "resolve_group_ids_for_user", AsyncMock(return_value=[group_id]))

        def fake_acl_find(query):
            captured_query.update(query)
            return SimpleNamespace(to_list=AsyncMock(return_value=[entry]))

        monkeypatch.setattr(executor_resolver.RegistryAclEntry, "find", fake_acl_find)

        result = await executor_resolver._load_accessible_mcp_server_ids("666666666666666666666666")

        assert result == {str(resource_id)}
        assert {
            "principalType": "group",
            "principalId": {"$in": [group_id]},
        } in captured_query["$or"]

    @pytest.mark.asyncio
    async def test_build_executor_registry_passes_both_acl_sets_to_resolver(self, monkeypatch: pytest.MonkeyPatch):
        loaded: dict = {}

        async def fake_resolve(key: str, **kwargs):
            loaded["accessible_agent_ids"] = kwargs.get("accessible_agent_ids")
            loaded["accessible_mcp_server_ids"] = kwargs.get("accessible_mcp_server_ids")
            return f"executor:{key}"

        monkeypatch.setattr(executor_resolver, "_resolve_executor", fake_resolve)

        async def fake_load_agent_ids(user_id: str) -> set[str]:
            loaded["agent_user_id"] = user_id
            return {"agent-1"}

        async def fake_load_mcp_ids(user_id: str) -> set[str]:
            loaded["mcp_user_id"] = user_id
            return {"server-1"}

        monkeypatch.setattr(executor_resolver, "_load_accessible_agent_ids", fake_load_agent_ids)
        monkeypatch.setattr(executor_resolver, "_load_accessible_mcp_server_ids", fake_load_mcp_ids)

        await executor_resolver.build_executor_registry(
            ["alpha"],
            llm=SimpleNamespace(),
            auth_context=None,
            jwt_config=_jwt_config(),
            user_id="666666666666666666666666",
        )

        assert loaded["agent_user_id"] == "666666666666666666666666"
        assert loaded["mcp_user_id"] == "666666666666666666666666"
        assert loaded["accessible_agent_ids"] == {"agent-1"}
        assert loaded["accessible_mcp_server_ids"] == {"server-1"}


@pytest.mark.unit
class TestA2APoolExecutorQueries:
    """Ensure make_a2a_pool_executor queries use config.enabled."""

    @pytest.mark.asyncio
    async def test_pool_initial_selection_queries_by_config_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """Initial pool query must filter on config.enabled=True."""
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

        result = await executor(StepInput(input="hello"), {})

        assert result.success is False  # no agents found → pool resolution failed
        assert len(captured_queries) == 1, "expected exactly one find() call"
        query = captured_queries[0]
        assert "status" not in query, f"status filter found in pool query: {query}"
        assert query.get("config.enabled") is True, f"config.enabled=True not in pool query: {query}"
        assert query.get("path") == {"$in": ["agent-a", "agent-b"]}

    @pytest.mark.asyncio
    async def test_pool_retry_path_queries_by_config_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """Retry path (selected_path already cached) must filter on config.enabled=True."""
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
        result = await executor(StepInput(input="hello"), state)

        assert result.success is False
        assert "not found or disabled" in result.error
        assert len(captured_args) >= 1
        args_str = str(captured_args)
        assert "status" not in args_str, f"status filter found in retry query: {captured_args}"
        assert "config.enabled" in args_str, f"config.enabled not in retry query: {captured_args}"
        assert captured_args[0] == {"path": "agent-a", "config.enabled": True}
