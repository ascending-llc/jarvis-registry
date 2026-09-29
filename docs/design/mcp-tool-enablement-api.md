# MCP Tool Enablement API

Per-server control over which downstream MCP tools are usable. A server's OWNER can disable
individual tools; disabled tools are hidden from discovery (`discover_servers`) and rejected by
the gateway (`execute_tool`). All tools are enabled by default.

## Table of Contents

1. [Concepts](#concepts)
2. [API Route Prefix](#api-route-prefix)
3. [API Endpoints](#api-endpoints)
   - 3.1. [Get Server Tools](#1-get-server-tools)
   - 3.2. [Update Server Disabled Tools](#2-update-server-disabled-tools)
4. [Access Control](#access-control)
5. [Enforcement & Scope](#enforcement--scope)
6. [Data Models](#data-models)
7. [Error Response Format](#error-response-format)

---

## Concepts

- **Per-server, not per-user.** The disabled-tools list belongs to the server. It is the same
  for every consumer of that server — there is no per-user or per-(user, server) override.
- **Denylist, enabled by default.** Only explicitly disabled tools are off. A tool absent from
  the list is enabled. A brand-new server has an empty list.
- **Keyed by downstream `mcpToolName`.** Entries are the raw downstream tool names (the
  `mcpToolName` field inside `config.toolFunctions`), the same identifier `execute_tool` sends
  to the downstream server.
- **Validated against the cached snapshot.** Updates are validated against the server's cached
  `config.toolFunctions`; no live downstream `tools/list` call is made during the request.
  Submitted names that are not current tools are **silently dropped**, not rejected.
- **Self-pruning.** A capabilities refresh that removes or renames a tool downstream drops the
  now-stale name from the disabled list automatically.

---

## API Route Prefix

```
/api/v1
```

---

## API Endpoints

### 1. Get Server Tools

```
GET /api/v1/servers/{server_id}/tools
```

Returns the server detail, including the full tool catalog and the current disabled-tools list.

**Access:** requires `VIEW` permission on the server.

**Response** (`ServerDetailResponse`, camelCase) — relevant fields:

```jsonc
{
  "id": "000000000000000000000001",
  "serverName": "github",
  "toolFunctions": { "search_repositories_mcp_github": { "...": "..." } },
  "disabledTools": ["delete_repository"],
  "...": "..."
}
```

- `disabledTools`: `string[]` — downstream tool names currently disabled. Empty for a server
  with no disabled tools.

### 2. Update Server Disabled Tools

```
PATCH /api/v1/servers/{server_id}/tools
```

Full-replaces the disabled-tools list.

**Access:** requires `SHARE` permission (**OWNER only**).

**Request** (`ServerToolsUpdateRequest`):

```jsonc
{
  "disabledTools": ["delete_repository", "force_push"]
}
```

- `disabledTools`: `string[]` — the complete replacement list. To re-enable a tool, submit a
  list that no longer contains it. Names that are not current tools are silently dropped.
- Last-write-wins: there is no optimistic concurrency control (consistent with
  `PATCH /servers/{server_id}`).

**Response:** the updated `ServerDetailResponse` (same shape as the GET above).

---

## Access Control

| Endpoint | Required scope | Required permission | Roles |
|---|---|---|---|
| `GET /servers/{server_id}/tools` | `servers-read` | `VIEW` | VIEWER, EDITOR, OWNER |
| `PATCH /servers/{server_id}/tools` | `servers-write` | `SHARE` | OWNER only |

The scope is checked first, by `ScopePermissionMiddleware` against `scopes.yml`; the ACL permission
is then checked by the route against the caller's role on that server.

`SHARE` is the bit exclusive to `RoleBits.OWNER`. A VIEWER or EDITOR calling `PATCH` receives
`403 Forbidden`.

---

## Enforcement & Scope

Disabling a tool takes effect on both discovery paths:

- **`discover_servers`** (vector search) filters out disabled tools via a Weaviate
  `tool_enabled == True` property filter. Disabled tools do not appear in results (unless the
  caller sets `include_disabled=true`, which also surfaces disabled servers today).
- **`execute_tool`** (gateway) checks the authoritative MongoDB document and returns a
  `CallToolResult` with `isError=true` for a disabled tool, **before any downstream network
  call**.

> **⚠️ Not enforced in direct-connect proxy mode.** Requests that reach a downstream server
> through the transparent proxy (`/proxy/...`) are **not** filtered against this list — that
> route only performs ACL and telemetry, and enforcing the denylist there would require
> intercepting and rewriting the downstream `tools/list` response, breaking its "almost
> transparent proxy" contract. Treat this list as governing **discovery and the `execute_tool`
> gateway**, not as a hard capability boundary across every connection mode. This is a known,
> accepted gap for this version.

Consistency: MongoDB is authoritative and strongly consistent (`execute_tool` reads it
directly). The Weaviate `tool_enabled` property is updated best-effort after the write, so a
just-disabled tool may briefly still appear in `discover_servers` results — it is still
rejected at `execute_tool`.

---

## Data Models

`registryDisabledTools` on `ExtendedMCPServer` (collection `mcpservers`, root level):

```python
registryDisabledTools: list[str] = Field(
    default_factory=list,
    description="Downstream tool names (mcpToolName) disabled by this server's OWNER.",
)
```

Exposed in the API as `disabledTools`. Registry-only field; not shared with the Chat
(`jarvis-api`) Mongoose schema or the mongoose-to-beanie generation pipeline.

---

## Error Response Format

Errors follow the shared route convention:

```json
{ "detail": "...message..." }
```

| Status | When |
|---|---|
| `403 Forbidden` | Caller lacks `SHARE` (PATCH) or `VIEW` (GET) on the server |
| `404 Not Found` | `server_id` does not exist |
| `400 Bad Request` | Malformed request body |
| `500 Internal Server Error` | Unexpected failure |

Submitting an unknown tool name is **not** an error — it is silently dropped and the request
succeeds.
