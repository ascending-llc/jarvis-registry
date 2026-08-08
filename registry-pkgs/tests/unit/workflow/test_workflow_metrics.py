import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agno.workflow import StepInput
from agno.workflow.types import StepOutput
from opentelemetry.metrics import Histogram as HistogramInstrument
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader, Sum
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

import registry_pkgs.telemetry.workflow_metrics as workflow_metrics_module
from registry_pkgs.core.config import TelemetryConfig
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.telemetry import WORKFLOW_LATENCY_BUCKETS
from registry_pkgs.telemetry.metrics_client import create_metrics_client
from registry_pkgs.telemetry.workflow_metrics import (
    initialize_workflow_metrics,
    metrics,
    record_agent_invocation,
    record_tool_calls,
    record_workflow_run,
)
from registry_pkgs.workflows.executor_resolver import _detect_agent_type, _instrumented_executor
from registry_pkgs.workflows.runner import WorkflowRunner


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
    record_workflow_run("my-workflow", "completed", 45.2)

    mock_ctr.assert_called_once_with(
        "workflow_runs_total",
        1,
        {"workflow_name": "my-workflow", "status": "completed"},
    )
    mock_hist.assert_called_once_with(
        "workflow_run_seconds",
        45.2,
        {"workflow_name": "my-workflow", "status": "completed"},
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
    assert args[0][4] == "StepOutputFailure"


@pytest.mark.asyncio
@patch("registry_pkgs.workflows.executor_resolver.record_agent_invocation")
async def test_instrumented_executor_records_cancellation(mock_record):
    async def _inner(step_input, session_state=None):
        raise asyncio.CancelledError

    wrapped = _instrumented_executor(_inner, "mcp", "my-key")
    with pytest.raises(asyncio.CancelledError):
        await wrapped(StepInput(input="hi"))

    args = mock_record.call_args
    assert args[0][2] is False
    assert args[0][4] == "CancelledError"


class _FakeToolMetrics:
    def __init__(self, duration: float | None = None):
        self.duration = duration


class _FakeToolExecution:
    def __init__(
        self,
        tool_name: str | None = None,
        tool_call_error: bool | None = None,
        metrics: _FakeToolMetrics | None = None,
    ):
        self.tool_name = tool_name
        self.tool_call_error = tool_call_error
        self.metrics = metrics


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_tool_calls_emits_per_tool_metrics(mock_hist, mock_ctr):
    tools = [
        _FakeToolExecution("list_repos", False, _FakeToolMetrics(3.2)),
        _FakeToolExecution("get_issues", False, _FakeToolMetrics(5.0)),
    ]
    record_tool_calls("github-server", tools)

    assert mock_ctr.call_count == 2
    assert mock_hist.call_count == 2
    first_ctr = mock_ctr.call_args_list[0]
    assert first_ctr[0] == (
        "workflow_tool_calls_total",
        1,
        {"executor_key": "github-server", "tool_name": "list_repos", "status": "success"},
    )
    first_hist = mock_hist.call_args_list[0]
    assert first_hist[0] == (
        "workflow_tool_call_seconds",
        3.2,
        {"executor_key": "github-server", "tool_name": "list_repos", "status": "success"},
    )


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_tool_calls_handles_missing_metrics(mock_hist, mock_ctr):
    tools = [_FakeToolExecution("my_tool", False, None)]
    record_tool_calls("server-a", tools)

    mock_ctr.assert_called_once()
    mock_hist.assert_not_called()


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_tool_calls_handles_error_status(mock_hist, mock_ctr):
    tools = [_FakeToolExecution("bad_tool", True, _FakeToolMetrics(1.0))]
    record_tool_calls("server-b", tools)

    attrs = mock_ctr.call_args[0][2]
    assert attrs["status"] == "error"


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_tool_calls_empty_list(mock_hist, mock_ctr):
    record_tool_calls("server-c", [])

    mock_ctr.assert_not_called()
    mock_hist.assert_not_called()


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_tool_calls_missing_tool_name(mock_hist, mock_ctr):
    tools = [_FakeToolExecution(None, False, _FakeToolMetrics(2.0))]
    record_tool_calls("server-d", tools)

    attrs = mock_ctr.call_args[0][2]
    assert attrs["tool_name"] == "unknown"


@patch.object(metrics, "record_counter")
@patch.object(metrics, "record_histogram")
def test_record_tool_calls_missing_duration(mock_hist, mock_ctr):
    tools = [_FakeToolExecution("my_tool", False, _FakeToolMetrics(None))]
    record_tool_calls("server-e", tools)

    mock_ctr.assert_called_once()
    mock_hist.assert_not_called()


def test_initialize_workflow_metrics_registers_only_workflow_instruments(tmp_path):
    config_path = tmp_path / "registry.yml"
    config_path.write_text(
        """
counters:
  - name: registry_operations_total
  - name: workflow_runs_total
histograms:
  - name: workflow_run_seconds
""",
        encoding="utf-8",
    )

    original_metrics = workflow_metrics_module.metrics
    try:
        client = initialize_workflow_metrics(TelemetryConfig(), config_path=str(config_path))

        assert set(client._counters) == {"workflow_runs_total"}
        assert set(client._histogram_configs) == {"workflow_run_seconds"}
    finally:
        workflow_metrics_module.metrics = original_metrics


def test_workflow_metrics_export_counter_and_histogram_with_expected_types():
    reader = InMemoryMetricReader()
    provider = MeterProvider(
        metric_readers=[reader],
        views=[
            View(
                instrument_type=HistogramInstrument,
                instrument_name="workflow_*",
                aggregation=ExplicitBucketHistogramAggregation(boundaries=WORKFLOW_LATENCY_BUCKETS),
            )
        ],
    )
    config = {
        "counters": [{"name": "workflow_runs_total"}],
        "histograms": [{"name": "workflow_run_seconds", "unit": "s"}],
    }

    try:
        with patch(
            "registry_pkgs.telemetry.metrics_client.metrics.get_meter",
            return_value=provider.get_meter("workflow-test"),
        ):
            client = create_metrics_client("workflow", config=config)
            client.record_counter("workflow_runs_total", 1, {"status": "completed"})
            client.record_histogram("workflow_run_seconds", 12.0, {"status": "completed"})

        metrics_data = reader.get_metrics_data()
        exported = {
            metric.name: metric.data
            for resource_metrics in metrics_data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }
        assert isinstance(exported["workflow_runs_total"], Sum)
        assert isinstance(exported["workflow_run_seconds"], Histogram)
        assert exported["workflow_run_seconds"].data_points[0].explicit_bounds == tuple(WORKFLOW_LATENCY_BUCKETS)
    finally:
        provider.shutdown()


@patch("registry_pkgs.workflows.runner.record_workflow_run")
def test_runner_records_only_terminal_run_with_end_to_end_duration(mock_record):
    started_at = datetime.now(UTC) - timedelta(seconds=30)
    terminal_run = SimpleNamespace(
        status=WorkflowRunStatus.COMPLETED,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=25),
    )
    paused_run = SimpleNamespace(
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        started_at=started_at,
        finished_at=None,
    )

    WorkflowRunner._record_run_metrics("my-workflow", paused_run)
    WorkflowRunner._record_run_metrics("my-workflow", terminal_run)

    mock_record.assert_called_once_with(
        workflow_name="my-workflow",
        status="completed",
        duration_seconds=25.0,
    )
