from opentelemetry import baggage
from opentelemetry import context as context_api
from opentelemetry.sdk.trace import TracerProvider

from registry_pkgs.telemetry import trace_propagation
from registry_pkgs.telemetry.trace_propagation import (
    MAX_BAGGAGE_VALUE_LENGTH,
    bounded_baggage_value,
    inject_trace_context,
)

_PROVIDER = TracerProvider()
_TRACER = _PROVIDER.get_tracer("test")


def _with_active_span(fn):
    """Run *fn* while a real recording span is current, so a traceparent exists."""
    with _TRACER.start_as_current_span("test-span"):
        return fn()


def test_strips_preexisting_trace_headers():
    incoming = {
        "Authorization": "Bearer x",
        "traceparent": "00-old-old-01",
        "tracestate": "vendor=1",
        "baggage": "attacker=1",
    }
    out = _with_active_span(lambda: inject_trace_context(incoming))

    # Original attacker/stale trace headers must not survive verbatim.
    assert out.get("traceparent") != "00-old-old-01"
    assert "attacker=1" not in out.get("baggage", "")
    assert out["Authorization"] == "Bearer x"


def test_injects_from_explicit_context():
    # Build the context inside the active span so it carries the span's trace id.
    out = _with_active_span(lambda: inject_trace_context({}, context=baggage.set_baggage("jarvis.test.key", "value")))

    assert "traceparent" in out
    assert "jarvis.test.key=value" in out["baggage"]


def test_injects_from_current_context_when_none():
    def run():
        ctx = baggage.set_baggage("jarvis.current.key", "cur")
        token = context_api.attach(ctx)
        try:
            return inject_trace_context({})
        finally:
            context_api.detach(token)

    out = _with_active_span(run)
    assert "jarvis.current.key=cur" in out["baggage"]


def test_drops_tracestate_from_result():
    out = _with_active_span(lambda: inject_trace_context({}))
    assert "tracestate" not in out


def test_never_raises_returns_original_on_failure(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("inject failed")

    monkeypatch.setattr(trace_propagation._TRACE_CONTEXT_PROPAGATOR, "inject", boom)
    incoming = {"Authorization": "Bearer x", "traceparent": "attacker", "baggage": "attacker=1"}

    out = inject_trace_context(incoming)

    assert out == {"Authorization": "Bearer x"}
    assert "traceparent" not in out
    assert "baggage" not in out
    assert any("Failed to inject trace context" in r.message for r in caplog.records)


def test_bounded_baggage_value_truncates():
    long = "a" * (MAX_BAGGAGE_VALUE_LENGTH + 50)
    assert bounded_baggage_value(long) == "a" * MAX_BAGGAGE_VALUE_LENGTH
    assert bounded_baggage_value("short") == "short"
