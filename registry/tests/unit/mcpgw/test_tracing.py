"""Unit tests for semantic MCP Gateway tracing."""

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import CallToolResult, Implementation, TextContent

from registry.mcpgw.tracing import _extract_caller_identity, trace_agent_execution, trace_tool_execution


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
    assert span_name == "registry.mcp.tool.execute"
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
@pytest.mark.asyncio
async def test_agent_trace_marks_error_result_as_failure() -> None:
    result = CallToolResult(content=[TextContent(type="text", text="denied")], isError=True)

    @trace_agent_execution
    async def execute(agent_id, message, ctx):
        return result

    span = MagicMock()
    span.is_recording.return_value = True
    with patch("registry.mcpgw.tracing._TRACER") as tracer:
        tracer.start_as_current_span.return_value = nullcontext(span)
        returned = await execute("agent-123", {"parts": [{"kind": "text", "text": "run"}]}, _make_ctx())

    assert returned is result
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert attributes["registry.operation.type"] == "a2a_agent"
    assert attributes["a2a.agent.id"] == "agent-123"
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

    span.set_attribute.assert_any_call(
        "langfuse.observation.input",
        '{"arguments":{},"server_id":"server-123","tool_name":"search"}',
    )


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
            patch("registry.mcpgw.tracing._serialize_audit_value", side_effect=TypeError("unsupported")),
        ):
            tracer.start_as_current_span.return_value = nullcontext(span)
            returned = await execute(_make_ctx(), "search", {}, "server-123")

    assert returned is result
    span.set_attribute.assert_any_call("registry.operation.success", True)
    assert "Failed to serialize or set trace attribute langfuse.observation.input" in caplog.text
    assert "server-123" not in caplog.text
