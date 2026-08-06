from __future__ import annotations

import logging

from registry_pkgs.telemetry.metrics_client import create_metrics_client

logger = logging.getLogger(__name__)

metrics = create_metrics_client("workflow")


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
    workflow_id: str,
    workflow_name: str,
    status: str,
    duration_seconds: float,
) -> None:
    attrs = {
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "status": status,
    }
    metrics.record_counter("workflow_runs_total", 1, attrs)
    metrics.record_histogram("workflow_run_seconds", duration_seconds, attrs)
