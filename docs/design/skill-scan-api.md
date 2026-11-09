# Skill Security Scan API

Read-only and manual-trigger API for the **advisory security scan** of skills stored in
Registry, plus the admin control that picks the LLM used for scanning. A frontend engineer
can build the scan UI against this document alone, without reading backend code.

**What a scan is.** Registry scans the skills it can fully read with existing OSS scanners
(Cisco `cisco-ai-skill-scanner`, NVIDIA SkillSpector), orchestrated by a separate worker.
Scans are **advisory**: they flag things for a human to investigate before installing a skill
with the CLI. False positives are expected, and findings from different tools are shown side
by side, **not** deduplicated. Registry never runs the scan inline — it queues a job and reads
the results the worker writes back; the UI learns a scan finished by **re-fetching** (there is
no push and no polling contract).

## Table of Contents

1. [Overview](#1-overview)
2. [Route prefix](#2-route-prefix)
3. [Endpoints](#3-endpoints)
   - 3.1 [GET /skills/{skill_id}/scan](#31-get-skillsskill_idscan)
   - 3.2 [POST /skills/{skill_id}/scan](#32-post-skillsskill_idscan)
   - 3.3 [GET /skills (scanSummary per item)](#33-get-skills-scansummary-per-item)
   - 3.4 [PUT /model-gateway/selection/skill-scan-model](#34-put-model-gatewayselectionskill-scan-model)
   - 3.5 [GET /model-gateway/selection (skillScanModelSourceId)](#35-get-model-gatewayselection-skillscanmodelsourceid)
4. [Data models](#4-data-models)
5. [Enums](#5-enums)
6. [Semantics the UI must respect](#6-semantics-the-ui-must-respect)
7. [Access control](#7-access-control)

---

## 1. Overview

- **Advisory, never proof.** A finding raises a concern; it does not prove the skill is
  malicious. False positives are accepted. Results from different tools are independent and are
  **not** deduplicated — the same issue may appear twice.
- **Asynchronous.** Scans run in a separate worker pod. The API returns the current state
  immediately. There is **no push notification and no polling contract** — the UI re-fetches
  `GET /skills/{id}/scan` (or the `scanSummary` on the list) to see progress and completion.
- **Triggers.** Registry **content writes** (create, update, upsert/delete file, GitHub sync)
  enqueue a scan automatically. Edits made in **Jarvis Chat** write MongoDB directly and do
  **not** enqueue; their results go stale and surface as `stale: true`. A user with EDIT can
  rescan manually with `POST`.
- **Skipped skills.** A skill with a supporting file Registry cannot read is never scanned and
  reports a `skipped` tool result with reason `chat_supporting_files_unsupported`. That is every
  Chat-created skill with supporting files, plus any Registry skill to which Chat later added a
  file — those bytes live in Chat's external storage.
- **Not backfilled.** Existing skills are not scanned at rollout; they report `not_scanned`
  until the next trigger.

---

## 2. Route prefix

All paths below are relative to **`/api/v1`**.

Scan endpoints live under the existing Skills API (`/skills`) and Model Gateway API
(`/model-gateway`); they share those APIs' auth, scope and error conventions.

---

## 3. Endpoints

| Endpoint | Scope | Skill ACL | Success |
|---|---|---|---|
| `GET /skills/{skill_id}/scan` | `skills-read` | VIEW | `200` |
| `POST /skills/{skill_id}/scan` | `skills-write` | EDIT | `202` queued · `200` already up to date |
| `GET /skills` (existing; adds `scanSummary` per item) | `skills-read` | VIEW per item | `200` |
| `PUT /model-gateway/selection/skill-scan-model` | `models-write` | — | `200` |
| `GET /model-gateway/selection` (existing; adds `skillScanModelSourceId`) | `models-read` | — | `200` |

`{skill_id}` is a 24-character hex MongoDB ObjectId. A value that is not a valid ObjectId is
rejected by FastAPI with **`422`** before the handler runs.

### Error detail shape (read this first)

Two different `detail` shapes appear, depending on the route:

- **Skill routes** (`/skills/...`) return a **plain string** `detail` for 401/403/404/503, and a
  **`{error, message}` object** only for 500.
- **Skill-scan-model route** returns a **`{error, message}` object** `detail` for 404/409/500.
- **Scope failures** (missing scope, enforced by middleware before the handler) always return
  the plain string `{"detail": "Insufficient permissions"}`.

- **`422`** (FastAPI request validation) returns an **array** of validation errors as `detail`.
  A 422 means the client sent a bad request (a malformed `skill_id`, or a missing body key).

**Frontend rule:** `detail` may be a string, an `{error, message}` object or (on `422`) an array.
Render:

```ts
typeof detail === "string"
  ? detail
  : Array.isArray(detail)
    ? "Invalid request"
    : detail.message;
```

---

### 3.1 `GET /skills/{skill_id}/scan`

Return the full scan state for one skill: the job (if any), the summary of the last **completed**
scan, and the per-tool results for that scan's content digest.

- **Scope:** `skills-read`  ·  **ACL:** VIEW on the skill  ·  **Success:** `200`
- No path body. Response body: [`SkillScanResponse`](#skillscanresponse).

**`not_scanned`** — never triggered (or not yet, pre-rollout skill):

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 3,
  "scanStatus": "not_scanned",
  "stale": false,
  "outdatedTool": false,
  "job": null,
  "summary": null,
  "results": []
}
```

**`pending`** — a scan is queued; the previous scan's `summary`/`results` are still shown
(here the skill changed since, so `stale: true`):

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 4,
  "scanStatus": "pending",
  "stale": true,
  "outdatedTool": false,
  "job": {
    "status": "pending",
    "rerequested": false,
    "triggerType": "auto",
    "requestedAt": "2026-11-06T10:15:00Z",
    "startedAt": null,
    "finishedAt": null,
    "attemptCount": 0,
    "errorCode": null
  },
  "summary": {
    "contentDigest": "00000000000000000000000000000000000000000000000000000000000000a1",
    "skillVersion": 3,
    "status": "succeeded",
    "maxSeverity": "medium",
    "severityCounts": { "medium": 1, "low": 2 },
    "scannedAt": "2026-11-05T22:00:00Z"
  },
  "results": [
    {
      "tool": "cisco-ai-skill-scanner",
      "toolVersion": "0.4.1",
      "status": "succeeded",
      "statusReason": null,
      "outdatedTool": false,
      "findings": [],
      "findingsTruncated": false,
      "llmProviderType": "aws_bedrock",
      "llmModelId": "anthropic.claude-3-5-sonnet-20241022-v2:0",
      "inputTokens": 1820,
      "outputTokens": 240,
      "durationMs": 7400,
      "errorMessage": null,
      "toolMetadata": {},
      "scannedAt": "2026-11-05T22:00:00Z"
    }
  ]
}
```

**`scanning`** — a worker claimed the job (`startedAt` set):

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 5,
  "scanStatus": "scanning",
  "stale": false,
  "outdatedTool": false,
  "job": {
    "status": "scanning",
    "rerequested": false,
    "triggerType": "manual",
    "requestedAt": "2026-11-06T11:00:00Z",
    "startedAt": "2026-11-06T11:00:04Z",
    "finishedAt": null,
    "attemptCount": 1,
    "errorCode": null
  },
  "summary": null,
  "results": []
}
```

**`completed`** — two tool results: one `succeeded` with a `high` finding, one `partial` whose
LLM pass did not run (`statusReason: "llm_unavailable"`). The aggregate `summary.status` is
`partial`:

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 5,
  "scanStatus": "completed",
  "stale": false,
  "outdatedTool": false,
  "job": {
    "status": "done",
    "rerequested": false,
    "triggerType": "manual",
    "requestedAt": "2026-11-06T11:00:00Z",
    "startedAt": "2026-11-06T11:00:04Z",
    "finishedAt": "2026-11-06T11:01:12Z",
    "attemptCount": 1,
    "errorCode": null
  },
  "summary": {
    "contentDigest": "00000000000000000000000000000000000000000000000000000000000000b2",
    "skillVersion": 5,
    "status": "partial",
    "maxSeverity": "high",
    "severityCounts": { "high": 1 },
    "scannedAt": "2026-11-06T11:01:12Z"
  },
  "results": [
    {
      "tool": "cisco-ai-skill-scanner",
      "toolVersion": "0.4.1",
      "status": "succeeded",
      "statusReason": null,
      "outdatedTool": false,
      "findings": [
        {
          "tool": "cisco-ai-skill-scanner",
          "ruleId": "SHADOW_FILE_READ",
          "severity": "high",
          "category": "credential_access",
          "toolCategory": "secrets",
          "title": "Reads /etc/shadow",
          "message": "A bundled script reads /etc/shadow, which holds password hashes.",
          "filePath": "scripts/collect.sh",
          "startLine": 12,
          "endLine": 12,
          "snippet": "cat /etc/shadow > /tmp/out",
          "remediation": "Remove the read of /etc/shadow.",
          "confidence": 0.92
        }
      ],
      "findingsTruncated": false,
      "llmProviderType": "aws_bedrock",
      "llmModelId": "anthropic.claude-3-5-sonnet-20241022-v2:0",
      "inputTokens": 2110,
      "outputTokens": 320,
      "durationMs": 8800,
      "errorMessage": null,
      "toolMetadata": {},
      "scannedAt": "2026-11-06T11:01:10Z"
    },
    {
      "tool": "skillspector",
      "toolVersion": "1.2.0",
      "status": "partial",
      "statusReason": "llm_unavailable",
      "outdatedTool": false,
      "findings": [],
      "findingsTruncated": false,
      "llmProviderType": null,
      "llmModelId": null,
      "inputTokens": null,
      "outputTokens": null,
      "durationMs": 3100,
      "errorMessage": null,
      "toolMetadata": {},
      "scannedAt": "2026-11-06T11:01:12Z"
    }
  ]
}
```

**`completed` with `stale: true`** — the skill changed (usually in Chat) after this scan;
`summary.skillVersion` (5) is behind `skillVersion` (6):

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 6,
  "scanStatus": "completed",
  "stale": true,
  "outdatedTool": false,
  "job": {
    "status": "done",
    "rerequested": false,
    "triggerType": "auto",
    "requestedAt": "2026-11-06T09:00:00Z",
    "startedAt": "2026-11-06T09:00:03Z",
    "finishedAt": "2026-11-06T09:00:51Z",
    "attemptCount": 1,
    "errorCode": null
  },
  "summary": {
    "contentDigest": "00000000000000000000000000000000000000000000000000000000000000c3",
    "skillVersion": 5,
    "status": "succeeded",
    "maxSeverity": "low",
    "severityCounts": { "low": 1 },
    "scannedAt": "2026-11-06T09:00:51Z"
  },
  "results": [
    {
      "tool": "skillspector",
      "toolVersion": "1.2.0",
      "status": "succeeded",
      "statusReason": null,
      "outdatedTool": false,
      "findings": [],
      "findingsTruncated": false,
      "llmProviderType": "azure_openai",
      "llmModelId": "gpt-4o",
      "inputTokens": 1500,
      "outputTokens": 90,
      "durationMs": 5200,
      "errorMessage": null,
      "toolMetadata": {},
      "scannedAt": "2026-11-06T09:00:51Z"
    }
  ]
}
```

**`completed` with an `outdatedTool: true` result** — the stored result came from a scanner
version or LLM that is no longer configured; top-level `outdatedTool` is true because at least
one result is outdated. It is still shown until the next scan replaces it:

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 5,
  "scanStatus": "completed",
  "stale": false,
  "outdatedTool": true,
  "job": {
    "status": "done",
    "rerequested": false,
    "triggerType": "auto",
    "requestedAt": "2026-10-20T08:00:00Z",
    "startedAt": "2026-10-20T08:00:02Z",
    "finishedAt": "2026-10-20T08:00:44Z",
    "attemptCount": 1,
    "errorCode": null
  },
  "summary": {
    "contentDigest": "00000000000000000000000000000000000000000000000000000000000000d4",
    "skillVersion": 5,
    "status": "succeeded",
    "maxSeverity": "medium",
    "severityCounts": { "medium": 1 },
    "scannedAt": "2026-10-20T08:00:44Z"
  },
  "results": [
    {
      "tool": "cisco-ai-skill-scanner",
      "toolVersion": "0.3.0",
      "status": "succeeded",
      "statusReason": null,
      "outdatedTool": true,
      "findings": [
        {
          "tool": "cisco-ai-skill-scanner",
          "ruleId": "NET_EXFIL",
          "severity": "medium",
          "category": "data_exfiltration",
          "toolCategory": null,
          "title": "Posts data to an external host",
          "message": "The skill sends collected text to a hard-coded URL.",
          "filePath": "SKILL.md",
          "startLine": null,
          "endLine": null,
          "snippet": null,
          "remediation": null,
          "confidence": null
        }
      ],
      "findingsTruncated": false,
      "llmProviderType": "aws_bedrock",
      "llmModelId": "anthropic.claude-3-haiku-20240307-v1:0",
      "inputTokens": 900,
      "outputTokens": 60,
      "durationMs": 4100,
      "errorMessage": null,
      "toolMetadata": {},
      "scannedAt": "2026-10-20T08:00:44Z"
    }
  ]
}
```

**`failed`** — the job exhausted its attempts (crash / lease loss), distinct from a `failed` tool
result. `summary`/`results` are whatever the last completed scan left (here none):

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 5,
  "scanStatus": "failed",
  "stale": false,
  "outdatedTool": false,
  "job": {
    "status": "failed",
    "rerequested": false,
    "triggerType": "auto",
    "requestedAt": "2026-11-06T11:00:00Z",
    "startedAt": "2026-11-06T11:00:04Z",
    "finishedAt": "2026-11-06T11:10:04Z",
    "attemptCount": 3,
    "errorCode": "lease_lost"
  },
  "summary": null,
  "results": []
}
```

> A `skipped` skill looks like a normal `completed` response whose tool results all have
> `"status": "skipped"` and `"statusReason": "chat_supporting_files_unsupported"`.

**Errors** (`detail` is a plain **string** except for 500):

| Status | When | `detail` |
|---|---|---|
| `401` | Not authenticated | string, e.g. `"Not authenticated"` |
| `403` | Missing `skills-read` scope | `"Insufficient permissions"` |
| `403` | Has scope but no VIEW on the skill | `"You do not have VIEW permissions for this resource."` |
| `404` | Skill does not exist | `"Skill not found"` |
| `422` | `skill_id` is not a valid ObjectId | FastAPI validation error object |
| `503` | ACL lookup temporarily unavailable | `"Permission check temporarily unavailable. Please try again later."` |
| `500` | Unexpected failure | `{"error": "internal_error", "message": "Internal server error"}` |

---

### 3.2 `POST /skills/{skill_id}/scan`

Request a manual scan. The response body is the **same** `SkillScanResponse` as `GET`.

- **Scope:** `skills-write`  ·  **ACL:** EDIT on the skill
- **`202 Accepted`** — a scan was queued (or a concurrent request already queued one). The body
  reflects the just-enqueued state, typically `scanStatus: "pending"`.
- **`200 OK`** — nothing to queue: every enabled tool already has a `succeeded` or `skipped`
  result for the skill's current content. The body is the skill's **current** scan state,
  whatever its `scanStatus`. This is usually `completed`, but it can be `scanning` if a worker has
  written every result and not yet closed the job, or `failed` if the job failed after writing
  them. The body is never empty. Read `scanStatus` / `summary`, not just the status code.

**`202` (queued):**

```json
{
  "skillId": "0000000000000000000000a1",
  "skillVersion": 7,
  "scanStatus": "pending",
  "stale": false,
  "outdatedTool": false,
  "job": {
    "status": "pending",
    "rerequested": false,
    "triggerType": "manual",
    "requestedAt": "2026-11-06T12:00:00Z",
    "startedAt": null,
    "finishedAt": null,
    "attemptCount": 0,
    "errorCode": null
  },
  "summary": null,
  "results": []
}
```

**`200` (nothing to queue):** same shape as a `GET` response (see 3.1), typically the
`completed` example.

**Errors:** same table as [3.1](#31-get-skillsskill_idscan), except the scope is `skills-write`
and the ACL check requires **EDIT** (`403` detail `"You do not have EDIT permissions for this
resource."`).

---

### 3.3 `GET /skills` (scanSummary per item)

The existing list endpoint. Each item gains a `scanSummary` field, a compact
[`SkillScanBriefResponse`](#skillscanbriefresponse). This endpoint **always populates it**, for
every item. A skill with no job reports `scanStatus: "not_scanned"`, not `null`. Scope
`skills-read`, VIEW filtered per item (unchanged from today).

One complete item (a `SkillMetadataResponse`) with a completed scan:

```json
{
  "id": "0000000000000000000000a1",
  "name": "repo-indexer",
  "displayTitle": null,
  "description": "Index a git repo",
  "category": "",
  "tags": [],
  "version": 5,
  "fileCount": 1,
  "alwaysApply": false,
  "enabled": true,
  "author": "0000000000000000000000f1",
  "authorName": "Jane Doe",
  "source": "inline",
  "sourceMetadata": null,
  "createdByRegistry": true,
  "origin": { "kind": "registry", "provider": null },
  "permissions": { "VIEW": true, "EDIT": true, "DELETE": true, "SHARE": true },
  "updatedAt": "2026-11-06T10:59:00Z",
  "scanSummary": {
    "scanStatus": "completed",
    "status": "succeeded",
    "maxSeverity": "high",
    "severityCounts": { "high": 1, "low": 2 },
    "stale": false,
    "outdatedTool": false,
    "scannedAt": "2026-11-06T11:01:12Z"
  }
}
```

`scanSummary` of a skill that has never been scanned (no job):

```json
{
  "scanStatus": "not_scanned",
  "status": null,
  "maxSeverity": null,
  "severityCounts": {},
  "stale": false,
  "outdatedTool": false,
  "scannedAt": null
}
```

The fields have the same defaults when a job exists but no scan has completed yet; only
`scanStatus` differs (`pending`, `scanning` or `failed`).

---

### 3.4 `PUT /model-gateway/selection/skill-scan-model`

Set the ModelSource used as the LLM for skill scanning. It must be a `chat` source whose
`providerType` is `aws_bedrock` or `azure_openai`. A `null` `modelSourceId` clears the slot (see
[Fallback when the slot is empty](#fallback-when-the-slot-is-empty)).

- **Scope:** `models-write`  ·  no skill ACL  ·  **Success:** `200`
- Request body: [`SetSkillScanModelRequest`](#setskillscanmodelrequest). `modelSourceId` is
  **required**: send an explicit `null` to clear the slot. Omitting the key returns `422`; it does
  not clear the slot.
- Response body: [`ModelGatewaySelectionResponse`](#modelgatewayselectionresponse).

Set it:

```json
{ "modelSourceId": "0000000000000000000000c1" }
```

Clear it:

```json
{ "modelSourceId": null }
```

Response:

```json
{
  "defaultWorkflowModelSourceId": "0000000000000000000000c1",
  "embeddingModelSourceId": "0000000000000000000000e2",
  "skillScanModelSourceId": "0000000000000000000000c1"
}
```

**Errors** (`detail` is a **`{error, message}` object**, mirroring `set_default_workflow_model`):

| Status | When | `detail` |
|---|---|---|
| `401` | Not authenticated | string, e.g. `"Not authenticated"` |
| `403` | Missing `models-write` scope | `"Insufficient permissions"` (plain string, from middleware) |
| `404` | ModelSource unknown or soft-deleted | `{"error": "not_found", "message": "..."}` |
| `409` | ModelSource is not a `chat` source (e.g. an `embedding` source), **or** is a `chat` source whose `providerType` is not `aws_bedrock` / `azure_openai` (i.e. `anthropic` or `openai`) | `{"error": "conflict", "message": "..."}` |
| `422` | Body is missing the `modelSourceId` key (or is otherwise malformed) | FastAPI validation error array |
| `500` | Unexpected failure | `{"error": "internal_error", "message": "Internal server error"}` |

#### Frontend note: the scan-model picker

- **List only `chat` sources whose `providerType` is `aws_bedrock` or `azure_openai`**
  (`SKILL_SCAN_PROVIDER_TYPES` in `frontend/src/pages/Models/constants.ts`). Skill content must
  stay in the client's own cloud account (AS-1980 D7), so first-party `anthropic` / `openai`
  sources are never used for scanning. The backend rejects them with `409`.

#### Fallback when the slot is empty

When `skillScanModelSourceId` is `null`, scanning uses `defaultWorkflowModelSourceId` **only if**
that default is a live `chat` source on `aws_bedrock` or `azure_openai`. Otherwise scans run
**without an LLM**, and LLM-backed tools report `status: "partial"` with
`statusReason: "llm_unavailable"`.

**Show this fallback state next to the picker** when the slot is empty:

- the default is Bedrock or Azure: "Using the default workflow model (`<name>`)".
- the default is unset or is `anthropic` / `openai`: "No scan model: scans run without an LLM".

---

### 3.5 `GET /model-gateway/selection` (skillScanModelSourceId)

The existing selection endpoint. The body gains `skillScanModelSourceId` (nullable) alongside the
existing two pointers. Scope `models-read`.

```json
{
  "defaultWorkflowModelSourceId": "0000000000000000000000c1",
  "embeddingModelSourceId": "0000000000000000000000e2",
  "skillScanModelSourceId": null
}
```

`skillScanModelSourceId: null` means no dedicated scan model is set. Scanning then uses
`defaultWorkflowModelSourceId` only if it is Bedrock or Azure, and otherwise runs without an LLM
(see [Fallback when the slot is empty](#fallback-when-the-slot-is-empty)).

---

## 4. Data models

Field names are exactly as returned (camelCase). "Nullable" means the field may be `null`;
"optional" fields always appear but may hold a default (`[]`, `{}`, `false`).

### SkillScanResponse

Body of `GET`/`POST /skills/{id}/scan`.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `skillId` | string | no | Skill ObjectId. |
| `skillVersion` | integer | no | The skill's current `version`. |
| `scanStatus` | [`scanStatus`](#scanstatus) enum | no | API-facing status derived from the job. |
| `stale` | boolean | no | `summary` exists and was produced for an older `skillVersion`. |
| `outdatedTool` | boolean | no | Any result came from a no-longer-configured tool/LLM. |
| `job` | [`SkillScanJobResponse`](#skillscanjobresponse) | yes | The current job; `null` when never triggered. |
| `summary` | [`SkillScanSummaryResponse`](#skillscansummaryresponse) | yes | Summary of the last **completed** scan. |
| `results` | [`SkillScanToolResultResponse`](#skillscantoolresultresponse)[] | no | Per-tool results for `summary.contentDigest`, ordered by tool name. Empty `[]` when no completed scan. |

### SkillScanJobResponse

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `status` | [job `status`](#job-status) enum | no | `pending` / `scanning` / `done` / `failed`. |
| `rerequested` | boolean | no | A new trigger arrived while this job was in flight; it will re-run. |
| `triggerType` | [`triggerType`](#triggertype) enum | no | `auto` (content write) or `manual` (POST). |
| `requestedAt` | datetime | no | When the job was enqueued. |
| `startedAt` | datetime | yes | When a worker claimed it. |
| `finishedAt` | datetime | yes | When it reached a terminal state. |
| `attemptCount` | integer | no | Job-level attempts so far (max 3). |
| `errorCode` | string | yes | Job-level failure code when `status: failed`. |

### SkillScanSummaryResponse

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `contentDigest` | string | no | SHA-256 of the scanned content; the dedup key the `results` belong to. |
| `skillVersion` | integer | no | The `version` this scan ran against. Compare with the top-level `skillVersion` for `stale`. |
| `status` | [result `status`](#result-status) enum | no | Aggregate across tools (`succeeded`/`partial`/`failed`/`skipped`). |
| `maxSeverity` | [`severity`](#severity) enum | yes | Highest severity across findings; `null` when there are none. |
| `severityCounts` | object (severity → integer) | no | Count of findings per severity; defaults to `{}`. |
| `scannedAt` | datetime | no | When this scan completed. |

### SkillScanToolResultResponse

One per enabled tool for the scanned digest.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `tool` | string | no | Tool name, e.g. `cisco-ai-skill-scanner`, `skillspector`. |
| `toolVersion` | string | no | Pinned tool version that produced this result. |
| `status` | [result `status`](#result-status) enum | no | `succeeded` / `partial` / `failed` / `skipped`. |
| `statusReason` | [`statusReason`](#statusreason) enum | yes | Why, when not plain `succeeded`. |
| `outdatedTool` | boolean | no | This result's fingerprint no longer matches the enabled tool. |
| `findings` | [`SkillScanFindingResponse`](#skillscanfindingresponse)[] | no | Findings; defaults to `[]`. |
| `findingsTruncated` | boolean | no | More than 200 findings; only the first 200 are stored. Defaults to `false`. |
| `llmProviderType` | [`providerType`](#llmprovidertype) enum | yes | LLM provider used (`aws_bedrock`/`azure_openai`), when the LLM pass ran. |
| `llmModelId` | string | yes | LLM model id used, when the LLM pass ran. |
| `inputTokens` | integer | yes | LLM input tokens consumed. |
| `outputTokens` | integer | yes | LLM output tokens consumed. |
| `durationMs` | integer | yes | Wall-clock run time of the tool. |
| `errorMessage` | string | yes | Tool-level error text, when `status: failed`. |
| `toolMetadata` | object | no | Free-form tool diagnostics; defaults to `{}`. |
| `scannedAt` | datetime | no | When this result was written (`updatedAt`). |

### SkillScanFindingResponse

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `tool` | string | no | Tool that produced the finding. |
| `ruleId` | string | yes | Tool-native rule identifier. |
| `severity` | [`severity`](#severity) enum | no | Normalized severity. |
| `category` | [`category`](#category) enum | no | Normalized category. |
| `toolCategory` | string | yes | Tool-native category, kept alongside the normalized one. |
| `title` | string | no | Short finding title. |
| `message` | string | no | Human-readable description. |
| `filePath` | string | yes | File the finding is in, relative to the skill root; `SKILL.md` is the main file. |
| `startLine` | integer | yes | First line of the finding. |
| `endLine` | integer | yes | Last line of the finding. |
| `snippet` | string | yes | Offending content, ≤ 500 chars. **Untrusted** — render as text, never as HTML/Markdown. |
| `remediation` | string | yes | Suggested fix, when the tool supplies one. |
| `confidence` | number (float) | yes | Tool confidence, when supplied. |

### SkillScanBriefResponse

`scanSummary` on each `GET /skills` item.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `scanStatus` | [`scanStatus`](#scanstatus) enum | no | Same mapping as the detail response. |
| `status` | [result `status`](#result-status) enum | yes | Aggregate status of the last completed scan; `null` before one completes. |
| `maxSeverity` | [`severity`](#severity) enum | yes | Highest severity across the last completed scan. |
| `severityCounts` | object (severity → integer) | no | Per-severity counts; defaults to `{}`. |
| `stale` | boolean | no | As in the detail response; defaults to `false`. |
| `outdatedTool` | boolean | no | As in the detail response; defaults to `false`. |
| `scannedAt` | datetime | yes | When the last completed scan finished; `null` if none. |

`SkillMetadataResponse.scanSummary` is typed nullable, but `GET /skills` always populates it. A
skill with no job is reported as `scanStatus: "not_scanned"`. Check `scanStatus`, not `null`.

### SetSkillScanModelRequest

Body of `PUT /model-gateway/selection/skill-scan-model`.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `modelSourceId` | string | yes | **Required key.** ModelSource id to use for scanning; an explicit `null` clears the slot. Omitting the key returns `422`. |

### ModelGatewaySelectionResponse

Body of `GET /model-gateway/selection` and the three selection setters.

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `defaultWorkflowModelSourceId` | string | yes | Default chat/workflow model. Scanning falls back to this only if it is Bedrock or Azure. |
| `embeddingModelSourceId` | string | yes | Embedding model (unrelated to scanning). |
| `skillScanModelSourceId` | string | yes | Dedicated scan model. `null` means "use the workflow default if it is Bedrock or Azure, otherwise scan without an LLM". |

---

## 5. Enums

String values exactly as serialized. Use these for display text, sorting and colouring.

### scanStatus

API-facing status on `SkillScanResponse` and `SkillScanBriefResponse`.

| Value | Meaning |
|---|---|
| `not_scanned` | No job exists; never triggered (or pre-rollout). |
| `pending` | A scan is queued, not yet started. |
| `scanning` | A worker is running the scan. |
| `completed` | The job is done (job status `done`); read `summary`/`results`. |
| `failed` | The job exhausted its attempts. |

### job status

`SkillScanJobResponse.status` (the job's own status; `done` maps to `completed` at the
`scanStatus` level).

| Value | Meaning |
|---|---|
| `pending` | Queued, not claimed. |
| `scanning` | Claimed and running. |
| `done` | Finished (surfaces as `scanStatus: "completed"`). |
| `failed` | Attempts exhausted (crash / lease loss), not a tool failure. |

### result status

`status` on `SkillScanToolResultResponse` and the aggregate on the summaries.

| Value | Meaning |
|---|---|
| `succeeded` | The tool ran fully (counts as done). |
| `partial` | The tool ran, but part of the analysis (usually the LLM pass) did not; findings are still valid. |
| `failed` | The tool errored. |
| `skipped` | The tool did not run (counts as done); see `statusReason`. |

### statusReason

Why a result is not plain `succeeded`.

| Value | Meaning |
|---|---|
| `llm_unavailable` | No LLM was configured or usable for this tool. |
| `llm_failed` | An LLM was configured but its pass failed. |
| `llm_skipped_size_limit` | The orchestrator ran the tool without the LLM because the content was too large. |
| `analysis_incomplete` | The tool reports partial coverage of the content. |
| `chat_supporting_files_unsupported` | The skill has a Chat-stored supporting file Registry cannot read, so it was skipped. |
| `tool_error` | The tool itself errored. |
| `timeout` | The tool exceeded its time limit. |
| `parse_error` | The tool's output could not be parsed. |

### severity

`severity` on findings, `maxSeverity` on summaries. **This is the sort/colour order**
(critical highest → info lowest).

| Value | Meaning |
|---|---|
| `critical` | Highest-risk finding. |
| `high` | High risk. |
| `medium` | Moderate risk. |
| `low` | Low risk. |
| `info` | Informational — **not** a security finding. |

### category

Normalized finding category.

| Value | Meaning |
|---|---|
| `prompt_injection` | Content that tries to hijack the agent's instructions. |
| `data_exfiltration` | Sending data to an external destination. |
| `command_execution` | Executing shell / system commands. |
| `credential_access` | Reading secrets, keys or credentials. |
| `obfuscation` | Hidden or encoded content masking intent. |
| `supply_chain` | Risky dependency or package behaviour. |
| `excessive_permissions` | Requesting more access than the task needs. |
| `harmful_content` | Content that could harm the user or others. |
| `social_engineering` | Manipulating the user into unsafe actions. |
| `other` | Does not fit the categories above. |

### triggerType

`SkillScanJobResponse.triggerType`.

| Value | Meaning |
|---|---|
| `auto` | Enqueued by a Registry content write. |
| `manual` | Enqueued by `POST /skills/{id}/scan`. |

### llmProviderType

`llmProviderType` on a tool result (the `ModelSource` provider that backed the LLM pass).

| Value | Meaning |
|---|---|
| `aws_bedrock` | AWS Bedrock-hosted model. |
| `azure_openai` | Azure OpenAI-hosted model. |

---

## 6. Semantics the UI must respect

- **`stale: true`** — the skill changed (usually in Jarvis Chat) after the last scan. The
  `summary`/`results` describe an older version. Offer a manual rescan (needs EDIT).
- **`outdatedTool: true`** (top level or per result) — the result came from a scanner version or
  LLM model that is no longer configured. Repointing the same `modelSourceId` at a different
  deployment (region, endpoint, api version) also counts. It is still shown until the next scan
  replaces it.
- **`summary` reflects the last *completed* scan.** While `scanStatus` is `pending` or
  `scanning`, `summary` and `results` may still hold the **previous** scan — do not assume they
  match the current content.
- **A `partial` result still has valid findings.** It means part of the analysis (usually the
  LLM pass) did not run; `statusReason` says why. Do not hide its findings.
- **Findings are not deduplicated.** Different tools are independent and may describe the same
  issue twice. Group by tool, not by assuming uniqueness.
- **`findingsTruncated: true`** — the tool reported more than 200 findings; only the first 200
  are stored. Tell the user the list is capped.
- **`snippet` is untrusted skill content**, capped at 500 characters. Render it as plain text —
  **never** as HTML or Markdown.
- **`filePath`** is relative to the skill root; `SKILL.md` is the main file.
- **A `POST` that returns `200` did nothing** — every enabled tool already has a `succeeded` or
  `skipped` result for the current content. Only `202` means a new scan was queued.
- **`failed` `scanStatus` (job attempts exhausted) is not the same as a `failed` tool result.**
  Both can be retried with `POST` by a user with EDIT.

---

## 7. Access control

Two layers: an **OAuth scope** (checked by middleware before the handler) and, for the skill
endpoints only, an **ACL permission** on the specific skill (VIEW to read, EDIT to scan).

Scopes come from `registry-pkgs/src/registry_pkgs/scopes.yml` `group_mappings`:

| Endpoint | `jarvis-registry-admin` | `-power-user` | `-user` | `-read-only` |
|---|---|---|---|---|
| `GET /skills/{id}/scan` (`skills-read`) | ✅ | ✅ | ✅ | ✅ |
| `POST /skills/{id}/scan` (`skills-write`) | ✅ | ✅ | ✅ | ❌ |
| `GET /skills` (`skills-read`) | ✅ | ✅ | ✅ | ✅ |
| `PUT /model-gateway/selection/skill-scan-model` (`models-write`) | ✅ | ❌ | ❌ | ❌ |
| `GET /model-gateway/selection` (`models-read`) | ✅ | ✅ | ✅ | ✅ |

- Only `jarvis-registry-admin` has `models-write`, so only an admin can pick the scan model.
- Reading a scan additionally needs **VIEW** on the skill; requesting one needs **EDIT**. Having
  the scope but not the ACL permission returns `403` with a plain-string `detail` naming the
  missing permission.
- `GET /skills` applies VIEW per item, as it does today; a user sees `scanSummary` only for the
  skills they can view.
