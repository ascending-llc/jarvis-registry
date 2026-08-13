"""Structured JSON logging with OTel trace context injection."""

from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime
from typing import Any

from opentelemetry.instrumentation.logging import LoggingInstrumentor


class StructuredLogFormatter(logging.Formatter):
    """Single-line JSON formatter for structured stdout logging."""

    def __init__(
        self,
        service_name: str = "",
        service_version: str = "",
    ) -> None:
        super().__init__()
        self._service_name = service_name
        self._service_version = service_version

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        if self._service_name:
            entry["service.name"] = self._service_name
        if self._service_version:
            entry["service.version"] = self._service_version

        entry["trace_id"] = getattr(record, "otelTraceID", "0")
        entry["span_id"] = getattr(record, "otelSpanID", "0")

        if record.exc_info and record.exc_info[1] is not None:
            entry["exception.type"] = type(record.exc_info[1]).__name__
            entry["exception.message"] = str(record.exc_info[1])
            entry["exception.stacktrace"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(entry, default=str, ensure_ascii=False)


_configured_loggers: set[str] = set()
_instrumentor_installed: bool = False


def configure_structured_logging(
    *logger_names: str,
    service_name: str = "",
    service_version: str = "",
    level: int | None = None,
) -> None:
    """Install ``LoggingInstrumentor`` and ``StructuredLogFormatter`` on named loggers."""
    global _instrumentor_installed  # noqa: PLW0603
    if not _instrumentor_installed:
        LoggingInstrumentor().instrument(
            set_logging_format=False,
            inject_trace_context=True,
            enable_log_auto_instrumentation=False,
        )
        _instrumentor_installed = True

    formatter = StructuredLogFormatter(
        service_name=service_name,
        service_version=service_version,
    )

    for name in logger_names:
        if name in _configured_loggers:
            continue

        target_logger = logging.getLogger(name)
        if level is not None:
            target_logger.setLevel(level)

        if target_logger.handlers:
            for handler in target_logger.handlers:
                handler.setFormatter(formatter)
        else:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            target_logger.addHandler(handler)
            target_logger.propagate = False

        _configured_loggers.add(name)
