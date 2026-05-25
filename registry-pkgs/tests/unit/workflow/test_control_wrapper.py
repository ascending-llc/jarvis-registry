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
class TestHumanReviewModelValidation:
    """``WorkflowNode._validate_shape`` enforces agno's per-primitive HumanReview rules."""

    def test_step_accepts_full_human_review(self):
        from registry_pkgs.models.enums import OnRejectPolicy
        from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode

        step = WorkflowNode(
            name="s",
            node_type="step",
            executor_key="tool",
            human_review=HumanReviewSpec(
                requires_confirmation=True,
                requires_user_input=True,
                requires_output_review=True,
                on_reject=OnRejectPolicy.SKIP,
                timeout_seconds=60,
            ),
        )
        assert step.human_review is not None
        assert step.human_review.requires_confirmation is True

    def test_parallel_rejects_any_hitl_field(self):
        from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode

        with pytest.raises(ValueError, match="parallel node does not support any HITL"):
            WorkflowNode(
                name="p",
                node_type="parallel",
                children=[
                    WorkflowNode(name="a", executor_key="x"),
                    WorkflowNode(name="b", executor_key="y"),
                ],
                human_review=HumanReviewSpec(requires_confirmation=True),
            )

    def test_loop_rejects_user_input_and_output_review(self):
        from registry_pkgs.models.workflow import HumanReviewSpec, LoopConfig, WorkflowNode

        # Iteration review IS allowed on loop
        loop_ok = WorkflowNode(
            name="loop_ok",
            node_type="loop",
            loop_config=LoopConfig(max_iterations=3),
            children=[WorkflowNode(name="c", executor_key="x")],
            human_review=HumanReviewSpec(requires_iteration_review=True),
        )
        assert loop_ok.human_review.requires_iteration_review is True

        # But user_input + output_review are not
        with pytest.raises(ValueError, match="requires_user_input is not supported on loop"):
            WorkflowNode(
                name="loop_bad",
                node_type="loop",
                loop_config=LoopConfig(max_iterations=3),
                children=[WorkflowNode(name="c", executor_key="x")],
                human_review=HumanReviewSpec(requires_user_input=True),
            )

    def test_condition_rejects_user_input(self):
        from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode

        with pytest.raises(ValueError, match="requires_user_input is not supported on condition"):
            WorkflowNode(
                name="c",
                node_type="condition",
                condition_cel="true",
                true_steps=[WorkflowNode(name="t", executor_key="x")],
                human_review=HumanReviewSpec(requires_user_input=True),
            )

    def test_step_rejects_iteration_review(self):
        from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode

        with pytest.raises(ValueError, match="requires_iteration_review is not supported on step"):
            WorkflowNode(
                name="s",
                node_type="step",
                executor_key="tool",
                human_review=HumanReviewSpec(requires_iteration_review=True),
            )
