"""Unit tests for the stateless gateway's Redis client-info cache."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import ClientCapabilities, Implementation, InitializeRequestParams
from redis.exceptions import RedisError

from registry.core.config import settings
from registry.mcpgw.core.client_info_store import (
    ClientInfoCaptureMiddleware,
    ClientInfoStore,
    _replay,
    client_info_key,
)
from registry.mcpgw.tools.utils import get_client_params

PREFIX = "jarvis-registry"
USER_ID = "507f1f77bcf86cd799439011"
JWT_CONFIG = settings.jwt_token_config


def _params(name: str = "claude-code") -> InitializeRequestParams:
    return InitializeRequestParams(
        protocolVersion="2025-11-25",
        capabilities=ClientCapabilities(),
        clientInfo=Implementation(name=name, version="1.0"),
    )


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls = 0
        self.raise_on_set = False
        self.raise_on_get = False

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.raise_on_set:
            raise RedisError("boom")
        self.store[key] = value

    def get(self, key: str) -> str | None:
        self.get_calls += 1
        if self.raise_on_get:
            raise RedisError("boom")
        return self.store.get(key)


def test_client_info_key_dcr_form_for_mcp_client_id():
    ctx = {"user_id": USER_ID, "client_id": "mcp-client-abc"}
    assert client_info_key(PREFIX, ctx, JWT_CONFIG) == f"{PREFIX}:mcpgw:client:{USER_ID}:mcp-client-abc"


def test_client_info_key_jti_form_for_shared_id_with_jti():
    ctx = {"user_id": USER_ID, "client_id": "user-generated", "jti": "jti-1"}
    assert client_info_key(PREFIX, ctx, JWT_CONFIG) == f"{PREFIX}:mcpgw:client:{USER_ID}:user-generated:jti-1"


def test_client_info_key_none_for_shared_id_without_jti():
    ctx = {"user_id": USER_ID, "client_id": "user-generated"}
    assert client_info_key(PREFIX, ctx, JWT_CONFIG) is None


def test_client_info_key_none_for_missing_user_id():
    ctx = {"user_id": None, "client_id": "mcp-client-abc"}
    assert client_info_key(PREFIX, ctx, JWT_CONFIG) is None


def test_client_info_key_none_for_empty_client_id():
    ctx = {"user_id": USER_ID, "client_id": ""}
    assert client_info_key(PREFIX, ctx, JWT_CONFIG) is None


# ------------------------------------------------------------------------- store


def test_store_save_then_load_round_trips():
    redis = _FakeRedis()
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    key = "k1"

    store.save(key, _params("vscode"))
    loaded = store.load(key)

    assert loaded is not None
    assert loaded.clientInfo.name == "vscode"
    # Only the three persisted fields are stored.
    assert set(json.loads(redis.store[key])) == {"clientInfo", "capabilities", "protocolVersion"}


def test_store_load_returns_none_on_miss():
    store = ClientInfoStore(_FakeRedis(), PREFIX, JWT_CONFIG)
    assert store.load("absent") is None


def test_store_load_returns_none_on_parse_error():
    redis = _FakeRedis()
    redis.store["bad"] = "{not valid json"
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    assert store.load("bad") is None


def test_store_load_returns_none_on_redis_error():
    redis = _FakeRedis()
    redis.raise_on_get = True
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    assert store.load("k1") is None


def _scope(method: str = "POST", user: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "http", "method": method, "state": {"user": user} if user is not None else {}}


async def _run_middleware(middleware: ClientInfoCaptureMiddleware, scope: dict, body: bytes) -> bytes:
    """Drive the ASGI middleware with one POST body; return the body the inner app received."""
    received: dict[str, bytes] = {}

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    async def inner_app(scope, receive, send) -> None:
        chunks = []
        more = True
        while more:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        received["body"] = b"".join(chunks)

    middleware._app = inner_app  # type: ignore[assignment]
    await middleware(scope, receive, lambda msg: None)
    return received["body"]


@pytest.mark.asyncio
async def test_middleware_saves_on_initialize_and_passes_body_through():
    redis = _FakeRedis()
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    middleware = ClientInfoCaptureMiddleware(None, lambda: store)  # type: ignore[arg-type]

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _params().model_dump(mode="json")}
    ).encode()
    scope = _scope(user={"user_id": USER_ID, "client_id": "mcp-client-abc"})

    passed_through = await _run_middleware(middleware, scope, body)

    assert passed_through == body
    assert redis.store[f"{PREFIX}:mcpgw:client:{USER_ID}:mcp-client-abc"]


@pytest.mark.asyncio
async def test_middleware_does_not_save_for_other_methods():
    redis = _FakeRedis()
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    middleware = ClientInfoCaptureMiddleware(None, lambda: store)  # type: ignore[arg-type]

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}).encode()
    scope = _scope(user={"user_id": USER_ID, "client_id": "mcp-client-abc"})

    passed_through = await _run_middleware(middleware, scope, body)

    assert passed_through == body
    assert redis.store == {}


@pytest.mark.asyncio
async def test_replay_delegates_to_original_receive_after_body():
    """After the buffered body, the wrapped receive MUST delegate to the original receive — never
    synthesize an http.disconnect. A synthetic disconnect would trip the SSE response's disconnect
    monitor and cancel the whole response (and its in-flight progress notifications) immediately."""
    calls = {"n": 0}

    async def original():
        calls["n"] += 1
        return {"type": "http.disconnect"}

    wrapped = _replay(b"hello", original)

    first = await wrapped()
    assert first == {"type": "http.request", "body": b"hello", "more_body": False}
    assert calls["n"] == 0  # body replayed without touching the original receive

    second = await wrapped()
    assert second == {"type": "http.disconnect"}
    assert calls["n"] == 1  # later calls delegate to the real receive, not a synthetic disconnect


@pytest.mark.asyncio
async def test_middleware_skips_capture_for_unauthenticated_request():
    redis = _FakeRedis()
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    middleware = ClientInfoCaptureMiddleware(None, lambda: store)  # type: ignore[arg-type]

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _params().model_dump(mode="json")}
    ).encode()
    scope = _scope(user=None)  # no authenticated user on the scope

    passed_through = await _run_middleware(middleware, scope, body)

    assert passed_through == body
    assert redis.store == {}


@pytest.mark.asyncio
async def test_middleware_does_not_dispatch_request_after_disconnect():
    redis = _FakeRedis()
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    middleware = ClientInfoCaptureMiddleware(None, lambda: store)  # type: ignore[arg-type]
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "synthetic", "arguments": {}},
        }
    ).encode()
    messages = [
        {"type": "http.request", "body": body, "more_body": True},
        {"type": "http.disconnect"},
    ]
    inner_called = False

    async def receive() -> dict:
        return messages.pop(0)

    async def inner_app(scope, receive, send) -> None:
        nonlocal inner_called
        inner_called = True

    middleware._app = inner_app  # type: ignore[assignment]
    await middleware(_scope(user={"user_id": USER_ID, "client_id": "mcp-client-abc"}), receive, lambda msg: None)

    assert inner_called is False
    assert redis.store == {}


@pytest.mark.asyncio
async def test_middleware_passes_malformed_body_through_without_raising():
    """A non-JSON body (that still trips the cheap 'initialize' pre-filter) must pass through
    untouched — a parse error here must never break the request, the gateway's top risk."""
    redis = _FakeRedis()
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    middleware = ClientInfoCaptureMiddleware(None, lambda: store)  # type: ignore[arg-type]

    body = b'{"method": "initialize" this is not valid json'
    scope = _scope(user={"user_id": USER_ID, "client_id": "mcp-client-abc"})

    passed_through = await _run_middleware(middleware, scope, body)

    assert passed_through == body
    assert redis.store == {}


@pytest.mark.asyncio
async def test_middleware_lets_request_through_when_redis_raises():
    redis = _FakeRedis()
    redis.raise_on_set = True
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    middleware = ClientInfoCaptureMiddleware(None, lambda: store)  # type: ignore[arg-type]

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _params().model_dump(mode="json")}
    ).encode()
    scope = _scope(user={"user_id": USER_ID, "client_id": "mcp-client-abc"})

    passed_through = await _run_middleware(middleware, scope, body)

    assert passed_through == body
    assert redis.store == {}


# ----------------------------------------------------------------- get_client_params


def _ctx(redis: _FakeRedis, user: dict[str, Any] | None):
    store = ClientInfoStore(redis, PREFIX, JWT_CONFIG)
    state = SimpleNamespace()
    if user is not None:
        state.user = user
    request = SimpleNamespace(state=state)
    lifespan_context = SimpleNamespace(client_info_store=store)
    request_context = SimpleNamespace(request=request, lifespan_context=lifespan_context)
    return SimpleNamespace(request_context=request_context)


def test_get_client_params_at_most_one_get_per_request():
    redis = _FakeRedis()
    ClientInfoStore(redis, PREFIX, JWT_CONFIG).save(
        f"{PREFIX}:mcpgw:client:{USER_ID}:mcp-client-abc", _params("vscode")
    )
    ctx = _ctx(redis, {"user_id": USER_ID, "client_id": "mcp-client-abc"})

    first = get_client_params(ctx)
    second = get_client_params(ctx)

    assert first is not None and first.clientInfo.name == "vscode"
    assert second is first
    assert redis.get_calls == 1


def test_get_client_params_returns_none_on_miss():
    redis = _FakeRedis()
    ctx = _ctx(redis, {"user_id": USER_ID, "client_id": "mcp-client-abc"})

    assert get_client_params(ctx) is None
    # A miss is memoized too: a second call does not hit Redis again.
    assert get_client_params(ctx) is None
    assert redis.get_calls == 1


def test_get_client_params_returns_none_and_memoizes_on_redis_error():
    redis = _FakeRedis()
    redis.raise_on_get = True
    ctx = _ctx(redis, {"user_id": USER_ID, "client_id": "mcp-client-abc"})

    assert get_client_params(ctx) is None
    # Degrades like a miss and is memoized, so the request's other readers don't retry the GET.
    assert get_client_params(ctx) is None
    assert redis.get_calls == 1
