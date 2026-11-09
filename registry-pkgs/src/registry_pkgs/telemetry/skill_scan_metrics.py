"""Metrics client for the skill-scan worker, mirroring ``workflow_metrics`` (filtered by the ``skill_scan_`` prefix).

Lives in registry-pkgs because the worker may depend only on registry-pkgs (CLAUDE.md workspace boundaries).
Logs never include skill content, environment values or API keys.
"""

from __future__ import annotations

import logging
from typing import Any

from registry_pkgs.core.config import TelemetryConfig
from registry_pkgs.telemetry.metrics_client import OTelMetricsClient, create_metrics_client, load_metrics_config

logger = logging.getLogger(__name__)

_PREFIX = "skill_scan_"

metrics = create_metrics_client("skill_scan")


def _skill_scan_only(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only skill-scan-owned metric definitions from the shared registry config."""
    if config is None:
        return None
    return {
        metric_type: [item for item in config.get(metric_type, []) if item.get("name", "").startswith(_PREFIX)]
        for metric_type in ("counters", "histograms")
    }


def initialize_skill_scan_metrics(telemetry_config: TelemetryConfig, config_path: str = "") -> OTelMetricsClient:
    """Load and register skill-scan instruments after the MeterProvider is configured."""
    global metrics
    config = load_metrics_config("registry", telemetry_config, config_path=config_path)
    metrics = create_metrics_client("skill_scan", config=_skill_scan_only(config))
    return metrics


def record_tool_run(tool: str, status: str, status_reason: str | None) -> None:
    metrics.record_counter(
        "skill_scan_tool_runs_total",
        1,
        {"tool": tool, "status": status, "status_reason": status_reason or "none"},
    )


def record_tool_duration(tool: str, duration_seconds: float) -> None:
    metrics.record_histogram("skill_scan_tool_duration_seconds", duration_seconds, {"tool": tool})


def record_llm_tokens(tool: str, direction: str, tokens: int) -> None:
    metrics.record_counter("skill_scan_llm_tokens_total", tokens, {"tool": tool, "direction": direction})


def record_job_finished(outcome: str) -> None:
    metrics.record_counter("skill_scan_jobs_finished_total", 1, {"outcome": outcome})
