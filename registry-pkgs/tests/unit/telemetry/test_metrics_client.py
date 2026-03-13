import logging
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from registry_pkgs.core.config import TelemetryConfig
from registry_pkgs.telemetry.metrics_client import (
    OTelMetricsClient,
    create_metrics_client,
    load_metrics_config,
    logger,
    safe_telemetry,
)


@pytest.mark.unit
@pytest.mark.metrics
class TestOTelMetricsClient:
    """Test suite for OTelMetricsClient."""

    @pytest.fixture
    def mock_meter(self):
        """Fixture to mock the OpenTelemetry meter and its instruments."""
        with patch("registry_pkgs.telemetry.metrics_client.metrics.get_meter") as mock_get_meter:
            meter_instance = MagicMock()
            mock_get_meter.return_value = meter_instance

            meter_instance.create_counter.return_value = MagicMock()
            meter_instance.create_histogram.return_value = MagicMock()

            yield mock_get_meter, meter_instance

    @pytest.fixture
    def sample_config(self):
        """Sample config with common metrics."""
        return {
            "counters": [
                {"name": "requests_total", "description": "Total requests", "unit": "1"},
                {"name": "errors_total", "description": "Total errors", "unit": "1"},
            ],
            "histograms": [
                {"name": "request_duration_seconds", "description": "Request duration", "unit": "s"},
            ],
        }

    def test_init_creates_meter(self, mock_meter):
        """Test initialization creates meter with service name."""
        mock_get_meter, meter_instance = mock_meter
        service_name = "test-service"

        OTelMetricsClient(service_name)

        mock_get_meter.assert_called_once_with(f"mcp.{service_name}")

    def test_init_with_config_creates_metrics(self, mock_meter, sample_config):
        """Test initialization with config creates counters and defers histograms."""
        mock_get_meter, meter_instance = mock_meter

        client = OTelMetricsClient("test-service", config=sample_config)

        # Should create 2 counters eagerly from config
        assert meter_instance.create_counter.call_count == 2
        # Histograms are deferred - not created at init time
        assert meter_instance.create_histogram.call_count == 0

        # Verify counters are registered
        assert "requests_total" in client._counters
        assert "errors_total" in client._counters
        # Histogram is deferred, not yet in _histograms
        assert "request_duration_seconds" in client._histogram_configs

    def test_init_without_config_creates_empty_registries(self, mock_meter):
        """Test initialization without config creates empty registries."""
        mock_get_meter, meter_instance = mock_meter

        client = OTelMetricsClient("test-service")

        # No metrics created without config
        assert meter_instance.create_counter.call_count == 0
        assert meter_instance.create_histogram.call_count == 0
        assert len(client._counters) == 0
        assert len(client._histograms) == 0

    def test_init_logs_error_on_failure(self, mock_meter):
        """Test initialization logs error when meter creation fails."""
        mock_get_meter, _ = mock_meter
        mock_get_meter.side_effect = Exception("Meter creation failed")

        with patch("registry_pkgs.telemetry.metrics_client.logger") as mock_logger:
            OTelMetricsClient("test-service")
            mock_logger.error.assert_called_once()


@pytest.mark.unit
@pytest.mark.metrics
class TestSafeTelemetryDecorator:
    """Test suite for safe_telemetry decorator."""

    def test_safe_telemetry_swallows_exception(self):
        """Test that safe_telemetry decorator swallows exceptions."""

        @safe_telemetry
        def failing_function():
            raise ValueError("Test error")

        # Should not raise, returns None
        result = failing_function()
        assert result is None

    def test_safe_telemetry_returns_value_on_success(self):
        """Test that safe_telemetry returns value when no exception."""

        @safe_telemetry
        def successful_function():
            return "success"

        result = successful_function()
        assert result == "success"

    def test_safe_telemetry_logs_warning_on_exception(self):
        """Test that safe_telemetry logs warning when exception occurs."""

        @safe_telemetry
        def failing_function():
            raise ValueError("Test error")

        with patch("registry_pkgs.telemetry.metrics_client.logger") as mock_logger:
            failing_function()
            mock_logger.warning.assert_called_once()
            assert "Test error" in str(mock_logger.warning.call_args)

    def test_safe_telemetry_preserves_function_metadata(self):
        """Test that safe_telemetry preserves function name and docstring."""

        @safe_telemetry
        def my_function():
            """My docstring."""
            pass

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."


@pytest.mark.unit
@pytest.mark.metrics
class TestCreateMetricsClient:
    """Test suite for create_metrics_client factory function."""

    def test_create_metrics_client_returns_client(self):
        """Test that factory function returns OTelMetricsClient instance."""
        with patch("registry_pkgs.telemetry.metrics_client.metrics.get_meter"):
            client = create_metrics_client("api")
            assert isinstance(client, OTelMetricsClient)
            assert client.service_name == "api"

    def test_create_metrics_client_uses_service_name(self):
        """Test that factory function passes service_name to client."""
        with patch("registry_pkgs.telemetry.metrics_client.metrics.get_meter") as mock_get_meter:
            create_metrics_client("worker")
            mock_get_meter.assert_called_once_with("mcp.worker")

    def test_create_metrics_client_with_config(self):
        """Test that factory function accepts config parameter."""
        config = {
            "counters": [{"name": "custom_counter", "description": "Custom counter", "unit": "1"}],
            "histograms": [{"name": "custom_histogram", "description": "Custom histogram", "unit": "s"}],
        }
        with patch("registry_pkgs.telemetry.metrics_client.metrics.get_meter") as mock_get_meter:
            meter_instance = MagicMock()
            mock_get_meter.return_value = meter_instance
            meter_instance.create_counter.return_value = MagicMock()
            meter_instance.create_histogram.return_value = MagicMock()

            client = create_metrics_client("api", config=config)

            assert isinstance(client, OTelMetricsClient)
            # Counter created eagerly, histogram deferred
            assert "custom_counter" in client._counters
            assert "custom_histogram" in client._histogram_configs


@pytest.mark.unit
@pytest.mark.metrics
class TestGenericMetricMethods:
    """Test suite for generic metric creation and recording methods."""

    @pytest.fixture
    def mock_meter(self):
        """Fixture to mock the OpenTelemetry meter and its instruments."""
        with patch("registry_pkgs.telemetry.metrics_client.metrics.get_meter") as mock_get_meter:
            meter_instance = MagicMock()
            mock_get_meter.return_value = meter_instance

            meter_instance.create_counter.return_value = MagicMock()
            meter_instance.create_histogram.return_value = MagicMock()

            yield mock_get_meter, meter_instance

    def test_create_counter_registers_metric(self, mock_meter):
        """Test that create_counter registers a new counter."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        counter = client.create_counter(name="test_counter", description="Test counter", unit="1")

        assert counter is not None
        assert "test_counter" in client._counters

    def test_create_counter_returns_existing(self, mock_meter):
        """Test that create_counter returns existing counter if already registered."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        counter1 = client.create_counter("test_counter", "Test counter")
        counter2 = client.create_counter("test_counter", "Different description")

        assert counter1 is counter2

    def test_create_histogram_registers_metric(self, mock_meter):
        """Test that create_histogram registers a new histogram."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        histogram = client.create_histogram(name="test_histogram", description="Test histogram", unit="s")

        assert histogram is not None
        assert "test_histogram" in client._histograms

    def test_create_histogram_returns_existing(self, mock_meter):
        """Test that create_histogram returns existing histogram if already registered."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        histogram1 = client.create_histogram("test_histogram", "Test histogram")
        histogram2 = client.create_histogram("test_histogram", "Different description")

        assert histogram1 is histogram2

    def test_record_counter_adds_value(self, mock_meter):
        """Test that record_counter adds value to registered counter."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        mock_counter = MagicMock()
        client._counters["my_counter"] = mock_counter

        client.record_counter("my_counter", 5.0, {"label": "value"})

        mock_counter.add.assert_called_once_with(5.0, {"label": "value"})

    def test_record_counter_warns_for_unregistered(self, mock_meter):
        """Test that record_counter warns when counter not registered."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        with patch("registry_pkgs.telemetry.metrics_client.logger") as mock_logger:
            client.record_counter("nonexistent_counter", 1.0)
            mock_logger.warning.assert_called_once()
            assert "nonexistent_counter" in str(mock_logger.warning.call_args)

    def test_record_histogram_records_value(self, mock_meter):
        """Test that record_histogram records value to registered histogram."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        mock_histogram = MagicMock()
        client._histograms["my_histogram"] = mock_histogram

        client.record_histogram("my_histogram", 0.5, {"label": "value"})

        mock_histogram.record.assert_called_once_with(0.5, {"label": "value"})

    def test_record_histogram_lazily_creates_deferred(self, mock_meter):
        """Test that record_histogram lazily creates a deferred histogram on first use."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        # Simulate a deferred histogram from config
        client._histogram_configs["lazy_histogram"] = {
            "description": "Lazy histogram",
            "unit": "s",
        }

        # First record should create the histogram then record
        client.record_histogram("lazy_histogram", 0.42, {"key": "val"})

        meter_instance.create_histogram.assert_called_once_with(
            name="lazy_histogram",
            description="Lazy histogram",
            unit="s",
        )
        # Config should be consumed
        assert "lazy_histogram" not in client._histogram_configs
        assert "lazy_histogram" in client._histograms

    def test_record_histogram_warns_for_unregistered(self, mock_meter):
        """Test that record_histogram warns when histogram not registered."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        with patch("registry_pkgs.telemetry.metrics_client.logger") as mock_logger:
            client.record_histogram("nonexistent_histogram", 1.0)
            mock_logger.warning.assert_called_once()
            assert "nonexistent_histogram" in str(mock_logger.warning.call_args)

    def test_record_metric_detects_counter(self, mock_meter):
        """Test that record_metric auto-detects and records to counter."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        mock_counter = MagicMock()
        client._counters["auto_counter"] = mock_counter

        client.record_metric("auto_counter", 3.0, {"key": "val"})

        mock_counter.add.assert_called_once_with(3.0, {"key": "val"})

    def test_record_metric_detects_histogram(self, mock_meter):
        """Test that record_metric auto-detects and records to histogram."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        mock_histogram = MagicMock()
        client._histograms["auto_histogram"] = mock_histogram

        client.record_metric("auto_histogram", 0.25, {"key": "val"})

        mock_histogram.record.assert_called_once_with(0.25, {"key": "val"})

    def test_record_metric_warns_for_unregistered(self, mock_meter):
        """Test that record_metric warns when metric not registered."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        with patch("registry_pkgs.telemetry.metrics_client.logger") as mock_logger:
            client.record_metric("unknown_metric", 1.0)
            mock_logger.warning.assert_called_once()
            assert "unknown_metric" in str(mock_logger.warning.call_args)

    def test_get_counter_returns_registered(self, mock_meter):
        """Test that get_counter returns registered counter."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        mock_counter = MagicMock()
        client._counters["my_counter"] = mock_counter

        result = client.get_counter("my_counter")
        assert result is mock_counter

    def test_get_counter_returns_none_for_unregistered(self, mock_meter):
        """Test that get_counter returns None for unregistered counter."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        result = client.get_counter("nonexistent")
        assert result is None

    def test_get_histogram_returns_registered(self, mock_meter):
        """Test that get_histogram returns registered histogram."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        mock_histogram = MagicMock()
        client._histograms["my_histogram"] = mock_histogram

        result = client.get_histogram("my_histogram")
        assert result is mock_histogram

    def test_get_histogram_returns_none_for_unregistered(self, mock_meter):
        """Test that get_histogram returns None for unregistered histogram."""
        _, meter_instance = mock_meter
        client = OTelMetricsClient("test-service")

        result = client.get_histogram("nonexistent")
        assert result is None

    def test_init_from_config(self, mock_meter):
        """Test that _init_from_config creates counters and defers histograms."""
        _, meter_instance = mock_meter

        config = {
            "counters": [
                {"name": "config_counter_1", "description": "Counter 1", "unit": "1"},
                {"name": "config_counter_2", "description": "Counter 2"},
            ],
            "histograms": [
                {"name": "config_histogram_1", "description": "Histogram 1", "unit": "ms"},
            ],
        }

        client = OTelMetricsClient("test-service", config=config)

        assert "config_counter_1" in client._counters
        assert "config_counter_2" in client._counters
        # Histogram is deferred until first use
        assert "config_histogram_1" in client._histogram_configs

    def test_capture_flag_true_creates_metric(self, mock_meter):
        """Test that capture=true (default) creates the counter and defers histogram."""
        _, meter_instance = mock_meter

        config = {
            "counters": [
                {"name": "enabled_counter", "description": "Enabled", "capture": True},
            ],
            "histograms": [
                {"name": "enabled_histogram", "description": "Enabled", "capture": True},
            ],
        }

        client = OTelMetricsClient("test-service", config=config)

        assert "enabled_counter" in client._counters
        assert "enabled_histogram" in client._histogram_configs

    def test_capture_flag_false_skips_metric(self, mock_meter):
        """Test that capture=false skips the metric registration."""
        _, meter_instance = mock_meter

        config = {
            "counters": [
                {"name": "disabled_counter", "description": "Disabled", "capture": False},
                {"name": "enabled_counter", "description": "Enabled", "capture": True},
            ],
            "histograms": [
                {"name": "disabled_histogram", "description": "Disabled", "capture": False},
                {"name": "enabled_histogram", "description": "Enabled"},  # Default is true
            ],
        }

        client = OTelMetricsClient("test-service", config=config)

        # Disabled metrics should not be registered
        assert "disabled_counter" not in client._counters
        assert "disabled_histogram" not in client._histogram_configs

        # Enabled metrics should be registered
        assert "enabled_counter" in client._counters
        assert "enabled_histogram" in client._histogram_configs

    def test_capture_flag_defaults_to_true(self, mock_meter):
        """Test that missing capture flag defaults to true."""
        _, meter_instance = mock_meter

        config = {
            "counters": [
                {"name": "no_capture_flag", "description": "No flag specified"},
            ]
        }

        client = OTelMetricsClient("test-service", config=config)

        # Should be registered since capture defaults to true
        assert "no_capture_flag" in client._counters


# Fixture: create temp dir, chdir, create config/metrics/{service_name}.yml, cleanup after
@pytest.fixture
def temp_dir_with_config():
    orig_cwd = os.getcwd()
    temp_dir = tempfile.mkdtemp()
    service_name = "testservice"
    config_dir = os.path.join(temp_dir, "config", "metrics")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, f"{service_name}.yml")
    config_data = {"foo": "bar", "num": 42}
    with open(config_path, "w") as f:
        yaml.safe_dump(config_data, f)
    os.chdir(temp_dir)

    yield {
        "temp_dir": temp_dir,
        "service_name": service_name,
        "config_path": config_path,
        "config_data": config_data,
    }

    os.chdir(orig_cwd)
    shutil.rmtree(temp_dir)


# Set logging level to DEBUG during test run
@pytest.fixture(autouse=True, scope="module")
def debug_logger():
    orig_level = logger.level
    logger.setLevel(logging.DEBUG)

    yield

    logger.setLevel(orig_level)


class TestLoadMetricsConfig:
    def test_config_path_exists(self, temp_dir_with_config, caplog):
        d = temp_dir_with_config
        with caplog.at_level("INFO"):
            result = load_metrics_config(d["service_name"], TelemetryConfig(), config_path=d["config_path"])
        assert result == d["config_data"]
        assert any("Loaded metrics config" in m for m in caplog.messages)

    def test_config_path_not_exists(self, temp_dir_with_config, caplog):
        d = temp_dir_with_config
        missing_path = d["config_path"] + ".missing"
        with caplog.at_level("WARNING"):
            result = load_metrics_config(d["service_name"], TelemetryConfig(), config_path=missing_path)
        assert result is None
        assert any(f"Metrics config not found at {missing_path}" in m for m in caplog.messages)

    def test_env_path_exists(self, temp_dir_with_config, caplog):
        d = temp_dir_with_config
        with caplog.at_level("INFO"):
            result = load_metrics_config(d["service_name"], TelemetryConfig(otel_metrics_config_path=d["config_path"]))
        assert result == d["config_data"]
        assert any("Loaded metrics config" in m for m in caplog.messages)

    def test_env_path_not_exists(self, temp_dir_with_config, caplog):
        with caplog.at_level("WARNING"):
            result = load_metrics_config(
                temp_dir_with_config["service_name"], TelemetryConfig(otel_metrics_config_path=".better-not-exist-ha!")
            )
        assert result is None
        assert any("Metrics config not found at" in m for m in caplog.messages)

    def test_default_path_exists(self, temp_dir_with_config, caplog):
        d = temp_dir_with_config
        with caplog.at_level("INFO"):
            result = load_metrics_config(d["service_name"], TelemetryConfig())
        assert result == d["config_data"]
        assert any("Loaded metrics config" in m for m in caplog.messages)

    def test_default_path_not_exists(self, temp_dir_with_config, caplog):
        d = temp_dir_with_config
        os.remove(d["config_path"])
        with caplog.at_level("WARNING"):
            result = load_metrics_config(d["service_name"], TelemetryConfig())
        assert result is None
        expected_path = os.path.join(os.getcwd(), "config", "metrics", f"{d['service_name']}.yml")
        assert any(f"Metrics config not found at {expected_path}" in m for m in caplog.messages)
