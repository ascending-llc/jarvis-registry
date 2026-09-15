"""Shared W3C trace-context propagation mechanics for outbound HTTP calls.

This module owns only the *mechanics* of trace-context propagation (the composite
propagator singleton, header stripping, and fail-open handling). It deliberately
does **not** decide what baggage keys mean or when to set them — every caller stays
responsible for what is ambient/curated in the ``Context`` it passes (or leaves as
current) before calling :func:`inject_trace_context`.

Keeping policy out of here matters: A2A's "clear inherited baggage, set only the
Langfuse keys" policy and MCP's "layer identity baggage onto whatever is ambient"
policy would break each other if folded into one shared codepath.
"""

import logging

from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

_TRACE_CONTEXT_PROPAGATOR = CompositePropagator(
    [
        TraceContextTextMapPropagator(),
        W3CBaggagePropagator(),
    ]
)
_TRACE_CONTEXT_HEADERS = frozenset({"baggage", "traceparent", "tracestate"})

BAGGAGE_KEY_WORKFLOW_RUN_ID = "jarvis.workflow.run_id"
BAGGAGE_KEY_NODE_ID = "jarvis.workflow.node_id"
BAGGAGE_KEY_ATTEMPT = "jarvis.workflow.attempt"
BAGGAGE_KEY_MCP_TOOL_NAME = "jarvis.mcp.tool_name"
BAGGAGE_KEY_MCP_SERVER_ID = "jarvis.mcp.server_id"
BAGGAGE_KEY_MCP_METHOD = "jarvis.mcp.method"

MAX_BAGGAGE_VALUE_LENGTH = 200


def bounded_baggage_value(value: str, max_length: int = MAX_BAGGAGE_VALUE_LENGTH) -> str:
    """Truncate a caller-supplied string before it becomes a baggage value."""
    return value[:max_length]


def inject_trace_context(headers: dict[str, str], *, context: Context | None = None) -> dict[str, str]:
    """Return a copy of *headers* with the current (or given) trace context injected."""
    outbound_headers = {k: v for k, v in headers.items() if k.lower() not in _TRACE_CONTEXT_HEADERS}
    try:
        _TRACE_CONTEXT_PROPAGATOR.inject(outbound_headers, context=context)
    except Exception:
        logger.warning("Failed to inject trace context into outbound headers", exc_info=True)
    outbound_headers.pop("tracestate", None)
    return outbound_headers
