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


class _AsyncContextManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
        sync._sync_to_beanie.assert_awaited_once_with(session.runs[-1], session_data={})

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
        sync._workflow_run.save.assert_awaited_once_with(session=None)

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
    async def test_update_workflow_run_passes_session_to_save(self):
        sync = _sync_with_fake_run()
        mongo_session = object()

        await sync._update_workflow_run(
            WorkflowRunOutput(content="done", status=RunStatus.completed),
            session=mongo_session,
        )

        sync._workflow_run.save.assert_awaited_once_with(session=mongo_session)

    @pytest.mark.asyncio
    async def test_upsert_node_run_creates_new_node_run(self, monkeypatch: pytest.MonkeyPatch):
        saved = []
        find_one_calls = []

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
                self.selected_a2a_key = None

            @classmethod
            async def find_one(cls, *args, **kwargs):
                find_one_calls.append((args, kwargs))
                return None

            async def save(self, **kwargs):
                saved.append((self, kwargs))

        monkeypatch.setattr(persistence, "NodeRun", FakeNodeRun)

        sync = _sync_with_fake_run()
        sync._node_by_name = {"fetch": WorkflowNode(name="fetch", executor_key="tool")}

        await sync._upsert_node_run(StepOutput(step_name="fetch", content="ok", success=True))

        assert find_one_calls[0][1] == {"session": None}
        node_run, save_kwargs = saved[0]
        assert node_run.node_name == "fetch"
        assert node_run.status == NodeRunStatus.COMPLETED
        assert node_run.attempt == 1
        assert node_run.output_snapshot == {"content": "ok"}
        assert node_run.finished_at is not None
        assert save_kwargs == {"session": None}

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

        async def fake_save(self, **kwargs):
            saved.append((self, kwargs))

        monkeypatch.setattr(NodeRun, "workflow_run_id", _FieldExpr("workflow_run_id"), raising=False)
        monkeypatch.setattr(NodeRun, "node_id", _FieldExpr("node_id"), raising=False)
        monkeypatch.setattr(NodeRun, "find_one", fake_find_one)
        monkeypatch.setattr(NodeRun, "save", fake_save)

        sync = _sync_with_fake_run()
        sync._node_by_name = {"fetch": WorkflowNode(id="node-1", name="fetch", executor_key="tool")}

        await sync._upsert_node_run(StepOutput(step_name="fetch", content="bad", success=False, error="boom"))

        saved_doc, save_kwargs = saved[0]
        assert saved_doc is existing
        assert existing.status == NodeRunStatus.FAILED
        assert existing.attempt == 2
        assert existing.error == "boom"
        assert existing.output_snapshot == {"content": "bad"}
        assert save_kwargs == {"session": None}

    @pytest.mark.asyncio
    async def test_upsert_node_run_passes_session_to_find_and_save(self, monkeypatch: pytest.MonkeyPatch):
        saved = []
        find_one_calls = []
        mongo_session = object()

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
                self.selected_a2a_key = None

            @classmethod
            async def find_one(cls, *args, **kwargs):
                find_one_calls.append((args, kwargs))
                return None

            async def save(self, **kwargs):
                saved.append((self, kwargs))

        monkeypatch.setattr(persistence, "NodeRun", FakeNodeRun)

        sync = _sync_with_fake_run()
        sync._node_by_name = {"fetch": WorkflowNode(name="fetch", executor_key="tool")}

        await sync._upsert_node_run(
            StepOutput(step_name="fetch", content="ok", success=True),
            session_data={"a2a_target_fetch": "agent-1"},
            session=mongo_session,
        )

        assert find_one_calls[0][1] == {"session": mongo_session}
        node_run, save_kwargs = saved[0]
        assert node_run.selected_a2a_key == "agent-1"
        assert save_kwargs == {"session": mongo_session}

    @pytest.mark.asyncio
    async def test_sync_to_beanie_uses_active_transaction_session(self, monkeypatch: pytest.MonkeyPatch):
        sync = _sync_with_fake_run()
        active_session = object()
        sync._write_run_and_nodes = AsyncMock()
        fake_db_client = SimpleNamespace(start_session=AsyncMock())

        monkeypatch.setattr(persistence, "get_current_session", lambda: active_session)
        monkeypatch.setattr(persistence.WorkflowRunSyncer, "db_client", property(lambda self: fake_db_client))

        run_output = WorkflowRunOutput(
            content="done",
            status=RunStatus.completed,
            step_results=[StepOutput(step_name="fetch", content="ok", success=True)],
        )

        await sync._sync_to_beanie(run_output, session_data={"a2a_target_fetch": "agent-1"})

        sync._write_run_and_nodes.assert_awaited_once_with(
            run_output,
            [run_output.step_results[0]],
            session_data={"a2a_target_fetch": "agent-1"},
            session=active_session,
        )
        fake_db_client.start_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_to_beanie_starts_transaction_for_terminal_status_without_active_session(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        sync = _sync_with_fake_run()

        async def start_transaction():
            return _AsyncContextManager(None)

        mongo_session = SimpleNamespace(start_transaction=start_transaction)

        def start_session():
            return _AsyncContextManager(mongo_session)

        fake_db_client = SimpleNamespace(start_session=start_session)
        sync._write_run_and_nodes = AsyncMock()

        monkeypatch.setattr(persistence, "get_current_session", lambda: None)
        monkeypatch.setattr(persistence.WorkflowRunSyncer, "db_client", property(lambda self: fake_db_client))

        run_output = WorkflowRunOutput(
            content="done",
            status=RunStatus.completed,
            step_results=[StepOutput(step_name="fetch", content="ok", success=True)],
        )

        await sync._sync_to_beanie(run_output)

        sync._write_run_and_nodes.assert_awaited_once_with(
            run_output,
            [run_output.step_results[0]],
            session_data={},
            session=mongo_session,
        )
