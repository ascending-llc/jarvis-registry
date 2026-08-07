from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from registry_pkgs.core.config import TelemetryConfig
from registry_pkgs.telemetry.metrics_client import OTelMetricsClient, create_metrics_client, load_metrics_config

logger = logging.getLogger(__name__)

metrics = create_metrics_client("workflow")


class _ToolMetrics(Protocol):
    duration: float | None


class _ToolExecution(Protocol):
    tool_name: str | None
    tool_call_error: bool | None
    metrics: _ToolMetrics | None


def _workflow_only(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only workflow-owned metric definitions from the shared registry config."""
    if config is None:
        return None
    return {
        metric_type: [item for item in config.get(metric_type, []) if item.get("name", "").startswith("workflow_")]
        for metric_type in ("counters", "histograms")
    }


def initialize_workflow_metrics(
    telemetry_config: TelemetryConfig,
    config_path: str = "",
) -> OTelMetricsClient:
    """Load and register workflow instruments after the MeterProvider is configured."""
    global metrics

    config = load_metrics_config("registry", telemetry_config, config_path=config_path)
    metrics = create_metrics_client("workflow", config=_workflow_only(config))
    return metrics


def record_agent_invocation(
    agent_type: str,
    executor_key: str,
    success: bool,
    duration_seconds: float,
    error_type: str = "none",
) -> None:
    attrs = {
        "agent_type": agent_type,
        "executor_key": executor_key,
        "status": "success" if success else "failure",
        "error_type": error_type,
    }
    metrics.record_counter("workflow_agent_invocations_total", 1, attrs)
    metrics.record_histogram("workflow_agent_call_seconds", duration_seconds, attrs)


def record_workflow_run(
    workflow_name: str,
    status: str,
    duration_seconds: float,
) -> None:
    attrs = {
        "workflow_name": workflow_name,
        "status": status,
    }
    metrics.record_counter("workflow_runs_total", 1, attrs)
    metrics.record_histogram("workflow_run_seconds", duration_seconds, attrs)


def record_tool_calls(
    executor_key: str,
    tool_executions: Sequence[_ToolExecution],
) -> None:
    """Extract and emit per-tool metrics from agno RunOutput.tools."""
    for tool_execution in tool_executions:
        tool_name = getattr(tool_execution, "tool_name", None) or "unknown"
        is_error = getattr(tool_execution, "tool_call_error", False) or False
        attrs = {
            "executor_key": executor_key,
            "tool_name": tool_name,
            "status": "error" if is_error else "success",
        }
        metrics.record_counter("workflow_tool_calls_total", 1, attrs)

        tool_metrics = getattr(tool_execution, "metrics", None)
        if tool_metrics is not None:
            duration = getattr(tool_metrics, "duration", None)
            if duration is not None:
                metrics.record_histogram("workflow_tool_call_seconds", duration, attrs)
