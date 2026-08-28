# Workflow Schedule Management API

## Table of Contents

1. [API Route Prefix](#api-route-prefix)
2. [API Endpoints](#api-endpoints)
   - 2.1. [Create Schedule](#1-create-schedule)
   - 2.2. [List Schedules](#2-list-schedules)
   - 2.3. [Get Schedule](#3-get-schedule)
   - 2.4. [Update Schedule](#4-update-schedule)
   - 2.5. [Delete Schedule](#5-delete-schedule)
   - 2.6. [Toggle Schedule (Enable / Disable)](#6-toggle-schedule-enable--disable)
3. [Access Control](#access-control)
   - 3.1. [Dual-Layer ACL](#dual-layer-acl)
   - 3.2. [RBAC Scopes](#rbac-scopes)
4. [Data Models](#data-models)
   - 4.1. [WorkflowSchedule](#workflowschedule)
   - 4.2. [Request / Response Schemas](#request--response-schemas)
5. [State Machine](#state-machine)
   - 5.1. [Schedule Enable / Disable](#schedule-enable--disable)
   - 5.2. [Cascade Disable (Workflow → Schedule)](#cascade-disable-workflow--schedule)
6. [Worker Lease Mechanism](#worker-lease-mechanism)
7. [Idempotency Guarantees](#idempotency-guarantees)
8. [Error Response Format](#error-response-format)

---

## API Route Prefix

```
/api/v1/workflows/{workflow_id}/schedules
```

All schedule endpoints are nested under their parent workflow.

---

## API Endpoints

### 1. Create Schedule

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/schedules`

**Request Body**:
```json
{
  "cron_expression": "0 2 * * *",
  "timezone": "Asia/Shanghai",
  "initial_input": { "env": "production" }
}
```

**Request Fields**:
- `cron_expression` (required, string, 9–120 chars): Standard five-field CRON expression. Validated via `croniter`.
- `timezone` (optional, string, 1–100 chars, default: `"UTC"`): IANA timezone name (e.g., `Asia/Shanghai`, `US/Eastern`).
- `initial_input` (optional, object): Key-value payload passed to the workflow run as initial input.

**Validation Rules**:
- `cron_expression` must contain exactly five whitespace-separated fields and pass `croniter.is_valid()`.
- `timezone` must be a valid IANA timezone recognized by Python's `zoneinfo.ZoneInfo`.

**Response**: `201 Created`
```json
{
  "id": "schedule-id-1",
  "workflowDefinitionId": "workflow-id-1",
  "cronExpression": "0 2 * * *",
  "timezone": "Asia/Shanghai",
  "initialInput": { "env": "production" },
  "enabled": false,
  "nextRunAt": null,
  "lastRunAt": null,
  "lastRunId": null,
  "lastRunStatus": null,
  "createdBy": "user-id-1",
  "createdAt": "2026-08-27T10:00:00Z",
  "updatedAt": "2026-08-27T10:00:00Z",
  "permissions": { "VIEW": true, "EDIT": true, "DELETE": true, "SHARE": true }
}
```

**Important Notes**:
- Schedules are **always created in the `disabled` state**. The caller must explicitly enable them via the Toggle endpoint.
- The parent workflow **must be enabled**; creating a schedule on a disabled workflow returns `409`.
- The creator is automatically granted **OWNER** permission (VIEW + EDIT + DELETE + SHARE) via an atomic MongoDB transaction.

**Errors**:
- `403` User lacks EDIT permission on the parent workflow
- `404` Workflow not found
- `409` Workflow is disabled
- `422` Invalid CRON expression or timezone

---

### 2. List Schedules

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/schedules`

**Response**: `200 OK`
```json
{
  "items": [
    {
      "id": "schedule-id-1",
      "workflowDefinitionId": "workflow-id-1",
      "cronExpression": "0 2 * * *",
      "timezone": "Asia/Shanghai",
      "initialInput": null,
      "enabled": true,
      "nextRunAt": "2026-08-28T02:00:00Z",
      "lastRunAt": "2026-08-27T02:00:00Z",
      "lastRunId": "run-id-1",
      "lastRunStatus": "completed",
      "createdBy": "user-id-1",
      "createdAt": "2026-08-27T10:00:00Z",
      "updatedAt": "2026-08-27T10:00:00Z",
      "permissions": { "VIEW": true, "EDIT": false, "DELETE": false, "SHARE": false }
    }
  ],
  "total": 1
}
```

**Important Notes**:
- Only returns schedules the user has **VIEW** access to (ACL-filtered at both workflow and schedule level).
- Results are sorted by `created_at` descending.

**Errors**:
- `403` User lacks VIEW permission on the parent workflow
- `404` Workflow not found
- `503` ACL service temporarily unavailable

---

### 3. Get Schedule

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/schedules/{schedule_id}`

**Response**: `200 OK` — Same shape as a single item in the List response.

**Errors**:
- `403` User lacks VIEW permission on the workflow or VIEW permission on the schedule
- `404` Workflow or schedule not found

---

### 4. Update Schedule

**Endpoint**: `PUT /api/v1/workflows/{workflow_id}/schedules/{schedule_id}`

**Request Body** (all fields optional, partial update via `exclude_unset`):
```json
{
  "cron_expression": "30 3 * * 1-5",
  "timezone": "US/Eastern",
  "initial_input": { "env": "staging" }
}
```

**Request Fields**:
- `cron_expression` (optional, string, 9–120 chars): New CRON expression. Same validation as create.
- `timezone` (optional, string, 1–100 chars): New IANA timezone.
- `initial_input` (optional, object | null): New initial input payload. Pass `null` to clear.

**Behavior**:
- If the schedule is **enabled** and `cron_expression` or `timezone` actually changed (compared to current values), `next_run_at` is automatically recalculated.
- If neither `cron_expression` nor `timezone` changed (even if sent in the request), `next_run_at` is **not** recalculated (idempotent — M2).
- If the schedule is **disabled**, `next_run_at` is never recalculated regardless of field changes.

**Response**: `200 OK` — Updated schedule object.

**Errors**:
- `403` User lacks EDIT permission on the workflow or EDIT permission on the schedule
- `404` Workflow or schedule not found
- `422` Invalid CRON expression, invalid timezone, or explicit `null` for required fields

---

### 5. Delete Schedule

**Endpoint**: `DELETE /api/v1/workflows/{workflow_id}/schedules/{schedule_id}`

**Behavior**:
1. Deletes the schedule document.
2. Deletes all associated ACL entries.
3. Both operations run in a single MongoDB transaction — if ACL cleanup fails, the entire delete is rolled back.

**Response**: `204 No Content`

**Errors**:
- `403` User lacks EDIT permission on the workflow or DELETE permission on the schedule
- `404` Workflow or schedule not found

---

### 6. Toggle Schedule (Enable / Disable)

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/schedules/{schedule_id}/toggle`

**Request Body**:
```json
{
  "enabled": true
}
```

**Behavior — Enable (`enabled: true`)**:
1. Validates that the parent workflow is **enabled** (otherwise `409`).
2. Calculates `next_run_at` from the schedule's CRON expression and timezone.
3. Clears `locked_until` and `lease_token` (fresh start).

**Behavior — Disable (`enabled: false`)**:
1. Sets `next_run_at`, `locked_until`, and `lease_token` to `null`.
2. The schedule is immediately invisible to the worker's claim query.

**Idempotency (M3)**:
- If the schedule's `enabled` state already matches the requested value, the endpoint returns the current state **without writing to the database**.

**Response**: `200 OK` — Updated schedule object.

**Errors**:
- `403` User lacks EDIT permission on the workflow or EDIT permission on the schedule
- `404` Workflow or schedule not found
- `409` Cannot enable: parent workflow is disabled or not found

---

## Access Control

### Dual-Layer ACL

Every schedule operation enforces **two layers** of ACL:

1. **Workflow-level permission**: The user must have the required permission on the parent `WorkflowDefinition` (VIEW or EDIT depending on the operation).
2. **Schedule-level permission**: The user must also have the required permission on the `WorkflowSchedule` itself (VIEW, EDIT, or DELETE depending on the operation).

Both checks happen inside the same MongoDB transaction for mutating operations.

**Per-endpoint permission matrix**:

| Endpoint | Workflow Permission | Schedule Permission |
|----------|-------------------|-------------------|
| `POST /` (create) | EDIT | — (OWNER auto-granted) |
| `GET /` (list) | VIEW | VIEW (ACL-filtered) |
| `GET /{id}` | VIEW | VIEW |
| `PUT /{id}` (update) | EDIT | EDIT |
| `DELETE /{id}` | EDIT | DELETE |
| `POST /{id}/toggle` | EDIT | EDIT |

### RBAC Scopes

Schedule endpoints are protected by middleware-level RBAC scopes defined in `scopes.yml`:

| Scope | Endpoints |
|-------|-----------|
| `workflows-read` | `GET /schedules`, `GET /schedules/{id}` |
| `workflows-write` | `POST /schedules`, `PUT /schedules/{id}`, `DELETE /schedules/{id}`, `POST /schedules/{id}/toggle` |
| `workflows-share` | `PUT /permissions/workflowSchedule/{resource_id}` |

---

## Data Models

### WorkflowSchedule

MongoDB collection: `workflow_schedules`

| Field | Type | Description |
|-------|------|-------------|
| `workflow_definition_id` | PydanticObjectId | Reference to the parent WorkflowDefinition |
| `cron_expression` | string | Standard five-field CRON expression |
| `timezone` | string | IANA timezone name (default: `"UTC"`) |
| `initial_input` | object \| null | Payload passed to the workflow run |
| `enabled` | bool | Whether the schedule is active (default: `false`) |
| `next_run_at` | datetime \| null | Next UTC fire time (calculated from CRON + timezone) |
| `locked_until` | datetime \| null | Lease expiry — worker holds the schedule until this time |
| `lease_token` | string \| null | UUID fencing token to prevent ABA problems during lease |
| `last_run_at` | datetime \| null | When the last run was triggered |
| `last_run_id` | PydanticObjectId \| null | ID of the last triggered WorkflowRun |
| `last_run_status` | WorkflowRunStatus \| null | Status of the last triggered run |
| `created_by` | PydanticObjectId | User who created the schedule |
| `created_at` | datetime | Auto-set on insert |
| `updated_at` | datetime | Auto-updated on save |

**Indexes**:
- `(enabled, next_run_at, locked_until)` — worker claim query: find enabled schedules whose `next_run_at` is past and `locked_until` is expired or null
- `workflow_definition_id` — parent lookup and cascade operations

### Request / Response Schemas

**ScheduleCreateRequest**:
```python
class ScheduleCreateRequest(BaseModel):
    cron_expression: str = Field(min_length=9, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    initial_input: dict[str, Any] | None = None
```

**ScheduleUpdateRequest**:
```python
class ScheduleUpdateRequest(BaseModel):
    cron_expression: str | None = Field(default=None, min_length=9, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    initial_input: dict[str, Any] | None = None
```

**ScheduleToggleRequest**:
```python
class ScheduleToggleRequest(BaseModel):
    enabled: bool
```

**ScheduleResponse**:
```python
class ScheduleResponse(BaseModel):
    id: str
    workflow_definition_id: str
    cron_expression: str
    timezone: str
    initial_input: dict[str, Any] | None
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_id: str | None
    last_run_status: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    permissions: ResourcePermissions
```

**ScheduleListResponse**:
```python
class ScheduleListResponse(BaseModel):
    items: list[ScheduleResponse]
    total: int
```

---

## State Machine

### Schedule Enable / Disable

```
                ┌──────────────────────┐
                │                      │
 ┌──────────┐  │  toggle(enabled=true) │  ┌──────────┐
 │ DISABLED  │──┘  + workflow enabled   └──│ ENABLED  │
 │           │←────────────────────────────│          │
 └──────────┘   toggle(enabled=false)     └──────────┘
                  or cascade disable
```

**State fields when enabled**:
- `enabled = true`
- `next_run_at` = calculated from CRON + timezone
- `locked_until = null`, `lease_token = null`

**State fields when disabled**:
- `enabled = false`
- `next_run_at = null`, `locked_until = null`, `lease_token = null`

### Cascade Disable (Workflow → Schedule)

When a workflow is disabled (via `toggle_workflow_status` or `update_workflow` with `enabled=false`), **all enabled schedules** for that workflow are automatically disabled in the same MongoDB transaction.

The cascade operation:
1. Runs `update_many` with filter `{workflow_definition_id: ..., enabled: true}`.
2. Sets `enabled=false`, `next_run_at=null`, `locked_until=null`, `lease_token=null`.
3. Is naturally idempotent — a second cascade matches zero documents.

This ensures no orphaned schedules fire after their parent workflow is disabled.

---

## Worker Lease Mechanism

The `workflow-worker` service polls for due schedules and claims them atomically:

1. **Claim query**: `find_one_and_update` with filter:
   ```
   enabled == true
   AND next_run_at <= now
   AND (locked_until == null OR locked_until <= now)
   ```
   Sets `locked_until = now + lease_duration` and `lease_token = uuid4()`.

2. **Lease renewal**: A background heartbeat extends `locked_until` periodically while the run is in progress.

3. **Fencing**: The `lease_token` UUID prevents ABA problems — if a worker's lease expires and another worker claims the schedule, the original worker's heartbeat will fail the token check and stop.

4. **Completion**: After the run finishes, the schedule is updated with `last_run_at`, `last_run_id`, `last_run_status`, and `next_run_at` is recalculated for the next fire time.

---

## Idempotency Guarantees

| Operation | Guard | Behavior |
|-----------|-------|----------|
| Toggle schedule (M3) | `schedule.enabled == requested_enabled` | Returns current state without DB write |
| Toggle workflow (M4) | `workflow.enabled == requested_enabled` | Returns current state without DB write |
| Update schedule (M2) | `cron_expression` and `timezone` unchanged | Skips `next_run_at` recalculation |
| Cascade disable (M1) | `update_many` filter `enabled: true` | Second call matches zero documents |

---

## Error Response Format

All error responses follow the structured format:

```json
{
  "detail": {
    "error": "error_code",
    "message": "Human-readable error message"
  }
}
```

| Status Code | Error Code | Meaning |
|-------------|-----------|---------|
| `403` | `insufficient_permissions` | User lacks required ACL permission |
| `404` | `resource_not_found` | Workflow or schedule not found |
| `409` | `conflict` | Workflow disabled (cannot enable schedule or create schedule) |
| `422` | `invalid_parameter` | Invalid CRON expression, invalid timezone, or null required field |
| `500` | `internal_error` | Internal server error |
| `503` | `service_unavailable` | ACL service temporarily unavailable |
