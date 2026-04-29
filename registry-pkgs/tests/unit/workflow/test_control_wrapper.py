from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowDirective, WorkflowRunStatus
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.control.wrapper import WorkflowCancelledError, with_control


@pytest.mark.unit
class TestControlWrapper:
    @pytest.mark.asyncio
    async def test_resume_from_pause_continues_same_step(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        queue.put(run_id, WorkflowDirective.PAUSE)
        queue.put(run_id, WorkflowDirective.RESUME)
        fake_run = SimpleNamespace(
            pause_timeout_seconds=60,
            paused_at=None,
            status=WorkflowRunStatus.RUNNING,
            pending_directive=WorkflowDirective.PAUSE,
            save=AsyncMock(),
        )

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="ok", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="fetch",
            step_config=None,
            directive_queue=queue,
        )

        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._read_mongodb_directive",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._record_attempt_start",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper.WorkflowRun.get",
            AsyncMock(return_value=fake_run),
        )

        result = await wrapped(SimpleNamespace(input="hello"), {})

        assert result.success is True
        assert result.content == "ok"
        assert fake_run.status == WorkflowRunStatus.RUNNING
        assert fake_run.pending_directive is None
        assert fake_run.paused_at is None
        executor.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_before_attempt_raises_cancelled_error(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        queue.put(run_id, WorkflowDirective.CANCEL)

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="ok", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="fetch",
            step_config=None,
            directive_queue=queue,
        )

        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._read_mongodb_directive",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._record_attempt_start",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._update_run_control_state",
            AsyncMock(),
        )

        with pytest.raises(WorkflowCancelledError, match="Workflow cancelled by user"):
            await wrapped(SimpleNamespace(input="hello"), {})

        executor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_timeout_raises_cancelled_error(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        queue.put(run_id, WorkflowDirective.PAUSE)

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="ok", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="fetch",
            step_config=None,
            directive_queue=queue,
        )

        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._read_mongodb_directive",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._record_attempt_start",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._update_run_control_state",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper.WorkflowRun.get",
            AsyncMock(return_value=SimpleNamespace(pause_timeout_seconds=0, paused_at=None, save=AsyncMock())),
        )

        with pytest.raises(WorkflowCancelledError, match="pause timeout"):
            await wrapped(SimpleNamespace(input="hello"), {})

        executor.assert_not_awaited()


@pytest.mark.unit
class TestWorkflowRunStateMachine:
    def test_retry_rejects_cancelled_runs(self):
        from registry_pkgs.models.enums import WorkflowDirective, WorkflowRunStateMachine

        with pytest.raises(ValueError, match="Cannot retry"):
            WorkflowRunStateMachine.apply_directive(WorkflowRunStatus.CANCELLED, WorkflowDirective.RETRY)
