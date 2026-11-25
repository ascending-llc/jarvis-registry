"""Execution intent and nonblocking consent storage shared by gateway entry points."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, ElicitResult, InputRequiredResult, TextContent
from redis.exceptions import RedisError
from tests.unit.mcpgw.test_agent import _make_agent, _make_ctx, _msg, _result_with_message
from tests.unit.mcpgw.test_server import _make_server, _modern_ctx

from registry.constants import UrlElicitationInputKey
from registry.mcpgw.tools import agent, server
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.exceptions import InternalServerException


def _entry(monkeypatch: pytest.MonkeyPatch, kind: str) -> SimpleNamespace:
    if kind != "agent":
        ctx = _modern_ctx(url_capability=True)
        target = _make_server()
        ctx.request_context.lifespan_context.server_service.get_server_by_id.return_value = target
        execute = AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="done")]))
        monkeypatch.setattr(server, "_call_with_oauth_recovery", execute)

        async def invoke() -> CallToolResult | InputRequiredResult:
            if kind == "resource":
                return await server.read_resource_impl(ctx, str(target.id), "test://doc")
            if kind == "prompt":
                return await server.get_prompt_impl(ctx, str(target.id), "greet", {"name": "Ada"})
            return await server.execute_tool_impl(ctx, "test", {}, str(target.id))

        store = ctx.request_context.lifespan_context.consent_store.has_server_consent
    else:
        ctx = _make_ctx(client_id=INTERACTIVE_CLIENT_ID)
        ctx.protocol_version = "2026-07-28"
        ctx.input_responses = None
        target = _make_agent()
        model = MagicMock()
        model.find_one = AsyncMock(return_value=target)
        monkeypatch.setattr(agent, "A2AAgent", model)
        execute = AsyncMock(return_value=_result_with_message("done"))
        monkeypatch.setattr(agent, "call_a2a", execute)

        async def invoke() -> CallToolResult | InputRequiredResult:
            return await agent.execute_agent_impl(str(target.id), _msg("test"), ctx)

        store = ctx.request_context.lifespan_context.consent_store.has_agent_consent
    return SimpleNamespace(
        ctx=ctx,
        invoke=invoke,
        execute=execute,
        store=store,
        pending=ctx.request_context.lifespan_context.pending_consent_store.save,
    )


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt", "agent"])
@pytest.mark.parametrize("consent", [False, True])
@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize("key", list(UrlElicitationInputKey))
async def test_cancel_stops_before_consent_and_execution(
    monkeypatch: pytest.MonkeyPatch, kind: str, consent: bool, action: str, key: UrlElicitationInputKey
) -> None:
    entry = _entry(monkeypatch, kind)
    entry.store.return_value = consent
    entry.ctx.input_responses = {key: ElicitResult(action=action)}
    result = await entry.invoke()
    assert isinstance(result, CallToolResult) and result.is_error
    assert isinstance(result.content[0], TextContent)
    assert "declined or cancelled" in result.content[0].text
    entry.store.assert_not_called()
    entry.pending.assert_not_called()
    entry.execute.assert_not_awaited()


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt", "agent"])
@pytest.mark.parametrize("key,action", [(UrlElicitationInputKey.CONSENT, "accept"), ("downstream", "decline")])
async def test_accept_or_other_input_rechecks_consent(
    monkeypatch: pytest.MonkeyPatch, kind: str, key: str, action: str
) -> None:
    entry = _entry(monkeypatch, kind)
    entry.store.return_value = True
    entry.ctx.input_responses = {key: ElicitResult(action=action)}
    result = await entry.invoke()
    assert not result.is_error
    entry.store.assert_called_once()
    entry.execute.assert_awaited_once()


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt", "agent"])
@pytest.mark.parametrize("operation", ["check", "save"])
async def test_consent_io_keeps_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch, kind: str, operation: str
) -> None:
    entry = _entry(monkeypatch, kind)
    entry.store.return_value = False
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocking(*args: object) -> bool:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "storage operation was not released"
        return False

    (entry.store if operation == "check" else entry.pending).side_effect = blocking
    task = asyncio.create_task(entry.invoke())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not task.done()
    finally:
        release.set()
        await task
    entry.execute.assert_not_awaited()


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt", "agent"])
@pytest.mark.parametrize("operation", ["check", "save"])
async def test_consent_storage_error_never_executes(monkeypatch: pytest.MonkeyPatch, kind: str, operation: str) -> None:
    entry = _entry(monkeypatch, kind)
    entry.store.return_value = False
    (entry.store if operation == "check" else entry.pending).side_effect = RedisError("storage unavailable")
    with pytest.raises(RedisError if kind == "agent" else InternalServerException):
        await entry.invoke()
    entry.execute.assert_not_awaited()


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt", "agent"])
async def test_cancel_still_checks_acl(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    entry = _entry(monkeypatch, kind)
    entry.ctx.request_context.lifespan_context.acl_service.get_accessible_resource_ids.return_value = set()
    entry.ctx.input_responses = {UrlElicitationInputKey.CONSENT: ElicitResult(action="cancel")}
    result = await entry.invoke()
    assert result.is_error
    assert "Access denied" in result.content[0].text
    entry.store.assert_not_called()
    entry.execute.assert_not_awaited()


@pytest.mark.parametrize("kind", ["tool", "resource", "prompt", "agent"])
async def test_legacy_does_not_interpret_modern_input_answers(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    entry = _entry(monkeypatch, kind)
    entry.ctx.protocol_version = "2025-11-25"
    entry.store.return_value = True
    entry.ctx.input_responses = {UrlElicitationInputKey.CONSENT: ElicitResult(action="decline")}
    result = await entry.invoke()
    assert not result.is_error
    entry.execute.assert_awaited_once()
