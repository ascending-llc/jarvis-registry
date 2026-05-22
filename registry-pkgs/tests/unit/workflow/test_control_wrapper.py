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

        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper.PAUSE_POLL_INTERVAL", 0.0)
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper.MONGO_POLL_EVERY_N", 1)
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


@pytest.mark.unit
class TestApprovalGate:
    """Approval gate: a STEP with require_approval holds until APPROVE/REJECT/timeout."""

    def _patch_common(self, monkeypatch, fake_run):
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._read_mongodb_directive", AsyncMock(return_value=None)
        )
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper._record_attempt_start", AsyncMock())
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper._update_run_control_state", AsyncMock())
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper._mark_node_status", AsyncMock())
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper._mark_node_failed", AsyncMock())
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper.WorkflowRun.get", AsyncMock(return_value=fake_run))

    @pytest.mark.asyncio
    async def test_approve_lets_step_execute(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        queue.put(run_id, WorkflowDirective.APPROVE)
        fake_run = SimpleNamespace(pause_timeout_seconds=60, paused_at=None)
        self._patch_common(monkeypatch, fake_run)

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="done", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="gate",
            step_config=None,
            directive_queue=queue,
            require_approval=True,
        )

        result = await wrapped(SimpleNamespace(input="hi"), {})

        assert result.success is True
        executor.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reject_fails_node_without_executing(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        queue.put(run_id, WorkflowDirective.REJECT)
        fake_run = SimpleNamespace(pause_timeout_seconds=60, paused_at=None)
        self._patch_common(monkeypatch, fake_run)

        marked = AsyncMock()
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper._mark_node_failed", marked)

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="done", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="gate",
            step_config=None,
            directive_queue=queue,
            require_approval=True,
        )

        result = await wrapped(SimpleNamespace(input="hi"), {})

        assert result.success is False
        assert "Rejected" in (result.error or "")
        executor.assert_not_awaited()
        marked.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_is_treated_as_rejection(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)  # no directive ever arrives
        fake_run = SimpleNamespace(pause_timeout_seconds=60, paused_at=None)
        self._patch_common(monkeypatch, fake_run)
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper.PAUSE_POLL_INTERVAL", 0.0)

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="done", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="gate",
            step_config=None,
            directive_queue=queue,
            require_approval=True,
            approval_timeout_seconds=1,
        )

        result = await wrapped(SimpleNamespace(input="hi"), {})

        assert result.success is False
        executor.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_while_awaiting_raises_cancelled(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        queue.put(run_id, WorkflowDirective.CANCEL)
        fake_run = SimpleNamespace(pause_timeout_seconds=60, paused_at=None)
        self._patch_common(monkeypatch, fake_run)

        executor = AsyncMock(return_value=SimpleNamespace(success=True, content="done", error=None))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="gate",
            step_config=None,
            directive_queue=queue,
            require_approval=True,
        )

        with pytest.raises(WorkflowCancelledError):
            await wrapped(SimpleNamespace(input="hi"), {})

        executor.assert_not_awaited()


@pytest.mark.unit
class TestApprovalStateMachineAndModel:
    def test_approve_only_valid_when_awaiting_approval(self):
        from registry_pkgs.models.enums import WorkflowDirective, WorkflowRunStateMachine, WorkflowRunStatus

        assert (
            WorkflowRunStateMachine.apply_directive(WorkflowRunStatus.AWAITING_APPROVAL, WorkflowDirective.APPROVE)
            == WorkflowRunStatus.RUNNING
        )
        assert (
            WorkflowRunStateMachine.apply_directive(WorkflowRunStatus.AWAITING_APPROVAL, WorkflowDirective.REJECT)
            == WorkflowRunStatus.RUNNING
        )
        with pytest.raises(ValueError, match="Cannot"):
            WorkflowRunStateMachine.apply_directive(WorkflowRunStatus.RUNNING, WorkflowDirective.APPROVE)

    def test_cancel_allowed_while_awaiting_approval(self):
        from registry_pkgs.models.enums import WorkflowDirective, WorkflowRunStateMachine, WorkflowRunStatus

        assert (
            WorkflowRunStateMachine.apply_directive(WorkflowRunStatus.AWAITING_APPROVAL, WorkflowDirective.CANCEL)
            == WorkflowRunStatus.CANCELLED
        )

    def test_require_approval_only_on_step_nodes(self):
        from registry_pkgs.models.workflow import WorkflowNode

        # STEP node accepts require_approval
        step = WorkflowNode(name="s", node_type="step", executor_key="tool", require_approval=True)
        assert step.require_approval is True

        # Container node rejects require_approval
        with pytest.raises(ValueError, match="require_approval is only supported on step nodes"):
            WorkflowNode(
                name="p",
                node_type="parallel",
                children=[
                    WorkflowNode(name="a", executor_key="x"),
                    WorkflowNode(name="b", executor_key="y"),
                ],
                require_approval=True,
            )
