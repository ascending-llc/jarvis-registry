"""Unit tests for semantic MCP Gateway tracing."""

import asyncio
import base64
import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import get_args
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import DataPart, FilePart, FileWithBytes, FileWithUri, TextPart
from mcp.types import (
    Annotations,
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    Icon,
    ImageContent,
    Implementation,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from registry.mcpgw.tools.agent import AgentMessageInput
from registry.mcpgw.tracing import (
    _TRACE_MODEL_FIELDS,
    DiscoveryKind,
    _build_agent_trace_input,
    _build_tool_trace_input,
    _extract_caller_identity,
    _serialize_trace_value,
    _set_span_attributes,
    trace_agent_execution,
    trace_discovery,
    trace_tool_execution,
)


def _make_ctx():
    state = SimpleNamespace(
        user={
            "user_id": "507f1f77bcf86cd799439011",
            "client_id": "mcp-client-123",
            "username": "kelvin",
            "auth_method": "jwt",
            "provider": "keycloak",
            "auth_source": "jwt_auth",
        }
    )
    request_context = SimpleNamespace(request=SimpleNamespace(state=state))
    client_params = SimpleNamespace(clientInfo=Implementation(name="Claude Code", version="1.2.3"))
    return SimpleNamespace(
        request_context=request_context,
        request_id="request-123",
        session=SimpleNamespace(client_params=client_params),
    )


@pytest.mark.unit
@pytest.mark.telemetry
def test_trace_projection_includes_relevant_mcp_result_fields() -> None:
    encoded_content = base64.b64encode(b"binary-content").decode("ascii")
    annotations = Annotations(audience=["assistant"], priority=0.8)
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text="completed",
                annotations=annotations,
                _meta={"vendor": "omit"},
            ),
            ImageContent(
                type="image",
                data=encoded_content,
                mimeType="image/png",
                _meta={"vendor": "omit"},
            ),
            AudioContent(
                type="audio",
                data=encoded_content,
                mimeType="audio/wav",
                _meta={"vendor": "omit"},
            ),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="file:///report.txt",
                    text="report contents",
                    mimeType="text/plain",
                    _meta={"vendor": "omit"},
                ),
                _meta={"vendor": "omit"},
            ),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri="urn:test:binary-content",
                    blob=encoded_content,
                    mimeType="application/octet-stream",
                    _meta={"vendor": "omit"},
                ),
            ),
            ResourceLink(
                type="resource_link",
                name="report",
                title="Audit report",
                uri="file:///report.pdf",
                description="Generated report",
                mimeType="application/pdf",
                size=42,
                icons=[Icon(src=f"data:image/png;base64,{encoded_content}")],
                annotations=annotations,
                _meta={"vendor": "omit"},
            ),
        ],
        structuredContent={"answer": {"encoded_looking_value": encoded_content}},
        isError=False,
        _meta={"vendor": "omit"},
    )

    trace_value = json.loads(_serialize_trace_value(result))

    assert trace_value == {
        "content": [
            {
                "annotations": {"audience": ["assistant"], "priority": 0.8},
                "text": "completed",
                "type": "text",
            },
            {
                "mimeType": "image/png",
                "type": "image",
            },
            {
                "mimeType": "audio/wav",
                "type": "audio",
            },
            {
                "resource": {
                    "mimeType": "text/plain",
                    "text": "report contents",
                    "uri": "file:///report.txt",
                },
                "type": "resource",
            },
            {
                "resource": {
                    "mimeType": "application/octet-stream",
                    "uri": "urn:test:binary-content",
                },
                "type": "resource",
            },
            {
                "annotations": {"audience": ["assistant"], "priority": 0.8},
                "description": "Generated report",
                "mimeType": "application/pdf",
                "name": "report",
                "size": 42,
                "title": "Audit report",
                "type": "resource_link",
                "uri": "file:///report.pdf",
            },
        ],
        "isError": False,
        "structuredContent": {"answer": {"encoded_looking_value": encoded_content}},
    }


@pytest.mark.unit
@pytest.mark.telemetry
def test_trace_projection_includes_relevant_a2a_message_fields() -> None:
    encoded_content = base64.b64encode(b"inline-file").decode("ascii")
    message = AgentMessageInput(
        parts=[
            TextPart(kind="text", text=encoded_content, metadata={"vendor": "omit"}),
            DataPart(
                kind="data",
                data={"encoded_looking_value": encoded_content},
                metadata={"vendor": "omit"},
            ),
            FilePart(
                kind="file",
                file=FileWithBytes(
                    bytes=encoded_content,
                    mimeType="application/pdf",
                    name="report.pdf",
                ),
                metadata={"vendor": "omit"},
            ),
            FilePart(
                kind="file",
                file=FileWithUri(
                    uri="https://files.example.com/report.pdf",
                    mimeType="application/pdf",
                    name="remote-report.pdf",
                ),
                metadata={"vendor": "omit"},
            ),
        ]
    )

    trace_value = json.loads(_serialize_trace_value(_build_agent_trace_input("agent-123", message)))

    assert trace_value == {
        "agent_id": "agent-123",
        "message": {
            "parts": [
                {"kind": "text", "text": encoded_content},
                {"data": {"encoded_looking_value": encoded_content}, "kind": "data"},
                {
                    "file": {
                        "mimeType": "application/pdf",
                        "name": "report.pdf",
                    },
                    "kind": "file",
                },
                {
                    "file": {
                        "mimeType": "application/pdf",
                        "name": "remote-report.pdf",
                        "uri": "https://files.example.com/report.pdf",
                    },
                    "kind": "file",
                },
            ]
        },
    }


@pytest.mark.unit
@pytest.mark.telemetry
def test_trace_projection_preserves_selected_business_data() -> None:
    encoded_looking_value = base64.b64encode(b"ordinary-text").decode("ascii")

    trace_value = json.loads(
        _serialize_trace_value(
            _build_tool_trace_input(
                "analyze",
                "server-123",
                {
                    "document": encoded_looking_value,
                    "nested": {"raw_bytes": b"binary"},
                },
            )
        )
    )

    assert trace_value == {
        "arguments": {
            "document": encoded_looking_value,
            "nested": {"raw_bytes": "[RAW PYTHON BYTES OMITTED]"},
        },
        "server_id": "server-123",
        "tool_name": "analyze",
    }


@pytest.mark.unit
@pytest.mark.telemetry
def test_trace_projection_covers_current_protocol_models() -> None:
    excluded_fields = {
        Annotations: set(),
        AudioContent: {"data", "meta"},
        BlobResourceContents: {"blob", "meta"},
        CallToolResult: {"meta"},
        DataPart: {"metadata"},
        EmbeddedResource: {"meta"},
        FilePart: {"metadata"},
        FileWithBytes: {"bytes"},
        FileWithUri: set(),
        ImageContent: {"data", "meta"},
        ResourceLink: {"icons", "meta"},
        TextContent: {"meta"},
        TextPart: {"metadata"},
        TextResourceContents: {"meta"},
    }

    assert set(_TRACE_MODEL_FIELDS) == set(excluded_fields)
    for model, excluded in excluded_fields.items():
        included = set(_TRACE_MODEL_FIELDS[model])
        assert included.isdisjoint(excluded)
        assert included | excluded == set(model.model_fields)

    assert set(AgentMessageInput.model_fields) == {"parts"}

    content_annotation = get_args(CallToolResult.model_fields["content"].annotation)[0]
    assert set(get_args(content_annotation)) == {
        AudioContent,
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
    }
    assert set(get_args(EmbeddedResource.model_fields["resource"].annotation)) == {
        BlobResourceContents,
        TextResourceContents,
    }

    annotated_part = get_args(AgentMessageInput.model_fields["parts"].annotation)[0]
    part_annotation = get_args(annotated_part)[0]
    assert set(get_args(part_annotation)) == {DataPart, FilePart, TextPart}
    assert set(get_args(FilePart.model_fields["file"].annotation)) == {FileWithBytes, FileWithUri}


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_tool_trace_records_caller_target_and_complete_io() -> None:
    result = CallToolResult(content=[TextContent(type="text", text="completed")])

    @trace_tool_execution
    async def execute(ctx, tool_name, arguments, server_id):
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        returned = await execute(
            _make_ctx(),
            "search",
            {
                "access_token": "do-not-export",
                "max_tokens": 100,
                "query": "otel",
                "secretary": "public-role",
                "token_count": 20,
            },
            "server-123",
        )

    assert returned is result
    (span_name,) = tracer.start_as_current_span.call_args.args
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    input_calls = [call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.input"]
    input_value = json.loads(input_calls[0].args[1])
    assert span_name == "mcp.tool.execute"
    assert attributes["langfuse.observation.type"] == "tool"
    assert attributes["langfuse.trace.name"] == "McpRun"
    assert attributes["langfuse.trace.tags"] == ["registry", "mcp"]
    assert attributes["langfuse.trace.metadata.app"] == "registry"
    assert attributes["langfuse.user.id"] == "507f1f77bcf86cd799439011"
    assert attributes["langfuse.trace.metadata.username"] == "kelvin"
    assert attributes["langfuse.trace.metadata.oauthClientId"] == "mcp-client-123"
    assert attributes["langfuse.trace.metadata.mcpClientName"] == "Claude Code"
    assert attributes["langfuse.trace.metadata.mcpClientVersion"] == "1.2.3"
    assert attributes["langfuse.trace.metadata.authMethod"] == "jwt"
    assert attributes["langfuse.trace.metadata.authProvider"] == "keycloak"
    assert attributes["langfuse.trace.metadata.authSource"] == "jwt_auth"
    assert attributes["langfuse.trace.metadata.requestId"] == "request-123"
    assert attributes["registry.caller.authenticated"] is True
    assert input_value == {
        "arguments": {
            "access_token": "do-not-export",
            "max_tokens": 100,
            "query": "otel",
            "secretary": "public-role",
            "token_count": 20,
        },
        "server_id": "server-123",
        "tool_name": "search",
    }
    span.set_attribute.assert_any_call("registry.operation.success", True)
    output_calls = [call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.output"]
    assert json.loads(output_calls[0].args[1])["content"][0]["text"] == "completed"


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_tool_trace_omits_unavailable_caller_attributes() -> None:
    state = SimpleNamespace(
        user={
            "user_id": None,
            "client_id": "",
            "provider": "keycloak",
            "auth_source": None,
        }
    )
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(request=SimpleNamespace(state=state)),
        session=SimpleNamespace(client_params=SimpleNamespace(clientInfo=SimpleNamespace(name="", version=None))),
    )

    @trace_tool_execution
    async def execute(ctx, tool_name, arguments, server_id):
        return CallToolResult(content=[])

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        await execute(ctx, "search", {}, "server-123")

    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert attributes["langfuse.trace.metadata.authProvider"] == "keycloak"
    assert attributes["registry.caller.authenticated"] is False
    assert "langfuse.user.id" not in attributes
    assert "langfuse.trace.metadata.oauthClientId" not in attributes
    assert "langfuse.trace.metadata.authSource" not in attributes
    assert "langfuse.trace.metadata.mcpClientName" not in attributes
    assert "langfuse.trace.metadata.mcpClientVersion" not in attributes
    assert attributes["langfuse.trace.tags"] == ["registry", "mcp"]
    assert all(value is not None and value != "" for value in attributes.values())


@pytest.mark.unit
@pytest.mark.telemetry
def test_caller_identity_failure_is_logged_without_request_data(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenContext:
        sensitive_marker = "DO_NOT_LOG_CALLER_CONTEXT"

        @property
        def request_context(self):
            raise RuntimeError("caller extraction failed")

    with caplog.at_level("WARNING", logger="registry.mcpgw.tracing"):
        identity = _extract_caller_identity(BrokenContext())

    assert identity == {}
    assert "Failed to extract caller identity for tracing" in caplog.text
    assert "DO_NOT_LOG_CALLER_CONTEXT" not in caplog.text


@pytest.mark.unit
@pytest.mark.telemetry
def test_span_attribute_failure_is_logged_without_attribute_data(caplog: pytest.LogCaptureFixture) -> None:
    span = MagicMock()
    span.is_recording.return_value = True
    span.set_attribute.side_effect = RuntimeError("attribute rejected")

    with caplog.at_level("WARNING", logger="registry.mcpgw.tracing"):
        _set_span_attributes(span, {"sensitive-key": "DO_NOT_LOG_ATTRIBUTE_VALUE"})

    assert "Failed to set trace attributes (RuntimeError)" in caplog.text
    assert "sensitive-key" not in caplog.text
    assert "DO_NOT_LOG_ATTRIBUTE_VALUE" not in caplog.text


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("discovery_kind", "expected_span_name", "expected_trace_name", "expected_tool_name", "expected_tags"),
    [
        ("mcp", "mcp.discovery", "McpDiscovery", "discover_servers", ["registry", "mcp", "discovery"]),
        ("a2a", "a2a.discovery", "AgentDiscovery", "discover_agents", ["registry", "agent", "discovery"]),
    ],
)
async def test_discovery_trace_records_caller_search_and_results(
    discovery_kind: DiscoveryKind,
    expected_span_name: str,
    expected_trace_name: str,
    expected_tool_name: str,
    expected_tags: list[str],
) -> None:
    results = [{"entity_type": "tool", "server_id": "server-123", "tool_name": "search"}]

    @trace_discovery
    async def discover(ctx, query, top_n, search_type, type_list, kind):
        return results

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        returned = await discover(_make_ctx(), "search tools", 3, "hybrid", ["tool"], discovery_kind)

    assert returned is results
    (span_name,) = tracer.start_as_current_span.call_args.args
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert span_name == expected_span_name
    assert attributes["langfuse.observation.type"] == "tool"
    assert attributes["langfuse.trace.name"] == expected_trace_name
    assert attributes["langfuse.trace.tags"] == expected_tags
    assert attributes["langfuse.trace.metadata.app"] == "registry"
    assert attributes["mcp.tool.name"] == expected_tool_name
    assert attributes["registry.operation.type"] == f"{discovery_kind}_discovery"
    input_call = next(
        call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.input"
    )
    output_call = next(
        call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.output"
    )
    assert json.loads(input_call.args[1]) == {
        "query": "search tools",
        "search_type": "hybrid",
        "top_n": 3,
        "type_list": ["tool"],
    }
    assert json.loads(output_call.args[1]) == results
    span.set_attribute.assert_any_call("registry.operation.success", True)


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_discovery_trace_preserves_search_failure() -> None:
    error = RuntimeError("search unavailable")

    @trace_discovery
    async def discover(ctx, query, top_n, search_type, type_list, kind):
        raise error

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        with pytest.raises(RuntimeError) as exc_info:
            await discover(_make_ctx(), "search tools", 3, "hybrid", ["tool"], "mcp")

    assert exc_info.value is error
    span.set_attribute.assert_any_call("registry.operation.success", False)


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_agent_trace_marks_error_result_as_failure() -> None:
    result = CallToolResult(content=[TextContent(type="text", text="denied")], isError=True)
    message = AgentMessageInput(parts=[TextPart(kind="text", text="run")])

    @trace_agent_execution
    async def execute(agent_id, message, ctx):
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        returned = await execute("agent-123", message, _make_ctx())

    assert returned is result
    (span_name,) = tracer.start_as_current_span.call_args.args
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert span_name == "a2a.agent.execute"
    assert attributes["langfuse.observation.type"] == "agent"
    assert attributes["langfuse.trace.name"] == "AgentRun"
    assert attributes["langfuse.trace.tags"] == ["registry", "agent"]
    assert attributes["langfuse.trace.metadata.app"] == "registry"
    assert attributes["registry.operation.type"] == "a2a_agent"
    assert attributes["a2a.agent.id"] == "agent-123"
    input_call = next(
        call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.input"
    )
    assert json.loads(input_call.args[1]) == {
        "agent_id": "agent-123",
        "message": {"parts": [{"kind": "text", "text": "run"}]},
    }
    span.set_attribute.assert_any_call("registry.operation.success", False)
    span.set_status.assert_called_once()


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_tool_trace_marks_cancellation_and_reraises() -> None:
    @trace_tool_execution
    async def execute(ctx, tool_name, arguments, server_id):
        raise asyncio.CancelledError

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        with pytest.raises(asyncio.CancelledError):
            await execute(_make_ctx(), "search", {}, "server-123")

    input_call = next(
        call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.input"
    )
    assert json.loads(input_call.args[1]) == {
        "arguments": {},
        "server_id": "server-123",
        "tool_name": "search",
    }
    span.set_attribute.assert_any_call("registry.operation.success", False)


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_agent_trace_marks_cancellation_and_reraises() -> None:
    message = AgentMessageInput(parts=[TextPart(kind="text", text="run")])

    @trace_agent_execution
    async def execute(agent_id, message, ctx):
        raise asyncio.CancelledError

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        with pytest.raises(asyncio.CancelledError):
            await execute("agent-123", message, _make_ctx())

    input_call = next(
        call for call in span.set_attribute.call_args_list if call.args[0] == "langfuse.observation.input"
    )
    assert json.loads(input_call.args[1]) == {
        "agent_id": "agent-123",
        "message": {"parts": [{"kind": "text", "text": "run"}]},
    }
    span.set_attribute.assert_any_call("registry.operation.success", False)


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_tool_trace_serialization_failure_is_logged_and_does_not_change_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = CallToolResult(content=[TextContent(type="text", text="completed")])

    @trace_tool_execution
    async def execute(ctx, tool_name, arguments, server_id):
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    with caplog.at_level("WARNING", logger="registry.mcpgw.tracing"):
        with (
            patch("registry.mcpgw.tracing._TRACER") as tracer,
            patch("registry.mcpgw.tracing._serialize_trace_value", side_effect=TypeError("unsupported")),
        ):
            tracer.start_as_current_span.return_value = nullcontext(span)
            returned = await execute(_make_ctx(), "search", {}, "server-123")

    assert returned is result
    span.set_attribute.assert_any_call("registry.operation.success", True)
    assert not any(call.args[0] == "langfuse.observation.input" for call in span.set_attribute.call_args_list)
    assert "Failed to project, serialize, or set trace attribute langfuse.observation.input" in caplog.text
    assert "server-123" not in caplog.text
    span.add_event.assert_any_call(
        "registry.tracing.attribute.error",
        attributes={
            "exception.type": "TypeError",
            "registry.tracing.attribute": "langfuse.observation.input",
        },
    )


@pytest.mark.unit
@pytest.mark.telemetry
@pytest.mark.asyncio
async def test_tool_trace_projection_failure_is_logged_and_does_not_change_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = CallToolResult(content=[TextContent(type="text", text="completed")])

    @trace_tool_execution
    async def execute(ctx, tool_name, arguments, server_id):
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    with caplog.at_level("WARNING", logger="registry.mcpgw.tracing"):
        with patch("registry.mcpgw.tracing._TRACER") as tracer:
            tracer.start_as_current_span.return_value = nullcontext(span)
            returned = await execute(_make_ctx(), "search", {"unsupported": object()}, "server-123")

    assert returned is result
    span.set_attribute.assert_any_call("registry.operation.success", True)
    assert not any(call.args[0] == "langfuse.observation.input" for call in span.set_attribute.call_args_list)
    assert "Failed to project, serialize, or set trace attribute langfuse.observation.input" in caplog.text
    assert "server-123" not in caplog.text
    span.add_event.assert_any_call(
        "registry.tracing.attribute.error",
        attributes={
            "exception.type": "TypeError",
            "registry.tracing.attribute": "langfuse.observation.input",
        },
    )
