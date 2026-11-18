from types import SimpleNamespace
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

from registry.constants import URL_ELICITATION_INPUT_KEY
from registry.core.config import settings
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
        mcp_client_service=MagicMock(),
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
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

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
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

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


def test_build_url_elicitation_result_message_matches_fallback_text():
    capable_ctx = _make_ctx(client_params=_make_client_params("vscode", supports_url_elicitation=True))
    fallback_ctx = _make_ctx(client_params=_make_client_params("claude-ai", supports_url_elicitation=False))

    with pytest.raises(utils.UrlElicitationRequiredError) as exc_info:
        utils._build_url_elicitation_result(
            capable_ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", elicitation_id="eid"
        )
    fallback = utils._build_url_elicitation_result(
        fallback_ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", elicitation_id="eid"
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
        utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human")

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
            ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", elicitation_id="eid"
        )

    assert result.is_error is True
    message = caplog.records[-1].getMessage()
    assert "no cached client params" in message
    assert "elicitation ID found: True" in message


def test_build_url_elicitation_result_modern_first_round_returns_input_required():
    ctx = _modern_ctx(url_capability=True)

    result = utils._build_url_elicitation_result(
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "please authorize", elicitation_id="eid"
    )

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {URL_ELICITATION_INPUT_KEY}
    params = result.input_requests[URL_ELICITATION_INPUT_KEY].params
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
        input_responses={URL_ELICITATION_INPUT_KEY: ElicitResult(action=action)},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human")

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert "declined or cancelled" in result.content[0].text


def test_build_url_elicitation_result_modern_retry_accepted_returns_url_text_not_prompt():
    """A retry only reaches here because the check failed again. Even on `accept`, never prompt again —
    return the URL text so the user can finish in the browser and ask for a retry (decision 2A)."""
    ctx = _make_ctx(
        protocol_version="2026-07-28",
        client_capabilities=ClientCapabilities(elicitation=ElicitationCapability(url=UrlElicitationCapability())),
        input_responses={URL_ELICITATION_INPUT_KEY: ElicitResult(action="accept")},
        client_params=None,
    )

    result = utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human")

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
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "please authorize", elicitation_id="eid"
    )

    assert isinstance(result, InputRequiredResult)
    assert set(result.input_requests) == {URL_ELICITATION_INPUT_KEY}


def test_build_url_elicitation_result_legacy_capable_raises():
    ctx = _legacy_ctx(_make_client_params("vscode", supports_url_elicitation=True))

    with pytest.raises(utils.UrlElicitationRequiredError):
        utils._build_url_elicitation_result(ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", elicitation_id="eid")


@pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
def test_build_url_elicitation_result_incapable_returns_fallback(protocol_version):
    ctx = _make_ctx(protocol_version=protocol_version, client_capabilities=None, client_params=None)

    result = utils._build_url_elicitation_result(
        ctx, _ELICITATION_URL, _ELICITATION_TEMPLATE, "human", elicitation_id="eid"
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


_PROGRESS_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "notifications/progress",
    "params": {"progressToken": "tok", "progress": 0.5, "total": 1.0},
}
_LOGGING_NOTIFICATION = {
    "jsonrpc": "2.0",
    "method": "notifications/message",
    "params": {"level": "info", "data": "downstream log line"},
}
_TOOLS_LIST_CHANGED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
_CANCELLED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "req-1"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol_version", "notification", "forwarded"),
    [
        # Progress is forwarded on both eras.
        ("2026-07-28", _PROGRESS_NOTIFICATION, True),
        ("2025-11-25", _PROGRESS_NOTIFICATION, True),
        # Logging is forwarded only on 2025-11-25 (deprecated / opt-in on 2026-07-28).
        ("2025-11-25", _LOGGING_NOTIFICATION, True),
        ("2026-07-28", _LOGGING_NOTIFICATION, False),
        # Downstream list-change notifications are dropped on both eras.
        ("2025-11-25", _TOOLS_LIST_CHANGED_NOTIFICATION, False),
        ("2026-07-28", _TOOLS_LIST_CHANGED_NOTIFICATION, False),
        ("2025-11-25", _CANCELLED_NOTIFICATION, False),
        ("2026-07-28", _CANCELLED_NOTIFICATION, False),
        # An absent/unknown protocol version fails closed for logging but still forwards progress.
        (None, _LOGGING_NOTIFICATION, False),
        (None, _PROGRESS_NOTIFICATION, True),
    ],
)
async def test_forward_notification_filters_by_era(protocol_version, notification, forwarded):
    session = MagicMock()
    session.send_notification = AsyncMock()

    await utils.forward_notification(
        session, dict(notification), related_request_id="req-1", protocol_version=protocol_version, progress_token="tok"
    )

    assert session.send_notification.await_count == (1 if forwarded else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2025-11-25", "2026-07-28"])
@pytest.mark.parametrize(
    ("expected", "received", "forwarded"),
    [
        (None, "tok", False),
        ("tok", "wrong", False),
        (0, "0", False),
        (0, False, False),
        (0, 0.0, False),
        (0, 0, True),
        ("tok", "tok", True),
        ("", "", True),
    ],
)
async def test_forward_progress_requires_matching_token(
    version: str, expected: str | int | None, received: str | int | float | bool, forwarded: bool
) -> None:
    session = SimpleNamespace(send_notification=AsyncMock())
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progressToken": received, "progress": 1},
    }
    await utils.forward_notification(
        session, notification, related_request_id=1, protocol_version=version, progress_token=expected
    )
    assert session.send_notification.await_count == int(forwarded)
    if forwarded:
        sent, request_id = session.send_notification.await_args.args
        assert sent.params.progress_token == expected
        assert request_id == 1


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
        ctx.input_responses = {URL_ELICITATION_INPUT_KEY: ElicitResult(action=action)}
    lifespan = ctx.request_context.lifespan_context
    lifespan.server_service.get_server_by_id.return_value = _make_server(server_id)
    lifespan.consent_store.has_server_consent.return_value = reason != "consent"
    downstream = AsyncMock()
    monkeypatch.setattr(server, "_downstream_tool_call", downstream)
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
    assert attributes["error_type"] == ("consent_required" if reason == "consent" else "url_elicitation")
    downstream.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_impl_without_server_consent_returns_elicitation_fallback(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.request.state.user["client_id"] = INTERACTIVE_CLIENT_ID
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    downstream_call = AsyncMock()
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert result.is_error is True
    assert "explicit consent" in result.content[0].text
    assert "/consent/server?nonce=" in result.content[0].text
    ctx.request_context.lifespan_context.pending_consent_store.save.assert_called_once()
    _, pending = ctx.request_context.lifespan_context.pending_consent_store.save.call_args.args
    assert pending["user_id"] == "507f1f77bcf86cd799439011"
    assert pending["client_id"] == INTERACTIVE_CLIENT_ID
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
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)
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
        "_downstream_tool_call",
        AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "ok"}]}}),
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
    downstream_call = AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "ok"}]}})
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error
    headers = downstream_call.await_args.args[3]
    assert "traceparent" in headers
    assert f"{BAGGAGE_KEY_MCP_TOOL_NAME}=tavily_search" in headers["baggage"]
    assert f"{BAGGAGE_KEY_MCP_SERVER_ID}={server_id}" in headers["baggage"]


@pytest.mark.asyncio
async def test_execute_tool_impl_injects_trace_context_into_session_init(monkeypatch):
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())

    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    srv = _make_server(server_id)
    srv.config["requiresInit"] = True  # force the downstream session-init HTTP call
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = srv

    mcp_client = ctx.request_context.lifespan_context.mcp_client_service
    mcp_client.get_session.return_value = None  # no stored session -> initialize
    init_session = AsyncMock(return_value="sess-123")
    mcp_client.initialize_mcp_session = init_session

    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(
        server, "build_authenticated_headers", AsyncMock(side_effect=lambda **kw: dict(kw["additional_headers"]))
    )
    monkeypatch.setattr(
        server,
        "_downstream_tool_call",
        AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "ok"}]}}),
    )

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error
    # initialize_mcp_session(target_url, init_headers, session_key, transport_type)
    init_headers = init_session.await_args.args[1]
    assert "traceparent" in init_headers
    assert f"{BAGGAGE_KEY_MCP_TOOL_NAME}=tavily_search" in init_headers["baggage"]
    assert f"{BAGGAGE_KEY_MCP_SERVER_ID}={server_id}" in init_headers["baggage"]


@pytest.mark.asyncio
async def test_execute_tool_impl_headless_agent_bypasses_server_consent(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.request.state.user["client_id"] = settings.headless_agent_client_id
    ctx.request_context.lifespan_context.consent_store.has_server_consent.return_value = False
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={}))
    downstream_call = AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "ok"}]}})
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

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
        "_downstream_tool_call",
        AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "boom"}], "isError": True}}),
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
        "_downstream_tool_call",
        AsyncMock(return_value={"error": {"code": -32602, "message": "bad arguments", "data": {"field": "q"}}}),
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
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

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
    downstream_call = AsyncMock(return_value={"result": {"content": [{"type": "text", "text": "ok"}]}})
    monkeypatch.setattr(server, "_downstream_tool_call", downstream_call)

    result = await execute_tool_impl(ctx, "tavily_search", {"query": "ai"}, server_id)

    assert not result.is_error
    downstream_call.assert_awaited()


# ----------------------------------------------------------------------
# Downstream 401 recovery (AS-1929)
# ----------------------------------------------------------------------

_OK = {"result": {"content": [{"type": "text", "text": "ok"}]}}


def _oauth_setup(monkeypatch, *, requires_init: bool = False):
    """An OAuth server whose header builder hands out the current token from `tokens`."""
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    srv = _make_server(server_id, auth_mode=McpAuthMode.OAUTH)
    srv.config["requiresInit"] = requires_init
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = srv
    mcp_client = ctx.request_context.lifespan_context.mcp_client_service
    mcp_client.get_session.return_value = None
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
async def test_tool_401_then_refresh_retries_once_with_new_token(monkeypatch):
    server_id, ctx, _, mcp_client, oauth_service, tokens = _oauth_setup(monkeypatch)

    async def refreshed(*args, **kwargs):
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized.side_effect = refreshed
    downstream = AsyncMock(side_effect=[_unauthorized(), _OK])
    monkeypatch.setattr(server, "_downstream_tool_call", downstream)

    result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    assert not result.is_error
    assert downstream.await_count == 2
    assert downstream.await_args_list[1].args[3]["Authorization"] == "Bearer t2"
    kwargs = oauth_service.recover_from_unauthorized.await_args.kwargs
    assert kwargs == {"rejected_access_token": "t1", "www_authenticate": "Bearer x", "is_retry": False}
    mcp_client.clear_session.assert_called_once_with(f"507f1f77bcf86cd799439011:{server_id}")


@pytest.mark.asyncio
async def test_tool_401_then_failed_refresh_returns_url_elicitation(monkeypatch):
    server_id, ctx, _, _, oauth_service, tokens = _oauth_setup(monkeypatch)

    async def failed(*args, **kwargs):
        tokens.clear()  # the rejected token was deleted; the rebuild starts a login
        return None

    oauth_service.recover_from_unauthorized.side_effect = failed
    downstream = AsyncMock(side_effect=[_unauthorized()])
    monkeypatch.setattr(server, "_downstream_tool_call", downstream)
    elicitation_result = CallToolResult(content=[TextContent(type="text", text="open the URL")], isError=True)
    elicitation = MagicMock(return_value=elicitation_result)
    monkeypatch.setattr(server, "_build_url_elicitation_result", elicitation)

    result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    assert result is elicitation_result
    assert elicitation.call_args.args[1] == "https://login"
    assert downstream.await_count == 1


@pytest.mark.asyncio
async def test_tool_second_401_loop_guard_returns_error_result(monkeypatch):
    server_id, ctx, _, _, oauth_service, tokens = _oauth_setup(monkeypatch)
    message = "github rejected a freshly issued access token; the server's OAuth configuration may be incompatible"

    async def recover(*args, is_retry, **kwargs):
        if is_retry:
            raise DownstreamAuthRejectedException(message)
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized.side_effect = recover
    downstream = AsyncMock(side_effect=[_unauthorized(), _unauthorized()])
    monkeypatch.setattr(server, "_downstream_tool_call", downstream)

    result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    assert result.is_error
    assert result.content[0].text == message
    assert downstream.await_count == 2
    assert [c.kwargs["is_retry"] for c in oauth_service.recover_from_unauthorized.await_args_list] == [False, True]


@pytest.mark.asyncio
async def test_tool_recovery_none_but_rebuild_has_token_retries_then_limits_sends(monkeypatch):
    server_id, ctx, _, _, oauth_service, tokens = _oauth_setup(monkeypatch)

    async def other_pod_refreshed(*args, is_retry, **kwargs):
        tokens[0] = "t-other" if not is_retry else "t-login"
        return None

    oauth_service.recover_from_unauthorized.side_effect = other_pod_refreshed
    downstream = AsyncMock(side_effect=[_unauthorized(), _unauthorized(), _OK])
    monkeypatch.setattr(server, "_downstream_tool_call", downstream)

    result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    assert result.is_error
    assert result.content[0].text == "Authorization for github changed during this call; retry it."
    assert downstream.await_count == 2  # no third send
    assert downstream.await_args_list[1].args[3]["Authorization"] == "Bearer t-other"


@pytest.mark.asyncio
async def test_tool_401_during_session_init_reaches_recovery(monkeypatch):
    server_id, ctx, _, mcp_client, oauth_service, tokens = _oauth_setup(monkeypatch, requires_init=True)

    async def refreshed(*args, **kwargs):
        tokens[0] = "t2"
        return "t2"

    oauth_service.recover_from_unauthorized.side_effect = refreshed
    mcp_client.initialize_mcp_session = AsyncMock(side_effect=[_unauthorized(), "sess-2"])
    downstream = AsyncMock(return_value=_OK)
    monkeypatch.setattr(server, "_downstream_tool_call", downstream)

    result = await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    assert not result.is_error
    assert oauth_service.recover_from_unauthorized.await_args.kwargs["rejected_access_token"] == "t1"
    assert downstream.await_args.args[3]["mcp-Session-Id"] == "sess-2"
    assert downstream.await_args.args[3]["Authorization"] == "Bearer t2"


@pytest.mark.asyncio
async def test_tool_401_for_non_oauth_server_is_not_recovered(monkeypatch):
    server_id = str(PydanticObjectId())
    ctx = _make_ctx(accessible_server_ids=[server_id])
    ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = _make_server(server_id)
    ctx.request_context.lifespan_context.oauth_service.recover_from_unauthorized = AsyncMock()
    monkeypatch.setattr(server, "record_server_request", MagicMock())
    monkeypatch.setattr(server, "build_authenticated_headers", AsyncMock(return_value={"Authorization": "Bearer k"}))
    monkeypatch.setattr(server, "_downstream_tool_call", AsyncMock(side_effect=_unauthorized()))

    with pytest.raises(DownstreamHttpFailureException):
        await execute_tool_impl(ctx, "tavily_search", {}, server_id)

    ctx.request_context.lifespan_context.oauth_service.recover_from_unauthorized.assert_not_awaited()


@pytest.mark.asyncio
async def test_downstream_tool_call_raises_unauthorized_with_challenge():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://x"'})

    ctx = MagicMock()
    ctx.request_context.lifespan_context.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(DownstreamUnauthorizedException) as exc_info:
        await server._downstream_tool_call(ctx, "https://mcp.example.com/mcp", {}, {})

    assert exc_info.value.www_authenticate == 'Bearer resource_metadata="https://x"'


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
        mcpgw_proxy_client=proxy_client,
        a2a_client_registry=MagicMock(),
        server_service=MagicMock(),
        mcp_server_repo=MagicMock(),
        a2a_agent_repo=MagicMock(),
        search_service=MagicMock(),
        mcp_client_service=MagicMock(),
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
    assert ctx1.proxy_client is proxy_client
    assert ctx2.proxy_client is proxy_client
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
