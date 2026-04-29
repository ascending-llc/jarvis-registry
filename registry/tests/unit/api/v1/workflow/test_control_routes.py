from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException, Request

from registry.api.v1.workflow.control_routes import (
    cancel_run,
    pause_run,
    resume_run,
    retry_run,
)
from registry.schemas.workflow_schemas import RetryRequest
from registry.services.workflow_control_service import WorkflowControlService
from registry_pkgs.models.enums import WorkflowRunStatus


@pytest.fixture
def sample_user_context():
    return {
        "user_id": str(PydanticObjectId()),
        "username": "testuser",
        "scopes": ["registry-admin"],
    }


@pytest.fixture
def mock_service():
    """Return a WorkflowControlService with all send_* methods mocked."""
    service = MagicMock(spec=WorkflowControlService)
    service.send_pause = AsyncMock()
    service.send_resume = AsyncMock()
    service.send_cancel = AsyncMock()
    service.send_retry = AsyncMock()
    return service


def _make_run(status: WorkflowRunStatus = WorkflowRunStatus.RUNNING) -> SimpleNamespace:
    """Build a lightweight fake WorkflowRun."""
    return SimpleNamespace(
        id=PydanticObjectId(),
        status=status,
    )


@pytest.mark.asyncio
async def test_pause_run_success(sample_user_context, mock_service):
    run = _make_run(WorkflowRunStatus.PAUSED)
    mock_service.send_pause.return_value = run

    result = await pause_run(
        workflow_id="wf-1",
        run_id="run-1",
        _current_user=sample_user_context,
        service=mock_service,
    )

    mock_service.send_pause.assert_awaited_once_with("wf-1", "run-1")
    assert result.run_id == str(run.id)
    assert result.status == WorkflowRunStatus.PAUSED
    assert result.message == "Run paused"


@pytest.mark.asyncio
async def test_pause_run_raises_http_exception(sample_user_context, mock_service):
    mock_service.send_pause.side_effect = HTTPException(status_code=400, detail="bad state")

    with pytest.raises(HTTPException) as exc_info:
        await pause_run(
            workflow_id="wf-1",
            run_id="run-1",
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad state"


@pytest.mark.asyncio
async def test_pause_run_wraps_unexpected_exception(sample_user_context, mock_service):
    mock_service.send_pause.side_effect = RuntimeError("boom")

    with pytest.raises(HTTPException) as exc_info:
        await pause_run(
            workflow_id="wf-1",
            run_id="run-1",
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"


@pytest.mark.asyncio
async def test_resume_run_success(sample_user_context, mock_service):
    run = _make_run(WorkflowRunStatus.RUNNING)
    mock_service.send_resume.return_value = run

    result = await resume_run(
        workflow_id="wf-1",
        run_id="run-1",
        _current_user=sample_user_context,
        service=mock_service,
    )

    mock_service.send_resume.assert_awaited_once_with("wf-1", "run-1")
    assert result.run_id == str(run.id)
    assert result.status == WorkflowRunStatus.RUNNING
    assert result.message == "Run resumed"


@pytest.mark.asyncio
async def test_resume_run_raises_http_exception(sample_user_context, mock_service):
    mock_service.send_resume.side_effect = HTTPException(status_code=400, detail="not paused")

    with pytest.raises(HTTPException) as exc_info:
        await resume_run(
            workflow_id="wf-1",
            run_id="run-1",
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_resume_run_wraps_unexpected_exception(sample_user_context, mock_service):
    mock_service.send_resume.side_effect = ValueError("unexpected")

    with pytest.raises(HTTPException) as exc_info:
        await resume_run(
            workflow_id="wf-1",
            run_id="run-1",
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancel_run_success(sample_user_context, mock_service):
    run = _make_run(WorkflowRunStatus.CANCELLED)
    mock_service.send_cancel.return_value = run

    result = await cancel_run(
        workflow_id="wf-1",
        run_id="run-1",
        _current_user=sample_user_context,
        service=mock_service,
    )

    mock_service.send_cancel.assert_awaited_once_with("wf-1", "run-1")
    assert result.run_id == str(run.id)
    assert result.status == WorkflowRunStatus.CANCELLED
    assert result.message == "Run cancelled"


@pytest.mark.asyncio
async def test_cancel_run_raises_http_exception(sample_user_context, mock_service):
    mock_service.send_cancel.side_effect = HTTPException(status_code=400, detail="already terminal")

    with pytest.raises(HTTPException) as exc_info:
        await cancel_run(
            workflow_id="wf-1",
            run_id="run-1",
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_run_wraps_unexpected_exception(sample_user_context, mock_service):
    mock_service.send_cancel.side_effect = ConnectionError("network fail")

    with pytest.raises(HTTPException) as exc_info:
        await cancel_run(
            workflow_id="wf-1",
            run_id="run-1",
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_run_success(sample_user_context, mock_service):
    child_run = _make_run(WorkflowRunStatus.PENDING)
    mock_service.send_retry.return_value = child_run

    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer test-token-123"}

    body = RetryRequest(from_node_id="node-2")

    result = await retry_run(
        workflow_id="wf-1",
        run_id="run-1",
        body=body,
        request=request,
        _current_user=sample_user_context,
        service=mock_service,
    )

    mock_service.send_retry.assert_awaited_once_with(
        "wf-1",
        "run-1",
        "node-2",
        registry_token="test-token-123",
        user_id=sample_user_context["user_id"],
    )
    assert result.run_id == str(child_run.id)
    assert result.status == WorkflowRunStatus.PENDING
    assert "node-2" in result.message


@pytest.mark.asyncio
async def test_retry_run_strips_bearer_prefix(sample_user_context, mock_service):
    child_run = _make_run(WorkflowRunStatus.PENDING)
    mock_service.send_retry.return_value = child_run

    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer  tok-with-space "}

    body = RetryRequest(from_node_id="node-1")

    await retry_run(
        workflow_id="wf-1",
        run_id="run-1",
        body=body,
        request=request,
        _current_user=sample_user_context,
        service=mock_service,
    )

    call_kwargs = mock_service.send_retry.call_args.kwargs
    assert call_kwargs["registry_token"] == "tok-with-space"


@pytest.mark.asyncio
async def test_retry_run_empty_auth_header(sample_user_context, mock_service):
    child_run = _make_run(WorkflowRunStatus.PENDING)
    mock_service.send_retry.return_value = child_run

    request = MagicMock(spec=Request)
    request.headers = {}

    body = RetryRequest(from_node_id="node-1")

    await retry_run(
        workflow_id="wf-1",
        run_id="run-1",
        body=body,
        request=request,
        _current_user=sample_user_context,
        service=mock_service,
    )

    call_kwargs = mock_service.send_retry.call_args.kwargs
    assert call_kwargs["registry_token"] == ""
    assert call_kwargs["user_id"] == sample_user_context["user_id"]


@pytest.mark.asyncio
async def test_retry_run_raises_http_exception(sample_user_context, mock_service):
    mock_service.send_retry.side_effect = HTTPException(status_code=400, detail="not finished")

    request = MagicMock(spec=Request)
    request.headers = {}

    body = RetryRequest(from_node_id="node-1")

    with pytest.raises(HTTPException) as exc_info:
        await retry_run(
            workflow_id="wf-1",
            run_id="run-1",
            body=body,
            request=request,
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_retry_run_wraps_unexpected_exception(sample_user_context, mock_service):
    mock_service.send_retry.side_effect = OSError("disk full")

    request = MagicMock(spec=Request)
    request.headers = {}

    body = RetryRequest(from_node_id="node-1")

    with pytest.raises(HTTPException) as exc_info:
        await retry_run(
            workflow_id="wf-1",
            run_id="run-1",
            body=body,
            request=request,
            _current_user=sample_user_context,
            service=mock_service,
        )

    assert exc_info.value.status_code == 500
