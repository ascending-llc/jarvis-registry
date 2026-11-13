"""Small ASGI helpers shared by the gateway's core middleware/gates."""

from __future__ import annotations

from starlette.types import Receive


async def read_http_body(receive: Receive, *, max_bytes: int | None = None) -> bytes | None:
    """Buffer an HTTP request body from an ASGI ``receive`` channel.

    Returns the body bytes, or ``None`` if the client disconnected before the final chunk (an
    incomplete request that must not be treated as a complete body). When ``max_bytes`` is set,
    reading stops once that many bytes have been accumulated — the caller only needs a bounded
    prefix (e.g. to read a JSON-RPC id) and must not buffer an unbounded body into memory.
    """
    chunks: list[bytes] = []
    total = 0
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] != "http.request":
            return None
        chunk = message.get("body", b"")
        if max_bytes is not None:
            # Slice within the chunk so a single oversized chunk can't blow the bound.
            chunk = chunk[: max_bytes - total]
        chunks.append(chunk)
        total += len(chunk)
        if max_bytes is not None and total >= max_bytes:
            break
        more_body = message.get("more_body", False)
    return b"".join(chunks)
