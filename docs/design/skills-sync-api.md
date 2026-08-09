# Skills Sync API

Base URL: `/proxy`

Authentication:

- `Authorization: Bearer <token>`
- Required scope: `skills-proxy-ops`

Content type:

- `Content-Type: application/json`

The API provides read-only Skill metadata and content for CLI sync-down. Only body-only skills (`fileCount == 0`) are
returned — skills with supporting files are not yet supported for sync. Access is controlled at the endpoint scope level.
Per-Skill ACL checks are not applied in this version.

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills` | List all body-only Skills (`fileCount == 0`) |
| `GET` | `/skills/{skill_id}/content` | Get the complete content of one Skill |

## 1. List Skills

`GET /skills`

Returns metadata for all body-only skills (skills with `fileCount == 0`), ordered by `updatedAt` ascending. The response
does not include the Skill body or supporting file content.

### Request Example

```http
GET /proxy/skills
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
      "fileCount": 0,
      "alwaysApply": false,
      "updatedAt": "2026-08-05T10:01:00Z",
      "deletedAt": null
    }
  ]
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `skills` | array | All body-only skill metadata |
| `skills[].id` | string | MongoDB ObjectID used by the content endpoint |
| `skills[].path` | string | Local directory name. Falls back to `name` when no separate path is stored. |
| `skills[].name` | string | Skill name |
| `skills[].description` | string | Skill description |
| `skills[].category` | string | Skill category |
| `skills[].tags` | string array | Skill tags |
| `skills[].version` | integer | Source Skill version; increments whenever the body or files change. Clients use this to detect updates |
| `skills[].fileCount` | integer | Always `0` for skills returned by this endpoint |
| `skills[].alwaysApply` | boolean | Whether the Skill is always applied |
| `skills[].updatedAt` | datetime | Last source update time |
| `skills[].deletedAt` | datetime or null | Soft-delete tombstone time |

## 2. Get Skill Content

`GET /skills/{skill_id}/content`

Returns the data required to reconstruct the local Skill directory. Clients normally call this endpoint only when the
metadata `version` differs from the version stored locally.

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
  "files": []
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
| `files` | array | Supporting files under the Skill directory (currently always empty for synced skills) |
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
└── SKILL.md
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
| `422 Unprocessable Entity` | ObjectID format is invalid |
| `500 Internal Server Error` | Unexpected server failure |

## Current Limitations

- Only body-only skills (`fileCount == 0`) are returned. Skills with supporting files require S3 content access that
  Registry does not yet support.
- Delete sync requires the source to retain a soft-delete tombstone. Physical MongoDB deletion cannot be discovered
  through this API — clients must compare the full ID list against their local manifest.
- The list endpoint is not paginated. The current target is a catalog of up to 1,000 mostly textual Skills.
