# Federation API

Base URL: `/api/v1`

Authentication:
- `Authorization: Bearer <token>`

Content type:
- `Content-Type: application/json`

## Scope Requirements

- `GET /federations`
- `GET /federations/{federation_id}`
  - Requires `federations-read`
- `POST /federations`
- `PUT /federations/{federation_id}`
- `DELETE /federations/{federation_id}`
- `POST /federations/{federation_id}/sync`
  - Requires `federations-write`
- `POST /federation/agentcore/runtime/sync`
  - Requires `system-ops`

## Common Enums

### providerType

```text
aws_agentcore
azure_ai_foundry
```

Current implementation note:
- `aws_agentcore` is the only supported federation provider in this version
- `azure_ai_foundry` is still reserved in the enum for compatibility, but create, update, and sync currently return `501 Not Implemented`

### federation status

```text
active
deleting
deleted
```

### federation syncStatus

```text
idle
pending
syncing
success
failed
```

`lastSync.status` uses the same enum. It represents the latest sync attempt snapshot for the federation, so it may also be `pending` or `syncing` while a job is in progress.

### federation jobType

```text
full_sync
config_resync
delete_sync
```

### federation job status

```text
pending
syncing
success
failed
```

## Runtime Auth Model

Each discovered runtime (MCP server or A2A agent) carries its own data-plane authentication configuration. This is inferred automatically during discovery and stored on the resource, not on the federation.

### Why per-resource, not per-federation

A single federation can discover runtimes with different authorizer configurations:

- runtime A → IAM
- runtime B → JWT
- runtime C → IAM

Placing `runtimeAccess` on the federation would force all runtimes to share one auth mode, which is incorrect. The correct model is:

- **Federation** = control plane (how to discover runtimes)
- **MCP server / A2A agent** = data plane (how to call that specific runtime)

### runtimeAccess shape on resources

`config.runtimeAccess` stored on each discovered resource:

**IAM mode:**
```json
{
  "config": {
    "runtimeAccess": {
      "mode": "iam"
    }
  }
}
```

**JWT mode:**
```json
{
  "config": {
    "runtimeAccess": {
      "mode": "jwt",
      "jwt": {
        "discoveryUrl": "https://issuer.example.com",
        "audiences": ["jarvis-services"]
      }
    }
  }
}
```

`discoveryUrl` is the OIDC well-known endpoint of the runtime's authorizer. The JWT `iss` claim sent to the runtime is derived from `discoveryUrl` (scheme + host), not from the local auth server URL. `audiences` comes from the runtime's `allowedAudience` configuration.

### providerConfig.runtimeAccess is not supported

Passing `runtimeAccess` inside `providerConfig` will be rejected:

```json
{
  "detail": {
    "error": "invalid_request",
    "message": "providerConfig.runtimeAccess is not supported"
  }
}
```

## Sync Strategy

Each sync run uses a version-aware, minimum-write strategy to maintain MongoDB and Weaviate consistency.

### Three sync scenarios

| Scenario | Condition | MongoDB | Weaviate |
|----------|-----------|---------|----------|
| First sync | Resource not in MongoDB | INSERT | Full build |
| Version unchanged | `runtimeVersion` same as stored | Skip write | Check version; skip if up to date, repair if stale |
| Version changed | `runtimeVersion` differs | UPDATE | Delete + full rebuild |

MCP servers and A2A agents are evaluated independently.

### Sync summary fields

The `summary` object returned in `lastSync` and dry-run responses:

| Field | Description |
|-------|-------------|
| `discoveredMcpServers` | Total MCP runtimes returned by discovery |
| `discoveredAgents` | Total A2A runtimes returned by discovery |
| `createdMcpServers` | Newly inserted into MongoDB |
| `updatedMcpServers` | Updated in MongoDB due to version change |
| `deletedMcpServers` | Removed from MongoDB (no longer discovered) |
| `unchangedMcpServers` | MongoDB skipped; version unchanged |
| `createdAgents` | Newly inserted into MongoDB |
| `updatedAgents` | Updated in MongoDB due to version change |
| `deletedAgents` | Removed from MongoDB (no longer discovered) |
| `unchangedAgents` | MongoDB skipped; version unchanged |
| `skippedAgents` | A2A agents skipped due to path conflict with another federation |
| `errors` | Count of per-resource enrichment errors |
| `errorMessages` | List of per-resource error strings |

### Weaviate sync behavior

Weaviate is a secondary search index rebuilt from MongoDB state. It runs after the MongoDB transaction commits.

- **Changed or missing resources**: delete existing Weaviate docs for that runtime, then full rebuild.
- **Unchanged resources** (version same in MongoDB): compare Weaviate version. If Weaviate is already up to date, skip. If stale or missing, rebuild without deleting first.
- **Weaviate failures**: logged and do not roll back the MongoDB transaction. Weaviate state can be repaired by re-running sync.

---

## 1. Create Federation

`POST /federations`

### Request Body

```json
{
  "providerType": "aws_agentcore",
  "displayName": "AgentCore Prod",
  "description": "Production federation",
  "tags": ["prod", "aws"],
  "providerConfig": {
    "region": "us-east-1",
    "assumeRoleArn": "arn:aws:iam::123456789012:role/demo"
  }
}
```

AWS `resourceTagsFilter` API shape example:

```json
{
  "providerConfig": {
    "resourceTagsFilter": {
      "env": "production",
      "team": "platform"
    }
  }
}
```

### Request Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `providerType` | `string` | Yes | `aws_agentcore` is supported. `azure_ai_foundry` currently returns `501 Not Implemented`. |
| `displayName` | `string` | Yes | 1-200 chars |
| `description` | `string \| null` | No | Federation description |
| `tags` | `string[]` | No | UI classification tags |
| `providerConfig` | `object` | No | Federation-level control-plane config only. For `aws_agentcore`, create may omit `region` and `assumeRoleArn`. Those fields become required during update and sync. `resourceTagsFilter` is optional; when provided, sync only imports runtimes whose AWS tags fully match every key:value pair. `providerConfig.runtimeAccess` is not supported and will be rejected. |

UI note for AWS:
- The form may let users type `env:production, team:platform`
- The frontend must convert that into `providerConfig.resourceTagsFilter` as a JSON object
- The backend does not accept the raw comma-separated string

### Success Response

Status: `201 Created`

Create only stores the federation definition. It does not trigger a sync job.

```json
{
  "id": "federation_demo_id",
  "providerType": "aws_agentcore",
  "displayName": "AgentCore Prod",
  "description": "Production federation",
  "tags": ["prod", "aws"],
  "status": "active",
  "syncStatus": "idle",
  "syncMessage": null,
  "providerConfig": {
    "region": "us-east-1",
    "assumeRoleArn": "arn:aws:iam::123456789012:role/demo"
  },
  "stats": {
    "mcpServerCount": 0,
    "agentCount": 0,
    "toolCount": 0,
    "importedTotal": 0
  },
  "lastSync": null,
  "recentJobs": [],
  "version": 1,
  "createdBy": "user_demo_id",
  "updatedBy": "user_demo_id",
  "createdAt": "2026-03-26T07:20:00Z",
  "updatedAt": "2026-03-26T07:20:10Z"
}
```

### Error Responses

`400 Bad Request`

```json
{
  "detail": {
    "error": "invalid_request",
    "message": "Unsupported federation provider type: some_provider"
  }
}
```

`401 Unauthorized`

```json
{
  "detail": "JWT or session authentication required"
}
```

`403 Forbidden`

```json
{
  "detail": "Insufficient permissions"
}
```

## 2. List Federations

`GET /federations`

### Query Parameters

| Param | Type | Required | Description |
|---|---|---:|---|
| `providerType` | `string` | No | Filter by provider |
| `syncStatus` | `string` | No | Filter by sync status |
| `tag` | `string` | No | Single tag filter |
| `tags` | `string[]` | No | Multi-tag filter |
| `query` | `string` | No | Search display name / description |
| `page` | `number` | No | Default `1` |
| `per_page` | `number` | No | Default `20`, max `100` |

Compatibility notes:
- `keyword` is still accepted as a deprecated alias for `query`
- `pageSize` is still accepted as a deprecated alias for `per_page`

Response note:
- `lastSync` reflects the latest sync attempt snapshot for the federation
- when a sync job is queued or running, `lastSync.status` may be `pending` or `syncing`
- if a sync attempt fails before the apply phase, `lastSync` is still updated to that failed attempt instead of preserving the older success snapshot

### Success Response

Status: `200 OK`

```json
{
  "federations": [
    {
      "id": "federation_demo_id",
      "providerType": "aws_agentcore",
      "displayName": "AgentCore Prod",
      "description": "Production federation",
      "tags": ["prod", "aws"],
      "status": "active",
      "syncStatus": "success",
      "syncMessage": null,
      "stats": {
        "mcpServerCount": 2,
        "agentCount": 1,
        "toolCount": 14,
        "importedTotal": 3
      },
      "lastSync": {
        "jobId": "job_demo_id",
        "jobType": "full_sync",
        "status": "success",
        "startedAt": "2026-03-26T07:20:00Z",
        "finishedAt": "2026-03-26T07:20:10Z",
        "summary": {
          "discoveredMcpServers": 2,
          "discoveredAgents": 1,
          "createdMcpServers": 2,
          "updatedMcpServers": 0,
          "deletedMcpServers": 0,
          "unchangedMcpServers": 0,
          "createdAgents": 1,
          "updatedAgents": 0,
          "deletedAgents": 0,
          "unchangedAgents": 0,
          "skippedAgents": 0,
          "errors": 0,
          "errorMessages": []
        }
      },
      "createdAt": "2026-03-26T07:20:00Z",
      "updatedAt": "2026-03-26T07:20:10Z"
    }
  ],
  "pagination": {
    "total": 1,
    "page": 1,
    "perPage": 20,
    "totalPages": 1
  }
}
```

## 3. Get Federation Detail

`GET /federations/{federation_id}`

### Path Parameters

| Param | Type | Required | Description |
|---|---|---:|---|
| `federation_id` | `string` | Yes | Mongo ObjectId |

### Success Response

Status: `200 OK`

Response shape is the same as `POST /federations`.

### Error Responses

`404 Not Found`

```json
{
  "detail": {
    "error": "not_found",
    "message": "Federation not found"
  }
}
```

## 4. Update Federation

`PUT /federations/{federation_id}`

### Request Body

```json
{
  "displayName": "AgentCore Prod Updated",
  "description": "Updated description",
  "tags": ["prod", "aws", "core"],
  "providerConfig": {
    "region": "us-east-1",
    "assumeRoleArn": "arn:aws:iam::123456789012:role/demo"
  },
  "version": 1,
  "syncAfterUpdate": true
}
```

AWS `resourceTagsFilter` API shape example:

```json
{
  "providerConfig": {
    "region": "us-east-1",
    "assumeRoleArn": "arn:aws:iam::123456789012:role/demo",
    "resourceTagsFilter": {
      "env": "production",
      "team": "platform"
    }
  }
}
```

### Request Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `displayName` | `string` | Yes | 1-200 chars |
| `description` | `string \| null` | No | Description |
| `tags` | `string[]` | No | UI tags |
| `providerConfig` | `object` | No | Provider-level control-plane config. For `aws_agentcore`, both `region` and `assumeRoleArn` are required. `providerConfig.runtimeAccess` is not supported and will be rejected. |
| `version` | `number` | Yes | Optimistic lock version |
| `syncAfterUpdate` | `boolean` | No | Default `true`. When `true` and `providerConfig` changed, a full sync is triggered after the update. |

### Success Response

Status: `200 OK`

Response shape is the same as `POST /federations`.

### Error Responses

`400 Bad Request`

```json
{
  "detail": {
    "error": "invalid_request",
    "message": "AWS AgentCore federation requires providerConfig.region, providerConfig.assumeRoleArn"
  }
}
```

`404 Not Found`

```json
{
  "detail": {
    "error": "not_found",
    "message": "Federation not found"
  }
}
```

`409 Conflict`

```json
{
  "detail": {
    "error": "conflict",
    "message": "Federation version conflict"
  }
}
```

`409 Conflict`

```json
{
  "detail": {
    "error": "conflict",
    "message": "Federation in status 'deleting' cannot be updated"
  }
}
```

`501 Not Implemented`

```json
{
  "detail": {
    "error": "not_implemented",
    "message": "Azure AI Foundry federation sync is not implemented yet"
  }
}
```

`502 Bad Gateway`

```json
{
  "detail": {
    "error": "external_service_error",
    "message": "Failed to list AgentCore runtimes in us-east-1: Token has expired and refresh failed"
  }
}
```

## 5. Sync Federation

`POST /federations/{federation_id}/sync`

Triggers a full sync. Requires the calling user to have **EDIT** permission on the federation.

### Request Body

```json
{
  "dryRun": false,
  "reason": "manual refresh"
}
```

### Request Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `dryRun` | `boolean` | No | When `true`, perform discovery and diff only. No job is created and no data is written. Default `false`. |
| `reason` | `string \| null` | No | Manual trigger reason |

For `aws_agentcore`, sync validates the stored federation config before discovery starts. Both `providerConfig.region` and `providerConfig.assumeRoleArn` must be present.

Runtime auth (`runtimeAccess`) is not read from `providerConfig`. It is inferred from each runtime's `authorizerConfiguration` during discovery and stored per resource.

### Sync Flow (dryRun=false)

```
1. Validate federation config
2. Discover runtimes from AgentCore control plane
   - For each runtime: infer auth mode (IAM or JWT) from authorizerConfiguration
   - Enrich each runtime (fetch tools, agent card) using its own auth
3. Apply mutations in a single MongoDB transaction
   - New runtimes → INSERT
   - Version changed → UPDATE
   - Version unchanged → skip MongoDB write (log only)
   - Stale runtimes (no longer discovered) → DELETE
4. Update federation stats and lastSync in the same transaction
5. Rebuild Weaviate search index (outside transaction)
   - Changed/deleted/missing runtimes → delete + rebuild
   - Unchanged runtimes → check Weaviate version; rebuild only if stale
```

### Success Response

Status: `200 OK`

```json
{
  "id": "job_demo_id",
  "federationId": "federation_demo_id",
  "jobType": "full_sync",
  "status": "success",
  "phase": "completed",
  "startedAt": "2026-03-26T07:21:00Z",
  "finishedAt": "2026-03-26T07:21:05Z"
}
```

### Partial Success Response

When some runtimes fail enrichment but others succeed, the job and federation are marked `failed`. The `lastSync.summary` includes per-resource error details:

```json
{
  "id": "job_demo_id",
  "federationId": "federation_demo_id",
  "jobType": "full_sync",
  "status": "failed",
  "phase": "failed",
  "startedAt": "2026-03-26T07:21:00Z",
  "finishedAt": "2026-03-26T07:21:08Z"
}
```

The federation's `lastSync.summary` will contain:

```json
{
  "errors": 1,
  "errorMessages": ["A2A agent pharmacy_fraud_a2a: enrichment failed: 403 Forbidden"]
}
```

### Dry-Run Success Response

Status: `200 OK`

```json
{
  "dryRun": true,
  "providerType": "aws_agentcore",
  "providerConfig": {
    "region": "us-east-1",
    "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"
  },
  "summary": {
    "discoveredMcpServers": 2,
    "discoveredAgents": 1,
    "createdMcpServers": 0,
    "updatedMcpServers": 1,
    "deletedMcpServers": 0,
    "unchangedMcpServers": 1,
    "createdAgents": 0,
    "updatedAgents": 0,
    "deletedAgents": 0,
    "unchangedAgents": 1,
    "skippedAgents": 0,
    "errors": 0,
    "errorMessages": []
  },
  "message": null
}
```

When `dryRun=true`:
- no `FederationSyncJob` is created
- federation `syncStatus`, `lastSync`, `stats`, and child resources are unchanged
- provider discovery and runtime enrichment operate on temporary in-memory resources only
- runtime type transitions such as `MCP → A2A` or `A2A → MCP` are previewed in the diff only; persisted child resources are not mutated during discovery
- vector sync is not executed

### Error Responses

`400 Bad Request`

```json
{
  "detail": {
    "error": "invalid_request",
    "message": "AWS AgentCore federation requires providerConfig.region, providerConfig.assumeRoleArn"
  }
}
```

`403 Forbidden`

```json
{
  "detail": "Insufficient permissions"
}
```

`404 Not Found`

```json
{
  "detail": {
    "error": "not_found",
    "message": "Federation not found"
  }
}
```

`409 Conflict`

```json
{
  "detail": {
    "error": "conflict",
    "message": "Federation in sync status 'syncing' cannot start a new sync"
  }
}
```

`409 Conflict`

```json
{
  "detail": {
    "error": "conflict",
    "message": "Federation in status 'disabled' cannot be synced"
  }
}
```

`501 Not Implemented`

```json
{
  "detail": {
    "error": "not_implemented",
    "message": "Azure AI Foundry federation sync is not implemented yet"
  }
}
```

`502 Bad Gateway`

```json
{
  "detail": {
    "error": "external_service_error",
    "message": "Failed to list AgentCore runtimes in us-east-1: Token has expired and refresh failed"
  }
}
```

## 6. Delete Federation

`DELETE /federations/{federation_id}`

### Success Response

Status: `200 OK`

```json
{
  "federationId": "federation_demo_id",
  "jobId": "delete_job_demo_id",
  "status": "deleted"
}
```

### Error Responses

`404 Not Found`

```json
{
  "detail": {
    "error": "not_found",
    "message": "Federation not found"
  }
}
```

`409 Conflict`

```json
{
  "detail": {
    "error": "conflict",
    "message": "Federation in status 'disabled' cannot be deleted"
  }
}
```

## 7. Manual AgentCore Runtime Sync

This is the legacy manual AgentCore runtime sync endpoint, not federation CRUD.

`POST /federation/agentcore/runtime/sync`

### Request Body

```json
{
  "dryRun": false,
  "awsRegion": "us-east-1",
  "runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/demo"
}
```

### Request Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `dryRun` | `boolean` | No | Preview only, default `false` |
| `awsRegion` | `string \| null` | No | Optional region override. If omitted, uses the configured default AgentCore region. |
| `runtimeArn` | `string \| null` | No | Optional single runtime sync. If omitted, scans all AgentCore runtimes in the selected region. |

### Success Response

Status: `200 OK`

```json
{
  "runtime_filter_count": 1,
  "discovered": {
    "mcp_servers": 1,
    "a2a_agents": 0,
    "skipped_runtimes": 0
  },
  "created": {
    "mcp_servers": 1,
    "a2a_agents": 0
  },
  "updated": {
    "mcp_servers": 0,
    "a2a_agents": 0
  },
  "deleted": {
    "mcp_servers": 0,
    "a2a_agents": 0
  },
  "skipped": {
    "mcp_servers": 0,
    "a2a_agents": 0
  },
  "errors": [],
  "mcp_servers": [
    {
      "action": "created",
      "server_name": "demo-runtime",
      "server_id": "server_demo_id",
      "changes": ["new server"],
      "error": null,
      "agent_name": null,
      "agent_id": null
    }
  ],
  "a2a_agents": [],
  "skipped_runtimes": [],
  "duration_seconds": 0.52
}
```

### Error Responses

`400 Bad Request`

```json
{
  "detail": {
    "error": "invalid_request",
    "message": "runtime_arn is required"
  }
}
```

`403 Forbidden`

```json
{
  "detail": "Insufficient permissions"
}
```

`500 Internal Server Error`

```json
{
  "detail": {
    "error": "internal_error",
    "message": "AgentCore runtime sync failed: ..."
  }
}
```

## Frontend Notes

- `tags` is used for federation list classification and filtering.
- `providerConfig` stores provider-level control-plane configuration only. It does not store runtime auth.
- `assumeRoleArn` belongs to AWS federation `providerConfig`. It controls control-plane access for this federation and must not be stored on MCP servers or A2A agents.
- `providerConfig.runtimeAccess` is not supported and will be rejected with `400`. Runtime auth is inferred per discovered resource during sync.
- Creating a federation does not trigger a sync job automatically.
- For `aws_agentcore`, create may save an incomplete provider config, but update and sync require both `providerConfig.region` and `providerConfig.assumeRoleArn`.
- For `aws_agentcore`, `providerConfig.resourceTagsFilter` is applied during sync as an AND filter. A runtime is imported only if all configured tag key:value pairs match its AWS resource tags.
- The UI-friendly string form `env:production, team:platform` must be converted by the frontend into `{ "env": "production", "team": "platform" }`.
- `toolCount` is returned in federation stats and can be displayed directly.
- `POST /federations/{federation_id}/sync` returns a job summary, not the full federation detail.
- `unchangedMcpServers` and `unchangedAgents` in the summary mean the runtime was discovered but its version matched what is already stored — MongoDB was not written. Weaviate consistency is still checked and repaired if stale.
- Per-resource enrichment errors (e.g. IAM 403, JWT 401) do not abort the sync. They are captured in `summary.errorMessages` and mark the job as `failed`, but successfully enriched resources are still applied.
- `azure_ai_foundry` remains in the provider enum for compatibility but is not yet implemented.
