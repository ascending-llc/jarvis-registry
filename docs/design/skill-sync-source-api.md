# Skill Sync Source Management API

## Table of Contents

1. [API Route Prefix](#api-route-prefix)
2. [API Endpoints](#api-endpoints)
   - 2.1. [Create Skill Sync Source](#1-create-skill-sync-source)
   - 2.2. [List Skill Sync Sources](#2-list-skill-sync-sources)
   - 2.3. [Get Skill Sync Source Detail](#3-get-skill-sync-source-detail)
   - 2.4. [Update Skill Sync Source](#4-update-skill-sync-source)
   - 2.5. [Delete Skill Sync Source](#5-delete-skill-sync-source)
   - 2.6. [Trigger Sync](#6-trigger-sync)
   - 2.7. [Initiate OAuth](#7-initiate-oauth)
   - 2.8. [OAuth Callback](#8-oauth-callback)
   - 2.9. [Get Sync Job](#9-get-sync-job)
3. [Access Control](#access-control)
4. [Data Models](#data-models)
   - 4.1. [SkillSyncSource](#skillsyncsource)
   - 4.2. [SkillSyncJob](#skillsyncjob)
   - 4.3. [Enums](#enums)
5. [State Machine](#state-machine)
6. [GitHub App OAuth Flow (PKCE)](#github-app-oauth-flow-pkce)
7. [Token Management](#token-management)
8. [Error Response Format](#error-response-format)

---

## API Route Prefix

```
/api/v1/skill-sync-sources
```

---

## API Endpoints

### 1. Create Skill Sync Source

**Endpoint**: `POST /api/v1/skill-sync-sources`

**Request Body**:
```json
{
  "displayName": "My Skills Repo",
  "description": "Production skill definitions",
  "tags": ["production", "internal"],
  "owner": "my-org",
  "repo": "skills-repo",
  "ref": "main",
  "paths": ["skills/", "prompts/mcp"],
  "githubAppClientId": "github-app-client-id",
  "githubAppClientSecret": "github-app-client-secret"
}
```

**Request Fields**:
- `displayName` (required, string): Human-readable name, 1–128 characters
- `description` (optional, string): Source description
- `tags` (optional, array of strings): Categorization tags
- `owner` (required, string): GitHub owner (user or org), 1–39 characters, alphanumeric + hyphens, regex: `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$`
- `repo` (required, string): GitHub repository name, 1–100 characters, regex: `^[A-Za-z0-9._-]+$`
- `ref` (optional, string): Git ref to sync from (default: `"main"`), 1–255 characters, validated against path traversal
- `paths` (required, array of strings, min 1): Repository-relative POSIX paths to scan for skills. Must be safe relative paths (no leading `/`, no `..`, no `\`). Each path is a **container**: only its direct child folders holding a `SKILL.md` become skills — the path itself is never a skill, so a `SKILL.md` at the path root is skipped. Use `["."]` to scan the repository root. Per the [Agent Skills spec](https://agentskills.io/specification), each skill's frontmatter `name` must exactly match its folder name; a mismatched skill fails discovery with `skill_name_mismatch`.
- `githubAppClientId` (required, string): GitHub App OAuth client ID
- `githubAppClientSecret` (required, string): GitHub App client secret (encrypted at rest via AES-CBC)

**Validation Rules**:
- `paths` must not contain duplicates after normalization
- `ref` must be a safe Git ref (no `..`, `//`, `@{`, or `\`)
- `owner` must match GitHub username format
- `repo` must match GitHub repository name format

**Response**: `201 Created`
```json
{
  "id": "source-id-1",
  "providerType": "github",
  "displayName": "My Skills Repo",
  "description": "Production skill definitions",
  "tags": ["production", "internal"],
  "owner": "my-org",
  "repo": "skills-repo",
  "ref": "main",
  "paths": ["skills/", "prompts/mcp"],
  "status": "active",
  "syncStatus": "idle",
  "syncMessage": null,
  "stats": { "skillCount": 0, "fileCount": 0 },
  "lastSync": null,
  "permissions": { "VIEW": true, "EDIT": true, "DELETE": true, "SHARE": true },
  "createdAt": "2026-08-19T10:30:00Z",
  "updatedAt": "2026-08-19T10:30:00Z",
  "githubAppClientId": "github-app-client-id",
  "hasClientSecret": true,
  "authorization": { "connected": false },
  "recentJobs": [],
  "createdBy": "user-id-1",
  "updatedBy": "user-id-1"
}
```

**Important Notes**:
- The creator is automatically granted **OWNER** permission (VIEW + EDIT + DELETE + SHARE) via an atomic MongoDB transaction
- `githubAppClientSecret` is never returned in any response; `hasClientSecret` indicates whether one is stored

**Error**:
- `422` Validation error (invalid paths, bad owner/repo format, missing required fields)
- `500` Internal server error

---

### 2. List Skill Sync Sources

**Endpoint**: `GET /api/v1/skill-sync-sources`

**Query Parameters**:
```typescript
{
  syncStatus?: string;     // Filter by sync status (e.g., "idle", "syncing", "failed")
  tag?: string;            // Filter by tag
  query?: string;          // Full-text search (displayName, description)
  page?: number;           // Page number (default: 1, min: 1)
  perPage?: number;        // Items per page (default: 20, min: 1, max: 100)
}
```

**Response**: `200 OK`
```json
{
  "sources": [
    {
      "id": "source-id-1",
      "providerType": "github",
      "displayName": "My Skills Repo",
      "description": "Production skill definitions",
      "tags": ["production"],
      "owner": "my-org",
      "repo": "skills-repo",
      "ref": "main",
      "paths": ["skills/"],
          "status": "active",
      "syncStatus": "success",
      "syncMessage": null,
      "stats": { "skillCount": 12, "fileCount": 8 },
      "lastSync": {
        "jobId": "job-id-1",
        "status": "success",
        "startedAt": "2026-08-19T10:00:00Z",
        "finishedAt": "2026-08-19T10:02:30Z",
        "commitSha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      },
      "permissions": { "VIEW": true, "EDIT": true, "DELETE": false, "SHARE": false },
      "createdAt": "2026-08-19T10:30:00Z",
      "updatedAt": "2026-08-19T15:45:00Z"
    }
  ],
  "pagination": {
    "total": 5,
    "page": 1,
    "perPage": 20,
    "totalPages": 1
  }
}
```

**Important Notes**:
- Only returns sources the authenticated user has VIEW access to (ACL-filtered)
- Each source includes per-resource `permissions` for the requesting user
- Results are sorted by `updatedAt` descending

**Error**:
- `500` Internal server error

---

### 3. Get Skill Sync Source Detail

**Endpoint**: `GET /api/v1/skill-sync-sources/{source_id}`

**Response**: `200 OK`
```json
{
  "id": "source-id-1",
  "providerType": "github",
  "displayName": "My Skills Repo",
  "description": "Production skill definitions",
  "tags": ["production"],
  "owner": "my-org",
  "repo": "skills-repo",
  "ref": "main",
  "paths": ["skills/"],
  "status": "active",
  "syncStatus": "success",
  "syncMessage": null,
  "stats": { "skillCount": 12, "fileCount": 8 },
  "lastSync": {
    "jobId": "job-id-1",
    "status": "success",
    "startedAt": "2026-08-19T10:00:00Z",
    "finishedAt": "2026-08-19T10:02:30Z",
    "commitSha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "permissions": { "VIEW": true, "EDIT": true, "DELETE": true, "SHARE": true },
  "createdAt": "2026-08-19T10:30:00Z",
  "updatedAt": "2026-08-19T15:45:00Z",
  "githubAppClientId": "github-app-client-id",
  "hasClientSecret": true,
  "authorization": { "connected": false },
  "recentJobs": [
    {
      "id": "job-id-1",
      "sourceId": "source-id-1",
      "jobType": "full_sync",
      "triggerType": "manual",
      "status": "success",
      "phase": "completed",
      "requestSnapshot": {
        "owner": "octocat",
        "repo": "skills",
        "ref": "main",
        "paths": ["skills"],
        "configRevision": 1
      },
      "discoverySummary": { "discoveredSkillCount": 12, "discoveredFileCount": 8, "skippedPaths": [] },
      "applySummary": { "skillsCreated": 12, "skillsUpdated": 0, "skillsDeleted": 0, "skillsFailed": 0, "filesCreated": 8, "filesUpdated": 0, "filesDeleted": 0 },
      "skillErrors": [],
      "errorCode": null,
      "error": null,
      "startedAt": "2026-08-19T10:00:00Z",
      "finishedAt": "2026-08-19T10:02:30Z",
      "createdAt": "2026-08-19T10:00:00Z",
      "updatedAt": "2026-08-19T10:02:30Z"
    }
  ],
  "createdBy": "user-id-1",
  "updatedBy": "user-id-1"
}
```

**Important Notes**:
- Detail response extends the list response with `githubAppClientId`, `hasClientSecret`, `authorization`, `recentJobs`, `createdBy`, `updatedBy`
- `authorization.connected` is per requesting user: `true` when that user holds an unexpired access or refresh
  token for this source. It is a database lookup only (GitHub is not called), so a token revoked on GitHub still
  reads `true` until a sync or test-connect returns `needsAuthorization`
- `recentJobs` returns the last 10 jobs sorted by `createdAt` descending

**Error**:
- `403` User does not have VIEW permission
- `404` Source not found
- `500` Internal server error

---

### 4. Update Skill Sync Source

**Endpoint**: `PUT /api/v1/skill-sync-sources/{source_id}`

**Request Body** (all fields optional, partial update via `exclude_unset`):
```json
{
  "displayName": "Updated Name",
  "description": "Updated description",
  "tags": ["production", "v2"],
  "owner": "new-org",
  "repo": "new-repo",
  "ref": "develop",
  "paths": ["src/skills/"],
  "githubAppClientId": "new-github-app-client-id",
  "githubAppClientSecret": "new-secret",
  "syncAfterUpdate": false
}
```

**Request Fields**:
- All create fields are accepted (same validation rules apply)
- `syncAfterUpdate` (optional, boolean, default: `false`): When `true`, triggers a sync after the update when a usable GitHub token is available; otherwise returns `needsAuthorization: true`

**Behavior**:
- Only `ACTIVE` sources can be updated (state machine guard)
- Fields are compared by value against the stored source; a field sent with its current value is not a change.
  The secret is compared against the decrypted stored secret (constant-time); if decryption fails it counts as changed
- A request with no real changes is a no-op: nothing is saved, and `configRevision` / `updatedBy` stay the same
- `configRevision` increments only when an execution-affecting field (`owner`, `repo`, `ref`, `paths`,
  `githubAppClientId`, `githubAppClientSecret`) really changes
- Really changing `githubAppClientId` or `githubAppClientSecret` automatically **deletes all stored OAuth tokens** for this source, forcing re-authorization on next sync

**Response**: `200 OK` — when `syncAfterUpdate` is omitted or `false`, returns the updated `SkillSyncSourceDetailResponse`

When `syncAfterUpdate=true` and sync starts successfully, returns `SkillSyncTriggerResponse`:
```json
{
  "job": {
    "id": "job-id-3",
    "sourceId": "source-id-1",
    "jobType": "full_sync",
    "triggerType": "manual",
    "status": "pending",
    "phase": "queued",
    "requestSnapshot": {
      "owner": "new-org",
      "repo": "new-repo",
      "ref": "develop",
      "paths": ["src/skills/"],
    },
    "discoverySummary": { "discoveredSkillCount": 0, "discoveredFileCount": 0, "skippedPaths": [] },
    "applySummary": { "skillsCreated": 0, "skillsUpdated": 0, "skillsDeleted": 0, "skillsFailed": 0, "filesCreated": 0, "filesUpdated": 0, "filesDeleted": 0 },
    "skillErrors": [],
    "errorCode": null,
    "error": null,
    "startedAt": null,
    "finishedAt": null,
    "createdAt": "2026-08-19T10:00:00Z",
    "updatedAt": "2026-08-19T10:00:00Z"
  },
  "needsAuthorization": false,
  "authorizeUrl": null
}
```

When authorization is required:
```json
{
  "job": null,
  "needsAuthorization": true,
  "authorizeUrl": null
}
```

**Error**:
- `403` User does not have EDIT permission
- `404` Source not found
- `409` Source cannot be updated (status is not `ACTIVE`)
- `500` Internal server error

---

### 5. Delete Skill Sync Source

**Endpoint**: `DELETE /api/v1/skill-sync-sources/{source_id}`

This endpoint:
1. Transition source status to `DELETING`
2. Create a `DELETE_SYNC` job that removes all synced skills outright, along with their auxiliary files, ACL entries, and stored GitHub tokens
3. Return `202 Accepted` with the job ID

Child skills are hard-deleted, matching how federation reconciles stale children. The
source document itself is soft-deleted (`status=deleted` plus a `deletedAt` timestamp) so
the delete outcome stays auditable. Stale-skill reconciliation during a normal sync — a
skill that disappeared upstream — hard-deletes the same way.

**Response**: `202 Accepted`
```json
{
  "sourceId": "source-id-1",
  "jobId": "job-id-2",
  "status": "deleting"
}
```

**Error**:
- `403` User does not have DELETE permission
- `404` Source not found
- `409` Source cannot be deleted (status is not `ACTIVE`)
- `500` Internal server error

---

### 6. Trigger Sync

**Endpoint**: `POST /api/v1/skill-sync-sources/{source_id}/sync`

**Request Body** (optional):
```typescript
{ dryRun?: boolean }  // default false
```

`dryRun=true` is a **test-connect** (mirrors the federation providers' `dryRun`): it validates the
configuration only, **synchronously, read-only** — no job is enqueued and no state is mutated. Use it to
check the config before committing a real sync.

**Behavior — real sync (`dryRun=false`, default):**
1. Check for an existing OAuth token (access → refresh fallback)
2. If no valid token is available, return `needsAuthorization: true`
3. If a token is valid, atomically transition the source to `pending`, persist a `FULL_SYNC` job, and return job details
4. The app-scoped job runner atomically claims persisted jobs with a renewable lease. An expired lease is reclaimed after process failure; after three abandoned attempts the job is failed and the source is released from its active state

**Behavior — test connect (`dryRun=true`):**
1. Check for an existing OAuth token (access → refresh fallback); if none, return `needsAuthorization: true`
2. With the token, make one read-only GitHub call resolving `owner/repo@ref` (validates repo exists,
   the GitHub App can read it, and the ref exists)
3. Return `{ "ok": true }` on success, or `{ "ok": false, "detail": "..." }` on failure
4. **Never** enqueues a job, marks the source `pending`, or is blocked by an in-progress sync (no `409`)

**Response — real sync**: `200 OK`
```json
{
  "job": {
    "id": "job-id-3",
    "sourceId": "source-id-1",
    "jobType": "full_sync",
    "triggerType": "manual",
    "status": "pending",
    "phase": "queued",
    "...": "..."
  },
  "needsAuthorization": false,
  "authorizeUrl": null
}
```

Or when re-authorization is needed:
```json
{
  "job": null,
  "needsAuthorization": true,
  "authorizeUrl": null
}
```

**Response — test connect (`dryRun=true`)**: `200 OK`
```json
{ "ok": true, "needsAuthorization": false, "detail": null }
```
```json
{ "ok": false, "needsAuthorization": false, "detail": "Repository octocat/skills ref main not found" }
```
```json
{ "ok": false, "needsAuthorization": true, "detail": null }
```

**Error**:
- `403` User does not have EDIT permission
- `404` Source not found
- `409` Source already has an active sync job (real sync only; not raised for `dryRun=true`)
- `500` Internal server error

---

### 7. Initiate OAuth

**Endpoint**: `GET /api/v1/skill-sync-sources/{source_id}/oauth/initiate`

This endpoint:
1. Generate PKCE `code_verifier` + `code_challenge` (S256 via authlib)
2. Store OAuth flow state in FlowStateManager (Redis or memory fallback)
3. Redirect (`307`) to `https://github.com/login/oauth/authorize` with PKCE parameters

**Response**: `307 Temporary Redirect`
- `Location: https://github.com/login/oauth/authorize?client_id=...&redirect_uri=...&state=...&code_challenge=...&code_challenge_method=S256`

**Error**:
- `403` User does not have EDIT permission
- `404` Source not found
- `500` Internal server error

---

### 8. OAuth Callback

**Endpoint**: `GET /api/v1/skill-sync-sources/oauth/callback`

This is the GitHub OAuth redirect target. It is a **single constant URL for the whole deployment** — the
`source_id` is no longer in the path; it is recovered from the `state` parameter. It is **unauthenticated**
(GitHub redirects do not carry session cookies).

**Query Parameters** (set by GitHub):
```typescript
{
  code?: string;    // Authorization code
  state?: string;   // CSRF state token
  error?: string;   // Error from GitHub (e.g., "access_denied")
}
```

**Behavior**:
- Resolves `source_id` from `state` first. If `state` is missing or does not resolve to a known flow
  (expired, already consumed, malformed) → redirects to the generic list page with `error=invalid_callback`
  (there is no source to attribute the error to).
- Once the source is resolved: if `error` is present or `code` is missing → redirects to that source's page
  with `error=auth_failed`.
- If source not found (404) → redirects to that source's page with `error=auth_failed`
- Otherwise, validates state token and consumes the stored flow from FlowStateManager
- Exchanges `code` for tokens at `https://github.com/login/oauth/access_token` with `code_verifier`
- Stores encrypted tokens (access + refresh) in MongoDB `tokens`
- **Stores the token only — it does NOT auto-trigger a sync.** The frontend then explicitly drives
  test-connect (`POST /{source_id}/sync {dryRun:true}`) and, when ready, the real sync.

**Response**: `307 Temporary Redirect`
- Success: `Location: {registry_client_url}/skill-sync-sources/{source_id}?status=connected`
- Resolved-source error: `Location: {registry_client_url}/skill-sync-sources/{source_id}?error=auth_failed`
- Unresolvable state: `Location: {registry_client_url}/skill-sync-sources?error=invalid_callback`

---

### 9. Get Sync Job

**Endpoint**: `GET /api/v1/skill-sync-sources/{source_id}/jobs/{job_id}`

**Response**: `200 OK`
```json
{
  "id": "job-id-1",
  "sourceId": "source-id-1",
  "jobType": "full_sync",
  "triggerType": "manual",
  "status": "syncing",
  "phase": "discovering",
  "requestSnapshot": {
    "owner": "octocat",
    "repo": "skills",
    "ref": "main",
    "paths": ["skills"],
    "configRevision": 1
  },
  "discoverySummary": {
    "discoveredSkillCount": 8,
    "discoveredFileCount": 5,
    "skippedPaths": ["skills/vendor", "skills/README.md"]
  },
  "applySummary": {
    "skillsCreated": 0,
    "skillsUpdated": 0,
    "skillsDeleted": 0,
    "skillsFailed": 0,
    "filesCreated": 0,
    "filesUpdated": 0,
    "filesDeleted": 0
  },
  "skillErrors": [],
  "errorCode": null,
  "error": null,
  "startedAt": "2026-08-19T10:00:00Z",
  "finishedAt": null,
  "createdAt": "2026-08-19T10:00:00Z",
  "updatedAt": "2026-08-19T10:01:15Z"
}
```

**Error**:
- `403` User does not have VIEW permission on the source
- `404` Source or job not found
- `500` Internal server error

---

## Access Control

Skill sync sources use the same ACL system as MCP Servers, A2A Agents, and Workflows:

| Property | Value |
|----------|-------|
| Resource type | `skill_sync_source` (in `RegistryResourceType` enum) |
| Permission bits | VIEW (1), EDIT (3), DELETE / OWNER (15) |
| Seed roles | `skill_sync_source_viewer` (permBits=1), `skill_sync_source_editor` (permBits=3), `skill_sync_source_owner` (permBits=15) |
| On create | Creator is automatically granted OWNER permission (atomic MongoDB transaction) |

**Per-endpoint permission requirements**:

| Endpoint | Required Permission |
|----------|-------------------|
| `POST /` (create) | Authenticated user (any) |
| `GET /` (list) | VIEW (ACL-filtered) |
| `GET /{id}` (detail) | VIEW |
| `PUT /{id}` (update) | EDIT |
| `DELETE /{id}` | DELETE |
| `POST /{id}/sync` | EDIT |
| `GET /{id}/oauth/initiate` | EDIT |
| `GET /oauth/callback` | None (unauthenticated, GitHub redirect) |
| `GET /{id}/jobs/{job_id}` | VIEW |

---

## Data Models

### SkillSyncSource

MongoDB collection: `skill_sync_sources`

| Field | Type | Description |
|-------|------|-------------|
| `providerType` | SkillSyncProviderType | Source provider, always `github` for now |
| `displayName` | string | Human-readable name |
| `description` | string \| null | Optional description |
| `tags` | string[] | Categorization tags |
| `owner` | string | GitHub owner (user or org) |
| `repo` | string | GitHub repository name |
| `ref` | string | Git ref (branch/tag), default `"main"` |
| `paths` | string[] | Repo-relative POSIX paths to scan |
| `configRevision` | integer | Internal monotonic revision for execution-affecting source configuration |
| `githubAppClientId` | string | GitHub App OAuth client ID |
| `githubAppClientSecretEncrypted` | string | AES-CBC encrypted client secret |
| `status` | SkillSyncSourceStatus | Source lifecycle status |
| `syncStatus` | SkillSyncStatus | Current sync state |
| `syncMessage` | string \| null | Last sync error/status message |
| `stats` | SkillSyncSourceStats | Live synced inventory counts: `{ skillCount, fileCount }` for non-deleted GitHub skills and their auxiliary files |
| `lastSync` | SkillSyncSourceLastSync \| null | Last completed sync snapshot; `commitSha` is either a 40-character Git commit SHA or `"unknown"` |
| `createdBy` | string \| null | Creator user ID |
| `updatedBy` | string \| null | Last updater user ID |
| `createdAt` | datetime | Auto-set on insert |
| `updatedAt` | datetime | Auto-updated on save |
| `deletedAt` | datetime \| null | Soft delete timestamp |

**Indexes**:
- `(providerType, status, updatedAt desc)` — filtered listing
- `(syncStatus, updatedAt desc)` — sync queue queries
- Text index on `(displayName, description)` — full-text search

### SkillSyncJob

MongoDB collection: `skill_sync_jobs`

| Field | Type | Description |
|-------|------|-------------|
| `sourceId` | PydanticObjectId | Reference to SkillSyncSource |
| `jobType` | SkillSyncJobType | `full_sync` / `config_resync` / `delete_sync` |
| `triggerType` | SkillSyncTriggerType | `manual` / `oauth_callback` / `api` |
| `triggeredBy` | string | User ID who triggered the job |
| `status` | SkillSyncJobStatus | Job status |
| `phase` | SkillSyncJobPhase | Detailed execution phase |
| `requestSnapshot` | SkillSyncFullRequestSnapshot \| SkillSyncDeleteRequestSnapshot | Typed, immutable execution input; full sync stores owner/repo/ref/paths/configRevision and delete stores action/configRevision |
| `discoverySummary` | SkillSyncDiscoverySummary | `{ discoveredSkillCount, discoveredFileCount, skippedPaths }` |
| `applySummary` | SkillSyncApplySummary | `{ skillsCreated/Updated/Deleted/Failed, filesCreated/Updated/Deleted }` |
| `skillErrors` | SkillSyncSkillError[] | Per-skill error details |
| `errorCode` | string \| null | Machine-readable error code |
| `error` | string \| null | Human-readable error message |
| `startedAt` | datetime \| null | When execution started |
| `finishedAt` | datetime \| null | When execution completed |
| `leaseOwner` | string \| null | Registry runner instance currently owning the job |
| `leaseExpiresAt` | datetime \| null | Renewable claim deadline used for crash recovery |
| `heartbeatAt` | datetime \| null | Last successful lease renewal |
| `attemptCount` | integer | Number of worker claims, including crash-recovery attempts |

**Indexes**:
- `(sourceId, createdAt desc)` — recent jobs query
- `(sourceId, status)` — active job guard
- `(status, leaseExpiresAt, createdAt)` — durable claim and expired-lease recovery

The durable runner executes only from `requestSnapshot`. If an execution-affecting source update increments
`configRevision` while a job is queued, that stale job fails before contacting GitHub; a new sync must be triggered
from the updated source configuration.

### Enums

**SkillSyncSourceStatus**: `active` | `deleting` | `deleted`

**SkillSyncStatus**: `idle` | `pending` | `syncing` | `success` | `partial_success` | `failed`

**SkillSyncJobType**: `full_sync` | `config_resync` | `delete_sync`

**SkillSyncTriggerType**: `manual` | `oauth_callback` | `api`

**SkillSyncJobStatus**: `pending` | `syncing` | `success` | `partial_success` | `failed`

**SkillSyncJobPhase**: `queued` | `downloading` | `extracting` | `discovering` | `applying` | `completed` | `failed`

**SkillSyncJobErrorCode**: `github_auth_failed` | `github_rate_limited` | `github_not_found` | `download_failed` | `download_too_large` | `extraction_failed` | `decompression_bomb` | `no_skills_found` | `sync_not_implemented` | `internal_error`

**SkillSyncSkillErrorCode**: `skill_parse_failed` | `skill_name_missing` | `skill_name_mismatch` | `duplicate_skill_name` | `file_too_large` | `too_many_files` | `skill_too_large` | `write_failed`

---

## State Machine

### Source Status Transitions

```
ACTIVE ──→ DELETING ──→ DELETED
  ↑            │
  └── (restore on delete failure)
```

- Only `ACTIVE` sources can be updated or start a sync
- Only `ACTIVE` sources can transition to `DELETING`
- `restore_after_delete_failure` reverts `DELETING` → `ACTIVE` with `syncStatus=FAILED`

### Sync Status Transitions

```
IDLE ──→ PENDING ──→ SYNCING ──→ SUCCESS
  ↑                      │        PARTIAL_SUCCESS
  └──────────────────────←── FAILED
```

- `can_start_sync`: allowed from `IDLE`, `SUCCESS`, `PARTIAL_SUCCESS`, `FAILED`
- Cannot start a new sync while in `PENDING` or `SYNCING`

### Job Status Transitions

```
PENDING → SYNCING → SUCCESS / PARTIAL_SUCCESS / FAILED
```

### Job Phase Transitions

```
QUEUED → DOWNLOADING → EXTRACTING → DISCOVERING → APPLYING → COMPLETED
                                                                FAILED
```

---

## GitHub App OAuth Flow (PKCE)

### Prerequisites

1. **Create a GitHub App** (not an OAuth App):
   - For an org's repositories, register the App under the org: org → Settings → Developer settings →
     GitHub Apps → New GitHub App. "Where can this GitHub App be installed?" → **Only on this account**
   - Set Callback URL to `{REGISTRY_URL}/api/v1/skill-sync-sources/oauth/callback`, e.g.
     `https://jarvis.example.com/gateway/api/v1/skill-sync-sources/oauth/callback`. The registry builds
     `redirect_uri` from `REGISTRY_URL`, so the scheme, host, and base path must match it exactly
     (one constant URL for all sources — the `source_id` is carried in the OAuth `state`, not the path)
   - Leave "Request user authorization (OAuth) during installation" **unchecked**. With it enabled,
     GitHub sends the installer to the Callback URL with a `code` but no `state` (and no PKCE), so
     the callback cannot resolve the source and redirects to `?error=invalid_callback`. Users
     authorize from Jarvis instead (Connect GitHub, or a sync / test-connect that needs it)

2. **Set permissions**: Repository permissions → Contents → **Read-only** (Metadata → Read-only is
   added automatically). Contents is what lets the App read a private repository's commits and tarball

3. **Generate client secret** on the App settings page

4. **Install the App on the org and grant repository access** (an org owner must do or approve this):
   - org → Settings → GitHub Apps → the App → **Configure** (or the App's public page → Install → the org)
   - Repository access → **Only select repositories**, and add every repository a skill sync source
     points at (or **All repositories**). Repositories added later take effect without re-authorizing
     in Jarvis
   - If the App's permissions change after installation (e.g. Contents added later), the new
     permissions apply only after an org owner accepts the permission update on the installation
   - Installing only grants repository access; each user still authorizes the App through Jarvis
     afterwards. A user's token reaches only repositories that the installation covers **and** that
     user can read, so every user who connects needs read access to the repository

   Without a matching installation, authorization succeeds but GitHub API calls return 404 — GitHub
   answers 404, not 403, for a private repository the token cannot see. The sync then fails with
   `github_not_found` ("Repository {owner}/{repo} ref {ref} not found") even though the repository and
   ref exist

### Flow Sequence

```
1. User clicks "Connect GitHub"
   → GET /api/v1/skill-sync-sources/{source_id}/oauth/initiate

2. Server generates PKCE parameters:
   - code_verifier = secrets.token_urlsafe(32)
   - code_challenge = create_s256_code_challenge(code_verifier)  # via authlib
   - Stores flow state in FlowStateManager (Redis / memory fallback)

3. Server redirects (307) to GitHub:
   → https://github.com/login/oauth/authorize
     ?client_id={githubAppClientId}
     &redirect_uri={callback_url}
     &state={encrypted_flow_state}
     &code_challenge={code_challenge}
     &code_challenge_method=S256

4. User authorizes on GitHub

5. GitHub redirects to callback:
   → GET /api/v1/skill-sync-sources/oauth/callback
     ?code={authorization_code}
     &state={state}   # source_id is resolved from state

6. Server exchanges code for tokens:
   → POST https://github.com/login/oauth/access_token
     client_id, client_secret, code, redirect_uri, code_verifier

7. Server stores encrypted tokens (AES-CBC) in MongoDB Token collection
   (token only — no sync is auto-triggered)

8. Server redirects to frontend:
   → {registry_client_url}/skill-sync-sources/{source_id}?status=connected

9. Frontend then drives test-connect and sync explicitly:
   → POST /skill-sync-sources/{source_id}/sync { dryRun: true }   # validate config
   → POST /skill-sync-sources/{source_id}/sync { dryRun: false }  # real sync
```

### Frontend Integration

Authorization and syncing are **three explicit steps**. The OAuth callback stores the token only — it
never auto-triggers a sync — so the frontend decides when to test and when to sync.

**Step 1 — Connect GitHub** (needed once, or whenever the token is gone/expired-and-unrefreshable)

- This is a **full-page browser navigation**, not a `fetch`/XHR — `oauth/initiate` responds `307` to
  `github.com`, which a fetch cannot follow across origins:
  `window.location.href = "/api/v1/skill-sync-sources/{source_id}/oauth/initiate"`
- After the user authorizes, GitHub → callback → the browser lands back on
  `{registry_client_url}/skill-sync-sources/{source_id}?status=connected`. Read that `status`/`error`
  query param to show a toast; then proceed to Step 2.
- Callback error params the frontend should handle: `error=auth_failed` (on the source page) and
  `error=invalid_callback` (on the generic list page, when the state could not be resolved).

**Step 2 — Test connection** (`POST /{source_id}/sync` with `{ "dryRun": true }`)

- `{ "needsAuthorization": true }` → token missing/expired → send the user back to **Step 1**.
- `{ "ok": true }` → config valid; enable the "Sync now" button.
- `{ "ok": false, "detail": "..." }` → show `detail` (repo/ref not found, no access, etc.).
- Read-only: safe to call anytime, even while a sync is already running (never returns `409`).

**Step 3 — Sync now** (`POST /{source_id}/sync` with `{ "dryRun": false }`, or an empty body)

- `{ "needsAuthorization": true }` → send back to **Step 1**.
- `{ "job": { ... } }` → poll `GET /{source_id}/jobs/{job_id}` until the job reaches a terminal
  status (`success` / `partial_success` / `failed`) to render progress.
- `409` → a sync is already active for this source; surface it and offer to poll the active job.

**Signal summary** (both `dryRun` values share the same token gate):

| Signal | Meaning | Frontend action |
|---|---|---|
| `needsAuthorization: true` | No usable OAuth token | Go to Step 1 (`oauth/initiate` full-page redirect) |
| dryRun `ok: true` | Config valid | Enable "Sync now" |
| dryRun `ok: false` + `detail` | Config invalid | Show `detail` |
| real-sync `job` | Sync enqueued | Poll `GET /{source_id}/jobs/{job_id}` |
| `?status=connected` (callback) | Token stored, no sync started | Toast, then Step 2 |
| `?error=auth_failed` / `?error=invalid_callback` | OAuth callback failed | Show error |

### Token Prefix Reference

| Prefix | Type | Description |
|--------|------|-------------|
| `ghu_` | GitHub App user-to-server token | Issued by GitHub Apps via user OAuth |
| `gho_` | OAuth App token | Issued by classic OAuth Apps (not used here) |

---

## Token Management

### Storage

OAuth tokens are stored in the `tokens` MongoDB collection with AES-CBC encryption:

| Token Type | Identifier Pattern | Default Lifetime |
|------------|-------------------|-----------------|
| `skill_sync_github_access` | `skillsync:{source_id}` | 10 years (GitHub Apps without expiration setting) |
| `skill_sync_github_refresh` | `skillsync:{source_id}` | 1 year |

### Resolution Flow

```
resolve_access_token(user_id, source_id, client_id, client_secret):
  1. Look up access token → if valid (not expired), return it
  2. Look up refresh token → if valid, refresh via GitHub API → store new tokens → return
  3. No valid token → return None (caller returns needsAuthorization=true)
```

### Token Cleanup

- When `githubAppClientId` or `githubAppClientSecret` is changed via `PUT` (by value, not merely sent), all stored tokens for the source are deleted
- When a source is deleted, all associated tokens are removed

---

## Error Response Format

All error responses follow the standard format:

```json
{
  "detail": "Human-readable error message"
}
```

| Status Code | Meaning |
|-------------|---------|
| `403` | Insufficient permissions |
| `404` | Resource not found |
| `409` | Conflict (invalid state for operation, e.g., updating a non-ACTIVE source) |
| `422` | Validation error (invalid input) |
| `500` | Internal server error |

---

## Durable Sync Integration Tests

The regular suite runs deterministic unit tests and skips tests that require a live MongoDB replica set. To verify
atomic worker leasing and transaction rollback against MongoDB itself:

```bash
SKILL_SYNC_MONGO_INTEGRATION_URI='mongodb://127.0.0.1:27017/?replicaSet=rs0' \
  LOG_FORMAT='%(levelname)s:%(name)s:%(message)s' \
  uv run pytest registry/tests/integration/test_skill_sync_durability.py
```

The integration file uses isolated, randomly named databases and removes them after every test.
