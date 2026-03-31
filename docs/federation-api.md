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

### federation jobType

```text
full_sync
config_resync
force_sync
delete_sync
```

### federation job status

```text
pending
syncing
success
failed
```

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
| `providerType` | `string` | Yes | `aws_agentcore` or `azure_ai_foundry` |
| `displayName` | `string` | Yes | 1-200 chars |
| `description` | `string \| null` | No | Federation description |
| `tags` | `string[]` | No | UI classification tags |
| `providerConfig` | `object` | No | Federation-level provider connection config. For `aws_agentcore`, create may omit `region` and `assumeRoleArn`. Those fields become required later during update and sync. `resourceTagsFilter` is optional and, when provided, sync only imports AgentCore runtimes whose AWS resource tags fully match every configured key:value pair. |

UI note for AWS:
- The form may let users type `env:production, team:platform`
- The frontend must convert that text into `providerConfig.resourceTagsFilter` as a JSON object
- The backend does not accept the raw comma-separated string as the stored API shape

### Success Response

Status: `201 Created`

Create only stores the federation definition. It does not trigger an automatic sync job.

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
        "jobType": "initial_sync",
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
          "errors": 0
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
| `providerConfig` | `object` | No | Provider-specific config. For `aws_agentcore`, both `providerConfig.region` and `providerConfig.assumeRoleArn` are required during update. |
| `version` | `number` | Yes | Optimistic lock version |
| `syncAfterUpdate` | `boolean` | No | Default `true` |

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
    "message": "Federation provider azure_ai_foundry is not implemented yet. The sync handler hook is ready; only the Azure discovery adapter is pending."
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

### Request Body

```json
{
  "force": false,
  "reason": "manual refresh"
}
```

### Request Fields

| Field | Type | Required | Description |
|---|---|---:|---|
| `force` | `boolean` | No | Reserved flag, default `false` |
| `reason` | `string \| null` | No | Manual trigger reason |

For `aws_agentcore`, sync validates the stored federation config before discovery starts.
The federation must already have both `providerConfig.region` and `providerConfig.assumeRoleArn`.

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
    "message": "Federation provider azure_ai_foundry is not implemented yet. The sync handler hook is ready; only the Azure discovery adapter is pending."
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
| `awsRegion` | `string \| null` | No | Optional region override. If omitted, the endpoint uses the configured/default AgentCore region. |
| `runtimeArn` | `string \| null` | No | Optional single runtime sync. If omitted, the endpoint scans all AgentCore runtimes in the selected region. |

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
- `providerConfig` stores provider-level configuration, not child resource details.
- `assumeRoleArn` belongs to AWS federation `providerConfig`. It controls control-plane access for this federation and should not be stored on MCP servers or A2A agents.
- Creating a federation does not trigger provider sync automatically.
- For `aws_agentcore`, create may save an incomplete provider config, but update and sync require both `providerConfig.region` and `providerConfig.assumeRoleArn`.
- For `aws_agentcore`, `providerConfig.resourceTagsFilter` is applied during sync as an AND filter. A runtime is imported only if all configured tag key:value pairs match the AWS resource tags on that runtime.
- The UI-friendly string form such as `env:production, team:platform` must be converted by the frontend into the API object form `{ "env": "production", "team": "platform" }`.
- `toolCount` is already returned in federation stats and can be displayed directly in the UI.
- `POST /federations/{federation_id}/sync` returns a job summary, not the full federation detail.
- Azure AI Foundry has a registered provider handler entrypoint, but sync may still return not implemented until the provider adapter is completed.
