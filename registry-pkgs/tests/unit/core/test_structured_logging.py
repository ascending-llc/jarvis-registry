"""Tests for structured JSON logging with OTel trace context injection."""

from __future__ import annotations

import json
from logging import INFO, LogRecord, StreamHandler, getLogger

import pytest

from registry_pkgs.core.structured_logging import (
    StructuredLogFormatter,
    _configured_loggers,
    configure_structured_logging,
)


@pytest.mark.unit
class TestStructuredLogFormatter:
    """StructuredLogFormatter outputs single-line JSON."""

    def _make_record(self, msg="test message", level=INFO, exc_info=None):
        record = LogRecord("test.logger", level, "test.py", 42, msg, (), exc_info)
        record.otelTraceID = "abc123"
        record.otelSpanID = "def456"
        return record

    def test_basic_json_output(self):
        fmt = StructuredLogFormatter(service_name="svc", service_version="v1")
        record = self._make_record()
        output = fmt.format(record)

        parsed = json.loads(output)
        assert parsed["severity"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "test message"
        assert parsed["service.name"] == "svc"
        assert parsed["service.version"] == "v1"
        assert parsed["trace_id"] == "abc123"
        assert parsed["span_id"] == "def456"
        assert "timestamp" in parsed

    def test_single_line(self):
        fmt = StructuredLogFormatter()
        record = self._make_record(msg="line1\nline2\nline3")
        output = fmt.format(record)

        assert "\n" not in output
        parsed = json.loads(output)
        assert "line1\nline2\nline3" in parsed["message"]

    def test_exception_info(self):
        fmt = StructuredLogFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = self._make_record(exc_info=exc_info)
        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["exception.type"] == "ValueError"
        assert parsed["exception.message"] == "boom"
        assert "Traceback" in parsed["exception.stacktrace"]

    def test_no_trace_attrs_defaults_to_zero(self):
        fmt = StructuredLogFormatter()
        record = LogRecord("test", INFO, "", 0, "msg", (), None)
        output = fmt.format(record)
        parsed = json.loads(output)

        assert parsed["trace_id"] == "0"
        assert parsed["span_id"] == "0"

    def test_empty_resource_fields_omitted(self):
        fmt = StructuredLogFormatter()
        record = self._make_record()
        output = fmt.format(record)
        parsed = json.loads(output)

        assert "service.name" not in parsed
        assert "service.version" not in parsed


@pytest.mark.unit
class TestConfigureStructuredLogging:
    """configure_structured_logging() wires formatter to named loggers."""

    @pytest.fixture(autouse=True)
    def reset_configured_loggers(self):
        _configured_loggers.clear()
        yield
        _configured_loggers.clear()

    def test_installs_on_existing_handlers(self):
        test_logger = getLogger("test_structured_existing")
        test_logger.handlers.clear()
        handler = StreamHandler()
        test_logger.addHandler(handler)

        configure_structured_logging("test_structured_existing", service_name="svc")

        assert isinstance(handler.formatter, StructuredLogFormatter)

    def test_creates_handler_if_none_exist(self):
        test_logger = getLogger("test_structured_nohandler")
        test_logger.handlers.clear()

        configure_structured_logging("test_structured_nohandler", service_name="svc")

        assert len(test_logger.handlers) == 1
        assert isinstance(test_logger.handlers[0].formatter, StructuredLogFormatter)

    def test_idempotent(self):
        test_logger = getLogger("test_structured_idempotent")
        test_logger.handlers.clear()
        handler = StreamHandler()
        test_logger.addHandler(handler)

        configure_structured_logging("test_structured_idempotent", service_name="svc")
        configure_structured_logging("test_structured_idempotent", service_name="svc")

        assert len(test_logger.handlers) == 1

    def test_multiple_loggers(self):
        for name in ("test_structured_multi_a", "test_structured_multi_b"):
            lg = getLogger(name)
            lg.handlers.clear()
            lg.addHandler(StreamHandler())

        configure_structured_logging(
            "test_structured_multi_a",
            "test_structured_multi_b",
            service_name="svc",
        )

        for name in ("test_structured_multi_a", "test_structured_multi_b"):
            lg = getLogger(name)
            assert isinstance(lg.handlers[0].formatter, StructuredLogFormatter)
