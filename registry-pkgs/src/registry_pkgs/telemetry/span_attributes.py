"""Shared helpers for building OpenTelemetry span attributes destined for Langfuse.

Used by both ``registry_pkgs.telemetry.workflow_tracing`` and
``registry.mcpgw.tracing`` so the attribute-cleaning rule lives in one place.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

type SpanAttributeValue = bool | int | float | str | list[str]

TRACE_APP = "registry"


def clean_span_attributes(attributes: Mapping[str, object]) -> dict[str, SpanAttributeValue]:
    cleaned: dict[str, SpanAttributeValue] = {}
    for key, value in attributes.items():
        if (isinstance(value, (bool, int, float, str)) and value != "") or (
            isinstance(value, list) and value and all(isinstance(item, str) and item != "" for item in value)
        ):
            cleaned[key] = value
    return cleaned


def set_span_attributes(span: Span, attributes: Mapping[str, object]) -> None:
    """Best-effort attribute assignment without exposing values in logs."""
    try:
        if not span.is_recording():
            return
        for key, value in clean_span_attributes(attributes).items():
            span.set_attribute(key, value)
    except Exception as exc:
        logger.warning("Failed to set trace attributes (%s)", type(exc).__name__)
