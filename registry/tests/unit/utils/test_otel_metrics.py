"""Labels of the metric recorders shared by the gateway and the direct proxy."""

import pytest

from registry.constants import ProxyOutcome
from registry.utils import otel_metrics


@pytest.mark.parametrize("mode", ["gateway", "proxy"])
def test_record_server_request_labels_the_mode(mock_telemetry_metrics, mode):
    otel_metrics.record_server_request("github", mode)

    mock_telemetry_metrics.record_counter.assert_called_once_with(
        "mcp_server_requests_total", 1, {"server_name": "github", "mode": mode}
    )


def test_record_proxy_request_labels_both_methods_and_the_outcome(mock_telemetry_metrics):
    otel_metrics.record_proxy_request("github", "GET", "none", ProxyOutcome.CANCELLED, 1.5)

    labels = {"server_name": "github", "http_method": "GET", "mcp_method": "none", "status": "cancelled"}
    mock_telemetry_metrics.record_counter.assert_called_once_with("mcp_proxy_requests_total", 1, labels)
    mock_telemetry_metrics.record_histogram.assert_called_once_with("mcp_proxy_request_duration_seconds", 1.5, labels)


def test_record_proxy_tool_call_labels_the_outcome(mock_telemetry_metrics):
    otel_metrics.record_proxy_tool_call("other", "github", ProxyOutcome.INPUT_REQUIRED)

    mock_telemetry_metrics.record_counter.assert_called_once_with(
        "mcp_proxy_tool_calls_total", 1, {"tool_name": "other", "server_name": "github", "status": "input_required"}
    )
