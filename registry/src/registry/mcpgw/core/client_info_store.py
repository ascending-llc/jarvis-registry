"""Redis-backed cache of MCP client ``initialize`` params, plus the ASGI capture middleware.

In stateless mode the gateway no longer keeps ``ctx.session.client_params`` in process, but URL-mode
elicitation support and deep-link client branding still need it on every request, possibly served by a
different pod. We cache the client's ``initialize`` params in Redis, keyed per (user, client) — with a
``jti`` discriminator for shared client ids — and read them back through :func:`get_client_params`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from mcp.types import InitializeRequestParams
from redis import Redis
from redis.exceptions import RedisError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from registry_pkgs.core.client_categories import ClientCategory, resolve_client_category
from registry_pkgs.core.config import JwtTokenConfig
from registry_pkgs.types import UserContextDict

logger = logging.getLogger(__name__)

# 30 days. Refreshed on every `initialize`, so an actively-used client's entry never expires out from
# under it; a client that stops connecting is reaped a month later.
_CLIENT_INFO_TTL_SECONDS = 2592000

# Only these fields drive URL-elicitation support and branding; store nothing else from `initialize`.
_PERSISTED_FIELDS = {"clientInfo", "capabilities", "protocolVersion"}


def client_info_key(prefix: str, user_context: UserContextDict, jwt_config: JwtTokenConfig) -> str | None:
    """Build the Redis key for a user's client-info, or ``None`` when it must not be cached.

    - No key without a ``user_id`` or with an empty ``client_id`` (treated as unauthenticated, matching
      ``_extract_authenticated_user_context``).
    - DCR client ids map one-to-one to an app, so they key on ``{user_id}:{client_id}`` alone.
    - Shared client ids (``user-generated``, ``jarvis-headless-agent``, ``jarvis-registry-cli``,
      ``REGISTRY_APP``) are presented by several apps, so they additionally key on the token's ``jti``.
      Without a ``jti`` there is no safe key — caching under a shared id alone would let one app's
      capabilities (e.g. URL-elicitation support) leak onto another.
    """
    user_id = user_context.get("user_id")
    client_id = user_context.get("client_id")
    if not user_id or not client_id:
        return None

    if resolve_client_category(client_id, jwt_config) is ClientCategory.MCP_DCR:
        return f"{prefix}:mcpgw:client:{user_id}:{client_id}"

    jti = user_context.get("jti")
    if not jti:
        return None
    return f"{prefix}:mcpgw:client:{user_id}:{client_id}:{jti}"


class ClientInfoStore:
    """Single-round-trip SET/GET of :class:`InitializeRequestParams`, keyed by :func:`client_info_key`."""

    def __init__(self, redis_client: Redis, key_prefix: str, jwt_config: JwtTokenConfig) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._jwt_config = jwt_config

    def key_for(self, user_context: UserContextDict) -> str | None:
        return client_info_key(self._key_prefix, user_context, self._jwt_config)

    def save(self, key: str, params: InitializeRequestParams) -> None:
        self._redis.set(
            key,
            params.model_dump_json(include=_PERSISTED_FIELDS),
            ex=_CLIENT_INFO_TTL_SECONDS,
        )

    def load(self, key: str) -> InitializeRequestParams | None:
        raw = self._redis.get(key)
        if raw is None:
            return None
        try:
            return InitializeRequestParams.model_validate_json(raw)
        except ValueError:
            logger.warning("Failed to parse cached client-info at key %s; ignoring.", key)
            return None


class ClientInfoCaptureMiddleware:
    """ASGI wrapper that captures ``initialize`` params into :class:`ClientInfoStore`.

    FastMCP exposes no handler hook for ``initialize`` and builds ``ServerSession`` internally, so the
    only place to capture client info is at the ASGI boundary. For a POST we buffer the body, and if it
    is a JSON-RPC ``initialize`` we persist its params before replaying the body to the inner app.

    Cache failures never block the request: parse and Redis errors are logged and the request proceeds.
    The store is resolved per request via ``store_provider`` because the container is built lazily,
    after the app (and this middleware) is mounted.
    """

    def __init__(self, app: ASGIApp, store_provider: Callable[[], ClientInfoStore | None]) -> None:
        self._app = app
        self._store_provider = store_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return

        body = await _read_body(receive)
        try:
            self._maybe_capture(scope, body)
        except Exception:
            logger.warning("Failed to capture client-info from initialize request; proceeding.", exc_info=True)

        await self._app(scope, _replay(body, receive), send)

    def _maybe_capture(self, scope: Scope, body: bytes) -> None:
        # Cheap pre-filter: only `initialize` bodies matter, so skip the JSON parse for the far more
        # frequent tool-call POSTs on the hot path. A rare false positive just falls through to parse.
        if b'"initialize"' not in body:
            return

        data = json.loads(body)
        # Batched JSON-RPC arrays are not handled; the gateway doesn't support batching.
        if not isinstance(data, dict) or data.get("method") != "initialize":
            return

        store = self._store_provider()
        if store is None:
            return

        # Key off scope state directly. The read side (get_client_params) additionally requires a
        # valid ObjectId user_id, so a malformed-but-truthy id may be written here yet never read —
        # a harmless orphan key reaped by TTL. This asymmetry is intentional, not a latent bug.
        user_context: UserContextDict | None = scope.get("state", {}).get("user")
        if user_context is None:
            return

        key = store.key_for(user_context)
        if key is None:
            return

        params = InitializeRequestParams.model_validate(data.get("params") or {})
        try:
            store.save(key, params)
        except RedisError:
            logger.warning("Failed to persist client-info to Redis; proceeding.", exc_info=True)


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        chunks.append(message.get("body", b""))
        more_body = message.get("more_body", False)
    return b"".join(chunks)


def _replay(body: bytes, original_receive: Receive) -> Receive:
    """Yield the buffered body once, then delegate every later call to the original ``receive``.

    Delegating — rather than returning a synthetic ``http.disconnect`` — is essential on the SSE path:
    the streaming response runs a disconnect monitor that polls ``receive()`` and cancels the whole
    response (including in-flight progress notifications) the instant it sees ``http.disconnect``. A
    synthetic disconnect would fire immediately and tear down slow/streaming tool calls. The real
    ``receive`` instead blocks until a genuine client disconnect, preserving streaming semantics.
    """
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await original_receive()

    return receive
