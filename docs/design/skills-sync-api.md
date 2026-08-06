# Skills Sync API

Base URL: `/proxy`

Authentication:

- `Authorization: Bearer <token>`
- Required scope: `skills-proxy-ops`

Content type:

- `Content-Type: application/json`

The API provides read-only Skill metadata and content for CLI sync-down. Access is controlled at the endpoint scope
level. Per-Skill ACL checks are not applied in this version.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills` | List all Skills or Skills changed since a cursor |
| `GET` | `/skills/{skill_id}/content` | Get the complete content of one Skill |

## 1. List Skills

`GET /skills`

Returns Skill metadata ordered by `updatedAt` ascending. The response does not include the Skill body or supporting file
content.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `since` | ISO 8601 datetime | No | Return records where `updatedAt >= since`. Omit for a full listing. |

The `since` cursor is inclusive. Clients must de-duplicate records by `id` when retrying a cursor.

### Request Example

```http
GET /proxy/skills?since=2026-08-05T10:00:00Z
Authorization: Bearer <token>
Accept: application/json
```

### Response

`200 OK`

```json
{
  "skills": [
    {
      "id": "skill-id",
      "path": "mongoose-to-beanie",
      "name": "mongoose-to-beanie",
      "description": "Convert Mongoose schemas to Beanie models",
      "category": "development",
      "tags": ["python", "mongodb"],
      "version": 3,
      "fileCount": 2,
      "alwaysApply": false,
      "contentHash": "sha256:<digest>",
      "updatedAt": "2026-08-05T10:01:00Z",
      "deletedAt": null
    }
  ],
  "cursor": "2026-08-05T10:01:00Z"
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `skills` | array | Skill metadata changed since the supplied cursor |
| `skills[].id` | string | MongoDB ObjectID used by the content endpoint |
| `skills[].path` | string | Local directory name. Falls back to `name` when no separate path is stored. |
| `skills[].name` | string | Skill name |
| `skills[].description` | string | Skill description |
| `skills[].category` | string | Skill category |
| `skills[].tags` | string array | Skill tags |
| `skills[].version` | integer | Source Skill version; not a replacement for `contentHash` |
| `skills[].fileCount` | integer | Number of supporting files, excluding `SKILL.md` |
| `skills[].alwaysApply` | boolean | Whether the Skill is always applied |
| `skills[].contentHash` | string | Versioned SHA-256 hash of the complete synchronizable Skill definition |
| `skills[].updatedAt` | datetime | Last source update time |
| `skills[].deletedAt` | datetime or null | Soft-delete tombstone time |
| `cursor` | datetime or null | Maximum `updatedAt` in this response; `null` when no records are returned |

## 2. Get Skill Content

`GET /skills/{skill_id}/content`

Returns the data required to reconstruct the local Skill directory. Clients normally call this endpoint only when the
metadata `contentHash` differs from the hash computed from local files.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `skill_id` | MongoDB ObjectID | Skill ID returned by the list endpoint |

### Request Example

```http
GET /proxy/skills/{skill_id}/content
Authorization: Bearer <token>
Accept: application/json
```

### Response

`200 OK`

```json
{
  "id": "skill-id",
  "name": "mongoose-to-beanie",
  "description": "Convert Mongoose schemas to Beanie models",
  "body": "# Mongoose to Beanie\n\nFollow these instructions...\n",
  "frontmatter": {
    "model": "claude-sonnet"
  },
  "alwaysApply": false,
  "disableModelInvocation": false,
  "userInvocable": true,
  "allowedTools": null,
  "category": "development",
  "contentHash": "sha256:<digest>",
  "files": [
    {
      "relativePath": "references/decisions.md",
      "content": "# Decisions\n",
      "mimeType": "text/markdown",
      "bytes": 12,
      "isBinary": false,
      "isExecutable": false
    }
  ]
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Skill ID |
| `name` | string | Skill name and `SKILL.md` frontmatter value |
| `description` | string | Skill description and `SKILL.md` frontmatter value |
| `body` | string | Markdown body without YAML frontmatter |
| `frontmatter` | object | Additional YAML frontmatter fields |
| `alwaysApply` | boolean | Maps to the `always-apply` YAML key |
| `disableModelInvocation` | boolean | Whether the skill disables direct model invocation |
| `userInvocable` | boolean | Whether users can invoke this skill directly |
| `allowedTools` | string array or null | Whitelist of tools this skill is allowed to use; `null` means no restriction |
| `category` | string | Maps to the `category` YAML key |
| `contentHash` | string | Hash of this response's synchronizable definition |
| `files` | array | Supporting files under the Skill directory |
| `files[].relativePath` | string | Normalized POSIX path relative to the Skill directory |
| `files[].content` | string or null | UTF-8 text content; `null` when content is unavailable |
| `files[].mimeType` | string | Source MIME type |
| `files[].bytes` | integer | Source file size in bytes |
| `files[].isBinary` | boolean or null | Source binary classification; Registry does not infer this value |
| `files[].isExecutable` | boolean | Whether the file should be written with execute permission |

## Local Directory Reconstruction

The CLI creates one directory per Skill:

```text
<skills-root>/mongoose-to-beanie/
├── SKILL.md
└── references/
    └── decisions.md
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
model: claude-sonnet
---

# Mongoose to Beanie
```

When `allowedTools` is non-null, it maps to `allowed-tools` in the frontmatter. When `isExecutable` is `true` on a
supporting file, the CLI must set the file's execute permission (`chmod +x`) after writing.

Clients must reject absolute paths, parent traversal (`..`), duplicate paths, backslashes, and non-normalized POSIX
paths before writing supporting files.

## Content Hash

`contentHash` uses Skill Content Hash v1 and has this format:

```text
sha256:<64 lowercase hexadecimal characters>
```

The logical manifest is:

```json
{
  "format": "jarvis-skill-content-v1",
  "skill": {
    "allowedTools": null,
    "alwaysApply": false,
    "body": "# Mongoose to Beanie\n",
    "category": "development",
    "description": "Convert Mongoose schemas to Beanie models",
    "disableModelInvocation": false,
    "frontmatter": {"model": "claude-sonnet"},
    "name": "mongoose-to-beanie",
    "userInvocable": true
  },
  "files": [
    {"content": "# Decisions\n", "isExecutable": false, "relativePath": "references/decisions.md"}
  ]
}
```

Hash calculation:

1. Sort supporting files by the unsigned UTF-8 bytes of `relativePath`.
2. Serialize the manifest as UTF-8 JSON with object keys sorted lexicographically, no insignificant whitespace, direct
   Unicode output, and non-finite numbers rejected.
3. Compute SHA-256 over the serialized bytes.
4. Prefix the lowercase hexadecimal digest with `sha256:`.

Python reference serialization:

```python
json.dumps(
    manifest,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
)
```

YAML formatting and key order do not affect the hash. Changes to any Skill field in the manifest, supporting-file path,
or supporting-file content change the hash.

## Error Responses

All errors use the standard FastAPI response shape:

```json
{
  "detail": "Error message"
}
```

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | Bearer token is missing, invalid, or expired |
| `403 Forbidden` | Token does not include `skills-proxy-ops` |
| `404 Not Found` | `skill_id` does not exist |
| `409 Conflict` | A binary, uncached, duplicate-path, unsafe-path, or otherwise unreconstructable file prevents a complete sync |
| `422 Unprocessable Entity` | Cursor or ObjectID format is invalid |
| `500 Internal Server Error` | Unexpected server failure |

When a `409` is returned, clients must preserve the existing local Skill and cursor and retry after the source content is
available.

## Current Limitations

- Binary and uncached large files cannot be synchronized until Registry can read their authoritative raw bytes.
- Delete sync requires the source to retain a soft-delete tombstone. Physical MongoDB deletion cannot be discovered.
- The list endpoint is not paginated. The current target is a catalog of up to 1,000 mostly textual Skills.
