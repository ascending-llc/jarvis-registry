"""ASGI gate that rejects the 2026-07-28 MCP era while it is behind a kill switch.

mcp 2.3.0 serves both 2025-11-25 and 2026-07-28 from one server, routing each request by its
``MCP-Protocol-Version`` header. Until the modern-era path ships, this gate rejects any request that
asks for an era outside the handshake set, so the live client population keeps negotiating 2025-11-25.
The rejection mirrors the SDK's own header-only era routing and names the handshake versions in
``supported`` (not the modern set), so an ``mode="auto"`` SDK client falls back to ``initialize`` and
negotiates 2025-11-25 instead of giving up.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from mcp_types import UnsupportedProtocolVersionErrorData
from mcp_types.jsonrpc import UNSUPPORTED_PROTOCOL_VERSION
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from starlette.types import ASGIApp, Receive, Scope, Send

from .asgi import read_http_body

logger = logging.getLogger(__name__)

_PROTOCOL_HEADER = b"mcp-protocol-version"
# A rejected request only needs enough body to read the JSON-RPC id; never buffer more. The gate sits
# outside the streamable app, so the SDK's max_request_body_size does not apply here.
_MAX_REJECT_BODY_BYTES = 65536


class ModernProtocolGate:
    """Reject non-handshake protocol eras unless the kill switch is on.

    Pass-through when:
      - ``enabled_provider()`` returns True (the modern era is enabled), or
      - the request carries no ``MCP-Protocol-Version`` header (handshake negotiates the version), or
      - the header value is a handshake version.

    Otherwise answer HTTP 400 with a JSON-RPC ``-32022`` error naming the handshake versions.
    """

    def __init__(self, app: ASGIApp, enabled_provider: Callable[[], bool]) -> None:
        self._app = app
        self._enabled_provider = enabled_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._enabled_provider():
            await self._app(scope, receive, send)
            return

        requested = _protocol_header(scope)
        if requested is None or requested in HANDSHAKE_PROTOCOL_VERSIONS:
            await self._app(scope, receive, send)
            return

        body = await read_http_body(receive, max_bytes=_MAX_REJECT_BODY_BYTES)
        await _reject(send, requested, _extract_id(body or b""))


def _protocol_header(scope: Scope) -> str | None:
    """Return the ``MCP-Protocol-Version`` header value, or ``None`` if absent."""
    for name, value in scope.get("headers", []):
        if name == _PROTOCOL_HEADER:
            return value.decode("latin-1")  # ASGI header encoding; never raises on bad bytes
    return None


def _extract_id(body: bytes):
    """Return the JSON-RPC ``id`` from the request body, or ``None`` when absent or unparseable."""
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict):
        return data.get("id")
    return None


async def _reject(send: Send, requested: str, request_id) -> None:
    error_data = UnsupportedProtocolVersionErrorData(
        supported=list(HANDSHAKE_PROTOCOL_VERSIONS),
        requested=requested,
    )
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": UNSUPPORTED_PROTOCOL_VERSION,
            "message": "Unsupported protocol version",
            "data": error_data.model_dump(by_alias=True, mode="json"),
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})
