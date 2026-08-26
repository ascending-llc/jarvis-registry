"""Langfuse root tracing for Registry workflow runs and continuations."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from functools import partial, wraps
from threading import Lock
from typing import Any
from weakref import WeakSet

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import Span as SDKSpan
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.trace import Span, Status, StatusCode

from .span_attributes import TRACE_APP, SpanAttributeValue, clean_span_attributes, set_span_attributes

logger = logging.getLogger(__name__)

_WORKFLOW_SPAN_NAME = "workflow.execute"
_WORKFLOW_CONTINUE_SPAN_NAME = "workflow.continue"
_WORKFLOW_TRACE_NAME = "WorkflowRun"
_WORKFLOW_TAGS = [TRACE_APP, "workflow"]
_TRACER = trace.get_tracer("registry.workflows")
_FAILED_STATUSES = frozenset({"cancelled", "failed"})
_TRACE_ATTRIBUTE_KEYS = frozenset(
    {
        "langfuse.session.id",
        "langfuse.trace.name",
        "langfuse.trace.tags",
        "langfuse.user.id",
    }
)
_TRACE_METADATA_PREFIX = "langfuse.trace.metadata."
_TRACE_ATTRIBUTES_CONTEXT_KEY = "registry.langfuse.trace_attributes"
_registered_providers: WeakSet[TracerProvider] = WeakSet()
_registration_lock = Lock()


def _trace_attributes(attributes: Mapping[str, object]) -> dict[str, SpanAttributeValue]:
    return {
        key: value
        for key, value in clean_span_attributes(attributes).items()
        if key in _TRACE_ATTRIBUTE_KEYS or key.startswith(_TRACE_METADATA_PREFIX)
    }


@contextmanager
def _propagate_langfuse_trace_attributes(attributes: Mapping[str, object]) -> Iterator[None]:
    """Apply trace-level attributes to every in-process span created in this context."""
    current_context = otel_context.get_current()
    current_attributes = otel_context.get_value(_TRACE_ATTRIBUTES_CONTEXT_KEY, current_context)
    inherited_attributes = current_attributes if isinstance(current_attributes, dict) else {}
    propagated = {**inherited_attributes, **_trace_attributes(attributes)}
    context = otel_context.set_value(_TRACE_ATTRIBUTES_CONTEXT_KEY, propagated, current_context)
    token = otel_context.attach(context)
    try:
        yield
    finally:
        otel_context.detach(token)


class LangfuseTraceAttributeSpanProcessor(SpanProcessor):
    """Copy active Workflow trace attributes onto newly-created child spans."""

    def on_start(self, span: SDKSpan, parent_context: Context | None = None) -> None:
        context = parent_context or otel_context.get_current()
        attributes = otel_context.get_value(_TRACE_ATTRIBUTES_CONTEXT_KEY, context)
        if isinstance(attributes, dict):
            set_span_attributes(span, attributes)


def ensure_langfuse_trace_attribute_processor(tracer_provider: TracerProvider) -> None:
    """Register the Workflow attribute propagation processor once per provider."""
    with _registration_lock:
        if tracer_provider in _registered_providers:
            return
        tracer_provider.add_span_processor(LangfuseTraceAttributeSpanProcessor())
        _registered_providers.add(tracer_provider)


def _auth_metadata(auth_context: dict[str, Any] | None) -> tuple[object, dict[str, object]]:
    if auth_context is None:
        return None, {}
    return auth_context.get("user_id"), {
        "username": auth_context.get("username"),
        "oauthClientId": auth_context.get("client_id"),
        "authMethod": auth_context.get("auth_method"),
        "authProvider": auth_context.get("provider"),
        "authSource": auth_context.get("auth_source"),
    }


def _build_workflow_attributes(
    *,
    operation_type: str,
    workflow_run_id: str,
    auth_context: dict[str, Any] | None,
    workflow_definition_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, object]:
    user_id, metadata = _auth_metadata(auth_context)
    metadata.update(
        {
            "workflowDefinitionId": workflow_definition_id,
            "workflowRunId": workflow_run_id,
        }
    )
    return clean_span_attributes(
        {
            "langfuse.observation.type": "chain",
            "langfuse.trace.name": _WORKFLOW_TRACE_NAME,
            "langfuse.trace.tags": tags or _WORKFLOW_TAGS,
            "langfuse.trace.metadata.app": TRACE_APP,
            "langfuse.trace.metadata.operationType": operation_type,
            **{f"langfuse.trace.metadata.{key}": value for key, value in metadata.items()},
            "langfuse.user.id": user_id,
            "langfuse.session.id": workflow_run_id,
            "registry.operation.type": operation_type,
            "registry.caller.authenticated": bool(user_id),
            "workflow.definition.id": workflow_definition_id,
            "workflow.run.id": workflow_run_id,
        }
    )


def _set_serialized_attribute(span: Span, key: str, value: object) -> None:
    try:
        if span.is_recording():
            span.set_attribute(key, json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.warning("Failed to serialize or set workflow trace attribute %s (%s)", key, type(exc).__name__)


def _status_value(run: object) -> str | None:
    status = getattr(run, "status", None)
    value = getattr(status, "value", status)
    return str(value).lower() if value is not None else None


def _record_workflow_result(span: Span, result: object) -> bool:
    if not isinstance(result, tuple) or len(result) != 2:
        return True

    run, node_runs = result
    status = _status_value(run)
    output = {
        "workflow_run_id": str(getattr(run, "id", "")),
        "status": status,
        "output": getattr(run, "final_output", None),
        "node_run_count": len(node_runs) if isinstance(node_runs, list) else None,
    }
    _set_serialized_attribute(span, "langfuse.observation.output", output)
    set_span_attributes(
        span,
        {
            "registry.operation.status": status,
            "workflow.node_run.count": output["node_run_count"],
        },
    )
    success = status not in _FAILED_STATUSES
    if not success:
        error_summary = getattr(run, "error_summary", None)
        try:
            span.set_status(Status(StatusCode.ERROR, str(error_summary or f"Workflow {status}")))
        except Exception as exc:
            logger.warning(
                "Failed to set workflow span status (%s) for status=%s",
                type(exc).__name__,
                status,
            )
    return success


async def _trace_workflow_call[ResultT](
    *,
    span_name: str,
    attributes: dict[str, object],
    trace_input: dict[str, object],
    call: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    with _propagate_langfuse_trace_attributes(attributes):
        with _TRACER.start_as_current_span(span_name, attributes=attributes) as span:
            _set_serialized_attribute(span, "langfuse.observation.input", trace_input)
            success = False
            try:
                result = await call()
                success = _record_workflow_result(span, result)
                return result
            finally:
                set_span_attributes(span, {"registry.operation.success": success})


def trace_workflow_run[ResultT](
    func: Callable[..., Awaitable[ResultT]],
) -> Callable[..., Awaitable[ResultT]]:
    """Trace an initial or retried ``WorkflowRunner.run`` operation."""

    @wraps(func)
    async def wrapper(
        runner: object,
        definition_id: str,
        user_text: str,
        *,
        auth_context: dict[str, Any] | None,
        existing_run_id: str,
        injected_outputs: dict[str, dict[str, Any]] | None = None,
        stop_after_node_id: str | None = None,
        definition_snapshot: dict[str, Any] | None = None,
    ) -> ResultT:
        attributes = _build_workflow_attributes(
            operation_type="workflow_execution",
            workflow_definition_id=definition_id,
            workflow_run_id=existing_run_id,
            auth_context=auth_context,
        )
        trace_input = {
            "workflow_definition_id": definition_id,
            "workflow_run_id": existing_run_id,
            "input": user_text,
        }
        return await _trace_workflow_call(
            span_name=_WORKFLOW_SPAN_NAME,
            attributes=attributes,
            trace_input=trace_input,
            call=partial(
                func,
                runner,
                definition_id,
                user_text,
                auth_context=auth_context,
                existing_run_id=existing_run_id,
                injected_outputs=injected_outputs,
                stop_after_node_id=stop_after_node_id,
                definition_snapshot=definition_snapshot,
            ),
        )

    return wrapper


def trace_workflow_continuation[ResultT](
    func: Callable[..., Awaitable[ResultT]],
) -> Callable[..., Awaitable[ResultT]]:
    """Trace one HITL continuation and group it with the original workflow run."""

    @wraps(func)
    async def wrapper(
        runner: object,
        *,
        existing_run_id: str,
        auth_context: dict[str, Any] | None,
    ) -> ResultT:
        attributes = _build_workflow_attributes(
            operation_type="workflow_continue",
            workflow_run_id=existing_run_id,
            auth_context=auth_context,
            tags=[*_WORKFLOW_TAGS, "continue"],
        )
        return await _trace_workflow_call(
            span_name=_WORKFLOW_CONTINUE_SPAN_NAME,
            attributes=attributes,
            trace_input={
                "workflow_run_id": existing_run_id,
                "action": "continue",
            },
            call=partial(
                func,
                runner,
                existing_run_id=existing_run_id,
                auth_context=auth_context,
            ),
        )

    return wrapper
