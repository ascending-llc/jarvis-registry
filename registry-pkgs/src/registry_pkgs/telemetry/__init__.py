import logging
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ..core.config import TelemetryConfig
from .decorators import (
    create_timed_context,
    track_duration,
)

__all__ = [
    "setup_metrics",
    "setup_tracing",
    "shutdown_telemetry",
    "LATENCY_BUCKETS",
    "WORKFLOW_LATENCY_BUCKETS",
    "track_duration",
    "create_timed_context",
]


logger = logging.getLogger(__name__)

_tracer_provider: TracerProvider | None = None

# Histogram bucket boundaries for latency metrics (in seconds)
# These buckets are designed to capture p50, p95, p99 accurately
LATENCY_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]

# Wider buckets for workflow agent calls and runs (typical range: 1s–300s).
# Histogram names use ``workflow_*`` prefix (without ``duration``) to avoid
# double-matching by the ``*duration*`` View below.
WORKFLOW_LATENCY_BUCKETS = [0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 75.0, 100.0, 150.0, 200.0, 300.0]

# OTel semantic-convention resource attribute key. Declared as a literal to avoid
# pulling in the optional `opentelemetry-semconv` dependency.
_SERVICE_VERSION = "service.version"
_OTLP_EXPORT_TIMEOUT_SECONDS = 5
_TRACE_MAX_QUEUE_SIZE = 2048
_TRACE_MAX_EXPORT_BATCH_SIZE = 512
_TRACE_SCHEDULE_DELAY_MILLIS = 5000

_agno_instrumented = False
_trace_exporter_configured = False


def _otlp_headers(telemetry_config: TelemetryConfig) -> dict[str, str] | None:
    token = telemetry_config.otel_gateway_token.get_secret_value()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _load_agno_instrumentation() -> tuple[type[Any], type[Any]]:
    """Import optional Agno tracing dependencies only when tracing is configured."""
    from openinference.instrumentation import TraceConfig
    from openinference.instrumentation.agno import AgnoInstrumentor

    return AgnoInstrumentor, TraceConfig


class SafeOTLPMetricExporter:
    """Wrapper that catches all exceptions during export."""

    def __init__(
        self,
        endpoint: str,
        timeout: int = _OTLP_EXPORT_TIMEOUT_SECONDS,
        headers: dict[str, str] | None = None,
    ):
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

            self._exporter = OTLPMetricExporter(endpoint=endpoint, timeout=timeout, headers=headers)
            self._endpoint = endpoint
        except Exception as e:
            logger.warning(f"Failed to create OTLP metric exporter: {e}")
            self._exporter = None

    @property
    def _preferred_temporality(self):
        """Delegate to underlying exporter."""
        if self._exporter:
            return self._exporter._preferred_temporality
        return {}

    @property
    def _preferred_aggregation(self):
        """Delegate to underlying exporter."""
        if self._exporter:
            return self._exporter._preferred_aggregation
        return {}

    def export(self, *args, **kwargs):
        """Export with error suppression."""
        if not self._exporter:
            return None
        try:
            return self._exporter.export(*args, **kwargs)
        except Exception:
            # Silently suppress export errors to prevent crashes
            # This can happen during test cleanup when streams are closed
            return None

    def shutdown(self, *args, **kwargs):
        """Shutdown with error suppression."""
        if not self._exporter:
            return
        try:
            return self._exporter.shutdown(*args, **kwargs)
        except Exception:  # nosec B110 - intentional suppression during teardown
            pass

    def force_flush(self, *args, **kwargs):
        """Force flush with error suppression."""
        if not self._exporter:
            return
        try:
            return self._exporter.force_flush(*args, **kwargs)
        except Exception:  # nosec B110 - intentional suppression during teardown
            pass


def _build_resource(service_name: str, telemetry_config: TelemetryConfig) -> Resource:
    """
    Build the OTel Resource with standard identifying attributes.

    Beyond service.name, include service.version so metrics can be correlated
    per-version in dashboards (industry-standard resource attribute).
    """
    return Resource.create(
        attributes={
            SERVICE_NAME: service_name,
            _SERVICE_VERSION: telemetry_config.build_version,
        }
    )


def setup_metrics(
    service_name: str,
    telemetry_config: TelemetryConfig,
    otlp_endpoint: str | None = None,
    enable_metrics: bool = True,
) -> None:
    """
    Configures OTel Metrics to send to a collector.
    Will NOT crash even if collector is unavailable - errors are suppressed.
    """
    logger.info("Setting up telemetry...")

    try:
        otlp_endpoint = otlp_endpoint or telemetry_config.otel_exporter_otlp_endpoint

        resource = _build_resource(service_name, telemetry_config)

        # Setup Metrics
        if enable_metrics:
            try:
                readers = []

                # Prometheus setup
                if telemetry_config.otel_prometheus_enabled:
                    try:
                        from opentelemetry.exporter.prometheus import PrometheusMetricReader
                        from prometheus_client import start_http_server

                        port = telemetry_config.otel_prometheus_port
                        start_http_server(port=port, addr="0.0.0.0")
                        readers.append(PrometheusMetricReader())
                        logger.info(f"Prometheus metrics enabled on port {port}")
                    except Exception as e:
                        logger.warning(f"Prometheus setup failed: {e}")

                # OTLP setup with safe wrapper
                if otlp_endpoint:
                    try:
                        safe_exporter = SafeOTLPMetricExporter(
                            endpoint=f"{otlp_endpoint}/v1/metrics",
                            timeout=_OTLP_EXPORT_TIMEOUT_SECONDS,
                            headers=_otlp_headers(telemetry_config),
                        )
                        reader = PeriodicExportingMetricReader(
                            safe_exporter, export_interval_millis=60000, export_timeout_millis=5000
                        )
                        readers.append(reader)
                        logger.info(f"OTLP metrics configured for {otlp_endpoint}")
                    except Exception as e:
                        logger.warning(f"OTLP metrics setup failed: {e}")

                views = [
                    View(
                        instrument_type=Histogram,
                        instrument_name="workflow_*",
                        aggregation=ExplicitBucketHistogramAggregation(boundaries=WORKFLOW_LATENCY_BUCKETS),
                    ),
                    View(
                        instrument_name="*duration*",
                        aggregation=ExplicitBucketHistogramAggregation(boundaries=LATENCY_BUCKETS),
                    ),
                ]
                # Always set meter provider with views so that histogram
                # bucket boundaries are applied regardless of reader config.
                provider = MeterProvider(resource=resource, metric_readers=readers, views=views)
                metrics.set_meter_provider(provider)
                if readers:
                    logger.info(f"Metrics initialized with {len(readers)} reader(s)")
                else:
                    logger.warning("No metric readers configured - metrics will not be exported")

            except Exception as e:
                logger.warning(f"Metrics setup failed: {e}")

        logger.info("Telemetry setup complete")

    except Exception as e:
        logger.warning(f"Telemetry initialization failed: {e}")


def setup_tracing(
    service_name: str,
    telemetry_config: TelemetryConfig,
    otlp_endpoint: str | None = None,
) -> None:
    """Configure OTel distributed tracing with OTLP export for agno agents.

    Uses AgnoInstrumentor to auto-instrument all agno Agent/Model/Tool calls.
    Shares the same OTLP collector endpoint and Resource as setup_metrics().
    """
    global _agno_instrumented, _trace_exporter_configured, _tracer_provider

    logger.info("Setting up tracing...")
    try:
        instrumentor_type, trace_config_type = _load_agno_instrumentation()
        current_provider = trace.get_tracer_provider()
        provider_is_new = not isinstance(current_provider, TracerProvider)
        tracer_provider = (
            TracerProvider(resource=_build_resource(service_name, telemetry_config))
            if provider_is_new
            else current_provider
        )

        if not _agno_instrumented:
            trace_config = trace_config_type(
                hide_inputs=telemetry_config.otel_trace_hide_inputs,
                hide_outputs=telemetry_config.otel_trace_hide_outputs,
                hide_llm_tools=telemetry_config.otel_trace_hide_llm_tools,
                hide_llm_invocation_parameters=telemetry_config.otel_trace_hide_llm_invocation_parameters,
            )
            instrumentor_type().instrument(tracer_provider=tracer_provider, config=trace_config)
            _agno_instrumented = True

        otlp_endpoint = otlp_endpoint or telemetry_config.otel_exporter_otlp_endpoint
        if otlp_endpoint and not _trace_exporter_configured:
            exporter = OTLPSpanExporter(
                endpoint=f"{otlp_endpoint}/v1/traces",
                timeout=_OTLP_EXPORT_TIMEOUT_SECONDS,
                headers=_otlp_headers(telemetry_config),
            )
            processor = BatchSpanProcessor(
                exporter,
                max_queue_size=_TRACE_MAX_QUEUE_SIZE,
                max_export_batch_size=_TRACE_MAX_EXPORT_BATCH_SIZE,
                schedule_delay_millis=_TRACE_SCHEDULE_DELAY_MILLIS,
            )
            tracer_provider.add_span_processor(processor)
            _trace_exporter_configured = True

        if provider_is_new:
            trace.set_tracer_provider(tracer_provider)
            _tracer_provider = tracer_provider
        logger.info("Agno tracing initialized (OTLP endpoint: %s)", otlp_endpoint)
    except Exception as exc:
        logger.warning("Failed to setup agno tracing: %s", exc)


def shutdown_telemetry() -> None:
    """Gracefully shutdown telemetry providers."""
    global _trace_exporter_configured, _tracer_provider

    try:
        if _tracer_provider is not None:
            _tracer_provider.shutdown()
            _tracer_provider = None
    except Exception:  # nosec B110 - intentional suppression during teardown
        pass

    try:
        provider = metrics.get_meter_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown(timeout_millis=1000)
    except Exception as exc:
        logger.warning("Failed to shutdown metrics: %s", exc)

    _trace_exporter_configured = False
