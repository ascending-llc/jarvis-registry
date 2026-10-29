# AS-1962 Spike — URL-mode elicitation on the 2026-07-28 MCP spec

**Result: URL-mode elicitation works on 2026-07-28.** Verified against real
`mcp 2.3.0`; self-checks pass for all three delivery paths below.

**On "same experience" — precisely:** at the protocol level the experience is
equivalent. A capable client receives the *same* `ElicitRequestURLParams` (same
`url` + `message`) under both specs; a client without the capability gets the
*same* `CallToolResult` fallback (URL as text) under both. What genuinely differs
is **completion**: 2026-07-28 has no back-channel, so the client must support the
`input_required` **retry** loop. A client that does not implement it cannot complete
the modern flow.

A real third-party agent **does** implement it: Claude Code CLI 2.1.310 negotiated
2026-07-28 and drove the full URL-elicitation retry loop to completion (see "Try with
a real agent" below). One nuance remains — in headless print mode the client
auto-accepted the elicitation without surfacing the URL to the user; whether the URL is
shown to a human needs an interactive run. And the real Registry already notes some
clients mishandle URL elicitation (e.g. Claude Desktop hangs on the legacy `-32042`
error, per `mcpgw/tools/utils.py`), so per-agent behavior must still be confirmed, not
assumed.

## Version relationship

"Package version (SDK)" != "protocol spec version": **one v2 server speaks both
specs**, and which one is used is decided per connection at negotiation.

| | Package | Specs it can speak |
|---|---|---|
| Repo today | mcp 1.28 (v1) | <= 2025-11-25 (no 2026-07-28) |
| This spike / post-migration | mcp 2.x (v2) | 2025-11-25 **and** 2026-07-28 |

## Run

Standalone uv project (`mcp>=2,<3`), decoupled from the repo root:

```bash
cd scripts/spike/as_1962_url_elicitation
uv run run_spike.py     # Client exercises 3 paths (below) + self-checks
uv run server.py        # standalone HTTP server -> http://127.0.0.1:8765/mcp (for a real agent)
```

`run_spike.py` covers three paths against the one v2 server:

1. `mode="2026-07-28"`, elicitation supported -> `InputRequiredResult` (URL mode).
2. `mode="2026-07-28"`, elicitation **not** supported -> `CallToolResult` fallback (URL as text).
3. `mode="legacy"` -> back-channel `ctx.elicit_url()`.

## Findings

**2026-07-28 URL-elicitation wire shape** (first `authorize` return):

```jsonc
{
  "resultType": "input_required",        // a tool return value, not a server-initiated request
  "inputRequests": {
    "authorize": {
      "method": "elicitation/create",
      "params": {
        "mode": "url",
        "message": "...",
        "url": "https://auth.example.com/authorize?state=abc123",
        "elicitationId": null            // <- stripped!
      }
    }
  },
  "requestState": "v1.16Tikn05...(encrypted opaque token)"   // carries continuation, replaces elicitationId
}
```

**How "done" is signaled**: 2026-07-28 does not use
`notifications/elicitation/complete`. Instead the **client retries the same tool
call with `requestState` + `input_responses`** (the `authorize` tool is invoked
twice). Calling `ctx.elicit_url()` on a 2026-07-28 connection raises
`NoBackChannelError`.

**Spec comparison**:

| Aspect | 2025-11-25 (legacy) | 2026-07-28 (modern) |
|---|---|---|
| Delivery | server sends an `elicitation/create` request (back-channel) | tool **returns** `InputRequiredResult` |
| `elicitationId` | present | **stripped** |
| Continuation / done | elicitationId + `elicitation/complete` | retry the tool call + encrypted `requestState` |
| Tool invocations | 1 | **2** |
| Client requirement | needs a back-channel (stateful) | must support the input_required retry loop (default 10 rounds) |

## Try with a real agent (manual — step 3)

`run_spike.py` uses the SDK's own `Client` as the agent stand-in, which proves the
mechanism but not how a third-party agent presents it. To check a real agent, point
Claude Code CLI at the running server (run interactively — a nested headless
`claude -p` can't authenticate and `--dangerously-skip-permissions` is sandbox-blocked):

```bash
uv run server.py &                      # start the server (watch its stderr)
cat > /tmp/spike-mcp.json <<'JSON'
{ "mcpServers": { "spike": { "type": "http", "url": "http://127.0.0.1:8765/mcp" } } }
JSON
claude --mcp-config /tmp/spike-mcp.json
#  then, in the session:  call the authorize tool
```

What to look for:
- The server's stderr prints `authorize: protocol=<ver> url_elicitation=<bool>` — tells
  you which spec the agent negotiated and whether it advertised the capability.
- How the agent surfaces the URL to you, and whether it transparently retries after
  you "open" the link (the 2026-07-28 `input_required` loop) or just shows the text.

### Observed: Claude Code CLI 2.1.310 (print mode)

Ran the `claude -p ... --mcp-config` command above. Server log:

```
[server] authorize: protocol=2026-07-28 url_elicitation=True   # 1st call
[server] authorize: protocol=2026-07-28 url_elicitation=True   # 2nd call (retry)
```

- A real third-party agent **negotiated 2026-07-28** and **advertised URL-elicitation**
  — confirms the ticket's premise that Claude Code probes the new spec.
- It drove the **full `input_required` loop end to end**: `authorize` was invoked twice
  (1st returned the URL elicitation, the client accepted and retried with `request_state`,
  2nd returned the final result). So the 2026-07-28 URL-elicitation mechanism works with a
  real agent, not just the SDK test client.
- Caveat (method): in non-interactive `-p` print mode the client **auto-accepted** the
  elicitation without surfacing the URL to the model — the agent reported "no URL, not
  asked to open a browser." The protocol round-trip still happened (see the two log lines).
  Whether the URL is actually shown to a human needs an **interactive** run
  (`claude --mcp-config /tmp/spike-mcp.json`, then "call the authorize tool"); not yet checked.

## Impact on the Registry change (for the design doc)

1. **Current implementation breaks on 2026-07-28**: both
   `_build_url_elicitation_result()` in `mcpgw/tools/utils.py` (via
   `UrlElicitationRequiredError`) and the hand-built `-32042` in `proxy_routes.py`
   are server-initiated, which is not possible under the new spec.
2. **Dual-stack branches on `ctx.protocol_version`**: modern returns
   `InputRequiredResult`; legacy keeps `elicit_url()` / the CallToolResult fallback.
   The same code path cannot serve both.
3. **Rewrite the "authorization complete" semantics**: replace `elicitationId` /
   `send_elicit_complete` with the `requestState` retry model.
4. **Version constants**: `LATEST_PROTOCOL_VERSION == "2026-07-28"`, but the
   handshake uses `LATEST_HANDSHAKE_VERSION == "2025-11-25"`; clean up the scattered
   hardcoded `"2024-11-05"`.
5. **Dependency upgrade**: mcp 2.x pulls in `mcp-types`, `httpx2`/`httpcore2`, etc.;
   re-check the tree on `uv sync`.
