"""
Metrics client for the Auth service.

This module exports a pre-configured metrics client for recording auth metrics.

Usage:
    from auth_server.utils.otel_metrics import metrics

    # Using generic client directly
    metrics.record_counter("custom_metric", 1, {"label": "value"})
"""

import logging

from registry_pkgs.telemetry.metrics_client import create_metrics_client, load_metrics_config

from ..core.config import settings

logger = logging.getLogger(__name__)


# Load configuration and create service-specific metrics client
_config = load_metrics_config("auth_server", settings.telemetry_config)
metrics = create_metrics_client("auth_server", config=_config)
