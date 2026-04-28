from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows import runner


class _FieldExpr:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return (self.name, "==", other)


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_construct(
        id=PydanticObjectId(),
        name="demo-workflow",
        nodes=[WorkflowNode(name="fetch", executor_key="tool")],
    )


def _run() -> WorkflowRun:
    return WorkflowRun.model_construct(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.RUNNING,
        finished_at=None,
    )


def _make_runner(**kwargs) -> runner.WorkflowRunner:
    """Return a WorkflowRunner with sensible test defaults."""
    from registry_pkgs.core.config import JwtSigningConfig

    defaults = {
        "llm": object(),
        "registry_url": "http://localhost:7860",
        "db_client": object(),
        "db_name": "jarvis",
        "jwt_config": JwtSigningConfig(
            jwt_private_key="fake-pem",
            jwt_issuer="https://jarvis.example.com",
            jwt_self_signed_kid="kid-v1",
            jwt_audience="jarvis-services",
        ),
    }
    defaults.update(kwargs)
    return runner.WorkflowRunner(**defaults)


@pytest.mark.unit
class TestWorkflowRunnerInit:
    def test_requires_db_client(self):
        with pytest.raises(ValueError, match="db_client"):
            _make_runner(db_client=None)

    def test_requires_db_name(self):
        with pytest.raises(ValueError, match="db_name"):
            _make_runner(db_name="")

    def test_selector_llm_defaults_to_none(self):
        r = _make_runner()
        # selector_llm=None is valid; build_executor_registry falls back to llm.
        assert r._selector_llm is None


@pytest.mark.unit
class TestWorkflowRunnerRun:
    @pytest.mark.asyncio
    async def test_raises_when_definition_not_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=None))
        r = _make_runner()

        with pytest.raises(ValueError, match="not found"):
            await r.run("missing-id", "hello", registry_token="tok", accessible_agent_ids=None)

    @pytest.mark.asyncio
    async def test_orchestrates_build_registry_create_execute_and_returns_node_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """run() must call _build_registry → _create_run → _execute in order."""
        definition = _definition()
        run_doc = _run()
        node_runs = [SimpleNamespace(node_name="fetch")]
        fake_registry = {"tool": object()}

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value=fake_registry))
        monkeypatch.setattr(runner.WorkflowRunner, "_create_run", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        accessible = {"agent-id-1"}
        actual_run, actual_nodes = await r.run(
            str(definition.id),
            "hello",
            registry_token="user-tok",
            accessible_agent_ids=accessible,
            trigger_source="api",
        )

        assert actual_run is run_doc
        assert actual_nodes == node_runs
        # registry_token + accessible_agent_ids are forwarded to _build_registry
        runner.WorkflowRunner._build_registry.assert_awaited_once_with(definition, "user-tok", accessible)
        runner.WorkflowRunner._create_run.assert_awaited_once_with(definition, "hello", "api")
        runner.WorkflowRunner._execute.assert_awaited_once_with(run_doc, definition, "hello", fake_registry)


@pytest.mark.unit
class TestBuildRegistry:
    @pytest.mark.asyncio
    async def test_extracts_keys_and_pool_nodes_from_definition(self, monkeypatch: pytest.MonkeyPatch):
        """_build_registry must forward executor_keys + pool_nodes to build_executor_registry."""
        from registry_pkgs.models.workflow import WorkflowNode

        definition = WorkflowDefinition.model_construct(
            id=PydanticObjectId(),
            name="test",
            nodes=[
                WorkflowNode(name="mcp-step", executor_key="mcp-tool"),
                WorkflowNode(name="pool-step", a2a_pool=["agent-a", "agent-b"]),
            ],
        )

        captured = {}

        async def fake_build(
            executor_keys,
            *,
            llm,
            registry_url,
            registry_token,
            jwt_config,
            accessible_agent_ids,
            pool_nodes,
            selector_llm,
        ):
            captured["executor_keys"] = executor_keys
            captured["pool_nodes"] = [n.name for n in pool_nodes]
            captured["registry_token"] = registry_token
            captured["accessible_agent_ids"] = accessible_agent_ids
            return {}

        monkeypatch.setattr(runner, "build_executor_registry", fake_build)

        r = _make_runner(registry_url="http://reg")
        await r._build_registry(definition, "my-token", {"agent-id-1"})

        assert captured["executor_keys"] == ["mcp-tool"]
        assert captured["pool_nodes"] == ["pool-step"]
        assert captured["registry_token"] == "my-token"
        assert captured["accessible_agent_ids"] == {"agent-id-1"}


@pytest.mark.unit
class TestCreateRun:
    @pytest.mark.asyncio
    async def test_inserts_running_workflow_run_with_snapshot(self, monkeypatch: pytest.MonkeyPatch):
        inserted = []

        class FakeWorkflowRun:
            def __init__(self, **kwargs):
                self.id = PydanticObjectId()
                self.__dict__.update(kwargs)

            async def insert(self):
                inserted.append(self)

        monkeypatch.setattr(runner, "WorkflowRun", FakeWorkflowRun)
        r = _make_runner()
        definition = _definition()

        run_doc = await r._create_run(definition, "hello", "script")

        assert inserted[0] is run_doc
        assert run_doc.workflow_definition_id == definition.id
        assert run_doc.status == WorkflowRunStatus.RUNNING
        assert run_doc.trigger_source == "script"
        assert run_doc.initial_input == {"user_text": "hello"}
        assert run_doc.definition_snapshot["name"] == definition.name


@pytest.mark.unit
class TestExecute:
    @pytest.mark.asyncio
    async def test_compiles_workflow_and_syncs_run_after_success(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock())
        run_doc = SimpleNamespace(sync=AsyncMock(), id="run-1")
        fake_registry = {"tool": object()}

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        await r._execute(run_doc, _definition(), "hello", fake_registry)

        workflow.arun.assert_awaited_once_with(
            input="hello",
            session_state={"user_text": "hello", "_workflow_run_id": "run-1"},
        )
        run_doc.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marks_run_failed_and_reraises_when_workflow_raises(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock(side_effect=RuntimeError("boom")))
        run_doc = SimpleNamespace(
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            finished_at=None,
            save=AsyncMock(),
            id="run-1",
        )

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        with pytest.raises(RuntimeError, match="boom"):
            await r._execute(run_doc, _definition(), "hello", {})

        assert run_doc.status == WorkflowRunStatus.FAILED
        assert run_doc.error_summary == "boom"
        assert isinstance(run_doc.finished_at, datetime)
        assert run_doc.finished_at.tzinfo == UTC
        run_doc.save.assert_awaited_once()
