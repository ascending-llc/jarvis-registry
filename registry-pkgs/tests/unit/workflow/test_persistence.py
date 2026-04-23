import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agno.run.base import RunStatus
from agno.run.workflow import WorkflowRunOutput
from agno.session import WorkflowSession
from agno.workflow import StepOutput
from beanie import PydanticObjectId

from registry_pkgs.models.enums import NodeRunStatus, WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowNode
from registry_pkgs.workflows import persistence


class _FieldExpr:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return (self.name, "==", other)


def _sync_with_fake_run():
    sync = object.__new__(persistence.WorkflowRunSyncer)
    sync._workflow_run = SimpleNamespace(
        id=PydanticObjectId(),
        status=WorkflowRunStatus.RUNNING,
        finished_at=None,
        final_output=None,
        save=AsyncMock(),
    )
    sync._node_by_name = {}
    return sync


@pytest.mark.unit
class TestWorkflowPersistence:
    def test_flatten_step_results_flattens_nested_lists(self):
        step_a = StepOutput(step_name="a", content="A")
        step_b = StepOutput(step_name="b", content="B")
        step_c = StepOutput(step_name="c", content="C")

        flat = persistence._flatten_step_results([step_a, [step_b, [step_c]], "ignored"])

        assert [step.step_name for step in flat] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_upsert_session_syncs_latest_workflow_run(self, monkeypatch: pytest.MonkeyPatch):
        super_upsert = AsyncMock(return_value={"ok": True})
        sync = _sync_with_fake_run()
        sync._sync_to_beanie = AsyncMock()

        monkeypatch.setattr(persistence.AsyncMongoDb, "upsert_session", super_upsert)
        session = WorkflowSession(
            session_id="session-1",
            runs=[
                WorkflowRunOutput(content="old"),
                WorkflowRunOutput(content="latest", status=RunStatus.completed),
            ],
        )

        result = await sync.upsert_session(session)

        assert result == {"ok": True}
        super_upsert.assert_awaited_once_with(session, deserialize=True)
        sync._sync_to_beanie.assert_awaited_once_with(session.runs[-1])

    @pytest.mark.asyncio
    async def test_upsert_sessions_is_not_supported_for_single_run_syncer(self, caplog):
        sync = _sync_with_fake_run()
        caplog.set_level(logging.ERROR)

        with pytest.raises(RuntimeError, match="WorkflowRunSyncer should not call upsert_sessions at all"):
            await sync.upsert_sessions([])

        assert "WorkflowRunSyncer should not call upsert_sessions at all" in caplog.text

    @pytest.mark.asyncio
    async def test_update_workflow_run_clears_finished_at_while_running(self):
        sync = _sync_with_fake_run()
        run_output = WorkflowRunOutput(content="still running", status=RunStatus.running)

        await sync._update_workflow_run(run_output)

        assert sync._workflow_run.status == WorkflowRunStatus.RUNNING
        assert sync._workflow_run.finished_at is None
        assert sync._workflow_run.final_output == {"content": "still running"}
        sync._workflow_run.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_workflow_run_sets_finished_at_once_for_terminal_status(self):
        sync = _sync_with_fake_run()
        existing_finished_at = datetime(2026, 4, 22, tzinfo=UTC)
        sync._workflow_run.finished_at = existing_finished_at
        run_output = WorkflowRunOutput(content="done", status=RunStatus.completed)

        await sync._update_workflow_run(run_output)

        assert sync._workflow_run.status == WorkflowRunStatus.COMPLETED
        assert sync._workflow_run.finished_at == existing_finished_at
        assert sync._workflow_run.final_output == {"content": "done"}

    @pytest.mark.asyncio
    async def test_update_workflow_run_fails_when_any_step_failed(self):
        sync = _sync_with_fake_run()
        run_output = WorkflowRunOutput(content="step failed", status=RunStatus.completed)

        await sync._update_workflow_run(
            run_output,
            [StepOutput(step_name="bad-step", content="boom", success=False, error="boom")],
        )

        assert sync._workflow_run.status == WorkflowRunStatus.FAILED
        assert sync._workflow_run.finished_at is not None
        assert sync._workflow_run.final_output == {"content": "step failed"}

    @pytest.mark.asyncio
    async def test_upsert_node_run_creates_new_node_run(self, monkeypatch: pytest.MonkeyPatch):
        saved = []

        class FakeNodeRun:
            workflow_run_id = _FieldExpr("workflow_run_id")
            node_id = _FieldExpr("node_id")

            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.attempt = 0
                self.status = NodeRunStatus.PENDING
                self.output_snapshot = None
                self.finished_at = None
                self.error = None

            @classmethod
            async def find_one(cls, *args, **kwargs):
                return None

            async def save(self):
                saved.append(self)

        monkeypatch.setattr(persistence, "NodeRun", FakeNodeRun)

        sync = _sync_with_fake_run()
        sync._node_by_name = {"fetch": WorkflowNode(name="fetch", executor_key="tool")}

        await sync._upsert_node_run(StepOutput(step_name="fetch", content="ok", success=True))

        node_run = saved[0]
        assert node_run.node_name == "fetch"
        assert node_run.status == NodeRunStatus.COMPLETED
        assert node_run.attempt == 1
        assert node_run.output_snapshot == {"content": "ok"}
        assert node_run.finished_at is not None

    @pytest.mark.asyncio
    async def test_upsert_node_run_updates_existing_node_run(self, monkeypatch: pytest.MonkeyPatch):
        saved: list[NodeRun] = []
        existing = NodeRun.model_construct(
            workflow_run_id=PydanticObjectId(),
            node_id="node-1",
            node_name="fetch",
            attempt=2,
            status=NodeRunStatus.PENDING,
        )

        async def fake_find_one(*args, **kwargs):
            return existing

        async def fake_save(self):
            saved.append(self)

        monkeypatch.setattr(NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        monkeypatch.setattr(NodeRun, "node_id", _FieldExpr("node_id"), raising=False)
        monkeypatch.setattr(NodeRun, "find_one", fake_find_one)
        monkeypatch.setattr(NodeRun, "save", fake_save)

        sync = _sync_with_fake_run()
        sync._node_by_name = {"fetch": WorkflowNode(id="node-1", name="fetch", executor_key="tool")}

        await sync._upsert_node_run(StepOutput(step_name="fetch", content="bad", success=False, error="boom"))

        assert saved[0] is existing
        assert existing.status == NodeRunStatus.FAILED
        assert existing.attempt == 3
        assert existing.error == "boom"
        assert existing.output_snapshot == {"content": "bad"}
