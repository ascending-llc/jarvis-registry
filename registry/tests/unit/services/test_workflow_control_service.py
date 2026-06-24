from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import workflow_control_service as wcs
from registry.services.workflow_control_service import WorkflowControlService
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows.control import DirectiveQueue


@pytest.mark.asyncio
async def test_send_retry_child_inherits_workflow_version(monkeypatch: pytest.MonkeyPatch):
    """A retry child run must inherit the parent's workflow_version so the replay
    stays tied to the same definition version (deterministic replay)."""
    parent_run = SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        workflow_version=2,
        status=WorkflowRunStatus.FAILED,
        initial_input={"user_text": "hi"},
        definition_snapshot={
            "name": "wf",
            "version": 2,
            "nodes": [{"id": "n1", "name": "s", "node_type": "step", "executor_key": "tool"}],
        },
    )

    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.id = PydanticObjectId()

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeRun)

    from registry_pkgs.models.workflow import WorkflowNode

    class _FakeDefinition:
        def __init__(self, **kwargs):
            self.nodes = [WorkflowNode(**n) for n in kwargs.get("nodes", [])]

    monkeypatch.setattr("registry_pkgs.models.workflow.WorkflowDefinition", _FakeDefinition)

    fake_node_run = MagicMock()
    fake_node_run.find.return_value.sort.return_value.to_list = AsyncMock(return_value=[])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run)

    run_mock = AsyncMock()
    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=run_mock),
    )
    service._load_run = AsyncMock(return_value=parent_run)

    await service.send_retry(
        str(parent_run.workflow_definition_id),
        str(parent_run.id),
        "n1",
        registry_token="tok",
        user_id="user-1",
    )
    # Let the fire-and-forget runner task settle to avoid pending-task warnings.
    await asyncio.sleep(0)

    assert captured["workflow_version"] == 2
    assert captured["parent_run_id"] == parent_run.id
    assert captured["definition_snapshot"] == parent_run.definition_snapshot
    assert run_mock.await_args.kwargs["definition_snapshot"] == parent_run.definition_snapshot


@pytest.mark.asyncio
async def test_trigger_run_persists_user_id(monkeypatch: pytest.MonkeyPatch):
    """trigger_run must capture the triggering user_id onto WorkflowRun (no raw
    token is ever persisted — resume re-mints a service JWT from identity)."""
    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.id = PydanticObjectId()

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeRun)
    monkeypatch.setattr(
        "registry_pkgs.models.workflow.WorkflowDefinition.get",
        AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId())),
    )

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock()),
    )

    await service.trigger_run(
        workflow_definition_id=str(PydanticObjectId()),
        user_text="hello",
        registry_token="raw-jwt",
        user_id="user-42",
    )
    await asyncio.sleep(0)  # let the fire-and-forget runner task settle

    assert captured["triggering_user_id"] == "user-42"
    # The raw bearer token must never be persisted on the run.
    assert "triggering_registry_token_encrypted" not in captured


def test_prepare_resume_credentials_remints_service_jwt(monkeypatch: pytest.MonkeyPatch):
    """Resume re-mints a service JWT from the persisted non-sensitive identity."""
    minted = "fresh-service-jwt"
    jwt_mock = MagicMock(return_value=minted)
    monkeypatch.setattr(wcs, "generate_service_jwt", jwt_mock)

    run = SimpleNamespace(
        triggering_user_id="user-42",
        triggering_username="alice",
        triggering_scopes=["workflows-read", "workflows-control"],
    )

    token = wcs._prepare_resume_credentials(run)

    assert token == minted
    jwt_mock.assert_called_once_with(
        user_id="user-42",
        username="alice",
        scopes=["workflows-read", "workflows-control"],
    )


def test_prepare_resume_credentials_without_user_id_returns_empty(monkeypatch: pytest.MonkeyPatch):
    """Script-driven runs with no triggering_user_id resume unauthenticated ("")."""
    jwt_mock = MagicMock()
    monkeypatch.setattr(wcs, "generate_service_jwt", jwt_mock)

    run = SimpleNamespace(triggering_user_id=None, triggering_username=None, triggering_scopes=None)

    assert wcs._prepare_resume_credentials(run) == ""
    jwt_mock.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_run_handles_empty_token(monkeypatch: pytest.MonkeyPatch):
    """When no user_id is provided (script-driven), triggering_user_id stays None."""
    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.id = PydanticObjectId()

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeRun)
    monkeypatch.setattr(
        "registry_pkgs.models.workflow.WorkflowDefinition.get",
        AsyncMock(return_value=SimpleNamespace(id=PydanticObjectId())),
    )

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock()),
    )

    await service.trigger_run(
        workflow_definition_id=str(PydanticObjectId()),
        user_text="hello",
        registry_token="",  # empty
        user_id=None,
    )
    await asyncio.sleep(0)

    assert captured["triggering_user_id"] is None
    assert "triggering_registry_token_encrypted" not in captured


def _req(*, confirmed=None, timeout_at=None, step_id="s1"):
    """Build a serialized pending-requirement dict like agno's StepRequirement.to_dict()."""
    return {"step_id": step_id, "confirmed": confirmed, "timeout_at": timeout_at}


class TestHasTimedOutRequirement:
    """_has_timed_out_requirement detects unresolved requirements past timeout_at."""

    def test_past_deadline_returns_true(self):
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        run = SimpleNamespace(pending_requirements=[_req(timeout_at=past)])
        assert wcs._has_timed_out_requirement(run) is True

    def test_future_deadline_returns_false(self):
        future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        run = SimpleNamespace(pending_requirements=[_req(timeout_at=future)])
        assert wcs._has_timed_out_requirement(run) is False

    def test_no_timeout_at_returns_false(self):
        run = SimpleNamespace(pending_requirements=[_req(timeout_at=None)])
        assert wcs._has_timed_out_requirement(run) is False

    def test_already_resolved_requirement_is_ignored(self):
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        run = SimpleNamespace(pending_requirements=[_req(confirmed=True, timeout_at=past)])
        assert wcs._has_timed_out_requirement(run) is False

    def test_naive_datetime_is_treated_as_utc(self):
        past_naive = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
        run = SimpleNamespace(pending_requirements=[_req(timeout_at=past_naive)])
        assert wcs._has_timed_out_requirement(run) is True


@pytest.mark.asyncio
async def test_get_run_status_nudges_continue_run_when_requirement_timed_out(monkeypatch: pytest.MonkeyPatch):
    """A polled AWAITING_APPROVAL run with an expired requirement triggers continue_run
    so agno can apply the gate's on_timeout policy."""
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    run = SimpleNamespace(
        id=PydanticObjectId(),
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        pending_requirements=[_req(timeout_at=past)],
        triggering_user_id="user-1",
        triggering_username="u",
        triggering_scopes=[],
    )

    fake_node_run = MagicMock()
    fake_node_run.find.return_value.to_list = AsyncMock(return_value=[])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run)
    monkeypatch.setattr(wcs, "generate_service_jwt", MagicMock(return_value="svc"))

    continue_mock = AsyncMock()
    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(continue_run=continue_mock),
    )
    service._load_run = AsyncMock(return_value=run)

    await service.get_run_status(str(PydanticObjectId()), str(run.id))
    await asyncio.sleep(0)  # let the fire-and-forget resume settle

    continue_mock.assert_awaited_once()
    assert continue_mock.await_args.kwargs["existing_run_id"] == str(run.id)


@pytest.mark.asyncio
async def test_get_run_status_does_not_nudge_when_not_timed_out(monkeypatch: pytest.MonkeyPatch):
    """No expired requirement → no continue_run side effect on a status poll."""
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    run = SimpleNamespace(
        id=PydanticObjectId(),
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        pending_requirements=[_req(timeout_at=future)],
        triggering_user_id="user-1",
    )

    fake_node_run = MagicMock()
    fake_node_run.find.return_value.to_list = AsyncMock(return_value=[])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run)

    continue_mock = AsyncMock()
    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(continue_run=continue_mock),
    )
    service._load_run = AsyncMock(return_value=run)

    await service.get_run_status(str(PydanticObjectId()), str(run.id))
    await asyncio.sleep(0)

    continue_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_terminal_status",
    [
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.PAUSED,
        WorkflowRunStatus.AWAITING_APPROVAL,
        WorkflowRunStatus.PENDING,
    ],
)
async def test_rerun_single_node_rejects_non_terminal_run(non_terminal_status: WorkflowRunStatus):
    """rerun_single_node must reject runs that are not completed or failed."""
    from fastapi import HTTPException

    run = SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=non_terminal_status,
        definition_snapshot=None,
    )

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=MagicMock,
    )
    service._load_run = AsyncMock(return_value=run)

    with pytest.raises(HTTPException) as exc_info:
        await service.rerun_single_node(
            str(run.workflow_definition_id),
            str(run.id),
            node_id="node-1",
            registry_token="tok",
            user_id="user-1",
        )

    assert exc_info.value.status_code == 400
    assert "terminal" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rerun_single_node_rejects_missing_upstream_snapshot(monkeypatch: pytest.MonkeyPatch):
    """rerun_single_node must fail with 409 when an upstream node has no output_snapshot."""
    from fastapi import HTTPException

    from registry_pkgs.models.workflow import WorkflowNode

    wf_id = PydanticObjectId()
    run_id = PydanticObjectId()

    parent_run = SimpleNamespace(
        id=run_id,
        workflow_definition_id=wf_id,
        status=WorkflowRunStatus.FAILED,
        workflow_version=1,
        initial_input={},
        definition_snapshot={
            "name": "test-workflow",
            "nodes": [
                {"id": "node-1", "name": "step-1", "node_type": "step", "executor_key": "tool"},
                {"id": "node-2", "name": "step-2", "node_type": "step", "executor_key": "tool"},
            ],
        },
    )

    class _FakeDefinition:
        def __init__(self, **kwargs):
            self.nodes = [WorkflowNode(**n) for n in kwargs.get("nodes", [])]

    monkeypatch.setattr("registry_pkgs.models.workflow.WorkflowDefinition", _FakeDefinition)

    upstream_nr = SimpleNamespace(
        id=PydanticObjectId(),
        node_id="node-1",
        workflow_run_id=run_id,
        output_snapshot=None,
    )

    fake_node_run_model = MagicMock()
    fake_node_run_model.find.return_value.sort.return_value.to_list = AsyncMock(return_value=[upstream_nr])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run_model)

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=MagicMock,
    )
    service._load_run = AsyncMock(return_value=parent_run)

    with pytest.raises(HTTPException) as exc_info:
        await service.rerun_single_node(
            str(wf_id),
            str(run_id),
            node_id="node-2",
            registry_token="tok",
            user_id="user-1",
        )

    assert exc_info.value.status_code == 409
    assert "node-1" in exc_info.value.detail
    assert "output_snapshot" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rerun_single_node_uses_highest_attempt_output_on_retry(monkeypatch: pytest.MonkeyPatch):
    """When a node was retried, rerun_single_node must use the highest-attempt
    NodeRun's output_snapshot — not an earlier failed attempt's record."""
    from registry_pkgs.models.workflow import WorkflowNode

    wf_id = PydanticObjectId()
    run_id = PydanticObjectId()

    parent_run = SimpleNamespace(
        id=run_id,
        workflow_definition_id=wf_id,
        status=WorkflowRunStatus.COMPLETED,
        workflow_version=1,
        initial_input={"user_text": "hi"},
        definition_snapshot={
            "name": "wf",
            "nodes": [
                {"id": "node-1", "name": "step-1", "node_type": "step", "executor_key": "tool"},
                {"id": "node-2", "name": "step-2", "node_type": "step", "executor_key": "tool"},
            ],
        },
    )

    class _FakeDefinition:
        def __init__(self, **kwargs):
            self.nodes = [WorkflowNode(**n) for n in kwargs.get("nodes", [])]

    monkeypatch.setattr("registry_pkgs.models.workflow.WorkflowDefinition", _FakeDefinition)

    # node-1 was retried: attempt=1 failed (no output), attempt=2 succeeded.
    # Sort ascending by attempt → attempt=2 is last → wins the dict.
    failed_nr = SimpleNamespace(
        id=PydanticObjectId(),
        node_id="node-1",
        attempt=1,
        output_snapshot=None,
        session_state_snapshot=None,
    )
    success_nr = SimpleNamespace(
        id=PydanticObjectId(),
        node_id="node-1",
        attempt=2,
        output_snapshot={"content": "ok"},
        session_state_snapshot=None,
    )

    captured_injected: dict = {}

    class _FakeChildRun:
        def __init__(self, **kwargs):
            self.id = PydanticObjectId()
            self.status = WorkflowRunStatus.PENDING

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeChildRun)

    fake_node_run_model = MagicMock()
    # sorted ascending: failed_nr (attempt=1) first, success_nr (attempt=2) last
    fake_node_run_model.find.return_value.sort.return_value.to_list = AsyncMock(return_value=[failed_nr, success_nr])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run_model)

    def capture_runner(*args, **kwargs):
        captured_injected.update(kwargs.get("injected_outputs", {}))
        return AsyncMock()()

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock(side_effect=capture_runner)),
    )
    service._load_run = AsyncMock(return_value=parent_run)

    # Should succeed — highest attempt of node-1 has output_snapshot
    child = await service.rerun_single_node(
        str(wf_id),
        str(run_id),
        node_id="node-2",
        registry_token="tok",
        user_id="user-1",
    )
    await asyncio.sleep(0)

    assert child is not None
    assert "node-1" in captured_injected
    assert captured_injected["node-1"]["content"] == "ok"


@pytest.mark.asyncio
async def test_rerun_single_node_injects_nested_step_outputs_for_container_nodes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Non-STEP top-level nodes (CONDITION/PARALLEL/LOOP/ROUTER) before the target
    must have their descendant STEP outputs injected so those child steps don't
    execute for real during the node rerun."""
    from registry_pkgs.models.workflow import WorkflowNode

    wf_id = PydanticObjectId()
    run_id = PydanticObjectId()

    # Workflow: [condition-block (CONDITION with child step-A), target-step (STEP)]
    step_a_id = "step-a"
    target_id = "target"

    parent_run = SimpleNamespace(
        id=run_id,
        workflow_definition_id=wf_id,
        status=WorkflowRunStatus.COMPLETED,
        workflow_version=1,
        initial_input={"user_text": "hi"},
        definition_snapshot={
            "name": "wf",
            "nodes": [
                {
                    "id": "cond-1",
                    "name": "condition-block",
                    "node_type": "condition",
                    "condition_cel": "true",
                    "true_steps": [{"id": step_a_id, "name": "step-a", "node_type": "step", "executor_key": "tool"}],
                },
                {"id": target_id, "name": "target-step", "node_type": "step", "executor_key": "tool"},
            ],
        },
    )

    class _FakeDefinition:
        def __init__(self, **kwargs):
            self.nodes = [WorkflowNode(**n) for n in kwargs.get("nodes", [])]

    monkeypatch.setattr("registry_pkgs.models.workflow.WorkflowDefinition", _FakeDefinition)

    # step-a ran and produced output
    step_a_nr = SimpleNamespace(
        id=PydanticObjectId(),
        node_id=step_a_id,
        attempt=1,
        output_snapshot={"content": "branch-result"},
        session_state_snapshot=None,
    )

    captured_injected: dict = {}

    class _FakeChildRun:
        def __init__(self, **kwargs):
            self.id = PydanticObjectId()
            self.status = WorkflowRunStatus.PENDING

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeChildRun)

    fake_node_run_model = MagicMock()
    fake_node_run_model.find.return_value.sort.return_value.to_list = AsyncMock(return_value=[step_a_nr])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run_model)

    def capture_runner(*args, **kwargs):
        captured_injected.update(kwargs.get("injected_outputs", {}))
        return AsyncMock()()

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock(side_effect=capture_runner)),
    )
    service._load_run = AsyncMock(return_value=parent_run)

    child = await service.rerun_single_node(
        str(wf_id),
        str(run_id),
        node_id=target_id,
        registry_token="tok",
        user_id="user-1",
    )
    await asyncio.sleep(0)

    assert child is not None
    # Nested step-a inside condition-block must have its output injected
    assert step_a_id in captured_injected, "Expected nested step-a output to be injected"
    assert captured_injected[step_a_id]["content"] == "branch-result"
    # Target node must NOT be in injected_outputs (it runs for real)
    assert target_id not in captured_injected


@pytest.mark.asyncio
async def test_replay_run_sets_parent_run_id(monkeypatch: pytest.MonkeyPatch):
    """replay_run must set parent_run_id on the new WorkflowRun so the lineage
    is traceable in the UI."""
    source_run = SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.COMPLETED,
        initial_input={"user_text": "hi"},
    )

    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.id = PydanticObjectId()
            self.status = WorkflowRunStatus.PENDING

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeRun)
    monkeypatch.setattr(
        "registry_pkgs.models.workflow.WorkflowDefinition.get",
        AsyncMock(return_value=SimpleNamespace(version=1)),
    )

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock(return_value=None)),
    )
    service._load_run = AsyncMock(return_value=source_run)

    new_run = await service.replay_run(
        str(source_run.workflow_definition_id),
        str(source_run.id),
        registry_token="tok",
        user_id="user-1",
    )

    assert new_run is not None
    assert captured.get("parent_run_id") == source_run.id, (
        f"replay_run must forward parent_run_id so the lineage is traceable; got {captured.get('parent_run_id')!r}"
    )
    assert captured.get("trigger_source") == "replay"
    assert captured.get("initial_input") == {"user_text": "hi"}


async def test_replay_run_forwards_json_fallback_for_non_user_text_input(monkeypatch: pytest.MonkeyPatch):
    """Regression guard (bug AS-1656): when the source run's initial_input has no
    ``user_text`` key, replay must still forward the whole payload as JSON to the
    runner — not an empty string — so the replayed first node receives the same
    input as the original run.
    """
    payload = {"foo": "bar", "n": 3}
    source_run = SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.COMPLETED,
        initial_input=payload,
    )

    class _FakeRun:
        def __init__(self, **kwargs):
            self.id = PydanticObjectId()
            self.status = WorkflowRunStatus.PENDING

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeRun)
    monkeypatch.setattr(
        "registry_pkgs.models.workflow.WorkflowDefinition.get",
        AsyncMock(return_value=SimpleNamespace(version=1)),
    )

    run_mock = AsyncMock(return_value=None)
    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=run_mock),
    )
    service._load_run = AsyncMock(return_value=source_run)

    await service.replay_run(
        str(source_run.workflow_definition_id),
        str(source_run.id),
        registry_token="tok",
        user_id="user-1",
    )

    run_mock.assert_called_once()
    forwarded_user_text = run_mock.call_args.args[1]
    assert json.loads(forwarded_user_text) == payload, (
        f"replay must forward the full payload as JSON, not drop it; got {forwarded_user_text!r}"
    )
