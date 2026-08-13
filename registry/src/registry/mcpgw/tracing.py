"""Semantic OpenTelemetry tracing for MCP Gateway executions."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from functools import wraps
from typing import Any

from a2a.types import DataPart, FilePart, FileWithBytes, FileWithUri, TextPart
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from mcp.types import (
    Annotations,
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from pydantic import AnyUrl, BaseModel

from .core.types import McpAppContext

logger = logging.getLogger(__name__)

_TOOL_SPAN_NAME = "registry.mcp.tool.execute"
_AGENT_SPAN_NAME = "registry.a2a.agent.execute"
_TRACER = trace.get_tracer("registry.mcpgw")
_RAW_PYTHON_BYTES_OMITTED = "[RAW PYTHON BYTES OMITTED]"
_TRACE_MODEL_FIELDS: dict[type[BaseModel], tuple[str, ...]] = {
    Annotations: ("audience", "priority"),
    AudioContent: ("type", "mimeType", "annotations"),
    BlobResourceContents: ("uri", "mimeType"),
    CallToolResult: ("content", "structuredContent", "isError"),
    DataPart: ("kind", "data"),
    EmbeddedResource: ("type", "resource", "annotations"),
    FilePart: ("kind", "file"),
    FileWithBytes: ("name", "mime_type"),
    FileWithUri: ("name", "mime_type", "uri"),
    ImageContent: ("type", "mimeType", "annotations"),
    ResourceLink: ("type", "name", "title", "uri", "description", "mimeType", "size", "annotations"),
    TextContent: ("type", "text", "annotations"),
    TextPart: ("kind", "text"),
    TextResourceContents: ("uri", "mimeType", "text"),
}


def _project_model_fields(value: BaseModel) -> dict[str, Any]:
    fields = _TRACE_MODEL_FIELDS.get(type(value))
    if fields is None:
        raise TypeError(f"Unsupported trace model type: {type(value).__name__}")
    projected: dict[str, Any] = {}
    for field_name in fields:
        field_value = getattr(value, field_name)
        if field_value is None:
            continue
        field_info = type(value).model_fields[field_name]
        alias = field_info.serialization_alias or field_info.alias
        key = alias if isinstance(alias, str) else field_name
        projected[key] = _project_trace_value(field_value)
    return projected


def _build_agent_trace_input(agent_id: str, message: Any) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "message": {"parts": message.parts},
    }


def _build_tool_trace_input(tool_name: str, server_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "arguments": arguments,
        "server_id": server_id,
        "tool_name": tool_name,
    }


def _project_trace_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _project_model_fields(value)

    if isinstance(value, dict):
        return {str(key): _project_trace_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_project_trace_value(item) for item in value]

    if isinstance(value, bytes):
        # This is an unexpected in-process Python value inside selected business data,
        # not one of the protocol-defined base64 string fields omitted above.
        return _RAW_PYTHON_BYTES_OMITTED

    if isinstance(value, AnyUrl):
        return str(value)

    if isinstance(value, Enum):
        return _project_trace_value(value.value)

    if isinstance(value, str):
        return value

    if value is None or isinstance(value, (bool, int, float)):
        return value

    raise TypeError(f"Unsupported trace value type: {type(value).__name__}")


def _serialize_trace_value(value: Any) -> str:
    serialized = _project_trace_value(value)
    return json.dumps(serialized, ensure_ascii=False, separators=(",", ":"))


def _clean_attributes(attributes: dict[str, Any]) -> dict[str, bool | int | float | str]:
    return {
        key: value for key, value in attributes.items() if isinstance(value, (bool, int, float, str)) and value != ""
    }


def _set_span_attributes(span: Span, attributes: dict[str, Any]) -> None:
    try:
        if not span.is_recording():
            return
        for key, value in _clean_attributes(attributes).items():
            span.set_attribute(key, value)
    except Exception:
        return


def _extract_caller_identity(ctx: Context[ServerSession, McpAppContext]) -> dict[str, Any]:
    try:
        request_context = getattr(ctx, "request_context", None)
        request = getattr(request_context, "request", None)
        state = getattr(request, "state", None)
        user_context = getattr(state, "user", None)
        identity: dict[str, Any] = {
            "authenticated": isinstance(user_context, dict) and bool(user_context.get("user_id")),
            "request_id": getattr(ctx, "request_id", None),
        }
        if isinstance(user_context, dict):
            identity.update(
                {
                    "auth_method": user_context.get("auth_method"),
                    "auth_provider": user_context.get("provider"),
                    "auth_source": user_context.get("auth_source"),
                    "oauth_client_id": user_context.get("client_id"),
                    "user_id": user_context.get("user_id"),
                    "username": user_context.get("username"),
                }
            )

        session = getattr(ctx, "session", None)
        client_params = getattr(session, "client_params", None)
        client_info = getattr(client_params, "clientInfo", None)
        if client_info is not None:
            identity["mcp_client_name"] = getattr(client_info, "name", None)
            identity["mcp_client_version"] = getattr(client_info, "version", None)

        return _clean_attributes(identity)
    except Exception:
        logger.warning("Failed to extract caller identity for tracing", exc_info=True)
        return {}


def _caller_attributes(ctx: Context[ServerSession, McpAppContext]) -> dict[str, Any]:
    identity = _extract_caller_identity(ctx)
    return _clean_attributes(
        {
            "langfuse.user.id": identity.get("user_id"),
            "langfuse.trace.metadata.username": identity.get("username"),
            "langfuse.trace.metadata.oauthClientId": identity.get("oauth_client_id"),
            "langfuse.trace.metadata.mcpClientName": identity.get("mcp_client_name"),
            "langfuse.trace.metadata.mcpClientVersion": identity.get("mcp_client_version"),
            "langfuse.trace.metadata.authMethod": identity.get("auth_method"),
            "langfuse.trace.metadata.authProvider": identity.get("auth_provider"),
            "langfuse.trace.metadata.authSource": identity.get("auth_source"),
            "langfuse.trace.metadata.requestId": identity.get("request_id"),
            "registry.caller.authenticated": identity.get("authenticated"),
        }
    )


def _set_serialized_attribute(
    span: Span,
    key: str,
    value: Any = None,
    *,
    projection: Callable[[], Any] | None = None,
) -> None:
    try:
        if span.is_recording():
            trace_value = projection() if projection is not None else value
            span.set_attribute(key, _serialize_trace_value(trace_value))
    except Exception as exc:
        logger.warning("Failed to project, serialize, or set trace attribute %s", key, exc_info=True)
        try:
            if span.is_recording():
                span.add_event(
                    "registry.tracing.attribute.error",
                    attributes={
                        "exception.type": type(exc).__name__,
                        "registry.tracing.attribute": key,
                    },
                )
        except Exception:
            logger.warning("Failed to record trace attribute error event for %s", key, exc_info=True)


def _record_result(span: Span, result: CallToolResult) -> None:
    success = result.isError is not True
    _set_serialized_attribute(span, "langfuse.observation.output", result)
    _set_span_attributes(span, {"registry.operation.success": success})
    if success:
        return
    try:
        span.set_status(Status(StatusCode.ERROR, "MCP operation returned an error result"))
    except Exception:
        return


def trace_tool_execution(
    func: Callable[
        [Context[ServerSession, McpAppContext], str, dict[str, Any], str],
        Awaitable[CallToolResult],
    ],
) -> Callable[
    [Context[ServerSession, McpAppContext], str, dict[str, Any], str],
    Awaitable[CallToolResult],
]:
    """Trace one downstream MCP tool execution with trace-relevant input and output."""

    @wraps(func)
    async def wrapper(
        ctx: Context[ServerSession, McpAppContext],
        tool_name: str,
        arguments: dict[str, Any],
        server_id: str,
    ) -> CallToolResult:
        attributes = {
            **_caller_attributes(ctx),
            "langfuse.observation.type": "span",
            "langfuse.trace.name": _TOOL_SPAN_NAME,
            "langfuse.trace.metadata.operationType": "mcp_tool",
            "registry.operation.type": "mcp_tool",
            "mcp.tool.name": tool_name,
            "mcp.server.id": server_id,
        }
        with _TRACER.start_as_current_span(_TOOL_SPAN_NAME, attributes=_clean_attributes(attributes)) as span:
            _set_serialized_attribute(
                span,
                "langfuse.observation.input",
                projection=lambda: _build_tool_trace_input(tool_name, server_id, arguments),
            )
            result = await func(ctx, tool_name, arguments, server_id)
            _record_result(span, result)
            return result

    return wrapper


def trace_agent_execution[AgentMessageT](
    func: Callable[
        [str, AgentMessageT, Context[ServerSession, McpAppContext]],
        Awaitable[CallToolResult],
    ],
) -> Callable[
    [str, AgentMessageT, Context[ServerSession, McpAppContext]],
    Awaitable[CallToolResult],
]:
    """Trace one downstream A2A agent execution with trace-relevant input and output."""

    @wraps(func)
    async def wrapper(
        agent_id: str,
        message: AgentMessageT,
        ctx: Context[ServerSession, McpAppContext],
    ) -> CallToolResult:
        attributes = {
            **_caller_attributes(ctx),
            "langfuse.observation.type": "span",
            "langfuse.trace.name": _AGENT_SPAN_NAME,
            "langfuse.trace.metadata.operationType": "a2a_agent",
            "registry.operation.type": "a2a_agent",
            "a2a.agent.id": agent_id,
        }
        with _TRACER.start_as_current_span(_AGENT_SPAN_NAME, attributes=_clean_attributes(attributes)) as span:
            _set_serialized_attribute(
                span,
                "langfuse.observation.input",
                projection=lambda: _build_agent_trace_input(agent_id, message),
            )
            result = await func(agent_id, message, ctx)
            _record_result(span, result)
            return result

    return wrapper
