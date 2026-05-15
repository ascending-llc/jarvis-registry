# Proxy Correctness: HTTP Transport Layer Handling

## The Problem Class

When a service acts as an HTTP proxy — sitting between an upstream server and a downstream reverse proxy — it cannot blindly forward response headers. Each hop in the chain is an **independent HTTP conversation** with its own transport framing. Headers that describe the upstream→proxy link must be consumed at the proxy boundary and never forwarded, because they are meaningless (or actively harmful) on the proxy→client link.

This is the purpose of RFC 2616 §13.5.1's hop-by-hop header list. Most proxies never encounter correctness issues here because well-behaved upstream servers produce clean responses. Problems only surface when the upstream emits non-standard transport framing that the downstream enforces strictly.

---

## The Specific Instance (AgentCore A2A → Jarvis Registry → Nginx)

### Upstream behavior: AWS AgentCore A2A runtime

AgentCore's A2A path routes through a streaming runtime. That runtime unconditionally adds `Transfer-Encoding: chunked` to **all** outbound responses — including complete `application/json` responses that already have a `Content-Length` header and a fully-buffered body.

This violates RFC 7230 §3.3.2, which states that a sender MUST NOT send `Content-Length` and `Transfer-Encoding` simultaneously. A well-behaved HTTP/1.1 server on a complete buffered response would send only `Content-Length`. AgentCore's streaming runtime adds `Transfer-Encoding: chunked` at its own network boundary, below the level of the application code, so the conflict is invisible to the agent developer.

Note: AgentCore's **MCP path** uses a different internal runtime that does not exhibit this behavior — MCP `application/json` responses are returned cleanly without the spurious `Transfer-Encoding`.

### Downstream behavior: Nginx

Nginx enforces RFC 7230 §3.3 strictly. When it receives a backend response containing both `Content-Length` and `Transfer-Encoding: chunked`, it rejects it with `502 Bad Gateway`. There is no configuration knob to relax this — it is a deliberate correctness check.

### The failure mode

Without proper hop-by-hop stripping in the proxy:

1. Jarvis Registry proxies the AgentCore response headers through unchanged.
2. The forwarded response carries both `Content-Length: N` and `Transfer-Encoding: chunked`.
3. Nginx sees this as an invalid response and returns 502 to the client.

The A2A non-streaming (buffered `application/json`) branch was the only path affected in practice, because:
- The A2A streaming (SSE) path never sends `Content-Length` in the first place, so no conflict arises.
- The MCP paths use AgentCore's MCP runtime, which doesn't add the spurious header.

---

## The Fix

The proxy must actively consume and reframe the transport layer rather than forwarding headers through.

### Hop-by-hop stripping

All eight RFC 2616 §13.5.1 hop-by-hop headers are stripped from the upstream response before constructing the outbound response:

```
connection, keep-alive, proxy-authenticate, proxy-authorization,
te, trailers, transfer-encoding, upgrade
```

Stripping `transfer-encoding` is the direct fix: the proxy has already de-chunked the body (via `httpx`'s `aread()`), so the encoding described by that header has been fully consumed at the proxy boundary.

### Per-branch `Content-Length` handling

`Content-Length` is not in the hop-by-hop frozenset because the correct treatment differs by response type:

**Buffered responses (`Response`):**
The upstream's `Content-Length` is popped explicitly. Starlette recalculates it from `len(content_bytes)` — the actual de-chunked body. This is correct because the upstream's value was computed before (or during) chunked encoding and may not match the raw body length.

**Streaming responses (`StreamingResponse`):**
The upstream's `Content-Length` is also popped explicitly. Starlette never sets it for a `StreamingResponse` because the total length is indeterminate. Uvicorn then independently adds `Transfer-Encoding: chunked` on the outbound connection — its own decision for its own link, not a forwarded upstream header.

This is the canonical RFC-specified proxy separation of concerns: the upstream's transport framing describes the upstream→proxy link; the proxy's runtime (uvicorn) independently negotiates transport framing for the proxy→nginx link. The two links both happen to use chunked encoding when streaming, but they do so via independent, correct decisions at each hop.

### `Connection: keep-alive` in SSE branches

The SSE response branches explicitly re-add `Connection: keep-alive` to the outbound headers. This is intentional: `connection` is a hop-by-hop header and is stripped from the upstream response, but the outbound SSE link to nginx requires it to hold the long-lived connection open. Re-adding it is not forwarding the upstream value — it is a fresh, independent decision for the outbound leg.

---

## Nginx Configuration for Streaming

Separately from the header issue, nginx requires explicit configuration to handle SSE and long-lived proxy connections correctly:

```nginx
proxy_set_header Connection '';   # HTTP/1.1 keepalive to backend
proxy_buffering off;              # SSE events must flow immediately, not be buffered
proxy_cache off;                  # no caching for streaming responses
proxy_connect_timeout 10s;
proxy_send_timeout    3600s;      # hold long-lived connections open
proxy_read_timeout    3600s;
```

Without `proxy_buffering off`, nginx accumulates SSE chunks before forwarding them, breaking real-time delivery. Without the extended timeouts, nginx kills any connection idle for more than the default 60 seconds.

These are orthogonal to the header correctness fix but are equally necessary for SSE paths to work end-to-end in production.

---

## Summary Table

| Branch | Upstream issue | Fix |
|---|---|---|
| A2A non-streaming (`Response`) | `Content-Length` + `Transfer-Encoding: chunked` conflict → nginx 502 | Strip hop-by-hop headers; pop `Content-Length`; Starlette recalculates |
| A2A SSE (`StreamingResponse`) | No header conflict (no `Content-Length`), but nginx buffering breaks SSE | Strip hop-by-hop; pop `Content-Length`; nginx `proxy_buffering off` |
| MCP non-streaming (`Response`) | No conflict (AgentCore MCP runtime is clean), but headers were unfiltered | Strip hop-by-hop; pop `Content-Length` for consistency and future safety |
| MCP SSE (`StreamingResponse`) | Nginx buffering breaks SSE | Strip hop-by-hop; pop `Content-Length`; nginx `proxy_buffering off` |
| GET SSE (`StreamingResponse`) | Nginx buffering breaks SSE | Strip hop-by-hop; pop `Content-Length`; nginx `proxy_buffering off` |

---

## Key References

- RFC 2616 §13.5.1 — Hop-by-hop headers that proxies MUST strip
- RFC 7230 §3.3 / §3.3.2 — Message body framing; prohibition on `Content-Length` + `Transfer-Encoding` coexistence
- `registry/src/registry/api/proxy_routes.py` — `_HOP_BY_HOP_HEADERS`, `_sanitize_hop_by_hop_headers()`, all five forward branches
- `frontend/nginx_http_only.conf` — `/proxy/` location block
