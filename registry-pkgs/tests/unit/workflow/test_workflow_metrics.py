from unittest.mock import patch

import pytest
from agno.workflow import StepInput
from agno.workflow.types import StepOutput

from registry_pkgs.telemetry.workflow_metrics import (
    metrics,
    record_agent_invocation,
    record_workflow_run,
)
from registry_pkgs.workflows.executor_resolver import _detect_agent_type, _instrumented_executor


def test_workflow_metrics_client_service_name():
    assert metrics.service_name == "workflow"


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_agent_invocation_success(mock_hist, mock_ctr):
    record_agent_invocation("mcp", "my-server", True, 1.5)

    mock_ctr.assert_called_once_with(
        "workflow_agent_invocations_total",
        1,
        {"agent_type": "mcp", "executor_key": "my-server", "status": "success", "error_type": "none"},
    )
    mock_hist.assert_called_once_with(
        "workflow_agent_call_seconds",
        1.5,
        {"agent_type": "mcp", "executor_key": "my-server", "status": "success", "error_type": "none"},
    )


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_agent_invocation_failure(mock_hist, mock_ctr):
    record_agent_invocation("a2a", "agent/path", False, 2.3, error_type="TimeoutError")

    mock_ctr.assert_called_once_with(
        "workflow_agent_invocations_total",
        1,
        {"agent_type": "a2a", "executor_key": "agent/path", "status": "failure", "error_type": "TimeoutError"},
    )


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_workflow_run(mock_hist, mock_ctr):
    record_workflow_run("wf-123", "my-workflow", "completed", 45.2)

    mock_ctr.assert_called_once_with(
        "workflow_runs_total",
        1,
        {"workflow_id": "wf-123", "workflow_name": "my-workflow", "status": "completed"},
    )
    mock_hist.assert_called_once_with(
        "workflow_run_seconds",
        45.2,
        {"workflow_id": "wf-123", "workflow_name": "my-workflow", "status": "completed"},
    )


# --- _detect_agent_type ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("github_mcp_executor", "mcp"),
        ("deep-intel_a2a_executor", "a2a"),
        ("my_node_pool_executor", "a2a_pool"),
        ("builtin_echo_executor", "builtin"),
        ("something_else", "unknown"),
        ("", "unknown"),
    ],
)
def test_detect_agent_type(name, expected):
    fn = lambda: None  # noqa: E731
    fn.__name__ = name
    assert _detect_agent_type(fn) == expected


def test_detect_agent_type_no_name():
    assert _detect_agent_type(object()) == "unknown"


# --- _instrumented_executor ---


@pytest.mark.asyncio
@patch("registry_pkgs.workflows.executor_resolver.record_agent_invocation")
async def test_instrumented_executor_records_success(mock_record):
    async def _inner(step_input, session_state=None):
        return StepOutput(content="ok", success=True)

    _inner.__name__ = "test_mcp_executor"

    wrapped = _instrumented_executor(_inner, "mcp", "my-key")
    result = await wrapped(StepInput(input="hi"))

    assert result.success is True
    mock_record.assert_called_once()
    args = mock_record.call_args
    assert args[0][0] == "mcp"
    assert args[0][1] == "my-key"
    assert args[0][2] is True  # success
    assert args[0][3] > 0  # duration > 0
    assert args[0][4] == "none"  # error_type


@pytest.mark.asyncio
@patch("registry_pkgs.workflows.executor_resolver.record_agent_invocation")
async def test_instrumented_executor_records_failure_on_exception(mock_record):
    async def _inner(step_input, session_state=None):
        raise TimeoutError("timed out")

    _inner.__name__ = "test_a2a_executor"

    wrapped = _instrumented_executor(_inner, "a2a", "agent-key")
    with pytest.raises(TimeoutError):
        await wrapped(StepInput(input="hi"))

    mock_record.assert_called_once()
    args = mock_record.call_args
    assert args[0][2] is False  # success
    assert args[0][4] == "TimeoutError"


@pytest.mark.asyncio
@patch("registry_pkgs.workflows.executor_resolver.record_agent_invocation")
async def test_instrumented_executor_records_step_output_failure(mock_record):
    async def _inner(step_input, session_state=None):
        return StepOutput(content="error", success=False)

    _inner.__name__ = "test_mcp_executor"

    wrapped = _instrumented_executor(_inner, "mcp", "my-key")
    result = await wrapped(StepInput(input="hi"))

    assert result.success is False
    args = mock_record.call_args
    assert args[0][2] is False
