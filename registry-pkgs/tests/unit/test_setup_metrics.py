from unittest.mock import patch

import pytest

from registry_pkgs.core.config import TelemetryConfig
from registry_pkgs.telemetry import setup_metrics


def test_setup_metrics_requires_service_name():
    """Test that setup_metrics requires service_name and uses it."""
    with pytest.raises(TypeError):
        setup_metrics()


@patch("registry_pkgs.telemetry.Resource")
@patch("registry_pkgs.telemetry.MeterProvider")
@patch("registry_pkgs.telemetry.metrics")
def test_setup_metrics_uses_service_name(mock_metrics, mock_provider, mock_resource):
    """Verify setup_metrics uses the provided service_name."""
    setup_metrics(service_name="test-service", telemetry_config=TelemetryConfig(), enable_metrics=False)

    mock_resource.create.assert_called_once()
    call_kwargs = mock_resource.create.call_args[1]
    assert call_kwargs["attributes"]["service.name"] == "test-service"


@patch("registry_pkgs.telemetry.Resource")
@patch("registry_pkgs.telemetry.MeterProvider")
@patch("registry_pkgs.telemetry.metrics")
@patch("registry_pkgs.telemetry.PeriodicExportingMetricReader")
@patch("registry_pkgs.telemetry.SafeOTLPMetricExporter")
def test_setup_metrics_with_otlp_endpoint(mock_exporter, mock_reader, mock_metrics, mock_provider, mock_resource):
    """Verify setup_metrics configures OTLP exporter when endpoint is provided."""
    setup_metrics(
        service_name="test-service",
        telemetry_config=TelemetryConfig(),
        otlp_endpoint="http://localhost:4318",
        enable_metrics=True,
    )

    mock_exporter.assert_called_once()
    call_args = mock_exporter.call_args
    assert "http://localhost:4318/v1/metrics" in str(call_args)


@patch("registry_pkgs.telemetry.Resource")
@patch("registry_pkgs.telemetry.MeterProvider")
@patch("registry_pkgs.telemetry.metrics")
def test_setup_metrics_disabled(mock_metrics, mock_provider, mock_resource):
    """Verify setup_metrics does not configure metrics when disabled."""
    setup_metrics(service_name="test-service", telemetry_config=TelemetryConfig(), enable_metrics=False)

    mock_provider.assert_not_called()
