from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.metrics import Histogram
from opentelemetry.sdk.trace import TracerProvider

import registry_pkgs.telemetry
from registry_pkgs.core.config import TelemetryConfig
from registry_pkgs.telemetry import setup_metrics, setup_tracing, shutdown_telemetry


@pytest.mark.unit
@pytest.mark.telemetry
class TestTelemetrySetup:
    """Test suite for OpenTelemetry setup configuration."""

    @pytest.fixture(autouse=True)
    def reset_otel_state(self):
        """Reset OTel global state before and after each test."""
        yield

        # Clean up after test to prevent background thread issues
        try:
            from opentelemetry import metrics

            provider = metrics.get_meter_provider()
            if hasattr(provider, "shutdown"):
                provider.shutdown(timeout_millis=100)
        except Exception as e:
            print(e)
            pass

    @pytest.fixture
    def mock_otel_deps(self):
        """
        Fixture to mock all OpenTelemetry dependencies to prevent actual
        network calls, port binding, or global state modification.
        """
        module_path = "registry_pkgs.telemetry"

        with (
            patch(f"{module_path}.metrics") as mock_metrics,
            patch(f"{module_path}.Resource") as mock_resource,
            patch(f"{module_path}.MeterProvider") as mock_meter_provider,
            patch(f"{module_path}.PeriodicExportingMetricReader") as mock_periodic_reader,
            patch(f"{module_path}.SafeOTLPMetricExporter") as mock_safe_exporter,
            patch(f"{module_path}.trace") as mock_trace,
            patch(f"{module_path}.OTLPSpanExporter") as mock_span_exporter,
            patch(f"{module_path}.TracerProvider") as mock_tracer_provider,
            patch(f"{module_path}.BatchSpanProcessor") as mock_span_processor,
        ):
            mock_resource_instance = MagicMock()
            mock_resource.create.return_value = mock_resource_instance

            mock_safe_exporter_instance = MagicMock()
            mock_safe_exporter.return_value = mock_safe_exporter_instance

            # Ensure periodic reader mock doesn't spawn real threads
            mock_periodic_reader_instance = MagicMock()
            mock_periodic_reader.return_value = mock_periodic_reader_instance

            # Ensure meter provider mock doesn't do real work
            mock_meter_provider_instance = MagicMock()
            mock_meter_provider.return_value = mock_meter_provider_instance

            yield {
                "metrics": mock_metrics,
                "resource": mock_resource,
                "meter_provider": mock_meter_provider,
                "periodic_reader": mock_periodic_reader,
                "safe_exporter": mock_safe_exporter,
                "trace": mock_trace,
                "span_exporter": mock_span_exporter,
                "tracer_provider": mock_tracer_provider,
                "span_processor": mock_span_processor,
            }

    def test_setup_metrics_defaults(self, mock_otel_deps):
        """Test setup with default arguments (metrics=True)."""
        service_name = "test-service"
        otlp_endpoint = "http://localhost:4318"

        setup_metrics(service_name, TelemetryConfig(), otlp_endpoint=otlp_endpoint)

        mock_otel_deps["resource"].create.assert_called_once()
        _, kwargs = mock_otel_deps["resource"].create.call_args
        assert kwargs["attributes"]["service.name"] == service_name

        mock_otel_deps["safe_exporter"].assert_called_once_with(
            endpoint=f"{otlp_endpoint}/v1/metrics",
            timeout=5,
            headers=None,
        )
        mock_otel_deps["metrics"].set_meter_provider.assert_called_once()

    def test_setup_metrics_disabled(self, mock_otel_deps):
        """Test setup with metrics disabled."""
        setup_metrics("test-service", TelemetryConfig(), enable_metrics=False)

        mock_otel_deps["metrics"].set_meter_provider.assert_not_called()

    def test_workflow_bucket_view_only_matches_histograms(self, mock_otel_deps):
        setup_metrics("test-service", TelemetryConfig())

        views = mock_otel_deps["meter_provider"].call_args.kwargs["views"]
        workflow_view = views[0]
        assert workflow_view._instrument_type is Histogram

    def test_setup_prometheus_enabled(self, mock_otel_deps):
        """Test that Prometheus reader is added when env var is set."""
        with (
            patch("opentelemetry.exporter.prometheus.PrometheusMetricReader") as _mock_prom_reader,
            patch("prometheus_client.start_http_server") as mock_start_server,
        ):
            # Suppress unused variable warning - we just need to mock it
            _ = _mock_prom_reader

            setup_metrics("test-service", TelemetryConfig(otel_prometheus_enabled=True))

            mock_start_server.assert_called_once_with(port=9464, addr="0.0.0.0")

            mock_otel_deps["meter_provider"].assert_called_once()

    def test_setup_no_endpoint_env_fallback(self, mock_otel_deps):
        """Test that it falls back to env var if no endpoint provided."""
        env_endpoint = "http://env-collector:4318"
        setup_metrics("test-service", TelemetryConfig(otel_exporter_otlp_endpoint=env_endpoint), otlp_endpoint=None)
        mock_otel_deps["safe_exporter"].assert_called_with(
            endpoint=f"{env_endpoint}/v1/metrics",
            timeout=5,
            headers=None,
        )

    def test_setup_metrics_passes_gateway_bearer_token(self, mock_otel_deps):
        config = TelemetryConfig(otel_gateway_token="secret-token")

        setup_metrics("test-service", config, otlp_endpoint="http://collector:4318")

        mock_otel_deps["safe_exporter"].assert_called_once_with(
            endpoint="http://collector:4318/v1/metrics",
            timeout=5,
            headers={"Authorization": "Bearer secret-token"},
        )

    def test_setup_handles_initialization_failure(self, mock_otel_deps):
        """Test that initialization failure is caught and logged."""
        mock_otel_deps["meter_provider"].side_effect = Exception("Init Failed")

        # Should not raise - errors are suppressed
        setup_metrics("test-service", TelemetryConfig(), enable_metrics=True)

        # set_meter_provider should not be called since MeterProvider failed
        mock_otel_deps["metrics"].set_meter_provider.assert_not_called()


@pytest.mark.unit
@pytest.mark.telemetry
class TestSafeOTLPMetricExporter:
    """Test suite for SafeOTLPMetricExporter wrapper."""

    def test_safe_exporter_suppresses_export_errors(self):
        """Test that export errors are suppressed."""
        from registry_pkgs.telemetry import SafeOTLPMetricExporter

        with patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter") as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter.export.side_effect = Exception("Export failed")
            mock_exporter_class.return_value = mock_exporter

            safe_exporter = SafeOTLPMetricExporter(endpoint="http://localhost:4318/v1/metrics")

            # Should not raise
            result = safe_exporter.export(MagicMock())
            assert result is None

    def test_safe_exporter_returns_value_on_success(self):
        """Test that export returns value when successful."""
        from registry_pkgs.telemetry import SafeOTLPMetricExporter

        with patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter") as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter.export.return_value = "success"
            mock_exporter_class.return_value = mock_exporter

            safe_exporter = SafeOTLPMetricExporter(endpoint="http://localhost:4318/v1/metrics")

            result = safe_exporter.export(MagicMock())
            assert result == "success"

    def test_safe_exporter_handles_creation_failure(self):
        """Test that exporter creation failure is handled gracefully."""
        from registry_pkgs.telemetry import SafeOTLPMetricExporter

        with patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter") as mock_exporter_class:
            mock_exporter_class.side_effect = Exception("Connection refused")

            safe_exporter = SafeOTLPMetricExporter(endpoint="http://localhost:4318/v1/metrics")

            # Should not raise, and export should be a no-op
            result = safe_exporter.export(MagicMock())
            assert result is None

    def test_safe_exporter_shutdown_suppresses_errors(self):
        """Test that shutdown errors are suppressed."""
        from registry_pkgs.telemetry import SafeOTLPMetricExporter

        with patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter") as mock_exporter_class:
            mock_exporter = MagicMock()
            mock_exporter.shutdown.side_effect = Exception("Shutdown failed")
            mock_exporter_class.return_value = mock_exporter

            safe_exporter = SafeOTLPMetricExporter(endpoint="http://localhost:4318/v1/metrics")

            # Should not raise
            safe_exporter.shutdown()


@pytest.mark.unit
@pytest.mark.telemetry
class TestShutdownTelemetry:
    """Test suite for shutdown_telemetry function."""

    def test_shutdown_telemetry_calls_provider_shutdown(self):
        """Test that shutdown calls the meter provider's shutdown method."""
        with patch("registry_pkgs.telemetry.metrics.get_meter_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_get_provider.return_value = mock_provider

            shutdown_telemetry()

            mock_provider.shutdown.assert_called_once_with(timeout_millis=1000)

    def test_shutdown_telemetry_handles_missing_shutdown(self):
        """Test that shutdown handles providers without shutdown method."""
        with patch("registry_pkgs.telemetry.metrics.get_meter_provider") as mock_get_provider:
            mock_provider = MagicMock(spec=[])  # No shutdown method
            mock_get_provider.return_value = mock_provider

            # Should not raise
            shutdown_telemetry()

    def test_shutdown_telemetry_suppresses_errors(self):
        """Test that shutdown errors are suppressed."""
        with patch("registry_pkgs.telemetry.metrics.get_meter_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.shutdown.side_effect = Exception("Shutdown error")
            mock_get_provider.return_value = mock_provider

            # Should not raise
            shutdown_telemetry()

    def test_shutdown_telemetry_shuts_down_trace_provider(self):
        mock_tracer_provider = MagicMock(spec=TracerProvider)
        with (
            patch("registry_pkgs.telemetry.trace.get_tracer_provider", return_value=mock_tracer_provider),
            patch("registry_pkgs.telemetry.metrics.get_meter_provider") as mock_get_provider,
        ):
            shutdown_telemetry()

        mock_tracer_provider.shutdown.assert_called_once_with()
        mock_get_provider.return_value.shutdown.assert_called_once_with(timeout_millis=1000)


@pytest.mark.unit
@pytest.mark.telemetry
class TestSetupTracing:
    """Test suite for setup_tracing"""

    @pytest.fixture(autouse=True)
    def reset_otel_trace_state(self):
        registry_pkgs.telemetry._agno_instrumented = False
        yield
        try:
            provider = trace.get_tracer_provider()
            if isinstance(provider, TracerProvider):
                provider.shutdown()
        except Exception:  # best-effort teardown; provider may already be shut down
            pass
        registry_pkgs.telemetry._agno_instrumented = False

    def test_setup_tracing_installs_tracer_provider(self):
        """After setup_tracing(), global TracerProvider is a real TracerProvider."""
        from opentelemetry.sdk.trace import TracerProvider

        mock_instrumentor_type = MagicMock()
        mock_trace_config_type = MagicMock()
        with (
            patch("opentelemetry.trace.get_tracer_provider", return_value=MagicMock()),
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
            patch(
                "registry_pkgs.telemetry._load_agno_instrumentation",
                return_value=(mock_instrumentor_type, mock_trace_config_type),
            ),
            patch("registry_pkgs.telemetry.OTLPSpanExporter"),
            patch("registry_pkgs.telemetry.BatchSpanProcessor"),
        ):
            setup_tracing("test-service", TelemetryConfig(), otlp_endpoint="http://localhost:4318")

            mock_set.assert_called_once()
            tp_arg = mock_set.call_args[0][0]
            assert isinstance(tp_arg, TracerProvider)
            mock_instrumentor_type.return_value.instrument.assert_called_once()
            _, instrument_kwargs = mock_instrumentor_type.return_value.instrument.call_args
            assert instrument_kwargs["tracer_provider"] is tp_arg
            assert instrument_kwargs["config"] is mock_trace_config_type.return_value
            mock_trace_config_type.assert_called_once_with(
                hide_inputs=True,
                hide_outputs=True,
                hide_llm_tools=True,
                hide_llm_invocation_parameters=True,
            )

    def test_setup_tracing_is_idempotent(self):
        """Second call is a no-op when TracerProvider already set."""
        from opentelemetry.sdk.trace import TracerProvider

        existing_provider = TracerProvider()
        mock_instrumentor_type = MagicMock()
        with (
            patch("opentelemetry.trace.get_tracer_provider", return_value=existing_provider),
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
            patch(
                "registry_pkgs.telemetry._load_agno_instrumentation",
                return_value=(mock_instrumentor_type, MagicMock()),
            ),
            patch("registry_pkgs.telemetry.OTLPSpanExporter"),
            patch("registry_pkgs.telemetry.BatchSpanProcessor"),
        ):
            setup_tracing("test-service", TelemetryConfig())
            setup_tracing("test-service", TelemetryConfig())

            mock_set.assert_not_called()
            mock_instrumentor_type.return_value.instrument.assert_called_once()

        existing_provider.shutdown()

    def test_setup_tracing_graceful_without_openinference(self):
        """When openinference not installed, logs warning and returns."""
        with (
            patch("registry_pkgs.telemetry._load_agno_instrumentation", side_effect=ImportError("missing")),
            patch("opentelemetry.trace.set_tracer_provider") as mock_set,
        ):
            registry_pkgs.telemetry.setup_tracing("test-service", TelemetryConfig())
            mock_set.assert_not_called()

    def test_setup_tracing_uses_same_resource_as_metrics(self):
        """setup_tracing() uses _build_resource() with same args pattern as setup_metrics()."""
        with (
            patch("registry_pkgs.telemetry._build_resource") as mock_build,
            patch("opentelemetry.trace.get_tracer_provider", return_value=MagicMock()),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("registry_pkgs.telemetry._load_agno_instrumentation", return_value=(MagicMock(), MagicMock())),
            patch("registry_pkgs.telemetry.OTLPSpanExporter"),
            patch("registry_pkgs.telemetry.BatchSpanProcessor"),
        ):
            mock_build.return_value = MagicMock()
            config = TelemetryConfig(build_version="v1.2.3")
            setup_tracing("my-service", config)

            mock_build.assert_called_once_with("my-service", config)
