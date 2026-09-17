from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agno.workflow import Step, StepOutput, Workflow
from agno.workflow.step import OnError
from beanie import PydanticObjectId
from opentelemetry import baggage

from registry_pkgs.models.enums import NodeRunStatus, WorkflowDirective, WorkflowRunStatus
from registry_pkgs.models.workflow import StepConfig
from registry_pkgs.telemetry.trace_propagation import (
    BAGGAGE_KEY_ATTEMPT,
    BAGGAGE_KEY_NODE_ID,
    BAGGAGE_KEY_WORKFLOW_RUN_ID,
)
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.control import wrapper as wrapper_module
from registry_pkgs.workflows.control.wrapper import WorkflowCancelledError, _record_attempt_result, with_control
from registry_pkgs.workflows.types import is_skip_tolerated_failure


class _NodeRunField:
    def __eq__(self, other: object) -> tuple[str, object]:
        return ("eq", other)


class _FakeNodeRun:
    workflow_run_id = _NodeRunField()
    node_id = _NodeRunField()
    existing: _FakeNodeRun | None = None
    events: list[str] = []
    save_error: Exception | None = None

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)
        self.status = NodeRunStatus.PENDING
        self.finished_at = None
        self.error = None

    @classmethod
    async def find_one(cls, *args: object, **kwargs: object) -> _FakeNodeRun | None:
        return cls.existing

    async def save(self) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.events.append("terminal-saved")


def _configure_fake_node_run() -> _FakeNodeRun:
    node_run = _FakeNodeRun()
    _FakeNodeRun.existing = node_run
    _FakeNodeRun.events = []
    _FakeNodeRun.save_error = None
    return node_run


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
            "registry_pkgs.workflows.control.wrapper._record_attempt_result",
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
    async def test_executor_exception_propagates_when_not_skip_tolerated(self, monkeypatch: pytest.MonkeyPatch):
        """A non-skip executor failure must persist and propagate the original exception."""
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)

        original = RuntimeError("RuntimeError: downstream server exploded")
        executor = AsyncMock(side_effect=original)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
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
        record_attempt_result = AsyncMock()
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._record_attempt_result",
            record_attempt_result,
        )

        with pytest.raises(RuntimeError, match="RuntimeError: downstream server exploded") as exc_info:
            await wrapped(SimpleNamespace(input="hello"), {})

        assert exc_info.value is original
        executor.assert_awaited_once()
        record_attempt_result.assert_awaited_once()
        persisted_result = record_attempt_result.await_args.args[-1]
        assert persisted_result.success is False
        assert persisted_result.error == "RuntimeError: downstream server exploded"
        assert persisted_result.content == ""
        record_attempt_result.assert_awaited_once_with(
            run_id,
            "node-1",
            "github",
            None,
            persisted_result,
        )

    @pytest.mark.asyncio
    async def test_executor_exception_triggers_retry(self, monkeypatch: pytest.MonkeyPatch):
        """When on_error=retry, a raising executor should be retried like a failed StepOutput."""
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)

        success_output = SimpleNamespace(success=True, content="done", error=None)
        executor = AsyncMock(side_effect=[RuntimeError("transient"), success_output])

        step_config = StepConfig(on_error="retry", max_retries=2, backoff_base_seconds=0.01, backoff_max_seconds=0.01)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=step_config,
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
        record_attempt_result = AsyncMock()
        monkeypatch.setattr(
            "registry_pkgs.workflows.control.wrapper._record_attempt_result",
            record_attempt_result,
        )
        monkeypatch.setattr("registry_pkgs.workflows.control.wrapper.asyncio.sleep", AsyncMock())

        result = await wrapped(SimpleNamespace(input="hello"), {})

        assert result.success is True
        assert result.content == "done"
        assert executor.await_count == 2
        record_attempt_result.assert_awaited_once_with(
            run_id,
            "node-1",
            "github",
            step_config,
            success_output,
        )

    @pytest.mark.asyncio
    async def test_exhausted_retries_persist_only_final_failure(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        failures = [
            StepOutput(content="", success=False, error="attempt 1"),
            StepOutput(content="", success=False, error="attempt 2"),
            StepOutput(content="", success=False, error="attempt 3"),
        ]
        executor = AsyncMock(side_effect=failures)
        step_config = StepConfig(on_error="retry", max_retries=2, backoff_base_seconds=0.01, backoff_max_seconds=0.01)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=step_config,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        record_attempt_result = AsyncMock()
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", record_attempt_result)
        monkeypatch.setattr(wrapper_module.asyncio, "sleep", AsyncMock())

        with pytest.raises(RuntimeError, match="attempt 3"):
            await wrapped(SimpleNamespace(input="hello"), {})

        assert executor.await_count == 3
        record_attempt_result.assert_awaited_once_with(
            run_id,
            "node-1",
            "github",
            step_config,
            failures[-1],
        )

    @pytest.mark.asyncio
    async def test_final_returned_failure_does_not_reuse_previous_exception(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        executor = AsyncMock(
            side_effect=[
                RuntimeError("stale exception"),
                StepOutput(content="", success=False, error="final returned failure"),
            ]
        )
        step_config = StepConfig(on_error="retry", max_retries=1, backoff_base_seconds=0.01)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="critical",
            step_config=step_config,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", AsyncMock())
        monkeypatch.setattr(wrapper_module.asyncio, "sleep", AsyncMock())

        with pytest.raises(RuntimeError, match="final returned failure"):
            await wrapped(SimpleNamespace(input="hello"), {})

    @pytest.mark.asyncio
    async def test_failed_output_returns_when_skip_tolerated(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        failure = StepOutput(content="", success=False, error="optional step failed")
        executor = AsyncMock(return_value=failure)
        step_config = StepConfig(on_error="skip")
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="optional",
            step_config=step_config,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        record_attempt_result = AsyncMock()
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", record_attempt_result)

        result = await wrapped(SimpleNamespace(input="hello"), {})

        assert result is failure
        record_attempt_result.assert_awaited_once_with(
            run_id,
            "node-1",
            "optional",
            step_config,
            failure,
        )

    @pytest.mark.asyncio
    async def test_raised_exception_returns_failed_output_when_skip_tolerated(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        executor = AsyncMock(side_effect=RuntimeError("optional exception"))
        step_config = StepConfig(on_error="skip")
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="optional",
            step_config=step_config,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", AsyncMock())

        result = await wrapped(SimpleNamespace(input="hello"), {})

        assert result.success is False
        assert result.error == "optional exception"

    @pytest.mark.asyncio
    async def test_terminal_result_persisted_before_success_returns(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        node_run = _configure_fake_node_run()
        monkeypatch.setattr(wrapper_module, "NodeRun", _FakeNodeRun)
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())

        async def executor(step_input: object, session_state: dict | None = None) -> StepOutput:
            return StepOutput(content="done", success=True)

        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=None,
            directive_queue=queue,
        )

        result = await wrapped(SimpleNamespace(input="hello"), {})
        _FakeNodeRun.events.append("wrapper-returned")

        assert result.success is True
        assert _FakeNodeRun.events == ["terminal-saved", "wrapper-returned"]
        assert node_run.status == NodeRunStatus.COMPLETED
        assert node_run.finished_at is not None
        assert node_run.error is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("step_config", "expected_status"),
        [
            (None, NodeRunStatus.FAILED),
            (StepConfig(on_error="skip"), NodeRunStatus.SKIPPED),
        ],
    )
    async def test_record_attempt_result_maps_terminal_failure_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        step_config: StepConfig | None,
        expected_status: NodeRunStatus,
    ):
        node_run = _configure_fake_node_run()
        monkeypatch.setattr(wrapper_module, "NodeRun", _FakeNodeRun)

        await _record_attempt_result(
            str(PydanticObjectId()),
            "node-1",
            "github",
            step_config,
            StepOutput(content="", success=False, error="boom"),
        )

        assert node_run.status == expected_status
        assert node_run.finished_at is not None
        assert node_run.error == "boom"

    @pytest.mark.asyncio
    async def test_wrapper_returns_result_when_terminal_persistence_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        _configure_fake_node_run()
        _FakeNodeRun.save_error = RuntimeError("mongo unavailable")
        monkeypatch.setattr(wrapper_module, "NodeRun", _FakeNodeRun)
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        output = StepOutput(content="done", success=True)
        executor = AsyncMock(return_value=output)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=None,
            directive_queue=queue,
        )

        result = await wrapped(SimpleNamespace(input="hello"), {})

        assert result is output
        executor.assert_awaited_once()
        assert "batch sync will retry" in caplog.text

    @pytest.mark.parametrize(
        ("success", "step_config", "expected"),
        [
            (True, StepConfig(on_error="skip"), False),
            (False, None, False),
            (False, StepConfig(on_error="fail"), False),
            (False, StepConfig(on_error="skip"), True),
        ],
    )
    def test_is_skip_tolerated_failure(
        self,
        success: bool,
        step_config: StepConfig | None,
        expected: bool,
    ):
        assert is_skip_tolerated_failure(success, step_config) is expected

    @pytest.mark.asyncio
    async def test_cancelled_error_not_swallowed_by_exception_handler(self, monkeypatch: pytest.MonkeyPatch):
        """WorkflowCancelledError raised by the executor must propagate — the try/except must not catch it."""
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)

        executor = AsyncMock(side_effect=WorkflowCancelledError("cancelled"))
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
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

        with pytest.raises(WorkflowCancelledError):
            await wrapped(SimpleNamespace(input="hello"), {})

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

    @pytest.mark.asyncio
    async def test_identity_baggage_visible_to_executor_and_attempt_varies(self, monkeypatch: pytest.MonkeyPatch):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        seen: list[dict[str, str | None]] = []

        async def executor(step_input: object, session_state: dict | None = None) -> StepOutput:
            seen.append(
                {
                    "run_id": baggage.get_baggage(BAGGAGE_KEY_WORKFLOW_RUN_ID),
                    "node_id": baggage.get_baggage(BAGGAGE_KEY_NODE_ID),
                    "attempt": baggage.get_baggage(BAGGAGE_KEY_ATTEMPT),
                }
            )
            if len(seen) == 1:
                return StepOutput(content="", success=False, error="transient")
            return StepOutput(content="done", success=True)

        step_config = StepConfig(on_error="retry", max_retries=2, backoff_base_seconds=0.01, backoff_max_seconds=0.01)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=step_config,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", AsyncMock())
        monkeypatch.setattr(wrapper_module.asyncio, "sleep", AsyncMock())

        result = await wrapped(SimpleNamespace(input="hello"), {})

        assert result.success is True
        assert len(seen) == 2
        assert seen[0]["attempt"] == "1"
        assert seen[1]["attempt"] == "2"
        assert seen[0]["run_id"] == run_id == seen[1]["run_id"]
        assert seen[0]["node_id"] == "node-1" == seen[1]["node_id"]

    @pytest.mark.asyncio
    async def test_no_baggage_leak_after_success(self, monkeypatch: pytest.MonkeyPatch):
        await self._assert_no_leak(monkeypatch, AsyncMock(return_value=StepOutput(content="ok", success=True)))

    @pytest.mark.asyncio
    async def test_no_baggage_leak_after_exception(self, monkeypatch: pytest.MonkeyPatch):
        await self._assert_no_leak(
            monkeypatch,
            AsyncMock(side_effect=RuntimeError("boom")),
            expected_error=RuntimeError,
        )

    @pytest.mark.asyncio
    async def test_no_baggage_leak_after_cancelled(self, monkeypatch: pytest.MonkeyPatch):
        await self._assert_no_leak(
            monkeypatch,
            AsyncMock(side_effect=WorkflowCancelledError("cancelled")),
            expect_cancel=True,
        )

    @pytest.mark.asyncio
    async def test_awaited_inner_task_sees_baggage_and_no_leak_after(self, monkeypatch: pytest.MonkeyPatch):
        """An inner task the executor awaits sees the baggage; nothing leaks once wrapped() returns.

        Guards the realistic case (executor awaits its work). A fire-and-forget task that outlives
        executor() is a documented, unfixable OTEL+asyncio ceiling — see wrapper.py.
        """
        import asyncio

        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        inner_seen: dict[str, str | None] = {}

        async def inner() -> None:
            inner_seen["attempt"] = baggage.get_baggage(BAGGAGE_KEY_ATTEMPT)
            inner_seen["run_id"] = baggage.get_baggage(BAGGAGE_KEY_WORKFLOW_RUN_ID)

        async def executor(step_input: object, session_state: dict | None = None) -> StepOutput:
            await asyncio.create_task(inner())
            return StepOutput(content="ok", success=True)

        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=None,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", AsyncMock())

        await wrapped(SimpleNamespace(input="hello"), {})

        # Inner awaited task inherited the baggage...
        assert inner_seen["attempt"] == "1"
        assert inner_seen["run_id"] == run_id
        # ...and nothing leaked into the enclosing context afterward.
        assert baggage.get_baggage(BAGGAGE_KEY_ATTEMPT) is None
        assert baggage.get_baggage(BAGGAGE_KEY_WORKFLOW_RUN_ID) is None

    async def _assert_no_leak(
        self,
        monkeypatch: pytest.MonkeyPatch,
        executor: AsyncMock,
        *,
        expect_cancel: bool = False,
        expected_error: type[BaseException] | None = None,
    ) -> None:
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        wrapped = with_control(
            executor,
            run_id=run_id,
            node_id="node-1",
            node_name="github",
            step_config=None,
            directive_queue=queue,
        )
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", AsyncMock())

        assert baggage.get_baggage(BAGGAGE_KEY_ATTEMPT) is None
        if expect_cancel:
            with pytest.raises(WorkflowCancelledError):
                await wrapped(SimpleNamespace(input="hello"), {})
        elif expected_error is not None:
            with pytest.raises(expected_error):
                await wrapped(SimpleNamespace(input="hello"), {})
        else:
            await wrapped(SimpleNamespace(input="hello"), {})
        # Baggage attached around executor() must not leak into a sibling call in this task.
        assert baggage.get_baggage(BAGGAGE_KEY_ATTEMPT) is None
        assert baggage.get_baggage(BAGGAGE_KEY_WORKFLOW_RUN_ID) is None
        assert baggage.get_baggage(BAGGAGE_KEY_NODE_ID) is None


@pytest.mark.unit
class TestWithControlHaltsAgnoWorkflow:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("raises_exception", [True, False])
    async def test_non_skip_failure_prevents_downstream_execution(
        self, monkeypatch: pytest.MonkeyPatch, raises_exception: bool
    ):
        run_id = str(PydanticObjectId())
        queue = DirectiveQueue()
        queue.register(run_id)
        failing_executor = AsyncMock(
            side_effect=RuntimeError("downstream server exploded") if raises_exception else None,
            return_value=StepOutput(content="", success=False, error="downstream server exploded"),
        )
        downstream_calls = 0

        async def downstream_executor(step_input: object, session_state: dict | None = None) -> StepOutput:
            nonlocal downstream_calls
            downstream_calls += 1
            return StepOutput(content="should not execute")

        record_attempt_result = AsyncMock()
        monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
        monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
        monkeypatch.setattr(wrapper_module, "_record_attempt_result", record_attempt_result)

        first_step = Step(
            name="mcp_node",
            executor=with_control(
                failing_executor,
                run_id=run_id,
                node_id="node-1",
                node_name="mcp_node",
                step_config=None,
                directive_queue=queue,
            ),
            max_retries=0,
            skip_on_failure=False,
            on_error=OnError.fail,
        )
        second_step = Step(name="downstream", executor=downstream_executor)
        workflow = Workflow(id="as-1813-test", name="as-1813-test", steps=[first_step, second_step])

        with pytest.raises(RuntimeError, match="downstream server exploded"):
            await workflow.arun(input="x")

        failing_executor.assert_awaited_once()
        assert downstream_calls == 0
        record_attempt_result.assert_awaited_once()


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
            step_objective="run step",
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
                    WorkflowNode(name="a", executor_key="x", step_objective="run a"),
                    WorkflowNode(name="b", executor_key="y", step_objective="run b"),
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
            children=[WorkflowNode(name="c", executor_key="x", step_objective="run c")],
            human_review=HumanReviewSpec(requires_iteration_review=True),
        )
        assert loop_ok.human_review.requires_iteration_review is True

        # But user_input + output_review are not
        with pytest.raises(ValueError, match="requires_user_input is not supported on loop"):
            WorkflowNode(
                name="loop_bad",
                node_type="loop",
                loop_config=LoopConfig(max_iterations=3),
                children=[WorkflowNode(name="c", executor_key="x", step_objective="run c")],
                human_review=HumanReviewSpec(requires_user_input=True),
            )

    def test_condition_rejects_user_input(self):
        from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode

        with pytest.raises(ValueError, match="requires_user_input is not supported on condition"):
            WorkflowNode(
                name="c",
                node_type="condition",
                condition_cel="true",
                true_steps=[WorkflowNode(name="t", executor_key="x", step_objective="run t")],
                human_review=HumanReviewSpec(requires_user_input=True),
            )

    def test_step_rejects_iteration_review(self):
        from registry_pkgs.models.workflow import HumanReviewSpec, WorkflowNode

        with pytest.raises(ValueError, match="requires_iteration_review is not supported on step"):
            WorkflowNode(
                name="s",
                node_type="step",
                executor_key="tool",
                step_objective="run step",
                human_review=HumanReviewSpec(requires_iteration_review=True),
            )
