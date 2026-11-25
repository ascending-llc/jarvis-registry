from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from beanie import PydanticObjectId
from mcp.shared.exceptions import MCPError, UrlElicitationRequiredError
from mcp.types import (
    CallToolResult,
    ClientCapabilities,
    ElicitationCapability,
    ElicitRequestURLParams,
    ElicitResult,
    Implementation,
    InitializeRequestParams,
    InputRequiredResult,
    TextContent,
    UrlElicitationCapability,
)
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from registry.constants import UrlElicitationInputKey
from registry.core.config import settings
from registry.core.mcp_config import MCPTransport
from registry.core.mcp_downstream import DownstreamOperation, PromptGet, ResourceRead, ToolCall
from registry.core.telemetry_decorators import (
    PromptExecutionMetricsContext,
    ResourceAccessMetricsContext,
)
from registry.mcpgw.tools import server, utils
from registry.mcpgw.tools.server import execute_tool_impl
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.exceptions import (
    DownstreamAuthRejectedException,
    DownstreamHttpFailureException,
    DownstreamUnauthorizedException,
    InternalServerException,
    UrlElicitationRequiredException,
)
from registry_pkgs.models.enums import McpAuthMode
from registry_pkgs.telemetry.trace_propagation import (
    BAGGAGE_KEY_MCP_SERVER_ID,
    BAGGAGE_KEY_MCP_TOOL_NAME,
)


@pytest.fixture(
    params=[ToolCall("tavily_search", {}), ResourceRead("test://doc"), PromptGet("greet", {"name": "Ada"})],
    ids=["tool", "resource", "prompt"],
)
def operation(request: pytest.FixtureRequest) -> DownstreamOperation:
    return request.param


async def _run_operation(
    ctx: Any, operation: DownstreamOperation, server_id: str, *, render: Callable | None = None
) -> CallToolResult | InputRequiredResult:
    # Tools go through the public entry point, so these tests also guard execute_tool_impl's delegation.
    if isinstance(operation, ToolCall):
        return await execute_tool_impl(ctx, operation.tool_name, operation.arguments, server_id)
    if isinstance(operation, ResourceRead):
        metrics = ResourceAccessMetricsContext()
    else:
        metrics = PromptExecutionMetricsContext(prompt_name=operation.name)
    async with metrics:
        return await server._execute_server_operation(
            ctx,
            operation,
            server_id,
            metrics_ctx=metrics,
            render=render if render is not None else lambda result, srv: _OK,
        )


async def test_shared_authorization_logs_correlation_without_username(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, operation: DownstreamOperation
) -> None:
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    username = "sensitive-name@example.invalid"
    ctx.request_context.request.state.user["username"] = username
    _patch_server_service(monkeypatch, _make_server(server_id))
    caplog.set_level("INFO", logger="registry.mcpgw.tools.server")

    result = await server._authorize_server_call(ctx, operation, server_id, Mock())

    assert isinstance(result, tuple)
    assert username not in caplog.text
    assert ctx.request_context.request.state.user["user_id"] not in caplog.text
    assert f"server_id={server_id}" in caplog.text
    assert "request_id=req-1" in caplog.text
    if isinstance(operation, ToolCall):
        assert f"execute_tool: server call tool_name={operation.tool_name} server_id={server_id}" in caplog.text
    else:
        assert "tool_name=" not in caplog.text


class _PermissiveAccessibleSet:
    """Mock-friendly container that allows every `<id> in self` ACL check."""

    def __contains__(self, _item: object) -> bool:
        return True


def _make_ctx(
    *,
    user_id: str | None = "507f1f77bcf86cd799439011",
    accessible_server_ids: list[str] | None = None,
    include_user_context: bool = True,
    client_params: InitializeRequestParams | None = None,
    protocol_version: str | None = None,
    client_capabilities: ClientCapabilities | None = None,
    input_responses: dict | None = None,
    session_client_params: InitializeRequestParams | None = None,
    seed_client_params: bool = True,
):
    accessible: object = _PermissiveAccessibleSet() if accessible_server_ids is None else accessible_server_ids
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=accessible)

    server_service = MagicMock()
    server_service.get_server_by_id = AsyncMock()

    lifespan_context = SimpleNamespace(
        acl_service=acl_service,
        server_service=server_service,
        downstream_transport=MagicMock(),
        downstream_response_cache=MagicMock(),
        downstream_era_cache=MagicMock(),
        oauth_service=MagicMock(),
        redis_client=MagicMock(),
        client_info_store=MagicMock(),
        consent_store=MagicMock(),
        pending_consent_store=MagicMock(),
    )
    lifespan_context.consent_store.has_server_consent.return_value = True
    request_state = SimpleNamespace()
    if include_user_context:
        request_state.user = {"user_id": user_id, "username": "test", "client_id": "claude"}
    # Pre-seed the per-request memo so get_client_params() returns these params without a Redis GET,
    # unless a test wants to exercise the uncached source (seed_client_params=False).
    if seed_client_params:
        request_state.mcp_client_params = client_params
    request_context = SimpleNamespace(
        lifespan_context=lifespan_context,
        request=SimpleNamespace(state=request_state),
        request_id="req-1",
    )

    ctx = MagicMock()
    ctx.request_context = request_context
    ctx.request_id = "req-1"
    # On a MagicMock, an unset protocol_version is a mock that is never in MODERN_PROTOCOL_VERSIONS, so the
    # default ctx is legacy. Tests opt into the modern era by passing protocol_version="2026-07-28".
    if protocol_version is not None:
        ctx.protocol_version = protocol_version
    ctx.client_capabilities = client_capabilities
    ctx.input_responses = input_responses
    ctx.session.client_params = session_client_params
    return ctx


def _make_server(
    server_id: str | None = None,
    disabled_tools: list[str] | None = None,
    auth_mode: McpAuthMode = McpAuthMode.NONE,
):
    oid = PydanticObjectId(server_id) if server_id else PydanticObjectId()
    disabled = disabled_tools or []
    return SimpleNamespace(
        id=oid,
        mcp_auth_mode=auth_mode,
        path="/github",
        serverName="github",
        config={
            "enabled": True,
            "requiresInit": False,
            "type": "streamable-http",
            "url": "https://example.com/mcp",
        },
        registryDisabledTools=disabled,
        is_tool_disabled=lambda name: name in disabled,
    )


def _make_client_params(
    name: str, *, supports_url_elicitation: bool, protocol_version: str = "2025-11-25"
) -> InitializeRequestParams:
    elicitation = ElicitationCapability(url=UrlElicitationCapability()) if supports_url_elicitation else None
    return InitializeRequestParams(
        protocolVersion=protocol_version,
        capabilities=ClientCapabilities(elicitation=elicitation),
        clientInfo=Implementation(name=name, version="1.0"),
    )


def _assert_stale_server_id_hint(result: CallToolResult) -> None:
    assert result.is_error is True
    assert len(result.content) == 1
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert content.text == server._STALE_SERVER_ID_MESSAGE
    assert "stale or wrong" in content.text
    assert "discover_servers with the same query" in content.text
    for name in ("tool_name", "resource_uri", "prompt_name", "server_id"):
        assert name in content.text
    assert "Never reuse this server_id" in content.text


# Module where the metrics context managers look up the domain record functions.
DECORATORS_PATH = "registry.core.telemetry_decorators"


def _patch_server_service(monkeypatch, server_obj):
    """Stub _get_server_service so get_server_by_id returns the given server (or None)."""
    fake_service = SimpleNamespace(get_server_by_id=AsyncMock(return_value=server_obj))
    monkeypatch.setattr(server, "_get_server_service", lambda ctx: fake_service)
    # record_server_request hits the real metrics client; neutralize it for unit tests.
    monkeypatch.setattr(server, "record_server_request", lambda *a, **k: None)


def _patch_server_service_raises(monkeypatch, exc: Exception):
    """Stub _get_server_service so get_server_by_id raises before `server` is ever assigned."""
    fake_service = SimpleNamespace(get_server_by_id=AsyncMock(side_effect=exc))
    monkeypatch.setattr(server, "_get_server_service", lambda ctx: fake_service)
    monkeypatch.setattr(server, "record_server_request", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_execute_tool_wrapper_uses_tool_name_as_downstream_name(monkeypatch):
    execute_tool = dict(server.get_tools())["execute_tool"]
    execute_mock = AsyncMock(return_value=SimpleNamespace(result="ok"))
    monkeypatch.setattr(server, "execute_tool_impl", execute_mock)

    ctx = SimpleNamespace()
    arguments = {"query": "ai"}

    await execute_tool(
        ctx=ctx,
        tool_name="tavily_search",
        arguments=arguments,
        server_id="server-123",
    )

    execute_mock.assert_awaited_once_with(ctx, "tavily_search", arguments, "server-123")


@pytest.mark.unit
@pytest.mark.metrics
class TestExecutionMetricsWiring:
    """The metrics context managers are wired into the FastMCP execution impls."""

    def _tool_ctx(self):
        # execute_tool_impl reads ctx.request_context.request.state.user up front
        # and now validates user_id as an ObjectId, so use a valid one.
        state = SimpleNamespace(user={"username": "alice", "user_id": "507f1f77bcf86cd799439011"})
        request = SimpleNamespace(state=state)
        request_context = SimpleNamespace(request=request)
        return SimpleNamespace(request_context=request_context)

    @pytest.mark.asyncio
    async def test_execute_tool_impl_records_server_not_found(self, monkeypatch):
        _patch_server_service(monkeypatch, None)
        with patch(f"{DECORATORS_PATH}._record_tool_execution") as mock_record:
            result = await server.execute_tool_impl(
                ctx=self._tool_ctx(),
                tool_name="tavily_search",
                arguments={},
                server_id="missing",
            )

        assert result.is_error is True
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["tool_name"] == "tavily_search"
        assert call_kwargs["success"] is False
        assert call_kwargs["error_type"] == "server_not_found"

    @pytest.mark.asyncio
    async def test_read_resource_impl_records_success(self, monkeypatch):
        server_obj = SimpleNamespace(serverName="docs-server", path="/docs")
        _patch_server_service(monkeypatch, server_obj)
        with patch(f"{DECORATORS_PATH}._record_resource_access") as mock_record:
            await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="s1",
                resource_uri="tavily://search-results/AI",
                ctx=SimpleNamespace(),
            )

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["server_name"] == "docs-server"
        assert call_kwargs["success"] is True
        assert call_kwargs["error_type"] == "none"
        # Unbounded resource_uri must never become a metric label.
        assert "resource_uri" not in call_kwargs

    @pytest.mark.asyncio
    async def test_read_resource_impl_never_logs_the_resource_uri(self, monkeypatch, caplog):
        _patch_server_service(monkeypatch, SimpleNamespace(serverName="docs-server", path="/docs"))
        caplog.set_level("DEBUG", logger="registry.mcpgw.tools.server")
        with patch(f"{DECORATORS_PATH}._record_resource_access"):
            await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="s1",
                resource_uri="gdrive:///alice/salary.xlsx",
                ctx=SimpleNamespace(),
            )

        assert "salary" not in caplog.text

    @pytest.mark.asyncio
    async def test_read_resource_impl_records_server_not_found(self, monkeypatch):
        _patch_server_service(monkeypatch, None)
        with patch(f"{DECORATORS_PATH}._record_resource_access") as mock_record:
            result = await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="missing",
                resource_uri="tavily://x",
                ctx=SimpleNamespace(),
            )

        _assert_stale_server_id_hint(result)
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["success"] is False
        assert call_kwargs["error_type"] == "server_not_found"

    @pytest.mark.asyncio
    async def test_read_resource_impl_get_server_by_id_raises_returns_internal_error(self, monkeypatch):
        """Regression test for [m1]: `server` must be bound before get_server_by_id can raise,
        otherwise the except block's `if server is not None` crashes with UnboundLocalError."""
        _patch_server_service_raises(monkeypatch, RuntimeError("db unavailable"))
        with patch(f"{DECORATORS_PATH}._record_resource_access"), pytest.raises(InternalServerException):
            await server.read_resource_impl(
                user_context={"username": "alice"},
                server_id="s1",
                resource_uri="tavily://x",
                ctx=SimpleNamespace(),
            )

    @pytest.mark.asyncio
    async def test_execute_prompt_impl_records_success(self, monkeypatch):
        server_obj = SimpleNamespace(serverName="prompt-server", path="/prompts")
        _patch_server_service(monkeypatch, server_obj)
        with patch(f"{DECORATORS_PATH}._record_prompt_execution") as mock_record:
            await server.execute_prompt_impl(
                user_context={"username": "alice"},
                server_id="s1",
                prompt_name="research_assistant",
                arguments={"topic": "AI"},
                ctx=SimpleNamespace(),
            )

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["prompt_name"] == "research_assistant"
        assert call_kwargs["server_name"] == "prompt-server"
        assert call_kwargs["success"] is True
        assert call_kwargs["error_type"] == "none"

    @pytest.mark.asyncio
    async def test_execute_prompt_impl_records_server_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_server_service(monkeypatch, None)
        with patch(f"{DECORATORS_PATH}._record_prompt_execution") as mock_record:
            result = await server.execute_prompt_impl(
                user_context={"username": "alice"},
                server_id="missing",
                prompt_name="research_assistant",
                arguments=None,
                ctx=SimpleNamespace(),
            )

        _assert_stale_server_id_hint(result)
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args[1]
        assert call_kwargs["success"] is False
        assert call_kwargs["error_type"] == "server_not_found"

    @pytest.mark.asyncio
    async def test_execute_prompt_impl_get_server_by_id_raises_returns_internal_error(self, monkeypatch):
        """Regression test for [m1]: `server` must be bound before get_server_by_id can raise,
        otherwise the except block's `if server is not None` crashes with UnboundLocalError."""
        _patch_server_service_raises(monkeypatch, RuntimeError("db unavailable"))
        with patch(f"{DECORATORS_PATH}._record_prompt_execution"), pytest.raises(InternalServerException):
            await server.execute_prompt_impl(
                user_context={"username": "alice"},
                server_id="s1",
                prompt_name="research_assistant",
                arguments={"topic": "AI"},
                ctx=SimpleNamespace(),
            )


@pytest.mark.asyncio
async def test_execute_tool_impl_missing_user_context_returns_auth_error():
    ctx = _make_ctx(include_user_context=False)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, str(PydanticObjectId()))

    assert result.is_error is True
    assert "Authentication required" in result.content[0].text
    ctx.request_context.lifespan_context.server_service.get_server_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_invalid_user_id_returns_auth_error():
    ctx = _make_ctx(user_id="not-an-objectid")

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, str(PydanticObjectId()))

    assert result.is_error is True
    assert "Authentication required" in result.content[0].text
    ctx.request_context.lifespan_context.server_service.get_server_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_no_server_returns_error():
    ctx = _make_ctx()
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = None

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, str(PydanticObjectId()))

    _assert_stale_server_id_hint(result)


@pytest.mark.asyncio
async def test_execute_tool_impl_acl_denied_returns_error(monkeypatch):
    server_id = str(PydanticObjectId())
    other_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[other_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.request.state.user["client_id"] = settings.headless_agent_client_id
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error is True
    assert "Access denied" in result.content[0].text
    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_not_called()
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_acl_runtime_error_returns_retryable_error(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx()
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.lifespan_context.acl_service.get_accessible_resource_ids.side_effect = RuntimeError(
        "acl unavailable"
    )
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error is True
    assert "Service temporarily unavailable" in result.content[0].text
    downstream_call.assert_not_awaited()


def test_get_state_metadata_returns_unrecognized_for_missing_client_params():
    result = utils._get_state_metadata(None)

    assert result["client_branding"] == utils.ClientBranding.UNRECOGNIZED


@pytest.mark.parametrize(
    ("client_name", "expected_branding"),
    [
        ("Visual Studio Code", utils.ClientBranding.VSCODE),
        ("probe (via mcp-remote 1.0)", utils.ClientBranding.CURSOR),
        ("mcp-stdio-client (via mcp-remote 1.0)", utils.ClientBranding.CURSOR),
        ("claude-code", utils.ClientBranding.UNRECOGNIZED),
        ("claude-ai/1.0", utils.ClientBranding.UNRECOGNIZED),
        ("Claude", utils.ClientBranding.UNRECOGNIZED),
        ("some-other-client", utils.ClientBranding.UNRECOGNIZED),
    ],
)
def test_get_state_metadata_maps_client_name_to_branding(client_name, expected_branding):
    client_params = _make_client_params(client_name, supports_url_elicitation=False)

    result = utils._get_state_metadata(client_params)

    assert result["client_branding"] == expected_branding


def _legacy_ctx(client_params: InitializeRequestParams | None):
    """A 2025-11-25 ctx whose cached initialize params are `client_params` (legacy support path)."""
    return _make_ctx(protocol_version="2025-11-25", client_params=client_params)


def _modern_ctx(*, url_capability: bool, session_client_params: InitializeRequestParams | None = None):
    """A 2026-07-28 ctx that declares capabilities via `_meta` (client_capabilities), with no cached params."""
    elicitation = ElicitationCapability(url=UrlElicitationCapability()) if url_capability else None
    return _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=elicitation),
        client_params=None,
        session_client_params=session_client_params,
    )


@pytest.mark.parametrize("client_name", ["claude-code", "claude-ai/1.0", "Claude"])
@pytest.mark.parametrize("supports_url_elicitation", [True, False])
def test_support_url_elicitation_claude_clients_follow_declared_capability(client_name, supports_url_elicitation):
    """Claude Code CLI and Claude Desktop both identify as "claude-code", and only Claude Desktop hangs on
    a -32042 response, so no Claude client name gets an override: each follows what it declares."""
    ctx = _legacy_ctx(_make_client_params(client_name, supports_url_elicitation=supports_url_elicitation))

    assert utils._support_url_elicitation(ctx) is supports_url_elicitation


def test_support_url_elicitation_non_claude_client_follows_declared_capability():
    ctx = _legacy_ctx(_make_client_params("some-other-client", supports_url_elicitation=False))

    assert utils._support_url_elicitation(ctx) is False


def test_support_url_elicitation_returns_false_for_missing_client_params():
    assert utils._support_url_elicitation(_legacy_ctx(None)) is False


@pytest.mark.parametrize(
    ("protocol_version", "expected"),
    [
        ("2025-11-25", True),
        # Codex 0.166.0 declares `url` while negotiating 2025-06-18, which predates URL mode.
        ("2025-06-18", False),
        ("2025-03-26", False),
        ("2024-11-05", False),
        # An unsupported future version negotiates to LATEST_PROTOCOL_VERSION.
        ("2026-07-28", True),
    ],
)
def test_support_url_elicitation_requires_negotiated_version_defining_url_mode(protocol_version, expected):
    client_params = _make_client_params("some-client", supports_url_elicitation=True, protocol_version=protocol_version)

    assert utils._support_url_elicitation(_legacy_ctx(client_params)) is expected


def test_support_url_elicitation_modern_reads_capabilities_without_client_info():
    """On 2026-07-28 the capability arrives via `_meta` even when the client omits `clientInfo`, so support
    is decided by `ctx.client_capabilities` alone — `get_client_params` can be None."""
    assert utils._support_url_elicitation(_modern_ctx(url_capability=True)) is True


def test_support_url_elicitation_modern_false_when_url_capability_absent():
    assert utils._support_url_elicitation(_modern_ctx(url_capability=False)) is False


def test_support_url_elicitation_modern_false_when_no_capabilities():
    ctx = _make_ctx(protocol_version="2026-07-28", client_capabilities=None, client_params=None)
    assert utils._support_url_elicitation(ctx) is False


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("2025-06-18", "2025-06-18"),
        ("2025-11-25", "2025-11-25"),
        ("2026-07-28", utils.LATEST_HANDSHAKE_VERSION),
        (1, utils.LATEST_HANDSHAKE_VERSION),
    ],
)
def test_negotiated_protocol_version_mirrors_sdk_negotiation(requested, expected):
    assert utils._negotiated_protocol_version(requested) == expected


_ELICITATION_URL = "https://registry.example.com/consent/server?nonce=abc"
_ELICITATION_TEMPLATE = "Show the user this {} so they can open it."
_KEY = UrlElicitationInputKey.CONSENT


def test_build_url_elicitation_result_message_matches_fallback_text():
    capable_ctx = _make_ctx(client_params=_make_client_params("vscode", supports_url_elicitation=True))
    fallback_ctx = _make_ctx(client_params=_make_client_params("claude-ai", supports_url_elicitation=False))

    with pytest.raises(utils.UrlElicitationRequiredError) as exc_info:
        utils._build_url_elicitation_result(
            capable_ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY, elicitation_id="eid"
        )
    fallback = utils._build_url_elicitation_result(
        fallback_ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY, elicitation_id="eid"
    )

    assert str(exc_info.value) == fallback.content[0].text
    assert f"URL `{_ELICITATION_URL}`" in fallback.content[0].text


def test_build_url_elicitation_result_logs_why_url_mode_was_not_used(caplog):
    ctx = _make_ctx(
        client_params=_make_client_params(
            "codex-mcp-client", supports_url_elicitation=True, protocol_version="2025-06-18"
        )
    )

    with caplog.at_level("INFO", logger=utils.logger.name):
        utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY)

    message = caplog.records[-1].getMessage()
    assert "'codex-mcp-client'" in message
    assert "'1.0'" in message
    assert "'2025-06-18'" in message
    assert "declared url: True" in message
    assert "elicitation ID found: False" in message


def test_build_url_elicitation_result_logs_missing_client_params(caplog):
    ctx = _make_ctx(client_params=None)

    with caplog.at_level("INFO", logger=utils.logger.name):
        result = utils._build_url_elicitation_result(
            ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY, elicitation_id="eid"
        )

    assert result.is_error is True
    message = caplog.records[-1].getMessage()
    assert "no cached client params" in message
    assert "elicitation ID found: True" in message


def test_build_url_elicitation_result_modern_first_round_returns_input_required():
    ctx = _modern_ctx(url_capability=True)

    result = utils._build_url_elicitation_result(
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "please authorize", input_key=_KEY, elicitation_id="eid"
    )

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {_KEY}
    params = result.input_requests[_KEY].params
    assert params.mode == "url"
    assert params.url == _ELICITATION_URL
    assert params.message == "please authorize"
    # No request_state is sealed; the retry re-runs the checks (decision 1A). elicitation_id stays off the wire.
    assert result.request_state is None
    assert params.elicitation_id is None


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_build_url_elicitation_result_modern_retry_declined_returns_text(action):
    ctx = _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability())),
        input_responses={_KEY: ElicitResult(action=action)},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY)

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert "declined or cancelled" in result.content[0].text


def test_build_url_elicitation_result_modern_retry_accepted_returns_url_text_not_prompt():
    """A retry only reaches here because the check failed again. Even on `accept`, never prompt again —
    return the URL text so the user can finish in the browser and ask for a retry (decision 2A)."""
    ctx = _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability())),
        input_responses={_KEY: ElicitResult(action="accept")},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY)

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert f"URL `{_ELICITATION_URL}`" in result.content[0].text


def test_build_url_elicitation_result_modern_retry_unknown_key_reprompts():
    """A retry whose input_responses answer a different input request (not ours) is not our retry: fall
    through to the first-round branch and prompt, rather than mistaking it for a declined/accepted answer."""
    ctx = _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability())),
        input_responses={"some_other_key": ElicitResult(action="accept")},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "please authorize", input_key=_KEY, elicitation_id="eid"
    )

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {_KEY}


@pytest.mark.parametrize(
    ("answered", "failing"),
    [
        (UrlElicitationInputKey.CONSENT, UrlElicitationInputKey.AUTHORIZATION),
        (UrlElicitationInputKey.AUTHORIZATION, UrlElicitationInputKey.CONSENT),
    ],
)
def test_build_url_elicitation_result_modern_retry_prompts_for_next_check(answered, failing):
    """A retry that answered one check and now fails a different one (first use: consent, then downstream
    OAuth) prompts for the new check under its own key instead of falling back to text."""
    ctx = _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability())),
        input_responses={answered: ElicitResult(action="accept")},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "please authorize", input_key=failing
    )

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {failing}
    assert result.input_requests[failing].params.url == _ELICITATION_URL


@pytest.mark.parametrize("key", list(UrlElicitationInputKey))
def test_build_url_elicitation_result_modern_retry_same_check_returns_text_for_each_key(key):
    """Decision 2A holds per key: a check that fails again after its own prompt never prompts again."""
    ctx = _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability())),
        input_responses={key: ElicitResult(action="accept")},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=key)

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert f"URL `{_ELICITATION_URL}`" in result.content[0].text


def test_build_url_elicitation_result_legacy_capable_raises():
    ctx = _legacy_ctx(_make_client_params("vscode", supports_url_elicitation=True))

    with pytest.raises(utils.UrlElicitationRequiredError):
        utils._build_url_elicitation_result(
            ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY, elicitation_id="eid"
        )


@pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
def test_build_url_elicitation_result_incapable_returns_fallback(protocol_version):
    ctx = _make_ctx(protocol_version=protocol_version, client_capabilities=None, client_params=None)

    result = utils._build_url_elicitation_result(
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", input_key=_KEY, elicitation_id="eid"
    )

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert f"URL `{_ELICITATION_URL}`" in result.content[0].text


def test_get_client_params_modern_reads_session_without_redis():
    session_params = _make_client_params("modern-client", supports_url_elicitation=True, protocol_version="2026-07-28")
    ctx = _make_ctx(protocol_version="2026-07-28", session_client_params=session_params, seed_client_params=False)

    assert utils.get_client_params(ctx) is session_params
    ctx.request_context.lifespan_context.client_info_store.key_for.assert_not_called()
    ctx.request_context.lifespan_context.client_info_store.load.assert_not_called()


def test_get_client_params_legacy_reads_redis_store():
    cached = _make_client_params("legacy-client", supports_url_elicitation=True)
    ctx = _make_ctx(protocol_version="2025-11-25", seed_client_params=False)
    store = ctx.request_context.lifespan_context.client_info_store
    store.key_for.return_value = "cache-key"
    store.load.return_value = cached

    assert utils.get_client_params(ctx) is cached
    store.load.assert_called_once_with("cache-key")


@pytest.mark.parametrize("token", ["tok", "", 0, None, True, 1.5, {}])
def test_get_progress_token_validates_request_metadata(token: object) -> None:
    ctx = SimpleNamespace(request_context=SimpleNamespace(meta={"progress_token": token}))
    expected = token if type(token) in (str, int) else None
    assert utils.get_progress_token(ctx) == expected


@pytest.mark.parametrize("token", ["tok", "", 0, True, False, 0.0])
def test_get_progress_token_preserves_wire_type(token: str | int | float | bool) -> None:
    from mcp.types import RequestParams

    params = {"_meta": {"progressToken": token}}
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(meta=RequestParams.model_validate(params).meta, params=params)
    )
    assert utils.get_progress_token(ctx) == (token if type(token) in (str, int) else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["consent", "oauth"])
@pytest.mark.parametrize("action", [None, "accept", "decline"])
async def test_modern_authorization_metric_distinguishes_pending_and_terminal_results(
    monkeypatch: pytest.MonkeyPatch, mock_telemetry_metrics: Mock, reason: str, action: str | None
) -> None:
    from registry.utils.otel_metrics import record_tool_execution

    server_id = str(PydanticObjectId())
    ctx = _modern_ctx(url_capability=True)
    if action is not None:
        key = UrlElicitationInputKey.CONSENT if reason == "consent" else UrlElicitationInputKey.AUTHORIZATION
        ctx.input_responses = {key: ElicitResult(action=action)}
    lifespan = ctx.request_context.lifespan_context
    lifespan.server_service.get_server_by_id.return_value = _make_server(server_id)
    lifespan.consent_store.has_server_consent.return_value = reason != "consent"
    downstream = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream)
    monkeypatch.setattr(server, "record_server_request", lambda *args: None)
    if reason == "oauth":
        monkeypatch.setattr(
            server,
            "_call_with_oauth_recovery",
            AsyncMock(
                side_effect=UrlElicitationRequiredException(
                    "authorize", auth_url="https://example.com/authorize", server_name="github"
                )
            ),
        )
    monkeypatch.setattr(f"{DECORATORS_PATH}._record_tool_execution", record_tool_execution)

    result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    assert isinstance(result, InputRequiredResult) is (action is None)
    if action is not None:
        assert result.is_error is True
    attributes = mock_telemetry_metrics.record_counter.call_args.args[2]
    assert attributes["status"] == ("input_required" if action is None else "failure")
    assert attributes["error_type"] == (
        "authorization_declined"
        if action == "decline"
        else ("consent_required" if reason == "consent" else "url_elicitation")
    )
    downstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_without_server_consent_returns_elicitation_fallback(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.request.state.user["client_id"] = INTERACTIVE_CLIENT_ID
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error is True
    assert "explicit consent" in result.content[0].text
    assert "/consent/server?nonce=" in result.content[0].text
    ctx.request_context.lifespan_context.pending_consent_store.save.assert_called_once()
    _, pending = ctx.request_context.lifespan_context.pending_consent_store.save.call_args.args
    assert pending["user_id"] == "507f1f77bcf86cd799439011"
    assert pending["client_id"] == INTERACTIVE_CLIENT_ID
    assert pending["consent_flow"] == "server"
    assert pending["server_path"] == "/github"
    assert pending["elicitation_id"]
    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_called_once_with(
        "507f1f77bcf86cd799439011",
        INTERACTIVE_CLIENT_ID,
        "/github",
    )
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_without_server_consent_raises_url_elicitation(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.request.state.user["client_id"] = INTERACTIVE_CLIENT_ID
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream_call)
    monkeypatch.setattr(utils, "_support_url_elicitation", lambda _client_params: True)

    with pytest.raises(utils.UrlElicitationRequiredError) as exc_info:
        await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    ctx.request_context.lifespan_context.pending_consent_store.save.assert_called_once()
    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_called_once_with(
        "507f1f77bcf86cd799439011",
        INTERACTIVE_CLIENT_ID,
        "/github",
    )

    # The elicitation id raised to the client must be threaded into the pending-consent payload so
    # the consent callback can later recover the client branding for the deep-link back.
    raised_elicitation_id = exc_info.value.elicitations[0].elicitation_id
    assert raised_elicitation_id
    _, pending = ctx.request_context.lifespan_context.pending_consent_store.save.call_args.args
    assert pending["elicitation_id"] == raised_elicitation_id
    # Clients that stringify -32042 only show the model the message, so it must carry the URL itself.
    consent_url = exc_info.value.elicitations[0].url
    assert "/consent/server?nonce=" in consent_url
    assert f"URL `{consent_url}`" in str(exc_info.value)
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_acl_allowed_proceeds(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(
        server,
        "call_downstream",
        AsyncMock(return_value=CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})),
    )

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error


@pytest.mark.asyncio
async def test_execute_tool_impl_injects_trace_context_into_downstream_headers(monkeypatch):
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())

    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={"Authorization": "Bearer x"}))
    downstream_call = AsyncMock(
        return_value=CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})
    )
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error
    headers = downstream_call.await_args.kwargs["headers"]
    assert "traceparent" in headers
    assert f"{BAGGAGE_KEY_MCP_TOOL_NAME}=tavily_search" in headers["baggage"]
    assert f"{BAGGAGE_KEY_MCP_SERVER_ID}={server_id}" in headers["baggage"]


_MISSING = object()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("streamable-http", MCPTransport.STREAMABLE_HTTP),
        ("http", MCPTransport.STREAMABLE_HTTP),
        (None, MCPTransport.STREAMABLE_HTTP),
        (_MISSING, MCPTransport.STREAMABLE_HTTP),
        ("sse", MCPTransport.SSE),
    ],
)
async def test_execute_tool_impl_normalizes_stored_transport(monkeypatch, configured, expected):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    server_obj = _make_server(server_id)
    if configured is _MISSING:
        del server_obj.config["type"]
    else:
        server_obj.config["type"] = configured
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = server_obj
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    downstream_call = AsyncMock(
        return_value=CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})
    )
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error
    assert downstream_call.await_args.kwargs["transport_type"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", ["websocket", "stdio"])
async def test_execute_tool_impl_rejects_unsupported_transport_without_calling(monkeypatch, configured):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    server_obj = _make_server(server_id)
    server_obj.config["type"] = configured
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = server_obj
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error
    assert f"'{configured}' transport" in result.content[0].text
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_headless_agent_bypasses_server_consent(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.request.state.user["client_id"] = settings.headless_agent_client_id
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    downstream_call = AsyncMock(
        return_value=CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})
    )
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_not_called()
    downstream_call.assert_awaited_once()
    assert not result.is_error


@pytest.mark.asyncio
async def test_execute_tool_impl_downstream_tool_error_records_error_type(monkeypatch):
    """Regression test for [m3]: a downstream tool-level failure (isError=True) must be
    recorded with a non-default error_type, not silently left as "none"."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(
        server,
        "call_downstream",
        AsyncMock(
            return_value=CallToolResult.model_validate({"content": [{"type": "text", "text": "boom"}], "isError": True})
        ),
    )

    with patch(f"{DECORATORS_PATH}._record_tool_execution") as mock_record:
        result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error is True
    call_kwargs = mock_record.call_args[1]
    assert call_kwargs["success"] is False
    assert call_kwargs["error_type"] == "downstream_tool_error"


@pytest.mark.asyncio
async def test_execute_tool_impl_downstream_jsonrpc_error_raises_mcp_error(monkeypatch):
    """A downstream JSON-RPC error is re-raised as MCPError carrying its code, message and data, so the
    gateway's error-shape wrapper can turn it into the v1 isError text."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(
        server,
        "call_downstream",
        AsyncMock(side_effect=MCPError(-32602, "bad arguments", {"field": "q"})),
    )

    with pytest.raises(MCPError) as exc_info:
        await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert exc_info.value.code == -32602
    assert str(exc_info.value) == "bad arguments"
    assert exc_info.value.data == {"field": "q"}


@pytest.mark.asyncio
async def test_execute_tool_impl_disabled_tool_returns_error(monkeypatch):
    """A tool on the server's disabled list is rejected before any downstream call."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx()  # permissive ACL: user can view
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(
        server_id, disabled_tools=["tavily_search"]
    )
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error is True
    assert "tavily_search" in result.content[0].text
    assert server_id in result.content[0].text
    assert "Do not retry this tool" in result.content[0].text
    assert "another matching discovered candidate" in result.content[0].text
    assert "tell the user it is unavailable" in result.content[0].text
    # No consent check and no downstream network call for a disabled tool.
    ctx.request_context.lifespan_context.consent_store.has_server_consent.assert_not_called()
    downstream_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_enabled_tool_not_blocked(monkeypatch):
    """A tool absent from the disabled list is not blocked by the disabled-tool check."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(
        server_id, disabled_tools=["other_tool"]
    )
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    downstream_call = AsyncMock(
        return_value=CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})
    )
    monkeypatch.setattr(server, "call_downstream", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error
    downstream_call.assert_awaited()


# ----------------------------------------------------------------------
# Downstream 401 recovery (AS-1929)
# ----------------------------------------------------------------------

_OK = CallToolResult.model_validate({"content": [{"type": "text", "text": "ok"}]})


def _oauth_setup(monkeypatch, *, requires_init: bool = False):
    """An OAuth server whose header builder hands out the current token from `tokens`."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    srv = _make_server(server_id, auth_mode=McpAuthMode.OAUTH)
    srv.config["requiresInit"] = requires_init
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = srv
    mcp_client = None
    oauth_service = ctx.request_context.lifespan_context.oauth_service
    oauth_service.recover_from_unauthorized = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    tokens = ["t1"]

    async def build_headers(**kwargs):
        if not tokens:
            raise UrlElicitationRequiredException("login", auth_url="https://login", server_name="github")
        return {**kwargs["additional_headers"], "Authorization": f"Bearer {tokens[0]}"}

    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(side_effect=build_headers))
    return server_id, ctx, srv, mcp_client, oauth_service, tokens


def _unauthorized() -> DownstreamUnauthorizedException:
    return DownstreamUnauthorizedException("Error calling downstream MCP server.", www_authenticate="Bearer x")


@pytest.mark.asyncio
async def test_tool_401_then_refresh_retries_once_with_new_token(monkeypatch, operation):
    server_id, ctx, _, mcp_client, oauth_service, tokens = _oauth_setup(monkeypatch)

    async def refreshed(*args, **kwargs):
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized.side_effect = refreshed
    downstream = AsyncMock(side_effect=[_unauthorized(), _OK])
    monkeypatch.setattr(server, "call_downstream", downstream)

    result = await _run_operation(ctx, operation, server_id)

    assert not result.is_error
    assert downstream.await_count == 2
    assert downstream.await_args_list[1].kwargs["headers"]["Authorization"] == "Bearer t2"
    kwargs = oauth_service.recover_from_unauthorized.await_args.kwargs
    assert kwargs == {"rejected_access_token": "t1", "www_authenticate": "Bearer x", "is_retry": False}


@pytest.mark.asyncio
async def test_tool_401_then_failed_refresh_returns_url_elicitation(monkeypatch, operation):
    server_id, ctx, _, _, oauth_service, tokens = _oauth_setup(monkeypatch)

    async def failed(*args, **kwargs):
        tokens.clear()  # the rejected token was deleted; the rebuild starts a login
        return None

    oauth_service.recover_from_unauthorized.side_effect = failed
    downstream = AsyncMock(side_effect=[_unauthorized()])
    monkeypatch.setattr(server, "call_downstream", downstream)
    elicitation_result = CallToolResult(content=[TextContent(type="text", text="open the URL")], isError=True)
    elicitation = MagicMock(return_value=elicitation_result)
    monkeypatch.setattr(server, "_build_url_elicitation_result", elicitation)

    result = await _run_operation(ctx, operation, server_id)

    assert result is elicitation_result
    assert elicitation.call_args.args[1] == "https://login"
    assert downstream.await_count == 1


@pytest.mark.asyncio
async def test_tool_second_401_loop_guard_returns_error_result(monkeypatch, operation):
    server_id, ctx, _, _, oauth_service, tokens = _oauth_setup(monkeypatch)
    message = "github rejected a freshly issued access token; the server's OAuth configuration may be incompatible"

    async def recover(*args, is_retry, **kwargs):
        if is_retry:
            raise DownstreamAuthRejectedException(message)
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized.side_effect = recover
    downstream = AsyncMock(side_effect=[_unauthorized(), _unauthorized()])
    monkeypatch.setattr(server, "call_downstream", downstream)

    result = await _run_operation(ctx, operation, server_id)

    assert result.is_error
    assert result.content[0].text == message
    assert downstream.await_count == 2
    assert [c.kwargs["is_retry"] for c in oauth_service.recover_from_unauthorized.await_args_list] == [False, True]
    assert [c.kwargs["rejected_access_token"] for c in oauth_service.recover_from_unauthorized.await_args_list] == [
        "t1",
        "t2",
    ]


@pytest.mark.asyncio
async def test_tool_recovery_none_but_rebuild_has_token_retries_then_limits_sends(monkeypatch, operation):
    server_id, ctx, _, _, oauth_service, tokens = _oauth_setup(monkeypatch)

    async def other_pod_refreshed(*args, is_retry, **kwargs):
        tokens[0] = "t-other" if not is_retry else "t-login"
        return None

    oauth_service.recover_from_unauthorized.side_effect = other_pod_refreshed
    downstream = AsyncMock(side_effect=[_unauthorized(), _unauthorized(), _OK])
    monkeypatch.setattr(server, "call_downstream", downstream)

    result = await _run_operation(ctx, operation, server_id)

    assert result.is_error
    assert result.content[0].text == "Authorization for github changed during this call; retry it."
    assert downstream.await_count == 2  # no third send
    assert downstream.await_args_list[1].kwargs["headers"]["Authorization"] == "Bearer t-other"


@pytest.mark.asyncio
async def test_tool_401_for_non_oauth_server_is_not_recovered(monkeypatch, operation):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.lifespan_context.oauth_service.recover_from_unauthorized = AsyncMock()
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={"Authorization": "Bearer k"}))
    monkeypatch.setattr(server, "call_downstream", AsyncMock(side_effect=_unauthorized()))

    with pytest.raises(DownstreamHttpFailureException):
        await _run_operation(ctx, operation, server_id)

    ctx.request_context.lifespan_context.oauth_service.recover_from_unauthorized.assert_not_awaited()


def test_create_mcp_app_drops_otel_middleware_and_disables_subscriptions():
    """The gateway must not trust client trace context (no built-in OpenTelemetryMiddleware) and the
    static tool list needs no subscription bus. In v2 the stateless/json_response transport settings
    move onto streamable_http_app(...), applied by app_factory."""
    from mcp.server._otel import OpenTelemetryMiddleware

    from registry.mcpgw.server import create_mcp_app

    mcp = create_mcp_app(container_provider=lambda: None)

    assert not any(isinstance(m, OpenTelemetryMiddleware) for m in mcp.middleware)
    # Transport settings now live on streamable_http_app; build the stateless app to confirm it accepts them.
    app = mcp.streamable_http_app(stateless_http=True, json_response=False)
    assert app is not None


@pytest.mark.asyncio
async def test_mcp_lifespan_caches_context_and_reuses_proxy_client():
    """Stateless FastMCP enters the lifespan on every request, so it must yield one cached context and
    the container's single proxy client both times — never a fresh AsyncClient per request."""
    from registry.mcpgw.server import create_mcp_app

    proxy_client = object()
    container = SimpleNamespace(
        mcpgw_downstream_transport=proxy_client,
        mcpgw_downstream_response_cache=object(),
        downstream_era_cache=object(),
        a2a_client_registry=MagicMock(),
        server_service=MagicMock(),
        mcp_server_repo=MagicMock(),
        a2a_agent_repo=MagicMock(),
        search_service=MagicMock(),
        oauth_service=MagicMock(),
        client_info_store=MagicMock(),
        redis_client=MagicMock(),
        acl_service=MagicMock(),
        consent_store=MagicMock(),
        pending_consent_store=MagicMock(),
    )
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return container

    mcp = create_mcp_app(container_provider=provider)
    lifespan = mcp.settings.lifespan

    async with lifespan(mcp) as ctx1:
        async with lifespan(mcp) as ctx2:
            pass

    assert ctx1 is ctx2
    assert ctx1.downstream_transport is proxy_client
    assert ctx2.downstream_transport is proxy_client
    # The container is resolved only on the first entry; the context is cached thereafter.
    assert calls["n"] == 1


async def _call_wrapped_tool(raise_exc: BaseException) -> CallToolResult | MCPError:
    """Register one tool through the gateway's error-shape wrapper and call it over an in-memory Client.

    Returns the tool result, or the MCPError the client raised for a top-level JSON-RPC error (caught
    inside the Client context so it isn't wrapped in the transport's ExceptionGroup).
    """
    from mcp import Client
    from mcp.server.mcpserver import Context, MCPServer

    from registry.mcpgw.server import _wrap_tool_error_shape

    seen_ctx: list[object] = []

    async def failing_tool(ctx: Context, query: str) -> CallToolResult:
        seen_ctx.append(ctx)
        raise raise_exc

    app = MCPServer("test-error-shape", subscriptions=False)
    app.tool(name="failing_tool")(_wrap_tool_error_shape("failing_tool", failing_tool))

    async with Client(app, mode="legacy") as client:
        try:
            result = await client.call_tool("failing_tool", {"query": "x"})
        except MCPError as exc:
            result = exc
    # functools.wraps keeps the signature, so the SDK still injects Context.
    assert len(seen_ctx) == 1 and isinstance(seen_ctx[0], Context)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        MCPError(code=-32000, message="downstream exploded"),
        InternalServerException("downstream exploded"),
    ],
    ids=["MCPError", "McpGatewayException"],
)
async def test_wrapped_tool_error_keeps_v1_is_error_shape(exc):
    result = await _call_wrapped_tool(exc)

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert len(result.content) == 1
    assert result.content[0].text == "Error executing tool failing_tool: downstream exploded"


@pytest.mark.asyncio
async def test_wrapped_tool_url_elicitation_still_returns_32042():
    elicitation = ElicitRequestURLParams(
        mode="url", message="Sign in", url="https://example.com/auth", elicitation_id="eid-1"
    )
    result = await _call_wrapped_tool(UrlElicitationRequiredError([elicitation], message="Sign in required"))

    assert isinstance(result, MCPError)
    assert result.code == -32042
    assert result.data["elicitations"][0]["elicitationId"] == "eid-1"


@pytest.mark.parametrize("kind", ["url", "state_only", "form", "legacy_url", "malformed_legacy"])
async def test_downstream_interactive_input_is_terminal_text(monkeypatch, kind, operation):
    from mcp.shared.exceptions import MCPError
    from mcp.types import ElicitRequest, ElicitRequestFormParams, ElicitRequestURLParams

    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id], protocol_version="2026-07-28")
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    url = "https://downstream.example/login"
    if kind == "url":
        outcome = InputRequiredResult(
            input_requests={"login": ElicitRequest(params=ElicitRequestURLParams(url=url, message="Sign in"))}
        )
    elif kind == "form":
        outcome = InputRequiredResult(
            input_requests={
                "form": ElicitRequest(
                    params=ElicitRequestFormParams(
                        message="Fill form", requested_schema={"type": "object", "properties": {}}
                    )
                )
            }
        )
    elif kind == "state_only":
        outcome = InputRequiredResult(request_state="opaque-state")
    else:
        outcome = MCPError(
            -32042,
            "login required",
            {"elicitations": [{"mode": "url", "url": url, "message": "Sign in", "elicitationId": "id"}]}
            if kind == "legacy_url"
            else {"elicitations": 2},
        )
    downstream = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    monkeypatch.setattr(server, "call_downstream", downstream)
    record_name = (
        "_record_tool_execution"
        if isinstance(operation, ToolCall)
        else ("_record_resource_access" if isinstance(operation, ResourceRead) else "_record_prompt_execution")
    )
    with patch(f"{DECORATORS_PATH}.{record_name}") as record:
        result = await _run_operation(ctx, operation, server_id)
    assert isinstance(result, CallToolResult) and result.is_error
    text = result.content[0].text
    assert (url in text) == (kind in {"url", "legacy_url"})
    assert "opaque-state" not in text
    assert record.call_args.kwargs["error_type"] == "downstream_input_required"
    downstream.assert_awaited_once()


@pytest.mark.parametrize("version", ["2025-11-25", "2026-07-28", None])
@pytest.mark.parametrize("token", [None, 0, ""])
async def test_sdk_callbacks_remap_progress_and_filter_logging(monkeypatch, version, token):
    from mcp.types import LoggingMessageNotificationParams

    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id], protocol_version=version)
    ctx.request_context.meta = {"progress_token": token}
    ctx.report_progress = AsyncMock()
    ctx.session.send_notification = AsyncMock()
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))

    async def downstream(**kwargs):
        if token is None:
            assert kwargs["on_progress"] is None
        else:
            await kwargs["on_progress"](0.5, 1.0, "working")
        if version == "2025-11-25":
            await kwargs["on_log"](LoggingMessageNotificationParams(level="info", data="log"))
        else:
            assert kwargs["on_log"] is None
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    monkeypatch.setattr(server, "call_downstream", downstream)
    with patch(f"{DECORATORS_PATH}._record_tool_execution") as record:
        assert not (await execute_tool_impl(ctx, "search", {}, server_id)).is_error
    assert record.call_args.kwargs["success"] is True
    assert record.call_args.kwargs["error_type"] == "none"
    assert ctx.report_progress.await_count == int(token is not None)
    assert ctx.session.send_notification.await_count == int(version == "2025-11-25")
    if version == "2025-11-25":
        assert ctx.session.send_notification.await_args.kwargs["related_request_id"] == "req-1"


@pytest.mark.parametrize("failure", ["initialize", "prefetch", "tool", "post_execution_list"])
async def test_real_sdk_oauth_recovery_respects_execution_phase(monkeypatch, failure) -> None:
    import json

    import httpx2
    from mcp.client.caching import InMemoryResponseCacheStore
    from tests.mcp_downstream_support import MemoryRedis, sdk_endpoint

    from registry.core.mcp_downstream import DownstreamCallUnauthorized, DownstreamEraCache
    from registry.core.mcp_transport import DownstreamPhase

    server_id, ctx, srv, _, oauth, tokens = _oauth_setup(monkeypatch)
    redis = MemoryRedis()
    redis.values[f"test:mcp_era:{server_id}"] = "legacy"

    async def refresh(*args, **kwargs) -> None:
        tokens[0] = "t2"

    oauth.recover_from_unauthorized.side_effect = refresh
    async with sdk_endpoint(era="legacy") as transport:
        original = transport.handle_async_request
        list_count = 0
        rejected = False
        tool_sends = 0

        async def handle(request: httpx2.Request) -> httpx2.Response:
            nonlocal list_count, rejected, tool_sends
            body = json.loads(await request.aread()) if request.method == "POST" else {}
            method = body.get("method")
            if method == "tools/call":
                tool_sends += 1
            if method == "tools/list":
                list_count += 1
                if failure == "post_execution_list" and list_count == 1:
                    # A callable tool can disappear from the listing or be omitted by a stale listing.
                    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})
            target = {
                "initialize": "initialize",
                "prefetch": "tools/list",
                "tool": "tools/call",
                "post_execution_list": "tools/list",
            }[failure]
            reject = method == target and not rejected
            if failure == "post_execution_list":
                reject = reject and list_count == 2
            if reject:
                rejected = True
                return httpx2.Response(401, headers={"www-authenticate": "Bearer phase-test"})
            return await original(request)

        monkeypatch.setattr(transport, "handle_async_request", handle)
        lifespan = ctx.request_context.lifespan_context
        lifespan.downstream_transport = transport
        lifespan.downstream_response_cache = InMemoryResponseCacheStore()
        lifespan.downstream_era_cache = DownstreamEraCache(redis, "test")
        srv.config["url"] = "http://downstream/mcp"
        if failure == "post_execution_list":
            with pytest.raises(DownstreamCallUnauthorized) as error:
                await execute_tool_impl(ctx, "tavily_search", {}, server_id)
            assert error.value.phase == DownstreamPhase.RESULT_VALIDATION
            assert error.value.method == "tools/list"
            assert error.value.www_authenticate == "Bearer phase-test"
            assert not error.value.oauth_retry_safe
            oauth.recover_from_unauthorized.assert_not_awaited()
            assert tool_sends == 1
        else:
            result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)
            assert not result.is_error
            oauth.recover_from_unauthorized.assert_awaited_once()
            assert tool_sends == (2 if failure == "tool" else 1)
            assert oauth.recover_from_unauthorized.await_args.kwargs["www_authenticate"] == "Bearer phase-test"
        # Regardless of where 401 occurs, the real tool executes exactly once.
        assert transport.count("tools/call") == 1


@pytest.mark.parametrize("pagination", [False, True], ids=["first-page-stalled", "endless-unique-cursors"])
async def test_tool_list_deadline_stops_before_execution_without_oauth_recovery(monkeypatch, pagination) -> None:
    import asyncio
    import json

    import httpx2
    from mcp.client.caching import InMemoryResponseCacheStore
    from tests.mcp_downstream_support import MemoryRedis, sdk_endpoint

    from registry.core.mcp_config import MCPClientConfig
    from registry.core.mcp_downstream import DownstreamEraCache

    monkeypatch.setattr(MCPClientConfig, "TOOLS_TIMEOUT", 0.1)
    server_id, ctx, srv, _, oauth, _ = _oauth_setup(monkeypatch)
    redis = MemoryRedis()
    redis.values[f"test:mcp_era:{server_id}"] = "legacy"
    pages = []
    cancelled = asyncio.Event()
    async with sdk_endpoint(era="legacy") as transport:
        original = transport.handle_async_request

        async def handle(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(await request.aread()) if request.method == "POST" else {}
            if body.get("method") != "tools/list":
                return await original(request)
            pages.append(body.get("params", {}).get("cursor"))
            try:
                if not pagination:
                    await asyncio.Event().wait()
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [], "nextCursor": f"page-{len(pages)}"},
                },
            )

        monkeypatch.setattr(transport, "handle_async_request", handle)
        lifespan = ctx.request_context.lifespan_context
        lifespan.downstream_transport = transport
        lifespan.downstream_response_cache = InMemoryResponseCacheStore()
        lifespan.downstream_era_cache = DownstreamEraCache(redis, "test")
        srv.config["url"] = "http://downstream/mcp"
        async with asyncio.timeout(5):
            with pytest.raises(DownstreamHttpFailureException, match="Timed out fetching.*tool list"):
                await execute_tool_impl(ctx, "tavily_search", {}, server_id)
        if not pagination:
            # Only a stalled page is guaranteed to be in flight at the deadline; with endless pages the
            # deadline may land in the client's own work between pages.
            assert cancelled.is_set()
        assert pages.count(None) == 1  # No second attempt starting at page one.
        assert len(pages) > 1 if pagination else len(pages) == 1
        assert transport.count("tools/call") == 0
        assert transport.count("initialize") == 1
        assert transport.closed == 0
        oauth.recover_from_unauthorized.assert_not_awaited()


@pytest.mark.parametrize(
    "stop", ["identity", "server", "acl_denied", "acl_error", "declined", "disabled", "consent", "success", "exempt"]
)
async def test_shared_authorization_order_and_short_circuits(
    monkeypatch: pytest.MonkeyPatch, operation: DownstreamOperation, stop: str
) -> None:
    events = []
    server_id = str(PydanticObjectId())
    ctx = _modern_ctx(url_capability=True)
    lifespan = ctx.request_context.lifespan_context
    srv = _make_server(server_id)
    extract = server._extract_authenticated_user_context

    def identity(ctx: Any) -> dict[str, Any] | None:
        events.append("identity")
        user = extract(ctx)
        if stop == "exempt":
            user["client_id"] = settings.headless_agent_client_id
        return None if stop == "identity" else user

    async def lookup(value: str) -> SimpleNamespace | None:
        assert value == server_id
        events.append("server")
        return None if stop == "server" else srv

    async def acl(**kwargs: Any) -> list[str]:
        events.append("acl")
        assert str(kwargs["user_id"]) == "507f1f77bcf86cd799439011"
        if stop == "acl_error":
            raise RuntimeError("ACL unavailable")
        return [] if stop == "acl_denied" else [server_id]

    def declined(ctx: Any) -> CallToolResult | None:
        events.append("declined")
        return CallToolResult(content=[], is_error=True) if stop == "declined" else None

    def disabled(name: str) -> bool:
        assert name == "tavily_search"
        events.append("disabled")
        return stop == "disabled"

    def consent(*args: Any) -> bool:
        events.append("consent")
        return stop != "consent"

    monkeypatch.setattr(server, "_extract_authenticated_user_context", identity)
    lifespan.server_service.get_server_by_id.side_effect = lookup
    lifespan.acl_service.get_accessible_resource_ids.side_effect = acl
    monkeypatch.setattr(server, "authorization_declined_result", declined)
    srv.is_tool_disabled = Mock(side_effect=disabled)
    lifespan.consent_store.has_server_consent.side_effect = consent
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    downstream = AsyncMock(return_value=_OK)
    monkeypatch.setattr(server, "call_downstream", downstream)
    count = Mock()
    monkeypatch.setattr(server, "record_server_request", count)
    result = await _run_operation(ctx, operation, server_id)

    expected = ["identity", "server", "acl", "declined"]
    if isinstance(operation, ToolCall):
        expected.append("disabled")
    if stop != "exempt":
        expected.append("consent")
    terminal = {
        "identity": "identity",
        "server": "server",
        "acl_denied": "acl",
        "acl_error": "acl",
        "declined": "declined",
        "consent": "consent",
    }.get(stop)
    if stop == "disabled" and isinstance(operation, ToolCall):
        terminal = "disabled"
    if terminal:
        expected = expected[: expected.index(terminal) + 1]
    assert events == expected
    if terminal:
        downstream.assert_not_awaited()
        count.assert_not_called()
        if stop == "consent":
            assert isinstance(result, InputRequiredResult)
            lifespan.pending_consent_store.save.assert_called_once()
        else:
            assert result.is_error
            lifespan.pending_consent_store.save.assert_not_called()
    else:
        assert result is _OK
        downstream.assert_awaited_once()
        count.assert_called_once_with(srv.serverName)
        lifespan.pending_consent_store.save.assert_not_called()
    if not isinstance(operation, ToolCall):
        srv.is_tool_disabled.assert_not_called()


@pytest.mark.parametrize("failure", ["unsupported_transport", "auth_changed", "native", "rendered_error"])
async def test_native_rendering_and_gateway_errors(
    monkeypatch: pytest.MonkeyPatch,
    mock_telemetry_metrics: Mock,
    operation: DownstreamOperation,
    failure: str,
) -> None:
    from mcp.types import GetPromptResult, ReadResourceResult
    from opentelemetry import baggage

    from registry.utils.otel_metrics import record_prompt_execution, record_resource_access, record_tool_execution

    if isinstance(operation, ToolCall):
        native = _OK
        record_name, record_function, metric_name = (
            "_record_tool_execution",
            record_tool_execution,
            "mcp_tool_execution_total",
        )
    elif isinstance(operation, ResourceRead):
        native = ReadResourceResult(contents=[])
        record_name, record_function, metric_name = (
            "_record_resource_access",
            record_resource_access,
            "mcp_resource_access_total",
        )
    else:
        native = GetPromptResult(messages=[])
        record_name, record_function, metric_name = (
            "_record_prompt_execution",
            record_prompt_execution,
            "mcp_prompt_execution_total",
        )
    # Exercise the real domain recorder; only the external metrics client is mocked.
    monkeypatch.setattr(f"{DECORATORS_PATH}.{record_name}", record_function)
    server_id, ctx, srv, _, oauth, _tokens = _oauth_setup(monkeypatch)
    if failure == "unsupported_transport":
        srv.config["type"] = "stdio"
    oauth.recover_from_unauthorized.side_effect = None
    downstream = AsyncMock(
        side_effect=[_unauthorized(), _unauthorized()] if failure == "auth_changed" else None, return_value=native
    )
    monkeypatch.setattr(server, "call_downstream", downstream)
    rendered = CallToolResult(content=[TextContent(type="text", text="rendered")], is_error=failure == "rendered_error")
    renderer = Mock(return_value=rendered)
    result = await _run_operation(ctx, operation, server_id, render=renderer)
    if failure in ("unsupported_transport", "auth_changed"):
        assert result.is_error
        renderer.assert_not_called()
        assert downstream.await_count == (0 if failure == "unsupported_transport" else 2)
        if failure == "auth_changed":
            assert "changed during this call" in result.content[0].text
            assert oauth.recover_from_unauthorized.await_count == 2
    elif isinstance(operation, ToolCall):
        assert result is native
        renderer.assert_not_called()
    else:
        assert result is rendered
        renderer.assert_called_once_with(native, srv)

    counter_calls = [
        call for call in mock_telemetry_metrics.record_counter.call_args_list if call.args[0] == metric_name
    ]
    assert len(counter_calls) == 1
    attributes = counter_calls[0].args[2]
    expected_failure = failure in {"unsupported_transport", "auth_changed"} or (
        failure == "rendered_error" and not isinstance(operation, ToolCall)
    )
    assert attributes["status"] == ("failure" if expected_failure else "success")
    assert attributes["server_name"] == srv.serverName
    assert attributes["error_type"] == {
        "unsupported_transport": "unsupported_transport",
        "auth_changed": "downstream_auth_changed",
    }.get(failure, "none")
    if downstream.await_count:
        kwargs = downstream.await_args.kwargs
        assert kwargs["operation"] == operation
        assert kwargs["headers"]["Accept"] == "application/json, text/event-stream"
        headers = kwargs["headers"]
        assert ("X-Tool-Name" in headers) == isinstance(operation, ToolCall)
        if isinstance(operation, ToolCall):
            assert headers["X-Tool-Name"] == operation.tool_name
        # The wrapper injects the same bounded per-server baggage on every operation.
        assert f"{BAGGAGE_KEY_MCP_SERVER_ID}={server_id}" in headers["baggage"]
        assert (BAGGAGE_KEY_MCP_TOOL_NAME in headers["baggage"]) == isinstance(operation, ToolCall)
        assert baggage.get_baggage(BAGGAGE_KEY_MCP_SERVER_ID) is None


async def test_closed_phase_401_never_recovers_for_shared_operations(
    monkeypatch: pytest.MonkeyPatch, operation: DownstreamOperation
) -> None:
    from registry.core.mcp_downstream import DownstreamCallUnauthorized
    from registry.core.mcp_transport import DownstreamPhase, ResponseRecorder

    server_id, ctx, _srv, _, oauth, _ = _oauth_setup(monkeypatch)
    recorder = ResponseRecorder()
    recorder.phase = DownstreamPhase.CLOSING
    downstream = AsyncMock(side_effect=DownstreamCallUnauthorized(recorder))
    monkeypatch.setattr(server, "call_downstream", downstream)
    with pytest.raises(DownstreamUnauthorizedException):
        await _run_operation(ctx, operation, server_id)
    downstream.assert_awaited_once()
    oauth.recover_from_unauthorized.assert_not_awaited()


@pytest.mark.parametrize(
    "operation", [ResourceRead("test://doc"), PromptGet("greet", {"name": "Ada"})], ids=["resource", "prompt"]
)
async def test_real_sdk_non_tool_oauth_recovery(
    monkeypatch: pytest.MonkeyPatch, operation: DownstreamOperation
) -> None:
    from mcp.client.caching import InMemoryResponseCacheStore
    from tests.mcp_downstream_support import MemoryRedis, sdk_endpoint

    from registry.core.mcp_downstream import DownstreamEraCache

    server_id, ctx, srv, _, oauth, tokens = _oauth_setup(monkeypatch)
    redis = MemoryRedis()
    redis.values[f"test:mcp_era:{server_id}"] = "legacy"

    async def refresh(*args, **kwargs) -> None:
        tokens[0] = "t2"

    oauth.recover_from_unauthorized.side_effect = refresh
    method = "resources/read" if isinstance(operation, ResourceRead) else "prompts/get"
    async with sdk_endpoint(era="legacy") as transport:
        transport.failure_method = method
        transport.fail_once = True
        lifespan = ctx.request_context.lifespan_context
        lifespan.downstream_transport = transport
        lifespan.downstream_response_cache = InMemoryResponseCacheStore()
        lifespan.downstream_era_cache = DownstreamEraCache(redis, "test")
        srv.config["url"] = "http://downstream/mcp"
        renderer = Mock(return_value=_OK)
        assert await _run_operation(ctx, operation, server_id, render=renderer) is _OK
        assert transport.count(method) == 2
        assert transport.count("tools/list") == 0
        renderer.assert_called_once()
        oauth.recover_from_unauthorized.assert_awaited_once()
        assert oauth.recover_from_unauthorized.await_args.kwargs == {
            "rejected_access_token": "t1",
            "www_authenticate": "Bearer test-challenge",
            "is_retry": False,
        }
        business_headers = [
            headers
            for message, headers in zip(transport.messages, transport.headers, strict=True)
            if message["method"] == method
        ]
        assert [headers["authorization"] for headers in business_headers] == ["Bearer t1", "Bearer t2"]


@pytest.mark.parametrize("scenario", ["oauth_recovery", "mapped_error", "unmapped_error"])
async def test_resource_read_never_logs_the_uri(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, scenario: str
) -> None:
    """A ResourceRead through call_downstream and _call_with_oauth_recovery keeps its URI out of every log."""
    from mcp.client.caching import InMemoryResponseCacheStore
    from tests.mcp_downstream_support import MemoryRedis, sdk_endpoint

    from registry.core import mcp_downstream
    from registry.core.mcp_downstream import DownstreamEraCache

    secret = "salary-2026"
    operation = ResourceRead(f"test://doc/{secret}")
    server_id, ctx, srv, _, oauth, tokens = _oauth_setup(monkeypatch)

    async def refresh(*args, **kwargs) -> None:
        tokens[0] = "t2"

    oauth.recover_from_unauthorized.side_effect = refresh
    if scenario == "unmapped_error":
        # Left unmapped by _map_error, so it reaches the shared catch-all with the URI in its text.
        monkeypatch.setattr(
            mcp_downstream,
            "_send_operation",
            AsyncMock(side_effect=ValueError(f"invalid contents for {operation.uri}")),
        )
    # Registry loggers at DEBUG; third-party at INFO (the SDK's DEBUG logs dump raw JSON-RPC payloads).
    caplog.set_level("INFO")
    caplog.set_level("DEBUG", logger="registry")
    async with sdk_endpoint(era="legacy") as transport:
        if scenario == "oauth_recovery":
            transport.failure_method, transport.fail_once = "resources/read", True
        elif scenario == "mapped_error":
            transport.failure_method, transport.failure_status = "resources/read", 500
        lifespan = ctx.request_context.lifespan_context
        lifespan.downstream_transport = transport
        lifespan.downstream_response_cache = InMemoryResponseCacheStore()
        lifespan.downstream_era_cache = DownstreamEraCache(MemoryRedis(), "test")
        srv.config["url"] = "http://downstream/mcp"
        renderer = Mock(return_value=_OK)
        if scenario == "oauth_recovery":
            assert await _run_operation(ctx, operation, server_id, render=renderer) is _OK
            oauth.recover_from_unauthorized.assert_awaited_once()
            assert transport.count("resources/read") == 2
        else:
            expected = DownstreamHttpFailureException if scenario == "mapped_error" else InternalServerException
            with pytest.raises(expected):
                await _run_operation(ctx, operation, server_id, render=renderer)
            if scenario == "unmapped_error":
                assert "unexpected exception during read_resource_impl: ValueError" in caplog.text

    assert caplog.records, "expected the pipeline to log something"
    assert secret not in caplog.text
