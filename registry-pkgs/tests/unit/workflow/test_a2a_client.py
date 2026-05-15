from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
from a2a.client import ClientConfig
from a2a.types import (
    AgentCard,
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    TransportProtocol,
)
from beanie import PydanticObjectId

from registry_pkgs.core.config import JwtSigningConfig
from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.workflows.a2a_client import _extract_event, call_a2a


def _jwt_config() -> JwtSigningConfig:
    return JwtSigningConfig(
        jwt_private_key="fake-pem",
        jwt_issuer="https://jarvis.example.com",
        jwt_self_signed_kid="kid-v1",
        jwt_audience="jarvis-services",
    )


def _make_agent(url: str = "https://agent.example.com", transport: str = "jsonrpc") -> A2AAgent:
    return A2AAgent.model_construct(
        id=PydanticObjectId(),
        path="/test-agent",
        card=AgentCard.model_construct(
            name="Test Agent",
            url=url,
            version="1.0.0",
            protocol_version="0.3.0",
            capabilities={},
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            skills=[],
        ),
        config=AgentConfig(title="Test Agent", type=transport, url=url),
        federationMetadata={},
    )


def _msg_event(text: str) -> Message:
    """Direct Message event (non-task path)."""
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text=text))],
        message_id="msg-id",
    )


def _artifact_event(text: str) -> tuple[Task, TaskArtifactUpdateEvent]:
    """Task-mode artifact update event with a text part."""
    artifact = Artifact(
        artifact_id="art-1",
        name="result",
        parts=[Part(root=TextPart(kind="text", text=text))],
    )
    update = TaskArtifactUpdateEvent(
        kind="artifact-update",
        task_id="task-1",
        context_id="ctx-1",
        artifact=artifact,
    )
    task = Task(
        id="task-1",
        context_id="ctx-1",
        kind="task",
        status=TaskStatus(state=TaskState.working),
    )
    return (task, update)


def _status_event(state: TaskState = TaskState.completed) -> tuple[Task, TaskStatusUpdateEvent]:
    """Task-mode status update event (carries no text content)."""
    task = Task(
        id="task-1",
        context_id="ctx-1",
        kind="task",
        status=TaskStatus(state=state),
    )
    update = TaskStatusUpdateEvent(
        kind="status-update",
        task_id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=state),
        final=state in {TaskState.completed, TaskState.failed, TaskState.canceled},
    )
    return (task, update)


async def _async_iter(*items) -> AsyncIterator:
    for item in items:
        yield item


def _mock_client(events: list) -> tuple[MagicMock, MagicMock]:
    """Return (mock_factory_instance, mock_client) with send_message yielding events."""
    mock_client = MagicMock()
    mock_client.send_message = MagicMock(return_value=_async_iter(*events))
    mock_factory = MagicMock()
    mock_factory.create = MagicMock(return_value=mock_client)
    return mock_factory, mock_client


# ── _extract_event unit tests ────────────────────────────────────────────────


def test_extract_event_message_returns_text():
    event = _msg_event("hello")
    text, parts = _extract_event(event)
    assert text == "hello"
    assert parts == []


def test_extract_event_artifact_update_returns_text():
    event = _artifact_event("chunk")
    text, parts = _extract_event(event)
    assert text == "chunk"
    assert parts == []


def test_extract_event_status_update_returns_empty():
    event = _status_event(TaskState.completed)
    text, parts = _extract_event(event)
    assert text == ""
    assert parts == []


def test_extract_event_tuple_with_none_update_returns_empty():
    task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.working),
    )
    text, parts = _extract_event((task, None))
    assert text == ""
    assert parts == []


# ── call_a2a integration tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_a2a_message_events_accumulated():
    agent = _make_agent()
    mock_factory, _ = _mock_client(
        [
            _msg_event("Hello "),
            _msg_event("world"),
            _msg_event("!"),
        ]
    )

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "Say hello", jwt_config=_jwt_config())

    assert result.success is True
    assert result.text == "Hello world!"


@pytest.mark.asyncio
async def test_call_a2a_on_chunk_called_for_each_chunk():
    agent = _make_agent()
    chunks: list[str] = []

    async def on_chunk(c: str) -> None:
        chunks.append(c)

    mock_factory, _ = _mock_client(
        [
            _msg_event("part1"),
            _msg_event("part2"),
            _msg_event("part3"),
        ]
    )

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config(), on_chunk=on_chunk)

    assert result.text == "part1part2part3"
    assert chunks == ["part1", "part2", "part3"]


@pytest.mark.asyncio
async def test_call_a2a_artifact_update_events_accumulated():
    agent = _make_agent()
    mock_factory, _ = _mock_client(
        [
            _artifact_event("chunk-a"),
            _status_event(TaskState.working),  # status — no text
            _artifact_event("chunk-b"),
            _status_event(TaskState.completed),  # status — no text
        ]
    )

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert result.text == "chunk-achunk-b"


@pytest.mark.asyncio
async def test_call_a2a_without_on_chunk_does_not_raise():
    agent = _make_agent()
    mock_factory, _ = _mock_client([_msg_event("response")])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config(), on_chunk=None)

    assert result.success is True
    assert result.text == "response"


@pytest.mark.asyncio
async def test_call_a2a_empty_response_returns_failure():
    agent = _make_agent()
    mock_factory, _ = _mock_client([])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert "no content" in result.error


@pytest.mark.asyncio
async def test_call_a2a_exception_returns_failure():
    agent = _make_agent()

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch(
            "registry_pkgs.workflows.a2a_client.ClientFactory",
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert "connection refused" in result.error


@pytest.mark.asyncio
async def test_call_a2a_on_chunk_failure_does_not_abort_accumulation():
    """on_chunk exceptions must not stop text accumulation."""
    agent = _make_agent()
    call_count = 0

    async def failing_on_chunk(chunk: str) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("MCP session gone")

    mock_factory, _ = _mock_client([_msg_event("part1"), _msg_event("part2")])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config(), on_chunk=failing_on_chunk)

    assert result.success is True
    assert result.text == "part1part2"
    assert call_count == 2


@pytest.mark.asyncio
async def test_call_a2a_uses_http_json_protocol_for_rest_transport():
    """ClientConfig must receive TransportProtocol.http_json for http_json agents."""
    agent = _make_agent(transport="http_json")
    mock_factory, _ = _mock_client([_msg_event("rest response")])

    captured_configs: list[ClientConfig] = []

    def capturing_factory(config: ClientConfig) -> MagicMock:
        captured_configs.append(config)
        return mock_factory

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", side_effect=capturing_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.text == "rest response"
    assert len(captured_configs) == 1
    assert TransportProtocol.http_json in captured_configs[0].supported_transports


@pytest.mark.asyncio
async def test_call_a2a_uses_grpc_channel_factory_for_grpc_transport():
    """ClientConfig must have grpc_channel_factory set for grpc agents."""
    agent = _make_agent(transport="grpc")
    mock_factory, _ = _mock_client([_msg_event("grpc response")])

    captured_configs: list[ClientConfig] = []

    def capturing_factory(config: ClientConfig) -> MagicMock:
        captured_configs.append(config)
        return mock_factory

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", side_effect=capturing_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.text == "grpc response"
    assert len(captured_configs) == 1
    assert captured_configs[0].grpc_channel_factory is not None
    assert TransportProtocol.grpc in captured_configs[0].supported_transports
