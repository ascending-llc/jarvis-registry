import hashlib
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from httpx import Response
from redis import Redis
from redis.exceptions import RedisError

from registry.services.skill_sync_oauth_service import SkillSyncOAuthService
from registry.utils.crypto_utils import encrypt_value
from registry_pkgs.oauth.flow_state_manager import FlowStateManager


def _source():
    return SimpleNamespace(
        id=PydanticObjectId(),
        githubAppClientId="client-id",
        githubAppClientSecretEncrypted=encrypt_value("client-secret"),
    )


@pytest.mark.asyncio
async def test_authorization_url_uses_pkce_and_persists_flow() -> None:
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.create_flow_metadata.return_value = SimpleNamespace(state="encoded-state", authorization_url="")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        redis_client=MagicMock(spec=Redis),
        redis_key_prefix="test",
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    url = await service.create_authorization_url(
        source=_source(),
        user_id="000000000000000000000001",
        redirect_uri="https://registry.example/callback",
    )

    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "state=encoded-state" in url
    flow_manager.create_flow.assert_called_once()
    assert "include_client_secret" not in flow_manager.create_flow_metadata.call_args.kwargs


@pytest.mark.asyncio
async def test_resolve_source_id_returns_flow_server_id() -> None:
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.get_flow.return_value = SimpleNamespace(server_id="source-123", state="state")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        redis_client=MagicMock(spec=Redis),
        redis_key_prefix="test",
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    assert await service.resolve_source_id("state") == "source-123"
    flow_manager.get_flow.assert_called_once_with("flow")


@pytest.mark.asyncio
async def test_resolve_source_id_raises_when_flow_missing() -> None:
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.get_flow.return_value = None
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        redis_client=MagicMock(spec=Redis),
        redis_key_prefix="test",
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    with pytest.raises(ValueError):
        await service.resolve_source_id("state")


@pytest.mark.asyncio
async def test_resolve_source_id_raises_when_state_does_not_match_flow() -> None:
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.get_flow.return_value = SimpleNamespace(server_id="source-123", state="the-real-state")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        redis_client=MagicMock(spec=Redis),
        redis_key_prefix="test",
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    with pytest.raises(ValueError):
        await service.resolve_source_id("a-forged-state")


@pytest.mark.asyncio
async def test_resolve_source_id_propagates_malformed_state() -> None:
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.decode_state.side_effect = ValueError("bad state")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        redis_client=MagicMock(spec=Redis),
        redis_key_prefix="test",
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    with pytest.raises(ValueError):
        await service.resolve_source_id("state")


@pytest.mark.asyncio
async def test_callback_consumes_state_and_stores_tokens() -> None:
    source = _source()
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.consume_flow.return_value = SimpleNamespace(
        server_id=str(source.id),
        user_id="000000000000000000000001",
        code_verifier="verifier",
    )
    response = Response(
        200,
        json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
        request=MagicMock(),
    )
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)
    token_service = MagicMock()
    token_service.store_tokens = AsyncMock()
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        redis_client=MagicMock(spec=Redis),
        redis_key_prefix="test",
        token_service=token_service,
        http_client=http_client,
    )

    user_id = await service.exchange_callback(
        source=source,
        code="code",
        state="state",
        redirect_uri="https://registry.example/callback",
    )

    assert user_id == "000000000000000000000001"
    token_service.store_tokens.assert_awaited_once()
    assert token_service.store_tokens.await_args.kwargs["tokens"].access_token == "access"


@pytest.fixture
def callback_context() -> SimpleNamespace:
    source = _source()
    flow_manager = MagicMock(spec=FlowStateManager)
    flow_manager.decode_state.side_effect = FlowStateManager.decode_state
    state = FlowStateManager.encode_state("flow", "security-token")
    flow_manager.consume_flow.return_value = SimpleNamespace(
        server_id=str(source.id), user_id="user-1", code_verifier="verifier"
    )
    redis_client = MagicMock(spec=Redis)
    redis_client.get.return_value = None
    token_service = MagicMock()
    token_service.store_tokens = AsyncMock()
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=Response(200, json={"access_token": "access"}, request=MagicMock()))
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=token_service,
        http_client=http_client,
        redis_client=redis_client,
        redis_key_prefix="test",
    )
    return SimpleNamespace(
        service=service,
        source=source,
        flow_manager=flow_manager,
        redis_client=redis_client,
        token_service=token_service,
        http_client=http_client,
        state=state,
        marker_key="test:skill_sync:oauth_completed:flow",
        marker_value=f"{hashlib.sha256(state.encode()).hexdigest()}:{source.id}",
    )


async def _exchange(ctx: SimpleNamespace) -> str:
    return await ctx.service.exchange_callback(
        source=ctx.source, code="code", state=ctx.state, redirect_uri="https://registry.example/callback"
    )


async def test_callback_records_completed_marker_after_storing_tokens(callback_context: SimpleNamespace) -> None:
    ctx = callback_context
    event_loop_thread = threading.get_ident()

    def check_write(*args: object, **kwargs: object) -> bool:
        ctx.token_service.store_tokens.assert_awaited_once()
        assert threading.get_ident() != event_loop_thread
        return True

    ctx.redis_client.set.side_effect = check_write

    assert await _exchange(ctx) == "user-1"

    ctx.flow_manager.consume_flow.assert_awaited_once_with("flow", ctx.state)
    ctx.redis_client.set.assert_called_once_with(ctx.marker_key, ctx.marker_value, ex=86400)
    assert ctx.state not in ctx.marker_value


@pytest.mark.parametrize("failure", ["missing_flow", "github_error", "missing_token", "token_storage"])
async def test_failed_exchange_does_not_record_completed_marker(
    callback_context: SimpleNamespace, failure: str
) -> None:
    ctx = callback_context
    if failure == "missing_flow":
        ctx.flow_manager.consume_flow.return_value = None
    elif failure == "token_storage":
        ctx.token_service.store_tokens.side_effect = RuntimeError("Storage unavailable")
    else:
        payload = {"error": "bad_verification_code"} if failure == "github_error" else {}
        ctx.http_client.post.return_value = Response(200, json=payload, request=MagicMock())

    with pytest.raises(RuntimeError if failure == "token_storage" else ValueError):
        await _exchange(ctx)

    ctx.redis_client.set.assert_not_called()
    if failure == "missing_flow":
        ctx.http_client.post.assert_not_awaited()
    if failure != "token_storage":
        ctx.token_service.store_tokens.assert_not_awaited()


async def test_marker_write_failure_preserves_success(
    callback_context: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = callback_context
    ctx.redis_client.set.side_effect = RedisError(f"sensitive: {ctx.state}")

    with caplog.at_level(logging.WARNING):
        assert await _exchange(ctx) == "user-1"

    ctx.token_service.store_tokens.assert_awaited_once()
    assert caplog.records
    assert ctx.state not in caplog.text
    assert "security-token" not in caplog.text


async def test_completed_flow_can_be_resolved_repeatedly_without_consuming_or_exchanging(
    callback_context: SimpleNamespace,
) -> None:
    ctx = callback_context
    event_loop_thread = threading.get_ident()

    def read_marker(key: str) -> str:
        assert key == ctx.marker_key
        assert threading.get_ident() != event_loop_thread
        return ctx.marker_value

    ctx.redis_client.get.side_effect = read_marker

    for _ in range(2):
        assert await ctx.service.resolve_completed_source_id(ctx.state) == str(ctx.source.id)

    ctx.flow_manager.consume_flow.assert_not_awaited()
    ctx.http_client.post.assert_not_awaited()
    ctx.token_service.store_tokens.assert_not_awaited()
    ctx.redis_client.set.assert_not_called()
    ctx.redis_client.delete.assert_not_called()


@pytest.mark.parametrize("marker", [None, "", "malformed", "wrong-hash:source-123"])
async def test_missing_or_invalid_completed_marker_returns_none(
    callback_context: SimpleNamespace, marker: str | None
) -> None:
    ctx = callback_context
    ctx.redis_client.get.return_value = marker

    assert await ctx.service.resolve_completed_source_id(ctx.state) is None


async def test_completed_marker_rejects_different_state_for_same_flow(callback_context: SimpleNamespace) -> None:
    ctx = callback_context
    ctx.redis_client.get.return_value = ctx.marker_value
    forged_state = FlowStateManager.encode_state("flow", "different-token")

    assert await ctx.service.resolve_completed_source_id(forged_state) is None
    ctx.redis_client.get.assert_called_once_with(ctx.marker_key)


async def test_completed_marker_rejects_malformed_state_without_reading_redis(
    callback_context: SimpleNamespace,
) -> None:
    ctx = callback_context

    assert await ctx.service.resolve_completed_source_id("garbage") is None
    ctx.redis_client.get.assert_not_called()


async def test_marker_read_failure_returns_none(
    callback_context: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = callback_context
    ctx.redis_client.get.side_effect = RedisError(f"sensitive: {ctx.state}")

    with caplog.at_level(logging.WARNING):
        assert await ctx.service.resolve_completed_source_id(ctx.state) is None

    assert caplog.records
    assert ctx.state not in caplog.text
    assert "security-token" not in caplog.text
