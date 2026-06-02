from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from beanie import PydanticObjectId
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent, TextResourceContents

from registry.mcpgw.tools import agent
from registry.mcpgw.tools.agent import AgentMessageInput, _convert_response, execute_agent_impl
from registry_pkgs.workflows.a2a_client import A2ACallResult


class _PermissiveAccessibleSet:
    """Mock-friendly container that says yes to every `<id> in self` check.

    Used as the default `acl_service.get_accessible_resource_ids` return value
    so existing tests don't need to know the agent_id in advance. Pass an
    explicit `accessible_agent_ids` list to `_make_ctx` to test ACL denial.
    """

    def __contains__(self, _item: object) -> bool:
        return True


def _make_ctx(
    jwt_config=None,
    a2a_httpx_client=None,
    *,
    user_id: str = "507f1f77bcf86cd799439011",
    accessible_agent_ids: list[str] | None = None,
):
    accessible: object = _PermissiveAccessibleSet() if accessible_agent_ids is None else accessible_agent_ids
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=accessible)

    lifespan_context = SimpleNamespace(
        jwt_signing_config=jwt_config or SimpleNamespace(),
        a2a_httpx_client=a2a_httpx_client,
        a2a_headers_provider=MagicMock(),
        acl_service=acl_service,
    )
    request_state = SimpleNamespace(user={"user_id": user_id})
    request_context = SimpleNamespace(
        lifespan_context=lifespan_context,
        request=SimpleNamespace(state=request_state),
    )
    ctx = AsyncMock()
    ctx.request_context = request_context
    return ctx


def _make_agent(agent_id: str | None = None):
    oid = PydanticObjectId(agent_id) if agent_id else PydanticObjectId()
    agent = MagicMock()
    agent.id = oid
    agent.path = "/test-agent"
    return agent


def _text_artifact(name: str, text: str, *, artifact_id: str = "a1", extra_parts: list[Part] | None = None) -> Artifact:
    parts: list[Part] = [Part(root=TextPart(kind="text", text=text))] if text else []
    if extra_parts:
        parts.extend(extra_parts)
    return Artifact(artifact_id=artifact_id, name=name, parts=parts)


def _completed_task(*artifacts: Artifact) -> Task:
    return Task(
        id="t1",
        context_id="c1",
        kind="task",
        status=TaskStatus(state=TaskState.completed),
        artifacts=list(artifacts) if artifacts else None,
    )


def _result_with_message(text: str) -> A2ACallResult:
    msg = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text=text))],
        message_id="m",
    )
    return A2ACallResult(message=msg, success=True)


def _result_with_task(*artifacts: Artifact) -> A2ACallResult:
    return A2ACallResult(task=_completed_task(*artifacts), success=True)


def _msg(text: str) -> AgentMessageInput:
    return AgentMessageInput(parts=[TextPart(kind="text", text=text)])


@pytest.mark.asyncio
async def test_execute_agent_invalid_id_returns_error():
    ctx = _make_ctx()
    result = await execute_agent_impl("not-a-valid-objectid", _msg("hello"), ctx)

    assert result.isError is True
    assert len(result.content) == 1
    assert "Invalid agent_id" in result.content[0].text


@pytest.mark.asyncio
async def test_execute_agent_not_found_returns_error():
    """The single 'not found / inactive' branch — `find_one(id, status=active)`
    returns None — should produce the combined error message."""
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())

    with patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model:
        mock_model.id = MagicMock()
        mock_model.status = MagicMock()
        mock_model.find_one = AsyncMock(return_value=None)

        result = await execute_agent_impl(valid_id, _msg("hello"), ctx)

    assert result.isError is True
    text = result.content[0].text
    assert "not found" in text
    assert "no longer active" in text


@pytest.mark.asyncio
async def test_execute_agent_happy_path_returns_text():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent.call_a2a", new_callable=AsyncMock) as mock_call,
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)
        mock_call.return_value = _result_with_message("Agent response text")

        result = await execute_agent_impl(valid_id, _msg("Do something"), ctx)

    assert result.isError is not True
    assert any(isinstance(c, TextContent) and "Agent response text" in c.text for c in result.content)


@pytest.mark.asyncio
async def test_execute_agent_denied_when_agent_not_in_user_acl():
    """ACL gate: if the requesting user does not have VIEW on the target
    agent, execute_agent must refuse before any call_a2a happens."""
    valid_id = str(PydanticObjectId())
    other_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_agent_ids=[other_id])  # caller can see "other_id" only
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent.call_a2a", new_callable=AsyncMock) as mock_call,
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)
        result = await execute_agent_impl(valid_id, _msg("Do something"), ctx)

    assert result.isError is True
    assert "Access denied" in result.content[0].text
    mock_call.assert_not_awaited()  # call_a2a must NOT be invoked when ACL denies


@pytest.mark.asyncio
async def test_execute_agent_rejects_missing_user_context():
    """When request.state.user has no user_id (no auth), reject before agent lookup."""
    valid_id = str(PydanticObjectId())
    ctx = _make_ctx(user_id=None)  # type: ignore[arg-type]
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent.call_a2a", new_callable=AsyncMock) as mock_call,
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)
        result = await execute_agent_impl(valid_id, _msg("Do something"), ctx)

    assert result.isError is True
    assert "Authentication required" in result.content[0].text
    mock_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_agent_forwards_a2a_httpx_client_to_call_a2a():
    """The shared client on McpAppContext must flow into call_a2a so the
    MCP tool reuses one connection pool across invocations."""
    import httpx

    shared = httpx.AsyncClient()
    try:
        ctx = _make_ctx(a2a_httpx_client=shared)
        valid_id = str(PydanticObjectId())
        agent = _make_agent(valid_id)
        captured: dict = {}

        async def fake_call_a2a(agent_obj, text, **kwargs):
            captured.update(kwargs)
            return _result_with_message("ok")

        with (
            patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model,
            patch("registry.mcpgw.tools.agent.call_a2a", side_effect=fake_call_a2a),
        ):
            mock_model.id = MagicMock()
            mock_model.find_one = AsyncMock(return_value=agent)
            await execute_agent_impl(valid_id, _msg("test"), ctx)

        assert captured.get("httpx_client") is shared
    finally:
        await shared.aclose()


@pytest.mark.asyncio
async def test_execute_agent_a2a_failure_returns_error():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent.call_a2a", new_callable=AsyncMock) as mock_call,
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)
        mock_call.return_value = A2ACallResult(success=False, error="connection refused")

        result = await execute_agent_impl(valid_id, _msg("Do something"), ctx)

    assert result.isError is True
    assert "connection refused" in result.content[0].text


@pytest.mark.asyncio
async def test_get_tools_returns_execute_agent():
    tools = agent.get_tools()
    names = [name for name, _ in tools]
    assert "execute_agent" in names


# ── response conversion ──────────────────────────────────────────────────────


def test_convert_response_message_no_label():
    """Message replies render as a single TextContent with no `[name]` prefix."""
    items = _convert_response(_result_with_message("hello"))
    assert len(items) == 1
    assert isinstance(items[0], TextContent)
    assert items[0].text == "hello"


def test_convert_response_task_single_artifact_with_label():
    items = _convert_response(_result_with_task(_text_artifact("Summary", "short version")))
    assert len(items) == 1
    assert isinstance(items[0], TextContent)
    assert items[0].text == "[Summary]\nshort version"


def test_convert_response_task_multiple_artifacts_keep_boundaries():
    items = _convert_response(
        _result_with_task(
            _text_artifact("Summary", "short", artifact_id="a1"),
            _text_artifact("Detail", "long", artifact_id="a2"),
        )
    )
    texts = [c.text for c in items if isinstance(c, TextContent)]
    assert texts == ["[Summary]\nshort", "[Detail]\nlong"]


def test_convert_response_task_file_with_bytes():
    b64_data = "aGVsbG8gcGRm"
    artifact = _text_artifact(
        "Report",
        "",
        extra_parts=[Part(root=FilePart(kind="file", file=FileWithBytes(bytes=b64_data, mimeType="application/pdf")))],
    )
    items = _convert_response(_result_with_task(artifact))
    assert len(items) == 1
    assert isinstance(items[0], EmbeddedResource)
    resource = items[0].resource
    assert isinstance(resource, BlobResourceContents)
    assert resource.blob == b64_data
    assert resource.mimeType == "application/pdf"
    assert "urn:a2a:file:" in str(resource.uri)


def test_convert_response_task_file_with_uri():
    artifact = _text_artifact(
        "Report",
        "",
        extra_parts=[
            Part(
                root=FilePart(
                    kind="file",
                    file=FileWithUri(uri="https://cdn.example.com/report.pdf", mimeType="application/pdf"),
                )
            )
        ],
    )
    items = _convert_response(_result_with_task(artifact))
    assert len(items) == 1
    assert isinstance(items[0], EmbeddedResource)
    resource = items[0].resource
    assert isinstance(resource, TextResourceContents)
    assert "cdn.example.com" in resource.text


def test_convert_response_task_data_payload():
    artifact = _text_artifact(
        "Stats",
        "",
        extra_parts=[Part(root=DataPart(kind="data", data={"key": "value", "count": 3}))],
    )
    items = _convert_response(_result_with_task(artifact))
    assert len(items) == 1
    assert isinstance(items[0], TextContent)
    assert '"key"' in items[0].text


def test_convert_response_task_multiple_files_get_unique_uris():
    b64_data = "ZGF0YQ=="
    artifact = _text_artifact(
        "Bundle",
        "",
        extra_parts=[
            Part(root=FilePart(kind="file", file=FileWithBytes(bytes=b64_data, mimeType="image/png"))),
            Part(root=FilePart(kind="file", file=FileWithBytes(bytes=b64_data, mimeType="image/png"))),
        ],
    )
    items = _convert_response(_result_with_task(artifact))
    uris = [str(item.resource.uri) for item in items if isinstance(item, EmbeddedResource)]
    assert len(uris) == 2
    assert uris[0] != uris[1], "each FileWithBytes must get a unique URI"


def test_convert_response_artifact_orders_text_then_files_then_data():
    """Per-artifact ordering: text → files → data."""
    artifact = _text_artifact(
        "Combo",
        "prose",
        extra_parts=[
            Part(root=FilePart(kind="file", file=FileWithUri(uri="https://x/y"))),
            Part(root=DataPart(kind="data", data={"k": 1})),
        ],
    )
    items = _convert_response(_result_with_task(artifact))
    assert len(items) == 3
    assert isinstance(items[0], TextContent)
    assert items[0].text == "[Combo]\nprose"
    assert isinstance(items[1], EmbeddedResource)
    assert isinstance(items[2], TextContent)
    assert '"k"' in items[2].text


def test_convert_response_task_status_message_only():
    """Completed task with no artifacts → use task.status.message."""
    msg = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text="done via status"))],
        message_id="m",
    )
    task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.completed, message=msg),
        artifacts=None,
    )
    items = _convert_response(A2ACallResult(task=task, success=True))
    assert len(items) == 1
    assert isinstance(items[0], TextContent)
    assert items[0].text == "done via status"


def test_convert_response_task_status_message_AND_artifacts_both_rendered():
    """Per spec, both content carriers must surface; status.message first."""
    status_msg = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text="finished — see report"))],
        message_id="m",
    )
    task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.completed, message=status_msg),
        artifacts=[_text_artifact("Report", "page1")],
    )
    items = _convert_response(A2ACallResult(task=task, success=True))
    text_items = [c.text for c in items if isinstance(c, TextContent)]
    # status.message first, artifacts second (matches host_agent.py order).
    assert text_items == ["finished — see report", "[Report]\npage1"]


def test_convert_response_empty_result():
    items = _convert_response(A2ACallResult(success=True))
    assert items == []


# ── status filter and IAM guard ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_agent_iam_unsupported_returns_error():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent.A2AAgent") as mock_model,
        patch(
            "registry.mcpgw.tools.agent.raise_if_iam_unsupported",
            side_effect=NotImplementedError("IAM-authenticated AgentCore A2A runtime is not supported"),
        ),
    ):
        mock_model.id = MagicMock()
        mock_model.status = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)

        result = await execute_agent_impl(valid_id, _msg("hello"), ctx)

    assert result.isError is True
    assert "IAM" in result.content[0].text


# ── AgentMessageInput validation ─────────────────────────────────────────────


def test_agent_message_input_single_text_part():
    msg = AgentMessageInput(parts=[TextPart(kind="text", text="hello")])
    assert len(msg.parts) == 1
    assert msg.parts[0].kind == "text"
    assert msg.parts[0].text == "hello"


def test_agent_message_input_multi_part_data_and_text():
    msg = AgentMessageInput(
        parts=[
            DataPart(kind="data", data={"month": "2024-01"}),
            TextPart(kind="text", text="Summarize spending by category"),
        ]
    )
    assert len(msg.parts) == 2
    assert msg.parts[0].kind == "data"
    assert msg.parts[1].kind == "text"


def test_agent_message_input_file_uri_part():
    msg = AgentMessageInput(
        parts=[FilePart(kind="file", file=FileWithUri(uri="s3://bucket/report.json", mimeType="application/json"))]
    )
    assert len(msg.parts) == 1
    assert msg.parts[0].kind == "file"
    assert "bucket" in str(msg.parts[0].file.uri)


def test_agent_message_input_empty_parts_rejected():
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        AgentMessageInput(parts=[])


def test_agent_message_input_invalid_kind_rejected():
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        AgentMessageInput.model_validate({"parts": [{"kind": "unknown", "text": "x"}]})


def test_agent_message_input_from_dict_text_part():
    msg = AgentMessageInput.model_validate({"parts": [{"kind": "text", "text": "run the analysis"}]})
    assert len(msg.parts) == 1
    assert msg.parts[0].kind == "text"
    assert msg.parts[0].text == "run the analysis"
