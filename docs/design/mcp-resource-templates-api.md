# MCP Resource Templates in the Server API

The server detail response now carries the downstream server's **resource templates**, and the
`resources` list follows a new rule for OAuth servers. This note is for frontend consumers.

## Table of Contents

1. [Concepts](#concepts)
2. [Affected Endpoints](#affected-endpoints)
3. [Response Fields](#response-fields)
4. [Behavior Changes](#behavior-changes)

---

## Concepts

- **Resource**: content addressed by a concrete URI, e.g. `file:///project/README.md`, listed by
  the downstream `resources/list`.
- **Resource template**: an [RFC 6570](https://www.rfc-editor.org/rfc/rfc6570) URI template, e.g.
  `github://repo/{owner}/{repo}`, listed by `resources/templates/list`. A client fills every
  variable and reads the result with `resources/read`. Templates describe parameterized, possibly
  unbounded resource spaces that a plain list cannot enumerate.
- Both are captured when a server is registered, enabled, or refreshed, and stored in the server's
  config (`config.resources`, `config.resourceTemplates`).

---

## Affected Endpoints

Every endpoint that returns `ServerDetailResponse`:

| Method | Path |
|---|---|
| `GET` | `/servers/{server_id}` |
| `POST` | `/servers` |
| `POST` | `/servers/{server_id}/refresh` |

The change is additive: no existing field changes its shape.

---

## Response Fields

### `resourceTemplates` (new)

`array | null`. Each entry:

| Field | Type | Description |
|---|---|---|
| `uriTemplate` | `string` | RFC 6570 URI template |
| `name` | `string` | Template name |
| `description` | `string \| null` | Human-readable description |
| `mimeType` | `string \| null` | MIME type of the resources the template resolves to |
| `annotations` | `object \| null` | MCP annotations (`audience`, `priority`, ...) as returned by the server |

```json
{
  "resourceTemplates": [
    {
      "uriTemplate": "github://repo/{owner}/{repo}",
      "name": "repository",
      "description": "A GitHub repository",
      "mimeType": "application/json",
      "annotations": null
    }
  ]
}
```

A server captured before this change, or one that does not declare the `resources` capability,
returns `[]`.

### `resources` (existing)

Unchanged shape (`uri`, `name`, `description`, `mimeType`, `annotations`).

---

## Behavior Changes

1. **OAuth servers: `resources` is always `[]`.** The capture runs with the token of the user who
   registered, enabled or refreshed the server, so the concrete resources it lists (file names,
   document URIs) are that user's data. Showing them to every viewer would reveal which files that
   user has. Resource templates and prompts describe the server rather than a user's data, so they
   are still returned. The UI should not present an empty `resources` list on an OAuth server as
   "this server has no resources".
2. **A failed listing keeps the previous value.** If one listing (resources, resource templates or
   prompts) fails during a refresh, for example on a timeout, the stored list for it is kept rather
   than cleared. The refresh itself still succeeds when the tools were fetched.
3. **Listings are complete.** Resources, resource templates and prompts are fetched across all
   pages, not only the first page.
4. **MCP App UI resources stay in the API.** `ui://` resources (MIME type
   `text/html;profile=mcp-app`) are still returned in `resources` / `resourceTemplates`. They are
   excluded only from semantic discovery (`discover_servers`).
