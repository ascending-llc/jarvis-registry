from __future__ import annotations

import asyncio
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
    fake_node_run.find.return_value.to_list = AsyncMock(return_value=[])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run)

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock()),
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
