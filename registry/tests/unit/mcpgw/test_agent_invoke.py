from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from a2a.client.errors import (
    A2AClientError,
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientJSONRPCError,
    A2AClientTimeoutError,
)
from a2a.types import (
    Artifact,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from beanie import PydanticObjectId
from fastapi import HTTPException
from mcp.types import CallToolResult

from registry.mcpgw.tools import agent_invoke
from registry.mcpgw.tools.agent_invoke import (
    extract_text,
    invoke_agent_impl,
    parts_to_text,
)

mock_agent_id = "a" * 24


def _make_text_part(text: str) -> Part:
    return Part(root=TextPart(kind="text", text=text))


def _make_message(text: str) -> Message:
    return Message(
        message_id="msg-1",
        role=Role.agent,
        parts=[_make_text_part(text)],
    )


def _make_task_with_artifacts(text: str) -> Task:
    return Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="art-1",
                parts=[_make_text_part(text)],
            )
        ],
    )


def _make_task_no_artifacts(status_text: str) -> Task:
    return Task(
        id="task-2",
        context_id="ctx-2",
        status=TaskStatus(
            state=TaskState.completed,
            message=_make_message(status_text),
        ),
    )


def _make_task_empty() -> Task:
    return Task(
        id="task-3",
        context_id="ctx-3",
        status=TaskStatus(state=TaskState.completed),
    )


def _make_agent(
    *,
    agent_id: str = mock_agent_id,
    enabled: bool = True,
    transport: str = "jsonrpc",
    url: str = "http://agent.test",
    title: str = "Test Agent",
) -> MagicMock:
    agent = MagicMock()
    agent.id = PydanticObjectId(agent_id)
    agent.isEnabled = enabled
    agent.config = MagicMock()
    agent.config.title = title
    agent.config.url = url
    agent.config.type = transport
    agent.card = MagicMock()
    agent.card.name = title
    agent.card.url = url
    agent.card.model_copy = MagicMock(return_value=agent.card)
    return agent


def _make_ctx(
    agent=None,
    acl_raises: bool = False,
    user_id: str = mock_agent_id,
) -> SimpleNamespace:
    """Build a minimal fake FastMCP Context."""
    agent_service = AsyncMock()
    agent_service.get_agent_by_id = AsyncMock(return_value=agent)

    acl_service = AsyncMock()
    if acl_raises:
        acl_service.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))
    else:
        acl_service.check_user_permission = AsyncMock(return_value=MagicMock())

    lifespan = SimpleNamespace(
        a2a_agent_service=agent_service,
        acl_service=acl_service,
        proxy_client=AsyncMock(spec=httpx.AsyncClient),
    )

    request = SimpleNamespace(state=SimpleNamespace(user={"user_id": user_id, "username": "tester"}))
    request_context = SimpleNamespace(lifespan_context=lifespan, request=request)
    return SimpleNamespace(request_context=request_context)


def test_parts_to_text_single():
    parts = [_make_text_part("hello")]
    assert parts_to_text(parts) == "hello"


def test_parts_to_text_multiple():
    parts = [_make_text_part("line1"), _make_text_part("line2")]
    assert parts_to_text(parts) == "line1\nline2"


def test_parts_to_text_skips_non_text():
    text_part = _make_text_part("keep")
    non_text = MagicMock()
    non_text.root = MagicMock(spec=[])  # not a TextPart
    assert parts_to_text([text_part, non_text]) == "keep"


def test_extract_text_from_message():
    msg = _make_message("hello from agent")
    assert extract_text(msg) == "hello from agent"


def test_extract_text_from_task_artifacts():
    task = _make_task_with_artifacts("artifact output")
    assert extract_text(task) == "artifact output"


def test_extract_text_from_task_status_message_fallback():
    task = _make_task_no_artifacts("status fallback")
    assert extract_text(task) == "status fallback"


def test_extract_text_from_task_json_fallback():
    task = _make_task_empty()
    result = extract_text(task)
    assert "task-3" in result  # JSON serialization contains the task id


@pytest.mark.asyncio
async def test_agent_not_found():
    ctx = _make_ctx(agent=None)
    result = await invoke_agent_impl(ctx, mock_agent_id, "hello")
    assert result.isError is True
    assert "No agent found" in result.content[0].text


@pytest.mark.asyncio
async def test_agent_disabled():
    agent = _make_agent(enabled=False)
    ctx = _make_ctx(agent=agent)
    result = await invoke_agent_impl(ctx, mock_agent_id, "hello")
    assert result.isError is True
    assert "disabled" in result.content[0].text


@pytest.mark.asyncio
async def test_acl_denied_raises():
    """ACL denial must bubble up as HTTPException, not be swallowed as isError."""
    agent = _make_agent()
    ctx = _make_ctx(agent=agent, acl_raises=True)
    with pytest.raises(HTTPException) as exc_info:
        await invoke_agent_impl(ctx, mock_agent_id, "hello")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_jsonrpc_transport_message_response():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)
    fake_result = _make_message("agent says hello")

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(return_value=fake_result)
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "ping")

    assert result.isError is not True
    assert result.content[0].text == "agent says hello"


@pytest.mark.asyncio
async def test_jsonrpc_transport_task_with_artifacts():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)
    fake_result = _make_task_with_artifacts("deep analysis report")

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(return_value=fake_result)
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "analyze this")

    assert result.isError is not True
    assert result.content[0].text == "deep analysis report"


@pytest.mark.asyncio
async def test_jsonrpc_transport_task_status_message_fallback():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)
    fake_result = _make_task_no_artifacts("processing complete")

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(return_value=fake_result)
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "do task")

    assert result.isError is not True
    assert result.content[0].text == "processing complete"


@pytest.mark.asyncio
async def test_http_json_transport_dispatched():
    agent = _make_agent(transport="http_json")
    ctx = _make_ctx(agent=agent)
    fake_result = _make_message("rest response")

    with (
        patch("registry.mcpgw.tools.agent_invoke.RestTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(return_value=fake_result)
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    MockTransport.assert_called_once()
    assert result.content[0].text == "rest response"


@pytest.mark.asyncio
async def test_skill_name_set_in_metadata():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)
    fake_result = _make_message("skill output")
    captured_params: list[MessageSendParams] = []

    async def _capture_send(params: MessageSendParams, **_):
        captured_params.append(params)
        return fake_result

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = _capture_send
        MockTransport.return_value = transport_instance

        await invoke_agent_impl(ctx, mock_agent_id, "hello", skill_name="search_flights")

    assert len(captured_params) == 1
    assert captured_params[0].message.metadata == {"skill": "search_flights"}


@pytest.mark.asyncio
async def test_timeout_returns_error():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "timeout" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_http_status_error_returns_error():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)
    mock_response = MagicMock()
    mock_response.status_code = 502

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(
            side_effect=httpx.HTTPStatusError("bad gateway", request=MagicMock(), response=mock_response)
        )
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "502" in result.content[0].text


@pytest.mark.asyncio
async def test_a2a_client_http_error_returns_error():
    """A2AClientHTTPError (e.g. 401 from Bedrock AgentCore) → isError=True, not InternalServerException."""
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(side_effect=A2AClientHTTPError(401, "Unauthorized"))
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "401" in result.content[0].text


@pytest.mark.asyncio
async def test_a2a_client_timeout_error_returns_error():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(side_effect=A2AClientTimeoutError("read timeout"))
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "timeout" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_a2a_client_jsonrpc_error_returns_error():
    from a2a.types import JSONRPCError, JSONRPCErrorResponse

    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)
    error_response = JSONRPCErrorResponse(id="1", error=JSONRPCError(code=-32600, message="Invalid Request"))

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(side_effect=A2AClientJSONRPCError(error_response))
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "JSON-RPC" in result.content[0].text


@pytest.mark.asyncio
async def test_a2a_client_json_error_returns_error():
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(side_effect=A2AClientJSONError("unexpected token"))
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "unparseable" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_a2a_client_base_error_returns_error():
    """Unknown A2AClientError subclass → isError=True (base catch)."""
    agent = _make_agent(transport="jsonrpc")
    ctx = _make_ctx(agent=agent)

    with (
        patch("registry.mcpgw.tools.agent_invoke.JsonRpcTransport") as MockTransport,
        patch("registry.mcpgw.tools.agent_invoke.record_server_request"),
    ):
        transport_instance = AsyncMock()
        transport_instance.send_message = AsyncMock(side_effect=A2AClientError("some unknown a2a error"))
        MockTransport.return_value = transport_instance

        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "communication error" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_unsupported_transport_returns_error():
    agent = _make_agent(transport="websocket")
    ctx = _make_ctx(agent=agent)

    with patch("registry.mcpgw.tools.agent_invoke.record_server_request"):
        result = await invoke_agent_impl(ctx, mock_agent_id, "hello")

    assert result.isError is True
    assert "websocket" in result.content[0].text


@pytest.mark.asyncio
async def test_get_tools_wrapper_delegates_to_impl(monkeypatch):
    invoke_agent_func = dict(agent_invoke.get_tools())["invoke_agent"]
    mock_impl = AsyncMock(return_value=CallToolResult(content=[]))
    monkeypatch.setattr(agent_invoke, "invoke_agent_impl", mock_impl)

    ctx = SimpleNamespace()
    await invoke_agent_func(ctx=ctx, agent_id="abc", message="hello", skill_name="fly")
    mock_impl.assert_awaited_once_with(ctx, "abc", "hello", "fly")
