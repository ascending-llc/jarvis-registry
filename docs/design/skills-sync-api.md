# Skills API

Base URL: `/api/v1`

## Authentication

The Skills API uses **dual authentication** — the auth method depends on the endpoint:

| Endpoint type | Auth method | Credential | Required scope |
|---------------|-------------|------------|----------------|
| Sync-down reads (`GET /skills`, `GET /skills/{id}/content`) | Session cookie **or** Bearer token | `jarvis_registry_session` cookie / `Authorization: Bearer <token>` | `skills-read` |
| All other reads (`GET /skills/{id}`, `GET /skills/{id}/files/{path}`) | Session cookie only | `jarvis_registry_session` cookie | `skills-read` |
| Writes (`POST`, `PATCH`, `DELETE`) | Session cookie only | `jarvis_registry_session` cookie + `X-Jarvis-CSRF` header | `skills-write` |

Sync-down reads accept Bearer tokens so that CLI agents can pull skill content using managed-agent tokens without
a browser session. Write endpoints are session-only to prevent proxy token replay.

Content type: `Content-Type: application/json`

## Access Control

Skills use the same ACL system as MCP Servers and A2A Agents:

- **Resource type**: `skill` (in `RegistryResourceType` enum)
- **Permission bits**: VIEW (1), EDIT (3), DELETE / OWNER (15)
- **Seed roles**: `skill_viewer` (permBits=1), `skill_editor` (permBits=3), `skill_owner` (permBits=15)
- **On create**: the author is automatically granted OWNER permission (atomic transaction)
- **On delete**: all ACL entries for the skill are removed (atomic transaction)

List endpoints return only skills the user has VIEW access to. Individual operations check the required permission
level (VIEW for reads, EDIT for updates/toggle, DELETE for deletion).

## Data Sharing with Jarvis Chat

Skills and SkillFiles are stored in shared MongoDB collections (`skills`, `skillfiles`). Registry uses Beanie's
`use_state_management = True` so that `save()` emits incremental `$set` updates, preserving Chat-only fields that
are not modelled on `ExtendedSkill`.

Key distinctions:

| Field | Purpose |
|-------|---------|
| `createdByRegistry: bool` | Only `true` for skills created through this API. Registry refuses to delete Chat-created skills (409). |
| `source` (on SkillFile) | `"registry-inline"` means content is stored in `body: bytes`. Other values (Chat sources) return `available: false`. |
| `enabled: bool` | Document-level toggle. Chat skills default to enabled; Registry skills can be toggled via the toggle endpoint. |

## Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/skills` | Dual | List skills (ACL-filtered) |
| `POST` | `/skills` | Session + CSRF | Create a skill |
| `GET` | `/skills/{skill_id}` | Session | Get skill detail with file metadata |
| `GET` | `/skills/{skill_id}/content` | Dual | Get skill content for CLI sync-down |
| `GET` | `/skills/{skill_id}/files/{file_path}` | Session | Get individual file content |
| `PUT` | `/skills/{skill_id}/files/{file_path}` | Session + CSRF | Create or replace a supporting file |
| `DELETE` | `/skills/{skill_id}/files/{file_path}` | Session + CSRF | Delete a supporting file |
| `PATCH` | `/skills/{skill_id}` | Session + CSRF | Update a skill (metadata only; not files) |
| `DELETE` | `/skills/{skill_id}` | Session + CSRF | Delete a skill |
| `POST` | `/skills/{skill_id}/toggle` | Session + CSRF | Toggle skill enabled state |

---

## 1. List Skills

`GET /skills`

Returns metadata for all skills the authenticated user has VIEW access to, ordered by `updatedAt` ascending.

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | boolean | — | Filter by enabled state |
| `fileCount` | integer (≥ 0) | — | Filter by file count |

### Request Example

```http
GET /api/v1/skills?enabled=true&fileCount=0
Authorization: Bearer <token>
Accept: application/json
```

### Response

`200 OK`

```json
{
  "skills": [
    {
      "id": "<skill-objectid>",
      "name": "mongoose-to-beanie",
      "displayTitle": "Mongoose to Beanie",
      "description": "Convert Mongoose schemas to Beanie models",
      "category": "development",
      "tags": ["python", "mongodb"],
      "path": "mongoose-to-beanie",
      "version": 3,
      "fileCount": 0,
      "alwaysApply": false,
      "enabled": true,
      "author": "<user-objectid>",
      "authorName": "Jane Doe",
      "source": "inline",
      "sourceMetadata": null,
      "createdByRegistry": true,
      "permissions": {
        "VIEW": true,
        "EDIT": true,
        "DELETE": true,
        "SHARE": true
      },
      "updatedAt": "2026-08-05T10:01:00Z",
      "deletedAt": null
    }
  ]
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `skills` | array | ACL-filtered skill metadata |
| `skills[].id` | string | MongoDB ObjectID |
| `skills[].name` | string | Unique name (kebab-case, `^[a-z0-9]+(?:-[a-z0-9]+)*$`) |
| `skills[].displayTitle` | string or null | Human-readable display title |
| `skills[].description` | string | Skill description |
| `skills[].category` | string | Skill category |
| `skills[].tags` | string[] | Tags |
| `skills[].path` | string | Local directory name; falls back to `name` |
| `skills[].version` | integer | Increments on each update |
| `skills[].fileCount` | integer | Number of supporting files |
| `skills[].alwaysApply` | boolean | Whether the skill is always applied |
| `skills[].enabled` | boolean | Whether the skill is active |
| `skills[].author` | string | Author's MongoDB user ID |
| `skills[].authorName` | string | Author's display name |
| `skills[].source` | string | Source type (`"inline"`, etc.) |
| `skills[].sourceMetadata` | object or null | Source-specific metadata |
| `skills[].createdByRegistry` | boolean | `true` only for skills created through this API; `false` for Chat-created skills |
| `skills[].permissions` | object | Caller's permissions (`VIEW`, `EDIT`, `DELETE`, `SHARE`) |
| `skills[].updatedAt` | datetime | Last update time |
| `skills[].deletedAt` | datetime or null | Soft-delete tombstone |

---

## 2. Create Skill

`POST /skills`

Creates a new skill. The authenticated user is automatically granted OWNER permission. A duplicate check is
performed on `(name, author)` — returns 409 on conflict.

### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | Yes | — | Kebab-case name, max 64 chars. Pattern: `^[a-z0-9]+(?:-[a-z0-9]+)*$` |
| `displayTitle` | string | No | `null` | Display title, max 128 chars |
| `description` | string | Yes | — | Description, max 1024 chars |
| `body` | string | No | `""` | Markdown body, max 100,000 chars |
| `category` | string | No | `""` | Category, max 128 chars |
| `tags` | string[] | No | `[]` | Tags |
| `alwaysApply` | boolean | No | `false` | Always-apply flag |
| `userInvocable` | boolean | No | `true` | Whether users can invoke directly |
| `disableModelInvocation` | boolean | No | `false` | Disable model invocation |
| `allowedTools` | string[] or null | No | `null` | Tool whitelist; `null` = unrestricted |
| `files` | `SkillFileInput[]` | No | `[]` | Supporting files created inline (see below); omit for a single-`SKILL.md` skill |

### `SkillFileInput`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `relativePath` | string | Yes | POSIX path relative to the skill directory. Server-validated: non-empty, no absolute paths, no `..`/`.` segments, no backslashes, normalized (no `./`, `//`, trailing `/`), not `SKILL.md`. |
| `content` | string | Exactly-one | UTF-8 text content. Provide **either** `content` **or** `body`, not both. |
| `body` | string | Exactly-one | Base64-encoded binary content. |
| `mimeType` | string | No | Defaults to `mimetypes.guess_type(relativePath)` or `application/octet-stream`. |
| `isExecutable` | boolean | No | Default `false`. The CLI applies `chmod +x` on sync when true. |
| `isBinary` | boolean or null | No | Optional. The server always recomputes it from the actual bytes; if the client sends a value, it must match the server's determination or the request is rejected (422). |

**Server-enforced limits** (all return 422 on breach):
- Single file: 5 MiB (`MAX_SKILL_FILE_SIZE`), applied to the **decoded** byte count.
- Total per skill: 10 MiB (`MAX_SKILL_FILES_TOTAL_SIZE`).
- Count per skill: 50 (`MAX_SKILL_FILE_COUNT`).
- Duplicate `relativePath` within a single request is rejected.

> **Wire size note.** Binary payloads travel as base64 in JSON, which expands the raw bytes by ~1.33×.
> A single 5 MiB binary is therefore ~6.7 MiB on the wire, and a full 10 MiB skill can approach ~13.3 MiB
> of JSON body. Front-facing proxies (nginx `client_max_body_size`, ALB request-size limits, API gateway
> caps) must allow at least this much or clients will receive `413 Payload Too Large` instead of Registry's
> `422`.

### Request Example

```http
POST /api/v1/skills
Cookie: jarvis_registry_session=<token>
X-Jarvis-CSRF: <hmac>
Content-Type: application/json

{
  "name": "mongoose-to-beanie",
  "description": "Convert Mongoose schemas to Beanie models",
  "body": "# Mongoose to Beanie\n\nFollow these instructions...\n",
  "category": "development",
  "tags": ["python", "mongodb"],
  "files": [
    {
      "relativePath": "scripts/run_review.sh",
      "content": "#!/usr/bin/env bash\nset -euo pipefail\n",
      "mimeType": "text/x-shellscript",
      "isExecutable": true
    },
    {
      "relativePath": "assets/logo.png",
      "body": "<base64-encoded>",
      "mimeType": "image/png"
    }
  ]
}
```

### Response

`201 Created` — returns `SkillDetailResponse` (same shape as GET detail). The response `files[]` reflects the files just created.

### Errors

| Status | Condition |
|--------|-----------|
| `409 Conflict` | A skill with this name already exists for this author |
| `422 Unprocessable Entity` | Name pattern validation failed; file path invalid; per-file / total / count limit exceeded; duplicate `relativePath`; `content`/`body` neither-or-both; invalid base64; `isBinary` declaration disagrees with the actual bytes |

---

## 3. Get Skill Detail

`GET /skills/{skill_id}`

Returns full skill detail including file metadata (without file content) and the caller's permissions.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `skill_id` | ObjectID | Skill ID |

### Response

`200 OK`

```json
{
  "id": "<skill-objectid>",
  "name": "mongoose-to-beanie",
  "displayTitle": "Mongoose to Beanie",
  "description": "Convert Mongoose schemas to Beanie models",
  "body": "# Mongoose to Beanie\n\nFollow these instructions...\n",
  "frontmatter": {
    "name": "mongoose-to-beanie",
    "description": "Convert Mongoose schemas to Beanie models",
    "alwaysApply": false
  },
  "category": "development",
  "tags": ["python", "mongodb"],
  "version": 3,
  "fileCount": 1,
  "enabled": true,
  "alwaysApply": false,
  "userInvocable": true,
  "disableModelInvocation": false,
  "allowedTools": null,
  "author": "<user-objectid>",
  "authorName": "Jane Doe",
  "source": "inline",
  "sourceMetadata": null,
  "createdByRegistry": true,
  "createdAt": "2026-08-01T12:00:00Z",
  "updatedAt": "2026-08-05T10:01:00Z",
  "files": [
    {
      "id": "<file-objectid>",
      "relativePath": "references/guide.md",
      "mimeType": "text/markdown",
      "bytes": 2048,
      "isBinary": false,
      "isExecutable": false,
      "source": "registry-inline"
    }
  ],
  "permissions": {
    "VIEW": true,
    "EDIT": true,
    "DELETE": true,
    "SHARE": true
  }
}
```

---

## 4. Get Skill Content (Sync-Down)

`GET /skills/{skill_id}/content`

Returns the data required to reconstruct the local skill directory. This is the primary endpoint for CLI sync.
Accepts both session cookie and Bearer token authentication.

### Response

`200 OK`

```json
{
  "id": "<skill-objectid>",
  "name": "mongoose-to-beanie",
  "description": "Convert Mongoose schemas to Beanie models",
  "body": "# Mongoose to Beanie\n\nFollow these instructions...\n",
  "frontmatter": {
    "name": "mongoose-to-beanie",
    "description": "Convert Mongoose schemas to Beanie models"
  },
  "alwaysApply": false,
  "disableModelInvocation": false,
  "userInvocable": true,
  "allowedTools": null,
  "category": "development",
  "createdByRegistry": true,
  "files": [
    {
      "relativePath": "references/guide.md",
      "content": "# Guide\n\nReference content here.",
      "mimeType": "text/markdown",
      "bytes": 2048,
      "isBinary": false,
      "isExecutable": false,
      "source": "registry-inline"
    },
    {
      "relativePath": "assets/icon.png",
      "body": "<base64-encoded>",
      "mimeType": "image/png",
      "bytes": 4096,
      "isBinary": true,
      "isExecutable": false,
      "source": "registry-inline"
    }
  ]
}
```

Each entry in `files[]` is a `SkillFileResponse`: text `registry-inline` files carry `content`; binary
`registry-inline` files carry `body` (base64) instead, with `content` omitted.

### File Availability

Files with `source: "registry-inline"` include their content. Files created in Jarvis Chat (other source values)
return `available: false` with a reason:

```json
{
  "relativePath": "data/config.json",
  "mimeType": "application/json",
  "bytes": 512,
  "isBinary": false,
  "source": "github",
  "available": false,
  "unavailableReason": "File content is not available in Registry because it was created in Jarvis Chat."
}
```

---

## 5. Get File Content

`GET /skills/{skill_id}/files/{file_path}`

Returns content for a single file. Registry-inline files return text content or base64-encoded binary.
Chat-created files return `available: false`.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `skill_id` | ObjectID | Skill ID |
| `file_path` | string | Relative file path (e.g., `references/guide.md`) |

### Response

`200 OK` — Registry-inline text file:

```json
{
  "relativePath": "references/guide.md",
  "content": "# Guide\n\nReference content here.",
  "mimeType": "text/markdown",
  "isBinary": false,
  "available": true
}
```

`200 OK` — Registry-inline binary file:

```json
{
  "relativePath": "assets/icon.png",
  "body": "<base64-encoded>",
  "mimeType": "image/png",
  "isBinary": true,
  "available": true
}
```

`200 OK` — Chat-created file:

```json
{
  "relativePath": "data/config.json",
  "mimeType": "application/json",
  "isBinary": false,
  "available": false,
  "unavailableReason": "File content is not available in Registry because it was created in Jarvis Chat."
}
```

---

## 5b. Upsert File

`PUT /skills/{skill_id}/files/{file_path}`

Create or replace a single `registry-inline` supporting file. Supports incremental file management — the client
sends only the file being changed, not the whole skill. Requires `EDIT` on the skill and
`createdByRegistry = true`. Bumps the skill's `version` and refreshes `updatedAt`.

### Request Body — `SkillFileUpsertRequest`

Same field set as `SkillFileInput` (see §2) **without** `relativePath` (comes from the URL): `content` or
`body` (exactly one), optional `mimeType`, `isExecutable`, `isBinary`.

### Response

- `201 Created` — the file did not exist and was created.
- `200 OK` — the file existed and was replaced.

Body: `SkillFileMetadataResponse`.

```json
{
  "id": "<file-objectid>",
  "relativePath": "scripts/run_review.sh",
  "mimeType": "text/x-shellscript",
  "bytes": 42,
  "isBinary": false,
  "isExecutable": true,
  "source": "registry-inline"
}
```

### Errors

| Status | Condition |
|--------|-----------|
| `403 Forbidden` | Caller lacks `EDIT` |
| `404 Not Found` | Skill does not exist |
| `409 Conflict` | Skill was created in Jarvis Chat (`createdByRegistry = false`); the target file exists but is not `registry-inline` (Chat/GitHub-owned); or the skill was modified concurrently and MongoDB rejected the write (retry the request) |
| `422 Unprocessable Entity` | Any file-validation failure listed in §2 (path, size, count, total, base64, `isBinary` mismatch, `content`/`body` cardinality) |

---

## 5c. Delete File

`DELETE /skills/{skill_id}/files/{file_path}`

Remove a single `registry-inline` supporting file. Requires `EDIT` and `createdByRegistry = true`. Bumps the
skill's `version` and refreshes `updatedAt`.

### Response

`204 No Content`.

### Errors

| Status | Condition |
|--------|-----------|
| `404 Not Found` | Skill or file does not exist |
| `409 Conflict` | `createdByRegistry = false`; the file's `source` is not `registry-inline`; or the skill was modified concurrently and MongoDB rejected the write (retry the request) |
| `422 Unprocessable Entity` | `file_path` fails relative-path validation |

---

## 6. Update Skill

`PATCH /skills/{skill_id}`

Partial update **of metadata only**. Supporting files are managed via `PUT`/`DELETE /skills/{skill_id}/files/{file_path}`
(§5b, §5c) so callers only re-send the file being changed. Increments `version` and updates `updatedAt`.
Frontmatter fields (`name`, `description`, `alwaysApply`, `userInvocable`, `disableModelInvocation`, `allowedTools`)
are automatically synced to the `frontmatter` object.

### Request Body

All fields are optional. Only include fields to update.

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `name` | string | No | New name (triggers duplicate check) |
| `displayTitle` | string | Yes | Display title |
| `description` | string | No | Description |
| `body` | string | No | Markdown body |
| `category` | string | No | Category |
| `tags` | string[] | No | Tags |
| `alwaysApply` | boolean | No | Always-apply flag |
| `userInvocable` | boolean | No | User-invocable flag |
| `disableModelInvocation` | boolean | No | Disable model invocation |
| `allowedTools` | string[] or null | Yes | Tool whitelist |

Sending `null` for non-nullable fields returns `422 Unprocessable Entity`. An empty body (no fields) returns the
current state without incrementing `version`.

### Request Example

```http
PATCH /api/v1/skills/<skill-objectid>
Cookie: jarvis_registry_session=<token>
X-Jarvis-CSRF: <hmac>
Content-Type: application/json

{
  "description": "Updated description",
  "category": "migration"
}
```

### Response

`200 OK` — returns `SkillDetailResponse`.

### Errors

| Status | Condition |
|--------|-----------|
| `409 Conflict` | New name conflicts with an existing skill |
| `422 Unprocessable Entity` | Non-nullable field set to `null` |

---

## 7. Delete Skill

`DELETE /skills/{skill_id}`

Permanently deletes a skill and all its files. Only skills created in Registry (`createdByRegistry: true`) can be
deleted. Chat-created skills return `409 Conflict`.

The operation is atomic (MongoDB transaction): deletes skill files, the skill document, and all ACL entries.

### Response

`204 No Content`

### Errors

| Status | Condition |
|--------|-----------|
| `404 Not Found` | Skill does not exist or is already deleted |
| `409 Conflict` | Skill was created in Jarvis Chat, not Registry |

---

## 8. Toggle Skill

`POST /skills/{skill_id}/toggle`

Enables or disables a skill. Requires EDIT permission.

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | boolean | Yes | New enabled state |

### Request Example

```http
POST /api/v1/skills/<skill-objectid>/toggle
Cookie: jarvis_registry_session=<token>
X-Jarvis-CSRF: <hmac>
Content-Type: application/json

{
  "enabled": false
}
```

### Response

`200 OK`

```json
{
  "id": "<skill-objectid>",
  "enabled": false
}
```

---

## Error Responses

All errors use the standard FastAPI response shape:

```json
{
  "detail": "Error message"
}
```

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | Authentication failed. Dual-auth endpoints include `WWW-Authenticate: Bearer` challenge. |
| `403 Forbidden` | Insufficient permissions (ACL check failed) |
| `404 Not Found` | Skill or file does not exist |
| `409 Conflict` | Duplicate name or attempting to delete a Chat-created skill |
| `422 Unprocessable Entity` | Validation error (name pattern, null on non-nullable field, invalid ObjectID) |
| `500 Internal Server Error` | Unexpected server failure |

## Local Directory Reconstruction

The CLI creates one directory per Skill:

```text
<skills-root>/mongoose-to-beanie/
├── SKILL.md
└── references/
    └── guide.md
```

`SKILL.md` is reconstructed by combining promoted fields with `frontmatter` and `body`:

```yaml
---
name: mongoose-to-beanie
description: Convert Mongoose schemas to Beanie models
always-apply: false
disable-model-invocation: false
user-invocable: true
category: development
---

# Mongoose to Beanie

Follow these instructions...
```

When `allowedTools` is non-null, it maps to `allowed-tools` in the frontmatter. When `isExecutable` is `true` on a
supporting file, the CLI must set the file's execute permission (`chmod +x`) after writing.

Clients should still normalize `relativePath` values before writing to disk. On the write side, Registry
already rejects absolute paths, parent traversal (`..`), duplicate paths, backslashes, non-normalized POSIX
paths, and the reserved `SKILL.md` name with `422 Unprocessable Entity`.

## Scopes

Defined in `registry-pkgs/src/registry_pkgs/scopes.yml`:

| Scope | Actions |
|-------|---------|
| `skills-read` | `skills:list`, `skills:get`, `skills:getContent`, `skills:getFileContent` |
| `skills-write` | `skills:create`, `skills:update`, `skills:delete`, `skills:toggle`, `skills:upsertFile`, `skills:deleteFile` |
