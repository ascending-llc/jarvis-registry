from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.client import ClientConfig
from a2a.types import (
    AgentCapabilities,
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
from registry_pkgs.models.enums import FederationProviderType
from registry_pkgs.workflows.a2a_client import (
    A2ACallResult,
    _ensure_a2a_result_fields,
    build_headers,
    call_a2a,
)


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
        path="test-agent",  # path is now in slug format (no slashes)
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


def _msg(text: str) -> Message:
    return Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text=text))],
        message_id="msg-id",
    )


def _task(state: TaskState, artifacts: list[Artifact] | None = None, task_id: str = "task-1") -> Task:
    return Task(
        id=task_id,
        context_id="ctx-1",
        kind="task",
        status=TaskStatus(state=state),
        artifacts=artifacts,
    )


def _artifact(name: str, text_parts: list[str], *, artifact_id: str = "art-1") -> Artifact:
    parts: list[Part] = [Part(root=TextPart(kind="text", text=t)) for t in text_parts]
    return Artifact(artifact_id=artifact_id, name=name, parts=parts)


def test_build_headers_returns_empty_headers_for_non_agentcore_agent():
    agent = _make_agent()

    assert build_headers(agent, jwt_config=_jwt_config()) == {}


def test_build_headers_returns_agentcore_jwt_and_session_header():
    agent = _make_agent()
    agent.federationMetadata = {"providerType": FederationProviderType.AWS_AGENTCORE}

    with patch("registry_pkgs.workflows.a2a_client._make_agentcore_jwt", return_value="signed-agentcore-jwt"):
        headers = build_headers(agent, jwt_config=_jwt_config())

    assert headers["Authorization"] == "Bearer signed-agentcore-jwt"
    assert headers["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"]


def _artifact_event(
    text: str,
    *,
    artifact_id: str = "art-1",
    artifact_name: str = "result",
    task: Task | None = None,
) -> tuple[Task, TaskArtifactUpdateEvent]:
    """Streaming artifact-update event with one text chunk."""
    artifact = Artifact(
        artifact_id=artifact_id,
        name=artifact_name,
        parts=[Part(root=TextPart(kind="text", text=text))],
    )
    update = TaskArtifactUpdateEvent(
        kind="artifact-update",
        task_id="task-1",
        context_id="ctx-1",
        artifact=artifact,
    )
    task = task or _task(TaskState.working, artifacts=[artifact])
    return (task, update)


def _status_event(
    state: TaskState = TaskState.completed, *, task: Task | None = None
) -> tuple[Task, TaskStatusUpdateEvent]:
    task = task or _task(state)
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


def _mock_client(events: list, *, get_task_responses: list[Task] | None = None) -> tuple[MagicMock, MagicMock]:
    """Return (mock_factory_instance, mock_client) with send_message yielding events.

    If `get_task_responses` is provided, `client.get_task` returns them in order
    (final response repeats if more polls happen).
    """
    mock_client = MagicMock()
    mock_client.send_message = MagicMock(return_value=_async_iter(*events))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    if get_task_responses:
        responses = list(get_task_responses)

        async def fake_get_task(request, *, context=None):
            return responses.pop(0) if len(responses) > 1 else responses[0]

        mock_client.get_task = AsyncMock(side_effect=fake_get_task)
    mock_factory = MagicMock()
    mock_factory.create = MagicMock(return_value=mock_client)
    return mock_factory, mock_client


# ── A2ACallResult.render_text ────────────────────────────────────────────────


def test_render_text_message_uses_message_text():
    msg = Message(
        kind="message",
        role=Role.user,
        parts=[
            Part(root=TextPart(kind="text", text="line one")),
            Part(root=TextPart(kind="text", text="line two")),
        ],
        message_id="m",
    )
    r = A2ACallResult(message=msg, success=True)
    # Message parts join with \n (semantic units per spec).
    assert r.render_text() == "line one\nline two"


def test_render_text_task_labels_named_artifacts():
    task = _task(
        TaskState.completed,
        artifacts=[
            _artifact("Summary", ["short"]),
            _artifact("Detail", ["long"], artifact_id="art-2"),
        ],
    )
    r = A2ACallResult(task=task, success=True)
    assert r.render_text() == "[Summary]\nshort\n\n[Detail]\nlong"


def test_render_text_task_concats_streaming_chunks_no_delimiter():
    """Inside one artifact, TextParts are token-level fragments — concat without delimiter."""
    task = _task(TaskState.completed, artifacts=[_artifact("Summary", ["Hello ", "world", "!"])])
    r = A2ACallResult(task=task, success=True)
    assert r.render_text() == "[Summary]\nHello world!"


def test_render_text_task_uses_status_message_when_no_artifacts():
    status_msg = _msg("done via status")
    task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.completed, message=status_msg),
        artifacts=None,
    )
    r = A2ACallResult(task=task, success=True)
    assert r.render_text() == "done via status"


def test_render_text_task_includes_status_message_AND_artifacts():
    """Per spec, status.message and artifacts both carry content; render both."""
    status_msg = _msg("here is your report")
    task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.completed, message=status_msg),
        artifacts=[_artifact("Report", ["page1"])],
    )
    r = A2ACallResult(task=task, success=True)
    # status.message first, then artifacts (matches host_agent.py order).
    assert r.render_text() == "here is your report\n\n[Report]\npage1"


def test_render_text_no_message_no_task_returns_empty():
    r = A2ACallResult(success=False, error="oops")
    assert r.render_text() == ""


def test_task_state_property():
    r_none = A2ACallResult(success=True)
    assert r_none.task_state is None
    r_task = A2ACallResult(task=_task(TaskState.failed), success=False)
    assert r_task.task_state == TaskState.failed


# ── call_a2a: Message path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_a2a_message_reply_sets_message_field():
    agent = _make_agent()
    mock_factory, _ = _mock_client([_msg("Hello world!")])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "Say hello", jwt_config=_jwt_config())

    assert result.success is True
    assert result.task is None
    assert result.task_state is None
    assert result.message is not None
    assert result.render_text() == "Hello world!"


# ── call_a2a: streaming Task path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_a2a_streaming_multiple_artifacts_preserves_boundaries():
    """Multiple artifacts → each rendered as its own labelled block."""
    agent = _make_agent()
    a1 = _artifact("Summary", ["short summary"], artifact_id="art-1")
    a2 = _artifact("Detail", ["long detail"], artifact_id="art-2")
    final_task = _task(TaskState.completed, artifacts=[a1, a2])

    events = [
        _artifact_event(
            "short summary",
            artifact_id="art-1",
            artifact_name="Summary",
            task=_task(TaskState.working, artifacts=[a1]),
        ),
        _artifact_event(
            "long detail",
            artifact_id="art-2",
            artifact_name="Detail",
            task=_task(TaskState.working, artifacts=[a1, a2]),
        ),
        _status_event(TaskState.completed, task=final_task),
    ]
    mock_factory, _ = _mock_client(events)

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert [a.name for a in result.task.artifacts] == ["Summary", "Detail"]
    rendered = result.render_text()
    assert "[Summary]\nshort summary" in rendered
    assert "[Detail]\nlong detail" in rendered


# ── call_a2a: non-streaming Task path (Bug 1 regression) ─────────────────────


@pytest.mark.asyncio
async def test_call_a2a_non_streaming_task_completion_returns_artifacts():
    """Bug 1: non-streaming path yields a single (Task, None) event with full
    artifacts on the Task. Previously dropped silently."""
    agent = _make_agent()
    completed_task = _task(TaskState.completed, artifacts=[_artifact("Result", ["the answer is 42"])])
    mock_factory, _ = _mock_client([(completed_task, None)])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert result.task_state == TaskState.completed
    assert result.task.artifacts[0].name == "Result"
    assert result.render_text() == "[Result]\nthe answer is 42"


# ── call_a2a: polling fallback (Bug 2) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_call_a2a_polls_when_server_returns_submitted():
    """Bug 2: server returns (Task{state=submitted}, None) ignoring blocking=True.
    call_a2a must poll get_task until terminal."""
    agent = _make_agent()
    submitted = _task(TaskState.submitted, artifacts=None)
    working = _task(TaskState.working, artifacts=None)
    completed = _task(TaskState.completed, artifacts=[_artifact("Done", ["finally"])])

    mock_factory, mock_client = _mock_client(
        [(submitted, None)],
        get_task_responses=[working, completed],
    )

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
        patch("registry_pkgs.workflows.a2a_client.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert result.task_state == TaskState.completed
    assert result.render_text() == "[Done]\nfinally"
    assert mock_client.get_task.await_count == 2


@pytest.mark.asyncio
async def test_call_a2a_polling_timeout_returns_failure():
    """Polling deadline exceeded → success=False with timeout error."""
    agent = _make_agent()
    submitted = _task(TaskState.submitted, artifacts=None)
    still_working = _task(TaskState.working, artifacts=None)

    mock_factory, _ = _mock_client(
        [(submitted, None)],
        get_task_responses=[still_working],
    )

    # Patch time.monotonic to jump past deadline on the second call.
    monotonic_values = iter([0.0, 1000.0, 1000.0, 1000.0])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
        patch("registry_pkgs.workflows.a2a_client.asyncio.sleep", new_callable=AsyncMock),
        patch("registry_pkgs.workflows.a2a_client.time.monotonic", side_effect=lambda: next(monotonic_values)),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert "polling timed out" in result.error


# ── call_a2a: error/empty paths ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_a2a_empty_stream_returns_failure():
    agent = _make_agent()
    mock_factory, _ = _mock_client([])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert "no" in result.error.lower()


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
async def test_call_a2a_input_required_returns_distinct_error():
    """Interrupted state: error message must say 'awaiting input', not generic
    'non-completed state'."""
    agent = _make_agent()
    paused = _task(TaskState.input_required, artifacts=[_artifact("Q", ["what color?"])])
    mock_factory, _ = _mock_client([(paused, None)])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert result.task_state == TaskState.input_required
    assert "awaiting additional user input" in result.error
    assert "start a new conversation" in result.error
    assert result.task is not None


@pytest.mark.asyncio
async def test_call_a2a_auth_required_returns_distinct_error():
    agent = _make_agent()
    paused = _task(TaskState.auth_required, artifacts=[_artifact("Auth", ["login required"])])
    mock_factory, _ = _mock_client([(paused, None)])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert result.task_state == TaskState.auth_required
    assert "awaiting authentication" in result.error


@pytest.mark.asyncio
async def test_call_a2a_status_message_alone_is_valid_content():
    """Task completed with only status.message (no artifacts) → success=True."""
    agent = _make_agent()
    status_msg = _msg("All done, no files needed.")
    task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.completed, message=status_msg),
        artifacts=None,
    )
    mock_factory, _ = _mock_client([(task, None)])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert result.render_text() == "All done, no files needed."


@pytest.mark.asyncio
async def test_call_a2a_non_completed_terminal_state_returns_failure_with_task():
    """Task ends in `failed` with artifacts: task still surfaced; success=False."""
    agent = _make_agent()
    failed_task = _task(TaskState.failed, artifacts=[_artifact("Error", ["agent crashed"])])
    mock_factory, _ = _mock_client([(failed_task, None)])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert result.task_state == TaskState.failed
    assert "non-completed" in result.error
    # We still surface the server response so callers can inspect.
    assert result.task is not None
    assert result.task.artifacts[0].name == "Error"


@pytest.mark.asyncio
async def test_call_a2a_failed_surfaces_status_message_detail():
    """Failed Task with a status.message → reason text surfaces in error; success=False."""
    agent = _make_agent()
    reason = "agent overloaded (HTTP 503), retryable, retry in a few minutes"
    failed_task = Task(
        id="t",
        context_id="c",
        kind="task",
        status=TaskStatus(state=TaskState.failed, message=_msg(reason)),
        artifacts=None,
    )
    mock_factory, _ = _mock_client([(failed_task, None)])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is False
    assert result.task_state == TaskState.failed
    assert "non-completed" in result.error
    assert reason in result.error


@pytest.mark.asyncio
async def test_call_a2a_uses_http_json_protocol_for_rest_transport():
    """ClientConfig must receive TransportProtocol.http_json for http_json agents."""
    agent = _make_agent(transport="http_json")
    mock_factory, _ = _mock_client([_msg("rest response")])

    captured_configs: list[ClientConfig] = []

    def capturing_factory(config: ClientConfig) -> MagicMock:
        captured_configs.append(config)
        return mock_factory

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", side_effect=capturing_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert result.render_text() == "rest response"
    assert len(captured_configs) == 1
    supported = captured_configs[0].supported_transports
    assert supported[0] == TransportProtocol.http_json
    assert TransportProtocol.jsonrpc in supported
    assert captured_configs[0].use_client_preference is True


@pytest.mark.asyncio
async def test_call_a2a_uses_jsonrpc_protocol_for_jsonrpc_transport():
    """Standard JSONRPC agent (config.type='jsonrpc', no card.preferred_transport):
    jsonrpc must be first in supported_transports with use_client_preference=True."""
    agent = _make_agent(transport="jsonrpc")
    mock_factory, _ = _mock_client([_msg("jsonrpc response")])

    captured_configs: list[ClientConfig] = []

    def capturing_factory(config: ClientConfig) -> MagicMock:
        captured_configs.append(config)
        return mock_factory

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", side_effect=capturing_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert result.render_text() == "jsonrpc response"
    assert len(captured_configs) == 1
    supported = captured_configs[0].supported_transports
    assert supported[0] == TransportProtocol.jsonrpc
    assert TransportProtocol.http_json in supported
    assert captured_configs[0].use_client_preference is True


@pytest.mark.asyncio
async def test_call_a2a_negotiates_transport_when_card_disagrees_with_config():
    """config.type='jsonrpc' but the agent card prefers HTTP+JSON (e.g. AgentCore runtime):
    supported_transports must list both, jsonrpc first, with use_client_preference=True so the
    SDK can fall back to whatever the card actually advertises instead of raising
    'no compatible transports found'."""
    agent = _make_agent(transport="jsonrpc")
    agent.card.preferred_transport = TransportProtocol.http_json
    mock_factory, _ = _mock_client([_msg("agentcore response")])

    captured_configs: list[ClientConfig] = []

    def capturing_factory(config: ClientConfig) -> MagicMock:
        captured_configs.append(config)
        return mock_factory

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", side_effect=capturing_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
    assert len(captured_configs) == 1
    supported = captured_configs[0].supported_transports
    assert supported[0] == TransportProtocol.jsonrpc
    assert TransportProtocol.http_json in supported
    assert captured_configs[0].use_client_preference is True


@pytest.mark.asyncio
async def test_call_a2a_forwards_shared_httpx_client_to_client_config():
    """When the caller supplies an httpx_client, it must be put on ClientConfig
    so the SDK reuses the same connection pool instead of creating a fresh one."""
    import httpx as _httpx

    agent = _make_agent()
    mock_factory, _ = _mock_client([_msg("ok")])

    captured_configs: list[ClientConfig] = []

    def capturing_factory(config: ClientConfig) -> MagicMock:
        captured_configs.append(config)
        return mock_factory

    shared = _httpx.AsyncClient()
    try:
        with (
            patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
            patch("registry_pkgs.workflows.a2a_client.ClientFactory", side_effect=capturing_factory),
        ):
            result = await call_a2a(agent, "test", jwt_config=_jwt_config(), httpx_client=shared)
    finally:
        await shared.aclose()

    assert result.success is True
    assert len(captured_configs) == 1
    assert captured_configs[0].httpx_client is shared


@pytest.mark.asyncio
async def test_call_a2a_does_not_close_shared_httpx_client():
    """When httpx_client is supplied, call_a2a must skip `async with client`
    so the shared pool stays alive across multiple calls. We assert by
    checking that `mock_client.__aexit__` was NOT awaited."""
    import httpx as _httpx

    agent = _make_agent()
    mock_factory, mock_client = _mock_client([_msg("ok")])

    shared = _httpx.AsyncClient()
    try:
        with (
            patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
            patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
        ):
            await call_a2a(agent, "test", jwt_config=_jwt_config(), httpx_client=shared)
    finally:
        await shared.aclose()

    mock_client.__aenter__.assert_not_called()
    mock_client.__aexit__.assert_not_called()


@pytest.mark.asyncio
async def test_call_a2a_uses_async_with_when_no_shared_httpx_client():
    """Default per-call mode: call_a2a must enter the BaseClient context manager
    so the SDK-created httpx pool is closed on exit."""
    agent = _make_agent()
    mock_factory, mock_client = _mock_client([_msg("ok")])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        await call_a2a(agent, "test", jwt_config=_jwt_config())  # no httpx_client

    mock_client.__aenter__.assert_awaited_once()
    mock_client.__aexit__.assert_awaited_once()


# ── Provider detection + headers_provider hook ────────────────────────────────


def test_is_azure_foundry_runtime_matches_provider_type_value():
    from registry_pkgs.workflows.a2a_client import is_azure_foundry_runtime

    agent = _make_agent()
    agent.federationMetadata = {"providerType": "azure_ai_foundry"}
    assert is_azure_foundry_runtime(agent) is True


def test_is_azure_foundry_runtime_false_for_unknown_provider():
    from registry_pkgs.workflows.a2a_client import is_azure_foundry_runtime

    agent = _make_agent()
    agent.federationMetadata = {"providerType": "aws_agentcore"}
    assert is_azure_foundry_runtime(agent) is False

    agent.federationMetadata = {}
    assert is_azure_foundry_runtime(agent) is False


@pytest.mark.asyncio
async def test_call_a2a_uses_headers_provider_when_supplied():
    """When headers_provider is supplied, it must be awaited and its result used —
    the default build_headers JWT path must NOT be touched."""
    agent = _make_agent()
    mock_factory, _ = _mock_client([_msg("ok")])

    provider_calls: list[A2AAgent] = []

    async def provider(target_agent: A2AAgent) -> dict[str, str]:
        provider_calls.append(target_agent)
        return {"Authorization": "Bearer entra-token"}

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers") as build_headers_spy,
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        await call_a2a(
            agent,
            "test",
            jwt_config=_jwt_config(),
            headers_provider=provider,
        )

    build_headers_spy.assert_not_called()
    assert provider_calls == [agent]


@pytest.mark.asyncio
async def test_call_a2a_falls_back_to_build_headers_when_no_provider():
    """Without headers_provider, the default sync build_headers must still run.
    This keeps the AWS AgentCore + plain-JWT paths unchanged."""
    agent = _make_agent()
    mock_factory, _ = _mock_client([_msg("ok")])

    with (
        patch(
            "registry_pkgs.workflows.a2a_client.build_headers",
            return_value={"Authorization": "Bearer self-signed"},
        ) as build_headers_spy,
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        await call_a2a(agent, "test", jwt_config=_jwt_config())

    build_headers_spy.assert_called_once()


@pytest.mark.asyncio
async def test_call_a2a_accepts_pre_parsed_message():
    """When call_a2a receives a pre-built Message, it must pass it directly to
    _consume_stream without re-wrapping via _create_message."""
    agent = _make_agent()
    pre_parsed = Message(
        kind="message",
        role=Role.user,
        parts=[Part(root=TextPart(kind="text", text="pre-parsed"))],
        message_id="pre-id",
    )
    mock_factory, _ = _mock_client([_msg("ok")])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
        patch("registry_pkgs.workflows.a2a_client._create_message") as mock_create,
    ):
        result = await call_a2a(agent, pre_parsed, jwt_config=_jwt_config())

    assert result.success is True
    mock_create.assert_not_called()


def _azure_foundry_agent() -> A2AAgent:
    agent = _make_agent(transport="jsonrpc")
    agent.federationMetadata = {"providerType": FederationProviderType.AZURE_AI_FOUNDRY}
    agent.card = AgentCard.model_construct(
        name="Azure Test Agent",
        url="https://agent.example.com",
        version="1.0.0",
        protocol_version="0.3.0",
        capabilities=AgentCapabilities(streaming=False),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=[],
    )
    return agent


def test_ensure_a2a_result_fields_adds_missing_artifact_id():
    result = {
        "kind": "task",
        "id": "task-1",
        "context_id": "ctx-1",
        "status": {"state": "completed"},
        "artifacts": [
            {"parts": [{"kind": "text", "text": "first"}]},
            {"parts": [{"kind": "text", "text": "second"}]},
        ],
    }
    _ensure_a2a_result_fields(result)

    assert result["artifacts"][0]["artifact_id"]
    assert result["artifacts"][1]["artifact_id"]
    assert result["artifacts"][0]["artifact_id"] != result["artifacts"][1]["artifact_id"]


def test_ensure_a2a_result_fields_is_idempotent():
    result = {
        "kind": "task",
        "artifacts": [{"artifact_id": "existing-id", "parts": [{"kind": "text", "text": "keep"}]}],
    }
    _ensure_a2a_result_fields(result)

    assert result["artifacts"][0]["artifact_id"] == "existing-id"


def test_ensure_a2a_result_fields_respects_camelcase_artifact_id():
    """A spec-compliant response using the camelCase wire alias must not be touched."""
    result = {
        "kind": "task",
        "artifacts": [{"artifactId": "wire-id", "parts": [{"kind": "text", "text": "keep"}]}],
    }
    _ensure_a2a_result_fields(result)

    assert result["artifacts"][0]["artifactId"] == "wire-id"
    assert "artifact_id" not in result["artifacts"][0]


def test_ensure_a2a_result_fields_leaves_message_result_untouched():
    result = {
        "kind": "message",
        "message_id": "msg-1",
        "role": "user",
        "parts": [{"kind": "text", "text": "hello"}],
    }
    _ensure_a2a_result_fields(result)

    assert "artifact_id" not in result


@pytest.mark.asyncio
async def test_call_a2a_tolerates_azure_foundry_missing_artifact_id():
    """Azure Foundry returns Task artifacts without artifact_id; the call must succeed."""
    agent = _azure_foundry_agent()

    raw_response = {
        "jsonrpc": "2.0",
        "id": "rpc-1",
        "result": {
            "kind": "task",
            "id": "task-1",
            "context_id": "ctx-1",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": "hello from azure"}]}],
        },
    }

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch(
            "a2a.client.transports.jsonrpc.JsonRpcTransport._send_request",
            new_callable=AsyncMock,
            return_value=raw_response,
        ) as send_request_spy,
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    send_request_spy.assert_awaited_once()
    assert result.success is True
    assert result.task is not None
    assert result.task.artifacts[0].artifact_id
    assert result.render_text() == "hello from azure"


@pytest.mark.asyncio
async def test_call_a2a_uses_standard_transport_for_non_azure_jsonrpc_agent():
    """Non-Azure JSON-RPC agents must keep the default strict transport; the base
    JsonRpcTransport._send_request should be used without our tolerant subclass."""
    agent = _make_agent(transport="jsonrpc")
    mock_factory, _ = _mock_client([_msg("ok")])

    with (
        patch("registry_pkgs.workflows.a2a_client.build_headers", return_value={}),
        patch("registry_pkgs.workflows.a2a_client.ClientFactory", return_value=mock_factory),
    ):
        result = await call_a2a(agent, "test", jwt_config=_jwt_config())

    assert result.success is True
