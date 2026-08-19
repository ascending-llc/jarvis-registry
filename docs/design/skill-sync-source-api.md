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
  "skillDiscoveryDepth": 2,
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
- `paths` (required, array of strings, min 1): Repository-relative POSIX paths to scan for skills. Must be safe relative paths (no leading `/`, no `..`, no `\`)
- `skillDiscoveryDepth` (optional, integer): Max directory depth for skill discovery (default: 2, range: 0–10)
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
  "skillDiscoveryDepth": 2,
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
      "skillDiscoveryDepth": 2,
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
  "skillDiscoveryDepth": 2,
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
  "recentJobs": [
    {
      "id": "job-id-1",
      "sourceId": "source-id-1",
      "jobType": "full_sync",
      "triggerType": "manual",
      "status": "success",
      "phase": "completed",
      "requestSnapshot": {},
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
- Detail response extends the list response with `githubAppClientId`, `hasClientSecret`, `recentJobs`, `createdBy`, `updatedBy`
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
  "skillDiscoveryDepth": 3,
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
- Changing `githubAppClientId` or `githubAppClientSecret` automatically **deletes all stored OAuth tokens** for this source, forcing re-authorization on next sync

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
      "skillDiscoveryDepth": 3
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
2. Create a `DELETE_SYNC` job to soft-delete all synced skills and delete their auxiliary files, ACL entries, and stored GitHub tokens
3. Return `202 Accepted` with the job ID

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

This endpoint:
1. Check for an existing OAuth token (access → refresh fallback)
2. If no valid token is available, return `needsAuthorization: true`
3. If a token is valid, atomically transition the source to `pending`, create a `FULL_SYNC` job, launch background sync, and return job details

**Response**: `200 OK`
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

**Error**:
- `403` User does not have EDIT permission
- `404` Source not found
- `409` Source already has an active sync job
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

**Endpoint**: `GET /api/v1/skill-sync-sources/{source_id}/oauth/callback`

This is the GitHub OAuth redirect target. It is **unauthenticated** (GitHub redirects do not carry session cookies).

**Query Parameters** (set by GitHub):
```typescript
{
  code?: string;    // Authorization code
  state?: string;   // CSRF state token
  error?: string;   // Error from GitHub (e.g., "access_denied")
}
```

**Behavior**:
- If `error` is present or `code`/`state` is missing → redirects to frontend with `error=auth_failed`
- If source not found → redirects to frontend with `error=auth_failed`
- Otherwise, validates state token and consumes the stored flow from FlowStateManager
- Exchanges `code` for tokens at `https://github.com/login/oauth/access_token` with `code_verifier`
- Stores encrypted tokens (access + refresh) in MongoDB `tokens`
- Triggers a `FULL_SYNC` job with `triggerType=oauth_callback`

**Response**: `307 Temporary Redirect`
- Success: `Location: {registry_client_url}/skill-sync-sources/{source_id}?status=syncing`
- Error: `Location: {registry_client_url}/skill-sync-sources/{source_id}?error=auth_failed`

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
  "requestSnapshot": {},
  "discoverySummary": {
    "discoveredSkillCount": 8,
    "discoveredFileCount": 5,
    "skippedPaths": ["vendor/"]
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
| `GET /{id}/oauth/callback` | None (unauthenticated, GitHub redirect) |
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
| `skillDiscoveryDepth` | int | Max directory depth for discovery (0–10) |
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
| `requestSnapshot` | dict | Frozen source config at job creation time |
| `discoverySummary` | SkillSyncDiscoverySummary | `{ discoveredSkillCount, discoveredFileCount, skippedPaths }` |
| `applySummary` | SkillSyncApplySummary | `{ skillsCreated/Updated/Deleted/Failed, filesCreated/Updated/Deleted }` |
| `skillErrors` | SkillSyncSkillError[] | Per-skill error details |
| `errorCode` | string \| null | Machine-readable error code |
| `error` | string \| null | Human-readable error message |
| `startedAt` | datetime \| null | When execution started |
| `finishedAt` | datetime \| null | When execution completed |

**Indexes**:
- `(sourceId, createdAt desc)` — recent jobs query
- `(sourceId, status)` — active job guard

### Enums

**SkillSyncSourceStatus**: `active` | `deleting` | `deleted`

**SkillSyncStatus**: `idle` | `pending` | `syncing` | `success` | `partial_success` | `failed`

**SkillSyncJobType**: `full_sync` | `config_resync` | `delete_sync`

**SkillSyncTriggerType**: `manual` | `oauth_callback` | `api`

**SkillSyncJobStatus**: `pending` | `syncing` | `success` | `partial_success` | `failed`

**SkillSyncJobPhase**: `queued` | `downloading` | `extracting` | `discovering` | `applying` | `completed` | `failed`

**SkillSyncJobErrorCode**: `github_auth_failed` | `github_rate_limited` | `github_not_found` | `download_failed` | `download_too_large` | `extraction_failed` | `decompression_bomb` | `no_skills_found` | `sync_not_implemented` | `internal_error`

**SkillSyncSkillErrorCode**: `skill_parse_failed` | `skill_name_missing` | `duplicate_skill_name` | `file_too_large` | `too_many_files` | `write_failed`

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
   - GitHub → Settings → Developer settings → GitHub Apps → New GitHub App
   - Set Callback URL to `https://<your-domain>/api/v1/skill-sync-sources/{source_id}/oauth/callback`
   - Enable "Request user authorization (OAuth) during installation"

2. **Set permissions**: Repository permissions → Contents → **Read-only**

3. **Generate client secret** on the App settings page

4. **Install the App** on the target org/user account, granting access to specific repositories

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
   → GET /api/v1/skill-sync-sources/{source_id}/oauth/callback
     ?code={authorization_code}
     &state={state}

6. Server exchanges code for tokens:
   → POST https://github.com/login/oauth/access_token
     client_id, client_secret, code, redirect_uri, code_verifier

7. Server stores encrypted tokens (AES-CBC) in MongoDB Token collection

8. Server redirects to frontend:
   → {registry_client_url}/skill-sync-sources/{source_id}?status=syncing
```

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

- When `githubAppClientId` or `githubAppClientSecret` is changed via `PUT`, all stored tokens for the source are deleted
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
