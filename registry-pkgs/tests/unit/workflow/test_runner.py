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


@pytest.mark.unit
class TestWorkflowRunner:
    def test_runner_requires_db_client_and_db_name(self):
        with pytest.raises(ValueError, match="db_client"):
            runner.WorkflowRunner(executor_registry={}, db_client=None, db_name="jarvis")

        with pytest.raises(ValueError, match="db_name"):
            runner.WorkflowRunner(executor_registry={}, db_client=object(), db_name="")

    @pytest.mark.asyncio
    async def test_run_raises_when_definition_is_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=None))
        workflow_runner = runner.WorkflowRunner(executor_registry={}, db_client=object(), db_name="jarvis")

        with pytest.raises(ValueError, match="not found"):
            await workflow_runner.run("missing-id", "hello")

    @pytest.mark.asyncio
    async def test_run_orchestrates_definition_execution_and_loads_node_runs(self, monkeypatch: pytest.MonkeyPatch):
        definition = _definition()
        run_doc = _run()
        node_runs = [SimpleNamespace(node_name="fetch")]

        monkeypatch.setattr(runner.WorkflowDefinition, "get", AsyncMock(return_value=definition))
        monkeypatch.setattr(runner.WorkflowRunner, "_create_run", AsyncMock(return_value=run_doc))
        monkeypatch.setattr(runner.WorkflowRunner, "_execute", AsyncMock())

        monkeypatch.setattr(runner.NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        find_query = SimpleNamespace(to_list=AsyncMock(return_value=node_runs))
        monkeypatch.setattr(runner.NodeRun, "find", lambda *args, **kwargs: find_query)

        workflow_runner = runner.WorkflowRunner(executor_registry={}, db_client=object(), db_name="jarvis")
        actual_run, actual_node_runs = await workflow_runner.run(str(definition.id), "hello", trigger_source="api")

        assert actual_run is run_doc
        assert actual_node_runs == node_runs
        runner.WorkflowRunner._create_run.assert_awaited_once_with(definition, "hello", "api")
        runner.WorkflowRunner._execute.assert_awaited_once_with(run_doc, definition, "hello")

    @pytest.mark.asyncio
    async def test_create_run_inserts_running_workflow_with_snapshot(self, monkeypatch: pytest.MonkeyPatch):
        inserted = []

        class FakeWorkflowRun:
            def __init__(self, **kwargs):
                self.id = PydanticObjectId()
                self.__dict__.update(kwargs)

            async def insert(self):
                inserted.append(self)

        monkeypatch.setattr(runner, "WorkflowRun", FakeWorkflowRun)
        workflow_runner = runner.WorkflowRunner(executor_registry={}, db_client=object(), db_name="jarvis")
        definition = _definition()

        run_doc = await workflow_runner._create_run(definition, "hello", "script")

        assert inserted[0] is run_doc
        assert run_doc.workflow_definition_id == definition.id
        assert run_doc.status == WorkflowRunStatus.RUNNING
        assert run_doc.trigger_source == "script"
        assert run_doc.initial_input == {"user_text": "hello"}
        assert run_doc.definition_snapshot["name"] == definition.name

    @pytest.mark.asyncio
    async def test_execute_syncs_run_after_success(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock())
        run_doc = SimpleNamespace(sync=AsyncMock())

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        workflow_runner = runner.WorkflowRunner(
            executor_registry={"tool": object()}, db_client=object(), db_name="jarvis"
        )

        await workflow_runner._execute(run_doc, _definition(), "hello")

        workflow.arun.assert_awaited_once_with(input="hello", session_state={"user_text": "hello"})
        run_doc.sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_marks_run_failed_when_workflow_raises(self, monkeypatch: pytest.MonkeyPatch):
        workflow = SimpleNamespace(arun=AsyncMock(side_effect=RuntimeError("boom")))
        run_doc = SimpleNamespace(
            status=WorkflowRunStatus.RUNNING,
            error_summary=None,
            finished_at=None,
            save=AsyncMock(),
            id="run-1",
        )

        monkeypatch.setattr(runner, "compile_workflow", lambda *args, **kwargs: workflow)
        workflow_runner = runner.WorkflowRunner(
            executor_registry={"tool": object()}, db_client=object(), db_name="jarvis"
        )

        with pytest.raises(RuntimeError, match="boom"):
            await workflow_runner._execute(run_doc, _definition(), "hello")

        assert run_doc.status == WorkflowRunStatus.FAILED
        assert run_doc.error_summary == "boom"
        assert isinstance(run_doc.finished_at, datetime)
        assert run_doc.finished_at.tzinfo == UTC
        run_doc.save.assert_awaited_once()
