from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agno.run.base import RunStatus
from agno.run.workflow import WorkflowRunOutput
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows import runner
from registry_pkgs.workflows.control.wrapper import WorkflowCancelledError


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
            await r.run("missing-id", "hello", registry_token="tok", user_id=None, existing_run_id="any-id")

    @pytest.mark.asyncio
    async def test_orchestrates_build_registry_execute_and_returns_node_runs(self, monkeypatch: pytest.MonkeyPatch):
        """run() must load existing run, call _build_registry → _execute in order."""
        definition = _definition()
        run_doc = SimpleNamespace(
            id=PydanticObjectId(),
            status=WorkflowRunStatus.PENDING,
            definition_snapshot=None,
            save=AsyncMock(),
        )
        node_runs = [SimpleNamespace(node_name="fetch")]
        fake_registry = {"tool": object()}

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value=fake_registry))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        user_id = "user-1"
        run_id = str(run_doc.id)
        actual_run, actual_nodes = await r.run(
            str(definition.id),
            "hello",
            registry_token="user-tok",
            user_id=user_id,
            existing_run_id=run_id,
        )

        assert actual_run is run_doc
        assert actual_nodes == node_runs
        runner.WorkflowRunner._build_registry.assert_awaited_once_with(definition, "user-tok", user_id)
        runner.WorkflowRunner._execute.assert_awaited_once_with(run_doc, definition, "hello", fake_registry, None)

    @pytest.mark.asyncio
    async def test_run_existing_executes_existing_run_without_creating_new_run(self, monkeypatch: pytest.MonkeyPatch):
        definition = _definition()
        existing_run = SimpleNamespace(
            id=PydanticObjectId(),
            workflow_definition_id=definition.id,
            status=WorkflowRunStatus.PENDING,
            error_summary="old error",
            finished_at=datetime.now(UTC),
            initial_input=None,
            definition_snapshot=None,
            save=AsyncMock(),
        )
        node_runs = [SimpleNamespace(node_name="fetch")]
        fake_registry = {"tool": object()}

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value=fake_registry))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())
        monkeypatch.setattr(
            runner.WorkflowRunner, "_create_run", AsyncMock(side_effect=AssertionError("should not create"))
        )

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        actual_run, actual_nodes = await r.run_existing(
            existing_run,
            "hello",
            registry_token="user-tok",
            accessible_agent_ids={"agent-id-1"},
        )

        assert actual_run is existing_run
        assert actual_nodes == node_runs
        assert existing_run.status == WorkflowRunStatus.RUNNING
        assert existing_run.error_summary is None
        assert existing_run.finished_at is None
        assert existing_run.initial_input == {"user_text": "hello"}
        assert existing_run.definition_snapshot["name"] == definition.name
        existing_run.save.assert_awaited_once()
        runner.WorkflowRunner._build_registry.assert_awaited_once_with(
            definition,
            "user-tok",
            {"agent-id-1"},
            registry_url=None,
        )
        runner.WorkflowRunner._execute.assert_awaited_once_with(existing_run, definition, "hello", fake_registry)
        runner.WorkflowRunner._create_run.assert_not_awaited()


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
            user_id,
            pool_nodes,
            selector_llm,
        ):
            captured["executor_keys"] = executor_keys
            captured["pool_nodes"] = [n.name for n in pool_nodes]
            captured["registry_token"] = registry_token
            captured["user_id"] = user_id
            return {}

        monkeypatch.setattr(runner, "build_executor_registry", fake_build)

        r = _make_runner(registry_url="http://reg")
        await r._build_registry(definition, "my-token", "user-1")

        assert captured["executor_keys"] == ["mcp-tool"]
        assert captured["pool_nodes"] == ["pool-step"]
        assert captured["registry_token"] == "my-token"
        assert captured["user_id"] == "user-1"


@pytest.mark.unit
class TestRunSetsRunningStatus:
    @pytest.mark.asyncio
    async def test_transitions_pending_run_to_running_and_stamps_snapshot(self, monkeypatch: pytest.MonkeyPatch):
        """run() must set status=RUNNING and definition_snapshot before executing."""
        definition = _definition()
        run_doc = SimpleNamespace(
            id=PydanticObjectId(),
            status=WorkflowRunStatus.PENDING,
            definition_snapshot=None,
            save=AsyncMock(),
        )

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRun, "get", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_build_registry", AsyncMock(return_value={}))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())
        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=[]))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        r = _make_runner()
        await r.run(str(definition.id), "hello", registry_token="tok", user_id=None, existing_run_id=str(run_doc.id))

        assert run_doc.status == WorkflowRunStatus.RUNNING
        assert run_doc.definition_snapshot["name"] == definition.name
        run_doc.save.assert_awaited_once()


@pytest.mark.unit
class TestExecute:
    @pytest.mark.asyncio
    async def test_compiles_workflow_and_syncs_run_after_success(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(
            arun=AsyncMock(return_value=WorkflowRunOutput(content="done", status=RunStatus.completed, step_results=[]))
        )
        run_doc = SimpleNamespace(
            sync=AsyncMock(),
            save=AsyncMock(),
            id="run-1",
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            final_output=None,
            finished_at=None,
        )
        fake_registry = {"tool": object()}

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        await r._execute(run_doc, _definition(), "hello", fake_registry)

        workflow.arun.assert_awaited_once_with(
            input="hello",
            session_state={"user_text": "hello", "_workflow_run_id": "run-1"},
        )
        run_doc.sync.assert_awaited_once()
        run_doc.save.assert_awaited_once()
        assert run_doc.status == WorkflowRunStatus.COMPLETED
        assert run_doc.final_output == {"content": "done"}
        assert run_doc.finished_at is not None

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

    @pytest.mark.asyncio
    async def test_marks_run_cancelled_when_workflow_is_cancelled(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock(side_effect=WorkflowCancelledError("Workflow cancelled by user")))
        run_doc = SimpleNamespace(
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            finished_at=None,
            save=AsyncMock(),
            sync=AsyncMock(),
            id="run-1",
        )

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        r = _make_runner()

        await r._execute(run_doc, _definition(), "hello", {})

        assert run_doc.status == WorkflowRunStatus.CANCELLED
        assert run_doc.error_summary == "Workflow cancelled by user"
        assert isinstance(run_doc.finished_at, datetime)
        assert run_doc.finished_at.tzinfo == UTC
        run_doc.save.assert_awaited_once()
        run_doc.sync.assert_not_called()
