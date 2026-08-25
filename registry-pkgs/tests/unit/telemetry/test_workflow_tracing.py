"""Unit tests for Workflow tracing and process-local attribute propagation."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from agno.workflow import Step, StepInput, StepOutput, Workflow
from openinference.instrumentation import TraceConfig
from openinference.instrumentation.agno import AgnoInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.telemetry.workflow_tracing import (
    LangfuseTraceAttributeSpanProcessor,
    _propagate_langfuse_trace_attributes,
    ensure_langfuse_trace_attribute_processor,
    trace_workflow_continuation,
    trace_workflow_run,
)


def _auth_context() -> dict[str, object]:
    return {
        "user_id": "user-1",
        "username": "kelvin",
        "client_id": "workflow-client",
        "auth_method": "jwt",
        "provider": "keycloak",
        "auth_source": "jwt_auth",
    }


def _attribute_json(span: MagicMock, key: str) -> dict[str, object]:
    call = next(call for call in span.set_attribute.call_args_list if call.args[0] == key)
    return json.loads(call.args[1])


@pytest.mark.unit
@pytest.mark.telemetry
def test_processor_propagates_only_trace_level_attributes_within_context() -> None:
    processor = LangfuseTraceAttributeSpanProcessor()
    child_span = MagicMock()
    child_span.is_recording.return_value = True
    attributes = {
        "langfuse.observation.type": "chain",
        "langfuse.trace.name": "WorkflowRun",
        "langfuse.trace.tags": ["registry", "workflow"],
        "langfuse.trace.metadata.workflowRunId": "run-1",
        "langfuse.user.id": "user-1",
        "registry.operation.type": "workflow_execution",
    }

    with _propagate_langfuse_trace_attributes(attributes):
        processor.on_start(child_span)

    child_span.set_attribute.assert_any_call("langfuse.trace.name", "WorkflowRun")
    child_span.set_attribute.assert_any_call("langfuse.trace.tags", ["registry", "workflow"])
    child_span.set_attribute.assert_any_call("langfuse.trace.metadata.workflowRunId", "run-1")
    child_span.set_attribute.assert_any_call("langfuse.user.id", "user-1")
    propagated_keys = {call.args[0] for call in child_span.set_attribute.call_args_list}
    assert "langfuse.observation.type" not in propagated_keys
    assert "registry.operation.type" not in propagated_keys

    span_after_context = MagicMock()
    span_after_context.is_recording.return_value = True
    processor.on_start(span_after_context)
    span_after_context.set_attribute.assert_not_called()


@pytest.mark.unit
@pytest.mark.telemetry
def test_processor_registration_is_idempotent_per_provider() -> None:
    provider = MagicMock()

    ensure_langfuse_trace_attribute_processor(provider)
    ensure_langfuse_trace_attribute_processor(provider)

    provider.add_span_processor.assert_called_once()
    processor = provider.add_span_processor.call_args.args[0]
    assert isinstance(processor, LangfuseTraceAttributeSpanProcessor)


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_workflow_run_trace_matches_execution_format_and_propagates_to_children() -> None:
    child_span = MagicMock()
    child_span.is_recording.return_value = True

    @trace_workflow_run
    async def run(
        runner,
        definition_id,
        user_text,
        *,
        auth_context,
        existing_run_id,
        injected_outputs=None,
        stop_after_node_id=None,
        definition_snapshot=None,
    ):
        del runner, definition_id, user_text, auth_context, injected_outputs, stop_after_node_id, definition_snapshot
        LangfuseTraceAttributeSpanProcessor().on_start(child_span)
        return (
            SimpleNamespace(
                id=existing_run_id,
                status=WorkflowRunStatus.COMPLETED,
                final_output={"content": "report ready"},
                error_summary=None,
            ),
            [SimpleNamespace(), SimpleNamespace()],
        )

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry_pkgs.telemetry.workflow_tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        result = await run(
            object(),
            "definition-1",
            "prepare a report",
            auth_context=_auth_context(),
            existing_run_id="run-1",
        )

    assert result[0].status == WorkflowRunStatus.COMPLETED
    (span_name,) = tracer.start_as_current_span.call_args.args
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert span_name == "workflow.execute"
    assert attributes["langfuse.observation.type"] == "chain"
    assert attributes["langfuse.trace.name"] == "WorkflowRun"
    assert attributes["langfuse.trace.tags"] == ["registry", "workflow"]
    assert attributes["langfuse.trace.metadata.app"] == "registry"
    assert attributes["langfuse.trace.metadata.operationType"] == "workflow_execution"
    assert attributes["langfuse.trace.metadata.workflowDefinitionId"] == "definition-1"
    assert attributes["langfuse.trace.metadata.workflowRunId"] == "run-1"
    assert attributes["langfuse.trace.metadata.username"] == "kelvin"
    assert attributes["langfuse.trace.metadata.oauthClientId"] == "workflow-client"
    assert attributes["langfuse.user.id"] == "user-1"
    assert attributes["langfuse.session.id"] == "run-1"
    assert attributes["registry.operation.type"] == "workflow_execution"
    assert attributes["registry.caller.authenticated"] is True
    assert _attribute_json(span, "langfuse.observation.input") == {
        "workflow_definition_id": "definition-1",
        "workflow_run_id": "run-1",
        "input": "prepare a report",
    }
    assert _attribute_json(span, "langfuse.observation.output") == {
        "workflow_run_id": "run-1",
        "status": "completed",
        "output": {"content": "report ready"},
        "node_run_count": 2,
    }
    span.set_attribute.assert_any_call("registry.operation.success", True)
    child_span.set_attribute.assert_any_call("langfuse.trace.name", "WorkflowRun")
    child_span.set_attribute.assert_any_call("langfuse.trace.tags", ["registry", "workflow"])
    child_span.set_attribute.assert_any_call("langfuse.trace.metadata.workflowRunId", "run-1")
    child_span.set_attribute.assert_any_call("langfuse.user.id", "user-1")


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_workflow_run_trace_status_failure_does_not_change_result(caplog: pytest.LogCaptureFixture) -> None:
    result = (
        SimpleNamespace(id="run-1", status=WorkflowRunStatus.FAILED, error_summary="model failed"),
        [],
    )

    @trace_workflow_run
    async def run(runner, definition_id, user_text, **kwargs):
        del runner, definition_id, user_text, kwargs
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    span.set_status.side_effect = RuntimeError("status rejected")
    with patch("registry_pkgs.telemetry.workflow_tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        returned = await run(object(), "definition-1", "hello", auth_context=None, existing_run_id="run-1")

    assert returned is result
    span.set_attribute.assert_any_call("registry.operation.success", False)
    span.set_status.assert_called_once()
    assert "Failed to set workflow span status (RuntimeError) for status=failed" in caplog.text


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_workflow_run_trace_preserves_execution_error() -> None:
    error = RuntimeError("workflow unavailable")

    @trace_workflow_run
    async def run(runner, definition_id, user_text, **kwargs):
        del runner, definition_id, user_text, kwargs
        raise error

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry_pkgs.telemetry.workflow_tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        with pytest.raises(RuntimeError) as exc_info:
            await run(object(), "definition-1", "hello", auth_context=None, existing_run_id="run-1")

    assert exc_info.value is error
    span.set_attribute.assert_any_call("registry.operation.success", False)

    span_after_error = MagicMock()
    span_after_error.is_recording.return_value = True
    LangfuseTraceAttributeSpanProcessor().on_start(span_after_error)
    span_after_error.set_attribute.assert_not_called()


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_workflow_trace_attribute_failure_does_not_change_result() -> None:
    result = (
        SimpleNamespace(id="run-1", status=WorkflowRunStatus.COMPLETED, error_summary=None),
        [],
    )

    @trace_workflow_run
    async def run(runner, definition_id, user_text, **kwargs):
        del runner, definition_id, user_text, kwargs
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    span.set_attribute.side_effect = RuntimeError("attribute rejected")
    with patch("registry_pkgs.telemetry.workflow_tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        returned = await run(object(), "definition-1", "hello", auth_context=None, existing_run_id="run-1")

    assert returned is result


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_workflow_continuation_trace_uses_same_session_with_continue_tag() -> None:
    @trace_workflow_continuation
    async def continue_run(runner, *, existing_run_id, auth_context):
        del runner, auth_context
        return (
            SimpleNamespace(
                id=existing_run_id,
                status=WorkflowRunStatus.COMPLETED,
                final_output={"content": "continued"},
                error_summary=None,
            ),
            [],
        )

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry_pkgs.telemetry.workflow_tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        await continue_run(object(), existing_run_id="run-1", auth_context=_auth_context())

    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert attributes["langfuse.trace.name"] == "WorkflowRun"
    assert attributes["langfuse.trace.tags"] == ["registry", "workflow", "continue"]
    assert attributes["langfuse.session.id"] == "run-1"
    assert attributes["langfuse.trace.metadata.operationType"] == "workflow_continue"
    assert _attribute_json(span, "langfuse.observation.input") == {
        "workflow_run_id": "run-1",
        "action": "continue",
    }
    assert _attribute_json(span, "langfuse.observation.output") == {
        "workflow_run_id": "run-1",
        "status": "completed",
        "output": {"content": "continued"},
        "node_run_count": 0,
    }


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_real_agno_workflow_spans_are_nested_and_receive_langfuse_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    ensure_langfuse_trace_attribute_processor(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentor = AgnoInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    instrumentor.instrument(
        tracer_provider=provider,
        config=TraceConfig(
            hide_inputs=False,
            hide_outputs=False,
            hide_llm_tools=True,
            hide_llm_invocation_parameters=True,
        ),
    )

    async def executor(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
        del step_input, session_state
        return StepOutput(content="done", success=True)

    @trace_workflow_run
    async def run(runner, definition_id, user_text, **kwargs):
        del runner, definition_id, user_text, kwargs
        workflow = Workflow(name="trace-test-workflow", steps=[Step(name="trace-step", executor=executor)])
        await workflow.arun(input="hidden input", session_id="run-1")
        return (
            SimpleNamespace(id="run-1", status=WorkflowRunStatus.COMPLETED, error_summary=None),
            [SimpleNamespace()],
        )

    try:
        with patch("registry_pkgs.telemetry.workflow_tracing._TRACER", provider.get_tracer("registry.workflows")):
            await run(object(), "definition-1", "hidden input", auth_context=_auth_context(), existing_run_id="run-1")
        provider.force_flush()
        spans = exporter.get_finished_spans()

        root_span = next(span for span in spans if span.name == "workflow.execute")
        agno_workflow_span = next(span for span in spans if span.name == "trace_test_workflow.arun")
        assert agno_workflow_span.parent is not None
        assert agno_workflow_span.parent.span_id == root_span.context.span_id
        step_span = next(span for span in spans if span.name.startswith("trace_step"))
        assert "input.value" in agno_workflow_span.attributes
        assert "output.value" in agno_workflow_span.attributes
        assert "input.value" in step_span.attributes
        assert "output.value" in step_span.attributes
        assert "hidden input" in str(agno_workflow_span.attributes["input.value"])
        assert "done" in str(agno_workflow_span.attributes["output.value"])
        assert "hidden input" in str(step_span.attributes["input.value"])
        assert "done" in str(step_span.attributes["output.value"])
        for span in spans:
            assert span.attributes["langfuse.trace.name"] == "WorkflowRun"
            assert span.attributes["langfuse.trace.tags"] == ("registry", "workflow")
            assert span.attributes["langfuse.trace.metadata.workflowRunId"] == "run-1"
            assert span.attributes["langfuse.user.id"] == "user-1"
    finally:
        instrumentor.uninstrument()
        provider.shutdown()
