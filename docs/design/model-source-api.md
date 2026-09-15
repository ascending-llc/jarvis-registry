# Model Source Management API

Catalog, CRUD, and gateway-selection API for cloud-hosted LLMs (the "Model Gateway").
A **ModelSource** is a catalog entry describing one model hosted on AWS Bedrock or
Azure OpenAI plus the connection details needed to reach it. A **ModelGatewaySelection**
singleton records which ModelSource currently backs each of the two global slots
(default workflow/chat model, and embedding model).

## Table of Contents

1. [Concepts](#concepts)
2. [API Route Prefix](#api-route-prefix)
3. [API Endpoints](#api-endpoints)
   - 3.1. [Create Model Source](#1-create-model-source)
   - 3.2. [List Model Sources](#2-list-model-sources)
   - 3.3. [Get Model Source Detail](#3-get-model-source-detail)
   - 3.4. [Update Model Source](#4-update-model-source)
   - 3.5. [Delete Model Source](#5-delete-model-source)
   - 3.6. [Get Gateway Selection](#6-get-gateway-selection)
   - 3.7. [Set Default Workflow Model (Planned — AS-1852)](#7-set-default-workflow-model-planned--as-1852)
   - 3.8. [Set Embedding Model (Planned — AS-1853)](#8-set-embedding-model-planned--as-1853)
4. [Access Control](#access-control)
5. [Data Models](#data-models)
6. [Credentials & Encryption](#credentials--encryption)
7. [Model Metadata](#model-metadata)
8. [Error Response Format](#error-response-format)
9. [Implementation Status](#implementation-status)

---

## Concepts

- **ModelSource** — one entry in the model catalog: display metadata + `mode`
  (`chat` or `embedding`) + a typed, per-provider `providerConfig`. Stored in the
  `model_sources` collection. Soft-deleted (`deletedAt`), never hard-deleted via the API.
- **ModelGatewaySelection** — a **singleton** document (collection `model_gateway_selection`,
  always at most one row) holding two pointer fields: `defaultWorkflowModelSourceId` and
  `embeddingModelSourceId`. It is *not* a usage log — it only records the two current global
  defaults.
- **No ACL — scope only.** Unlike `Federation` / `SkillSyncSource`, a ModelSource is treated
  as a system-wide **config option**, not a per-user-owned resource. There is no
  `RegistryResourceType` entry and no per-record ownership: authorization is enforced entirely
  by `ScopePermissionMiddleware` against `scopes.yml`. Anyone with `models-read` sees every
  model; anyone with `models-write` may modify any model.

---

## API Route Prefix

```
/api/v1
```

(Endpoints are `/api/v1/model-sources...` and `/api/v1/model-gateway/selection...`.)

---

## API Endpoints

### 1. Create Model Source

**Endpoint**: `POST /api/v1/model-sources`
**Scope**: `models-write`

**Request Body (AWS Bedrock)**:
```json
{
  "displayName": "Claude 3.5 Sonnet",
  "description": "Default reasoning model",
  "tags": ["bedrock", "chat"],
  "mode": "chat",
  "providerConfig": {
    "providerType": "aws_bedrock",
    "awsRegion": "us-east-1",
    "modelIdOrArn": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "baseModelId": "anthropic.claude-3-5-sonnet-20240620-v1:0"
  }
}
```

**Request Body (Azure OpenAI)** — `apiKey` is plaintext on input; the service encrypts it
before storage and it is never returned:
```json
{
  "displayName": "Azure GPT-4o",
  "mode": "chat",
  "providerConfig": {
    "providerType": "azure_openai",
    "endpoint": "https://acme.openai.azure.com",
    "deploymentName": "gpt4o-prod",
    "baseModelId": "gpt-4o",
    "apiVersion": "2024-10-21",
    "apiKey": "sk-..."
  }
}
```

**Request Fields**:
- `displayName` (required, string, 1–128 chars)
- `description` (optional, string)
- `tags` (optional, string[])
- `mode` (required, enum): `chat` | `embedding`
- `providerConfig` (required, discriminated on `providerType`): see [Data Models](#data-models)

**Response**: `201 Created` → [`ModelSourceDetailResponse`](#modelsourcedetailresponse)
(no `metadata` on create).

---

### 2. List Model Sources

**Endpoint**: `GET /api/v1/model-sources`
**Scope**: `models-read`

**Query Parameters**:
- `mode` (optional, enum): filter by `chat` | `embedding`
- `providerType` (optional, enum): filter by `aws_bedrock` | `azure_openai`
- `tag` (optional, string): filter by a single tag
- `query` (optional, string): full-text search over `displayName` / `description`
- `page` (optional, int, default 1, ≥1)
- `per_page` (optional, int, default 20, 1–100)

**Response**: `200 OK`
```json
{
  "modelSources": [
    {
      "id": "000000000000000000000001",
      "displayName": "Claude 3.5 Sonnet",
      "description": "Default reasoning model",
      "tags": ["bedrock", "chat"],
      "mode": "chat",
      "providerType": "aws_bedrock",
      "createdAt": "2026-09-15T08:16:44Z",
      "updatedAt": "2026-09-15T08:16:44Z"
    }
  ],
  "pagination": { "total": 1, "page": 1, "perPage": 20, "totalPages": 1 }
}
```

Results are sorted by `updatedAt` descending and exclude soft-deleted entries. List items are
lightweight — no `providerConfig` and no `metadata` (fetch detail for those).

---

### 3. Get Model Source Detail

**Endpoint**: `GET /api/v1/model-sources/{model_source_id}`
**Scope**: `models-read`

**Response**: `200 OK` → [`ModelSourceDetailResponse`](#modelsourcedetailresponse), including
live [`metadata`](#model-metadata) resolved from `litellm.get_model_info()` and a masked
`providerConfig` (`hasApiKey: true|false` instead of the encrypted key).
`404` if not found or soft-deleted.

```json
{
  "id": "000000000000000000000002",
  "displayName": "Azure text-embedding-3-small",
  "mode": "embedding",
  "providerType": "azure_openai",
  "tags": ["azure", "embedding"],
  "createdAt": "2026-09-15T08:20:24Z",
  "updatedAt": "2026-09-15T08:20:24Z",
  "providerConfig": {
    "providerType": "azure_openai",
    "endpoint": "https://acme.openai.azure.com",
    "deploymentName": "embed-small",
    "baseModelId": "text-embedding-3-small",
    "apiVersion": "2024-10-21",
    "hasApiKey": true
  },
  "metadata": {
    "maxInputTokens": 8191,
    "maxOutputTokens": null,
    "inputCostPerToken": 2e-08,
    "outputCostPerToken": 0.0,
    "supportsPromptCaching": null,
    "unavailableReason": null
  },
  "createdBy": "000000000000000000000009",
  "updatedBy": "000000000000000000000009"
}
```

---

### 4. Update Model Source

**Endpoint**: `PATCH /api/v1/model-sources/{model_source_id}`
**Scope**: `models-write`

Partial update — only the fields present in the body are changed (`displayName`,
`description`, `tags`, `mode`, `providerConfig`).

**Credential handling:** `providerConfig` is replaced as a whole when supplied. For an Azure
config, if `apiKey` is **omitted**, the previously stored encrypted key is **preserved**; if a
new `apiKey` is supplied, it is re-encrypted. (Omitting `providerConfig` entirely leaves the
stored config and key untouched.)

**Response**: `200 OK` → [`ModelSourceDetailResponse`](#modelsourcedetailresponse)
(no `metadata`). `404` if not found/soft-deleted.

---

### 5. Delete Model Source

**Endpoint**: `DELETE /api/v1/model-sources/{model_source_id}`
**Scope**: `models-write`

Soft-delete — sets `deletedAt`; the row remains in Mongo. A subsequent `GET`/`DELETE` on the
same id returns `404`.

**Delete guard:** returns `409 Conflict` when the model is currently referenced by a gateway
selection slot (`defaultWorkflowModelSourceId` or `embeddingModelSourceId`). _(AS-1852 extends
this guard to also block deletion when any `WorkflowNode.model_source_id` references the model.)_

**Response**: `200 OK`
```json
{ "id": "000000000000000000000001", "deletedAt": "2026-09-15T08:16:44Z" }
```

---

### 6. Get Gateway Selection

**Endpoint**: `GET /api/v1/model-gateway/selection`
**Scope**: `models-read`

**Response**: `200 OK` — the current two global default slots. On a fresh deployment (no
selection set yet) both are `null`.
```json
{ "defaultWorkflowModelSourceId": null, "embeddingModelSourceId": null }
```

---

### 7. Set Default Workflow Model (Planned — AS-1852)

**Endpoint**: `PUT /api/v1/model-gateway/selection/default-workflow-model`
**Scope**: `models-write`

Sets `defaultWorkflowModelSourceId`. Requires the target ModelSource to have `mode == chat`
(otherwise `409`). Takes effect on the **next workflow run — no restart** (resolved live).

**Request Body**: `{ "modelSourceId": "<id>" }`

---

### 8. Set Embedding Model (Planned — AS-1853)

**Endpoint**: `PUT /api/v1/model-gateway/selection/embedding-model`
**Scope**: `models-write`

Sets `embeddingModelSourceId`. Requires the target ModelSource to have `mode == embedding`
(otherwise `409`). Takes effect on the **next registry pod restart**, not immediately (the
vector `DatabaseClient` is built once at startup).

**Request Body**: `{ "modelSourceId": "<id>" }`

---

## Access Control

Model sources are **scope-only** — no ACL, no per-record ownership.

| Property | Value |
|----------|-------|
| Resource type | **None** — no `RegistryResourceType` entry |
| Ownership | None — `createdBy` / `updatedBy` are audit-only, not used for authorization |
| Visibility | Any `models-read` holder sees every model source |
| Authorization | `ScopePermissionMiddleware` against `scopes.yml` (unmatched endpoints default-deny) |

**Per-endpoint scope**:

| Endpoint | Required Scope |
|----------|----------------|
| `POST /model-sources` | `models-write` |
| `GET /model-sources` | `models-read` |
| `GET /model-sources/{id}` | `models-read` |
| `PATCH /model-sources/{id}` | `models-write` |
| `DELETE /model-sources/{id}` | `models-write` |
| `GET /model-gateway/selection` | `models-read` |
| `PUT /model-gateway/selection/default-workflow-model` (AS-1852) | `models-write` |
| `PUT /model-gateway/selection/embedding-model` (AS-1853) | `models-write` |

**Group grants** (`scopes.yml` → `group_mappings`):

| Group | `models-read` | `models-write` |
|-------|:---:|:---:|
| `jarvis-registry-admin` | ✅ | ✅ |
| `jarvis-registry-power-user` | ✅ | ✅ |
| `jarvis-registry-user` | ✅ | ❌ |
| `jarvis-registry-read-only` | ✅ | ❌ |

---

## Data Models

### ModelSource

| Field | Type | Notes |
|-------|------|-------|
| `id` | ObjectId | |
| `displayName` | string | |
| `description` | string \| null | |
| `tags` | string[] | |
| `mode` | `ModelSourceMode` | `chat` \| `embedding` |
| `providerConfig` | discriminated union | `AwsBedrockModelConfig` \| `AzureOpenAIModelConfig` |
| `createdBy` / `updatedBy` | string \| null | audit only |
| `createdAt` / `updatedAt` | datetime | |
| `deletedAt` | datetime \| null | soft-delete marker |

Collection `model_sources`; indexes: `(providerConfig.providerType, mode, updatedAt)`,
`(mode, deletedAt)`, and a text index on `(displayName, description)`.

### AwsBedrockModelConfig

| Field | Type | Notes |
|-------|------|-------|
| `providerType` | `"aws_bedrock"` | discriminator |
| `awsRegion` | string | e.g. `us-east-1` |
| `modelIdOrArn` | string | Bedrock model id **or** an Application Inference Profile ARN |
| `baseModelId` | string | canonical foundation-model id, used only for metadata lookup |

No credential fields — AWS access uses the pod's **IRSA** ambient identity.

### AzureOpenAIModelConfig

| Field | Type | Notes |
|-------|------|-------|
| `providerType` | `"azure_openai"` | discriminator |
| `endpoint` | string | `https://{resource}.openai.azure.com` |
| `deploymentName` | string | used for the actual API calls |
| `baseModelId` | string | canonical OpenAI id, used only for metadata lookup |
| `apiVersion` | string | e.g. `2024-10-21` |
| `apiKeyEncrypted` | string \| null | encrypted at rest; **response returns `hasApiKey: bool` instead** |

Primary Azure auth is **Workload Identity** (ambient, no field); `apiKeyEncrypted` is the
optional fallback. Note: the embedding path (AS-1853) supports only the API-key fallback, not
Workload Identity.

### ModelGatewaySelection (singleton)

| Field | Type | Notes |
|-------|------|-------|
| `defaultWorkflowModelSourceId` | ObjectId \| null | current default chat model |
| `embeddingModelSourceId` | ObjectId \| null | current default embedding model |
| `updatedBy` | string \| null | |
| `updatedAt` | datetime | |

### Enums

- `ModelSourceProviderType`: `aws_bedrock`, `azure_openai`
- `ModelSourceMode`: `chat`, `embedding`

---

## Credentials & Encryption

- Only Azure's `apiKey` is a secret. It is submitted in plaintext, encrypted by the service
  with the registry `encryption_key` (same key `SkillSyncSource` uses), and stored as
  `apiKeyEncrypted`. It is never returned — responses expose `hasApiKey: bool`.
- AWS Bedrock stores **no** credentials (IRSA).
- **The registry does not verify cloud-side invoke permission at registration time.** A model
  the pod's IAM role / managed identity cannot actually call can still be registered; the
  failure surfaces later, at invocation time (AS-1852 run / AS-1853 embedding).

---

## Model Metadata

`GET /model-sources/{id}` populates `metadata` live via `litellm.get_model_info()` keyed on
`bedrock/{baseModelId}` or `azure/{baseModelId}`. This reads litellm's in-memory model-cost map
(no network I/O) and is not snapshotted onto the document. When a model is not in litellm's map
(e.g. an AIP ARN, or an unmapped id), metadata degrades gracefully: the fields are `null` and
`unavailableReason` carries the reason — the request still returns `200`, never `500`.

---

## Error Response Format

All error responses use the standard structured shape:

```json
{ "detail": { "error": "not_found", "message": "Model source not found" } }
```

| Status | Meaning |
|--------|---------|
| `403` | Insufficient permissions (missing `models-read` / `models-write`) |
| `404` | Model source not found or soft-deleted |
| `409` | Conflict — delete blocked (model in use), or selection mode mismatch (AS-1852/1853) |
| `422` | Validation error (invalid input / unknown `providerType`) |
| `500` | Internal server error |
