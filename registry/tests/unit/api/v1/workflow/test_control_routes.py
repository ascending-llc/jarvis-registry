from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.api.v1.workflow.control_routes import (
    approve_run,
    cancel_run,
    pause_run,
    replay_run,
    rerun_node,
    resume_run,
    retry_run,
)
from registry.schemas.workflow_schemas import ResolveRequirementRequest, RetryRequest
from registry.services.workflow_control_service import WorkflowControlService
from registry_pkgs.models.enums import RequirementResolution, WorkflowRunStatus


@pytest.fixture
def wf_id() -> str:
    """A valid ObjectId string for the workflow path parameter (ACL helper parses it)."""
    return str(PydanticObjectId())


@pytest.fixture
def sample_user_context():
    return {
        "user_id": str(PydanticObjectId()),
        "client_id": "registry-client",
        "username": "testuser",
        "groups": [],
        "scopes": ["registry-admin"],
        "auth_method": "jwt",
        "provider": "jwt",
        "auth_source": "jwt_auth",
    }


@pytest.fixture
def mock_acl():
    """ACL service whose VIEW check always passes."""
    acl = MagicMock()
    acl.check_user_permission = AsyncMock()
    return acl


@pytest.fixture
def mock_service():
    """Return a WorkflowControlService with all directive methods mocked."""
    service = MagicMock(spec=WorkflowControlService)
    service.send_pause = AsyncMock()
    service.send_resume = AsyncMock()
    service.send_cancel = AsyncMock()
    service.send_retry = AsyncMock()
    service.resolve_requirement = AsyncMock()
    service.rerun_single_node = AsyncMock()
    service.replay_run = AsyncMock()
    return service


def _make_run(status: WorkflowRunStatus = WorkflowRunStatus.RUNNING) -> SimpleNamespace:
    """Build a lightweight fake WorkflowRun."""
    return SimpleNamespace(
        id=PydanticObjectId(),
        status=status,
    )


@pytest.mark.asyncio
async def test_pause_run_success(wf_id, sample_user_context, mock_service, mock_acl):
    run = _make_run(WorkflowRunStatus.PAUSED)
    mock_service.send_pause.return_value = run

    result = await pause_run(
        workflow_id=wf_id,
        run_id="run-1",
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_acl.check_user_permission.assert_awaited_once()
    mock_service.send_pause.assert_awaited_once_with(wf_id, "run-1")
    assert result.run_id == str(run.id)
    assert result.status == WorkflowRunStatus.PAUSED
    assert result.message == "Pause directive sent"


@pytest.mark.asyncio
async def test_pause_run_raises_http_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_pause.side_effect = HTTPException(status_code=400, detail="bad state")

    with pytest.raises(HTTPException) as exc_info:
        await pause_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad state"


@pytest.mark.asyncio
async def test_pause_run_wraps_unexpected_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_pause.side_effect = RuntimeError("boom")

    with pytest.raises(HTTPException) as exc_info:
        await pause_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"


@pytest.mark.asyncio
async def test_pause_run_forbidden_without_view(wf_id, sample_user_context, mock_service, mock_acl):
    """A caller lacking VIEW on the workflow is rejected before the directive is sent."""
    mock_acl.check_user_permission.side_effect = HTTPException(status_code=403, detail="no view")

    with pytest.raises(HTTPException) as exc_info:
        await pause_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403
    mock_service.send_pause.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_run_success(wf_id, sample_user_context, mock_service, mock_acl):
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.send_resume.return_value = run

    result = await resume_run(
        workflow_id=wf_id,
        run_id="run-1",
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.send_resume.assert_awaited_once_with(wf_id, "run-1")
    assert result.run_id == str(run.id)
    assert result.status == WorkflowRunStatus.RUNNING
    assert result.message == "Resume directive sent"


@pytest.mark.asyncio
async def test_resume_run_raises_http_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_resume.side_effect = HTTPException(status_code=400, detail="not paused")

    with pytest.raises(HTTPException) as exc_info:
        await resume_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_resume_run_wraps_unexpected_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_resume.side_effect = ValueError("unexpected")

    with pytest.raises(HTTPException) as exc_info:
        await resume_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancel_run_success(wf_id, sample_user_context, mock_service, mock_acl):
    run = _make_run(WorkflowRunStatus.CANCELLED)
    mock_service.send_cancel.return_value = run

    result = await cancel_run(
        workflow_id=wf_id,
        run_id="run-1",
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.send_cancel.assert_awaited_once_with(wf_id, "run-1")
    assert result.run_id == str(run.id)
    assert result.status == WorkflowRunStatus.CANCELLED
    assert result.message == "Cancel directive sent"


@pytest.mark.asyncio
async def test_cancel_run_raises_http_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_cancel.side_effect = HTTPException(status_code=400, detail="already terminal")

    with pytest.raises(HTTPException) as exc_info:
        await cancel_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_run_wraps_unexpected_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_cancel.side_effect = ConnectionError("network fail")

    with pytest.raises(HTTPException) as exc_info:
        await cancel_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_run_success(wf_id, sample_user_context, mock_service, mock_acl):
    child_run = _make_run(WorkflowRunStatus.PENDING)
    mock_service.send_retry.return_value = child_run

    body = RetryRequest(from_node_id="node-2")

    result = await retry_run(
        workflow_id=wf_id,
        run_id="run-1",
        body=body,
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.send_retry.assert_awaited_once_with(
        wf_id,
        "run-1",
        "node-2",
        auth_context=sample_user_context,
        user_id=sample_user_context["user_id"],
    )
    assert result.run_id == str(child_run.id)
    assert result.status == WorkflowRunStatus.PENDING
    assert "node-2" in result.message


@pytest.mark.asyncio
async def test_retry_run_raises_http_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_retry.side_effect = HTTPException(status_code=400, detail="not finished")

    body = RetryRequest(from_node_id="node-1")

    with pytest.raises(HTTPException) as exc_info:
        await retry_run(
            workflow_id=wf_id,
            run_id="run-1",
            body=body,
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_retry_run_wraps_unexpected_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.send_retry.side_effect = OSError("disk full")

    body = RetryRequest(from_node_id="node-1")

    with pytest.raises(HTTPException) as exc_info:
        await retry_run(
            workflow_id=wf_id,
            run_id="run-1",
            body=body,
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Approve (HITL): /approve → resolve_requirement with 5-way resolution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_approve_run_confirm_calls_resolve_requirement(wf_id, sample_user_context, mock_service, mock_acl):
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.resolve_requirement.return_value = run

    result = await approve_run(
        workflow_id=wf_id,
        run_id="run-1",
        body=ResolveRequirementRequest(stepId="node-1", resolution=RequirementResolution.CONFIRM),
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.resolve_requirement.assert_awaited_once_with(
        wf_id,
        "run-1",
        step_id="node-1",
        resolution=RequirementResolution.CONFIRM,
        feedback=None,
        edited_output=None,
        user_input=None,
        selected_choices=None,
        auth_context=sample_user_context,
    )
    assert result.resolvedStepId == "node-1"
    assert "confirm" in result.message


@pytest.mark.asyncio
async def test_approve_run_edit_passes_edited_output(wf_id, sample_user_context, mock_service, mock_acl):
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.resolve_requirement.return_value = run

    await approve_run(
        workflow_id=wf_id,
        run_id="run-1",
        body=ResolveRequirementRequest(
            stepId="node-1",
            resolution=RequirementResolution.EDIT,
            editedOutput={"content": "human-edited content"},
        ),
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )
    call_kwargs = mock_service.resolve_requirement.await_args.kwargs
    assert call_kwargs["resolution"] == RequirementResolution.EDIT
    assert call_kwargs["edited_output"] == {"content": "human-edited content"}


@pytest.mark.asyncio
async def test_approve_run_user_input_passes_form_data(wf_id, sample_user_context, mock_service, mock_acl):
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.resolve_requirement.return_value = run

    await approve_run(
        workflow_id=wf_id,
        run_id="run-1",
        body=ResolveRequirementRequest(
            stepId="node-2",
            resolution=RequirementResolution.USER_INPUT,
            userInput={"discount_pct": 15},
        ),
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )
    call_kwargs = mock_service.resolve_requirement.await_args.kwargs
    assert call_kwargs["resolution"] == RequirementResolution.USER_INPUT
    assert call_kwargs["user_input"] == {"discount_pct": 15}


@pytest.mark.asyncio
async def test_approve_run_forbidden_without_view(wf_id, sample_user_context, mock_service, mock_acl):
    mock_acl.check_user_permission.side_effect = HTTPException(status_code=403, detail="no view")

    with pytest.raises(HTTPException) as exc_info:
        await approve_run(
            workflow_id=wf_id,
            run_id="run-1",
            body=ResolveRequirementRequest(stepId="node-1", resolution=RequirementResolution.CONFIRM),
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403
    mock_service.resolve_requirement.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_run_conflict_returns_409(wf_id, sample_user_context, mock_service, mock_acl):
    """Run not in awaiting_approval → 409 from service propagates."""
    mock_service.resolve_requirement.side_effect = HTTPException(status_code=409, detail="state changed")

    with pytest.raises(HTTPException) as exc_info:
        await approve_run(
            workflow_id=wf_id,
            run_id="run-1",
            body=ResolveRequirementRequest(stepId="node-1", resolution=RequirementResolution.CONFIRM),
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_run_reject_calls_resolve_requirement(wf_id, sample_user_context, mock_service, mock_acl):
    """Reject resolution must forward feedback to the service layer."""
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.resolve_requirement.return_value = run

    await approve_run(
        workflow_id=wf_id,
        run_id="run-1",
        body=ResolveRequirementRequest(
            stepId="node-1",
            resolution=RequirementResolution.REJECT,
            feedback="Try again with more context",
        ),
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    call_kwargs = mock_service.resolve_requirement.await_args.kwargs
    assert call_kwargs["resolution"] == RequirementResolution.REJECT
    assert call_kwargs["feedback"] == "Try again with more context"


@pytest.mark.asyncio
async def test_approve_run_reject_with_retry_policy(wf_id, sample_user_context, mock_service, mock_acl):
    """When the gate is configured with on_reject=retry, a reject decision must set
    confirmed=False so agno's continue_run can re-execute the step (bounded by
    max_retries)."""
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.resolve_requirement.return_value = run

    result = await approve_run(
        workflow_id=wf_id,
        run_id="run-1",
        body=ResolveRequirementRequest(stepId="node-1", resolution=RequirementResolution.REJECT),
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.resolve_requirement.assert_awaited_once_with(
        wf_id,
        "run-1",
        step_id="node-1",
        resolution=RequirementResolution.REJECT,
        feedback=None,
        edited_output=None,
        user_input=None,
        selected_choices=None,
        auth_context=sample_user_context,
    )
    assert result.resolvedStepId == "node-1"
    assert "reject" in result.message


@pytest.mark.asyncio
async def test_rerun_node_success(wf_id, sample_user_context, mock_service, mock_acl):
    child_run = _make_run(WorkflowRunStatus.PENDING)
    mock_service.rerun_single_node = AsyncMock(return_value=child_run)

    result = await rerun_node(
        workflow_id=wf_id,
        run_id="run-1",
        node_id="node-3",
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.rerun_single_node.assert_awaited_once_with(
        wf_id,
        "run-1",
        "node-3",
        auth_context=sample_user_context,
        user_id=sample_user_context["user_id"],
    )
    assert result.run_id == str(child_run.id)
    assert result.status == WorkflowRunStatus.PENDING
    assert "node-3" in result.message


@pytest.mark.asyncio
async def test_rerun_node_forbidden_without_view(wf_id, sample_user_context, mock_service, mock_acl):
    mock_acl.check_user_permission.side_effect = HTTPException(status_code=403, detail="Forbidden")

    with pytest.raises(HTTPException) as exc_info:
        await rerun_node(
            workflow_id=wf_id,
            run_id="run-1",
            node_id="node-3",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_rerun_node_wraps_unexpected_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.rerun_single_node = AsyncMock(side_effect=OSError("disk full"))

    with pytest.raises(HTTPException) as exc_info:
        await rerun_node(
            workflow_id=wf_id,
            run_id="run-1",
            node_id="node-3",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# replay_run: POST /replay
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replay_run_success(wf_id, sample_user_context, mock_service, mock_acl):
    new_run = _make_run(WorkflowRunStatus.PENDING)
    mock_service.replay_run = AsyncMock(return_value=new_run)

    result = await replay_run(
        workflow_id=wf_id,
        run_id="run-1",
        current_user=sample_user_context,
        service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.replay_run.assert_awaited_once_with(
        wf_id,
        "run-1",
        auth_context=sample_user_context,
        user_id=sample_user_context["user_id"],
    )
    assert result.run_id == str(new_run.id)
    assert result.status == WorkflowRunStatus.PENDING
    assert str(new_run.id) in result.message


@pytest.mark.asyncio
async def test_replay_run_forbidden_without_view(wf_id, sample_user_context, mock_service, mock_acl):
    mock_acl.check_user_permission.side_effect = HTTPException(status_code=403, detail="Forbidden")

    with pytest.raises(HTTPException) as exc_info:
        await replay_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_replay_run_wraps_unexpected_exception(wf_id, sample_user_context, mock_service, mock_acl):
    mock_service.replay_run = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(HTTPException) as exc_info:
        await replay_run(
            workflow_id=wf_id,
            run_id="run-1",
            current_user=sample_user_context,
            service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 500
