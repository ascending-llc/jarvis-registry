from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import DataPart, FilePart, FileWithBytes, FileWithUri, Part
from beanie import PydanticObjectId
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent, TextResourceContents

from registry.mcpgw.tools import agent_invoke
from registry.mcpgw.tools.agent_invoke import _convert_artifacts, execute_agent_impl
from registry_pkgs.workflows.a2a_client import A2ACallResult


def _make_ctx(jwt_config=None):
    lifespan_context = SimpleNamespace(jwt_signing_config=jwt_config or SimpleNamespace())
    request_context = SimpleNamespace(lifespan_context=lifespan_context)
    ctx = AsyncMock()
    ctx.request_context = request_context
    return ctx


def _make_agent(agent_id: str | None = None):
    oid = PydanticObjectId(agent_id) if agent_id else PydanticObjectId()
    agent = MagicMock()
    agent.id = oid
    agent.path = "/test-agent"
    return agent


@pytest.mark.asyncio
async def test_execute_agent_invalid_id_returns_error():
    ctx = _make_ctx()
    result = await execute_agent_impl("not-a-valid-objectid", "hello", ctx)

    assert result.isError is True
    assert len(result.content) == 1
    assert "Invalid agent_id" in result.content[0].text


@pytest.mark.asyncio
async def test_execute_agent_not_found_returns_error():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())

    with patch("registry.mcpgw.tools.agent_invoke.A2AAgent") as mock_model:
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=None)

        result = await execute_agent_impl(valid_id, "hello", ctx)

    assert result.isError is True
    assert "not found" in result.content[0].text


@pytest.mark.asyncio
async def test_execute_agent_happy_path_returns_text():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent_invoke.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent_invoke.call_a2a", new_callable=AsyncMock) as mock_call,
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)
        mock_call.return_value = A2ACallResult(text="Agent response text", success=True)

        result = await execute_agent_impl(valid_id, "Do something", ctx)

    assert result.isError is not True
    assert any(isinstance(c, TextContent) and "Agent response text" in c.text for c in result.content)


@pytest.mark.asyncio
async def test_execute_agent_a2a_failure_returns_error():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent_invoke.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent_invoke.call_a2a", new_callable=AsyncMock) as mock_call,
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)
        mock_call.return_value = A2ACallResult(text="", success=False, error="connection refused")

        result = await execute_agent_impl(valid_id, "Do something", ctx)

    assert result.isError is True
    assert "connection refused" in result.content[0].text


@pytest.mark.asyncio
async def test_execute_agent_on_chunk_calls_ctx_log():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    async def fake_call_a2a(agent_obj, text, *, jwt_config, on_chunk=None):
        if on_chunk:
            await on_chunk("chunk1")
            await on_chunk("chunk2")
        return A2ACallResult(text="chunk1chunk2", success=True)

    with (
        patch("registry.mcpgw.tools.agent_invoke.A2AAgent") as mock_model,
        patch("registry.mcpgw.tools.agent_invoke.call_a2a", side_effect=fake_call_a2a),
    ):
        mock_model.id = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)

        result = await execute_agent_impl(valid_id, "stream me", ctx)

    assert ctx.log.await_count == 2
    assert result.isError is not True


@pytest.mark.asyncio
async def test_get_tools_returns_execute_agent():
    tools = agent_invoke.get_tools()
    names = [name for name, _ in tools]
    assert "execute_agent" in names


# ── artifact conversion ──────────────────────────────────────────────────────


def test_convert_artifacts_file_with_bytes():
    # FileWithBytes.bytes is already a base64 string (a2a SDK convention)
    b64_data = "aGVsbG8gcGRm"  # base64 of b"hello pdf"
    part = Part(root=FilePart(file=FileWithBytes(bytes=b64_data, mimeType="application/pdf"), kind="file"))
    result = A2ACallResult(text="", artifacts=[part])

    items = _convert_artifacts(result)

    assert len(items) == 1
    assert isinstance(items[0], EmbeddedResource)
    resource = items[0].resource
    assert isinstance(resource, BlobResourceContents)
    assert resource.blob == b64_data
    assert resource.mimeType == "application/pdf"
    assert "urn:a2a:file:" in str(resource.uri)


def test_convert_artifacts_file_with_uri():
    part = Part(
        root=FilePart(
            file=FileWithUri(uri="https://cdn.example.com/report.pdf", mimeType="application/pdf"), kind="file"
        )
    )
    result = A2ACallResult(text="", artifacts=[part])

    items = _convert_artifacts(result)

    assert len(items) == 1
    assert isinstance(items[0], EmbeddedResource)
    resource = items[0].resource
    assert isinstance(resource, TextResourceContents)
    assert "cdn.example.com" in resource.text
    assert resource.mimeType == "application/pdf"


def test_convert_artifacts_data_part():
    part = Part(root=DataPart(data={"key": "value", "count": 3}, kind="data"))
    result = A2ACallResult(text="", artifacts=[part])

    items = _convert_artifacts(result)

    assert len(items) == 1
    assert isinstance(items[0], TextContent)
    assert '"key"' in items[0].text
    assert '"value"' in items[0].text


def test_convert_artifacts_multiple_file_bytes_get_unique_uris():
    b64_data = "ZGF0YQ=="  # base64 of b"data"
    parts = [
        Part(root=FilePart(file=FileWithBytes(bytes=b64_data, mimeType="image/png"), kind="file")),
        Part(root=FilePart(file=FileWithBytes(bytes=b64_data, mimeType="image/png"), kind="file")),
    ]
    result = A2ACallResult(text="", artifacts=parts)

    items = _convert_artifacts(result)

    uris = [str(item.resource.uri) for item in items]
    assert uris[0] != uris[1], "each FileWithBytes artifact must get a unique URI"


def test_convert_artifacts_empty():
    result = A2ACallResult(text="hello", artifacts=[])
    assert _convert_artifacts(result) == []


# ── status filter and IAM guard ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_agent_inactive_agent_returns_error():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())

    with patch("registry.mcpgw.tools.agent_invoke.A2AAgent") as mock_model:
        mock_model.id = MagicMock()
        mock_model.status = MagicMock()
        mock_model.find_one = AsyncMock(return_value=None)

        result = await execute_agent_impl(valid_id, "hello", ctx)

    assert result.isError is True
    assert "no longer active" in result.content[0].text


@pytest.mark.asyncio
async def test_execute_agent_iam_unsupported_returns_error():
    ctx = _make_ctx()
    valid_id = str(PydanticObjectId())
    agent = _make_agent(valid_id)

    with (
        patch("registry.mcpgw.tools.agent_invoke.A2AAgent") as mock_model,
        patch(
            "registry.mcpgw.tools.agent_invoke.raise_if_iam_unsupported",
            side_effect=NotImplementedError("IAM-authenticated AgentCore A2A runtime is not supported"),
        ),
    ):
        mock_model.id = MagicMock()
        mock_model.status = MagicMock()
        mock_model.find_one = AsyncMock(return_value=agent)

        result = await execute_agent_impl(valid_id, "hello", ctx)

    assert result.isError is True
    assert "IAM" in result.content[0].text
