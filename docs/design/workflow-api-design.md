# Workflow Management API

## API Route Prefix

```
/api/v1/workflows
```

---

## API Endpoints

### 1. List Workflows

**Endpoint**: `GET /api/v1/workflows`

**Query Parameters**:
```typescript
{
  query?: string;           // Search keywords (name, description)
  page?: number;            // Page number (default: 1)
  perPage?: number;         // Items per page (default: 20, max: 100)
}
```

**Response**: `200 OK`
```json
{
  "workflows": [
    {
      "id": "507f1f77bcf86cd799439011",
      "name": "Customer Onboarding Workflow",
      "description": "Automated workflow for new customer onboarding",
      "numNodes": 5,
      "createdAt": "2024-01-15T10:30:00Z",
      "updatedAt": "2024-01-20T15:45:00Z"
    }
  ],
  "pagination": {
    "total": 50,
    "page": 1,
    "perPage": 20,
    "totalPages": 3
  }
}
```

**Error**:
- `500` Internal server error

---

### 2. Get Workflow Detail

**Endpoint**: `GET /api/v1/workflows/{workflow_id}`

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "name": "Customer Onboarding Workflow",
  "description": "Automated workflow for new customer onboarding",
  "nodes": [
    {
      "id": "node-1",
      "name": "Validate Customer Data",
      "nodeType": "step",
      "executorKey": "data-validator",
      "config": {
        "validationRules": ["email", "phone"]
      },
      "children": [],
      "conditionCel": null,
      "loopConfig": null
    },
    {
      "id": "node-2",
      "name": "Parallel Processing",
      "nodeType": "parallel",
      "executorKey": null,
      "config": {},
      "children": [
        {
          "id": "node-2-1",
          "name": "Send Welcome Email",
          "nodeType": "step",
          "executorKey": "email-sender",
          "config": {
            "template": "welcome"
          },
          "children": [],
          "conditionCel": null,
          "loopConfig": null
        },
        {
          "id": "node-2-2",
          "name": "Create User Account",
          "nodeType": "step",
          "executorKey": "account-creator",
          "config": {},
          "children": [],
          "conditionCel": null,
          "loopConfig": null
        }
      ],
      "conditionCel": null,
      "loopConfig": null
    }
  ],
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Error**:
- `400` Invalid workflow ID
- `404` Workflow not found
- `500` Internal server error

---

### 3. Create Workflow

**Endpoint**: `POST /api/v1/workflows`

**Request Body**:
```json
{
  "name": "Customer Onboarding Workflow",
  "description": "Automated workflow for new customer onboarding with email validation, parallel processing, and conditional routing",
  "nodes": [
    {
      "name": "Validate Customer Email",
      "nodeType": "step",
      "executorKey": "mcp-email-validator",
      "config": {
        "validationRules": ["format", "domain", "mx_record"],
        "allowedDomains": ["company.com", "partner.com"]
      }
    },
    {
      "name": "Check Customer Type",
      "nodeType": "condition",
      "conditionCel": "input.customerType == 'enterprise'",
      "children": [
        {
          "name": "Enterprise Onboarding Path",
          "nodeType": "parallel",
          "children": [
            {
              "name": "Send Welcome Email",
              "nodeType": "step",
              "executorKey": "mcp-email-sender",
              "config": {
                "template": "enterprise_welcome",
                "fromAddress": "onboarding@company.com"
              }
            },
            {
              "name": "Create Premium Account",
              "nodeType": "step",
              "executorKey": "a2a-account-manager",
              "config": {
                "accountType": "premium",
                "features": ["sso", "api_access", "priority_support"]
              }
            },
            {
              "name": "Notify Sales Team",
              "nodeType": "step",
              "executorKey": "mcp-slack-notifier",
              "config": {
                "channel": "#sales-enterprise",
                "messageTemplate": "New enterprise customer: {{customerName}}"
              }
            }
          ]
        },
        {
          "name": "Standard Onboarding",
          "nodeType": "step",
          "executorKey": "a2a-standard-onboarding-agent",
          "config": {
            "accountType": "standard",
            "trialDays": 14
          }
        }
      ]
    },
    {
      "name": "Send Onboarding Complete Email",
      "nodeType": "step",
      "executorKey": "mcp-email-sender",
      "config": {
        "template": "onboarding_complete",
        "includeLoginLink": true
      }
    }
  ]
}
```

**Request Fields**:
- `name` (required, string): Workflow name
- `description` (optional, string): Workflow description
- `nodes` (required, array): At least one root node required
  - `id` (optional, string): Node ID (auto-generated if not provided)
  - `name` (required, string): Node name
  - `nodeType` (required, string): Node type (`step`, `parallel`, `loop`, `condition`, `router`)
  - `executorKey` (required for `step` nodes, string): MCP tool name or A2A agent name
  - `config` (optional, object): Node configuration
  - `children` (optional, array): Child nodes for container nodes
  - `conditionCel` (optional, string): CEL expression for condition/router nodes
  - `loopConfig` (optional, object): Loop configuration
    - `maxIterations` (required, number): Maximum iterations (min: 1)
    - `endConditionCel` (optional, string): CEL expression for loop termination

**Validation Rules**:
- `step` nodes must have `executorKey` and no children
- `parallel` nodes must have at least 2 children and no `executorKey`
- `condition` nodes must have 1-2 children and `conditionCel`
- `loop` nodes must have `loopConfig` and at least 1 child
- `router` nodes must have at least 2 children with unique names and `conditionCel`

**Response**: `201 Created`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "name": "Customer Onboarding Workflow",
  "description": "Automated workflow for new customer onboarding",
  "nodes": [...],
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-15T10:30:00Z"
}
```

**Error**:
- `400` Validation error (invalid node structure, duplicate node names in router, etc.)
- `500` Internal server error

---

### 4. Update Workflow

**Endpoint**: `PUT /api/v1/workflows/{workflow_id}`

**Request Body** (all fields optional):
```json
{
  "name": "Customer Onboarding Workflow v2",
  "description": "Updated workflow with phone verification and improved enterprise features",
  "nodes": [
    {
      "name": "Validate Customer Email",
      "nodeType": "step",
      "executorKey": "mcp-email-validator",
      "config": {
        "validationRules": ["format", "domain", "mx_record", "disposable_check"],
        "allowedDomains": ["company.com", "partner.com", "newpartner.com"]
      }
    },
    {
      "name": "Verify Phone Number",
      "nodeType": "step",
      "executorKey": "mcp-phone-verifier",
      "config": {
        "countryCode": "US",
        "sendVerificationSMS": true,
        "timeout": 300
      }
    },
    {
      "name": "Route by Customer Type",
      "nodeType": "router",
      "conditionCel": "input.customerType",
      "children": [
        {
          "name": "enterprise",
          "nodeType": "parallel",
          "children": [
            {
              "name": "Send Enterprise Welcome Email",
              "nodeType": "step",
              "executorKey": "mcp-email-sender",
              "config": {
                "template": "enterprise_welcome_v2",
                "fromAddress": "onboarding@company.com",
                "cc": ["sales@company.com"]
              }
            },
            {
              "name": "Create Premium Account",
              "nodeType": "step",
              "executorKey": "a2a-account-manager",
              "config": {
                "accountType": "premium",
                "features": ["sso", "api_access", "priority_support", "custom_branding"],
                "slaLevel": "gold"
              }
            },
            {
              "name": "Assign Account Manager",
              "nodeType": "step",
              "executorKey": "mcp-crm-integration",
              "config": {
                "action": "assign_account_manager",
                "tier": "enterprise"
              }
            }
          ]
        },
        {
          "name": "business",
          "nodeType": "step",
          "executorKey": "a2a-business-onboarding-agent",
          "config": {
            "accountType": "business",
            "trialDays": 30,
            "features": ["api_access", "priority_support"]
          }
        },
        {
          "name": "standard",
          "nodeType": "step",
          "executorKey": "a2a-standard-onboarding-agent",
          "config": {
            "accountType": "standard",
            "trialDays": 14,
            "features": ["basic_support"]
          }
        }
      ]
    },
    {
      "name": "Setup Complete Notification",
      "nodeType": "step",
      "executorKey": "mcp-email-sender",
      "config": {
        "template": "onboarding_complete_v2",
        "includeLoginLink": true,
        "includeGettingStartedGuide": true,
        "includeVideoTutorial": true
      }
    }
  ]
}
```

**Request Fields** (all optional):
- `name` (string): Update workflow name
- `description` (string): Update workflow description
- `nodes` (array): Update workflow nodes (follows same structure and validation as create)

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "name": "Updated Workflow Name",
  "description": "Updated description",
  "nodes": [...],
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Error**:
- `400` Validation error or invalid workflow ID
- `404` Workflow not found
- `500` Internal server error

---

### 5. Delete Workflow

**Endpoint**: `DELETE /api/v1/workflows/{workflow_id}`

**Response**: `204 No Content`

**Error**:
- `400` Invalid workflow ID
- `404` Workflow not found
- `500` Internal server error

**Note**: Deletes all associated workflow runs when deleting workflow

---

### 6. Trigger Workflow Run

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/runs`

**Request Body Example 1** (New workflow execution):
```json
{
  "triggerSource": "manual",
  "initialInput": {
    "customerEmail": "john.doe@company.com",
    "customerName": "John Doe",
    "customerType": "enterprise",
    "phoneNumber": "+1-555-0123",
    "companyName": "Acme Corporation",
    "industry": "technology"
  }
}
```

**Request Body Example 2** (Retry failed workflow with selective node re-execution):
```json
{
  "triggerSource": "retry",
  "initialInput": {
    "customerEmail": "john.doe@company.com",
    "customerName": "John Doe",
    "customerType": "enterprise",
    "phoneNumber": "+1-555-0123",
    "companyName": "Acme Corporation",
    "industry": "technology"
  },
  "parentRunId": "507f1f77bcf86cd799439020",
  "resolvedDependencies": [
    {
      "nodeId": "67a3f2e8c4b1d5a6f7e8d9c0",
      "resolution": "reuse_previous_output",
      "sourceNodeRunId": "67a3f2e8c4b1d5a6f7e8d9c1"
    },
    {
      "nodeId": "67a3f2e8c4b1d5a6f7e8d9c2",
      "resolution": "reuse_previous_output",
      "sourceNodeRunId": "67a3f2e8c4b1d5a6f7e8d9c3"
    },
    {
      "nodeId": "67a3f2e8c4b1d5a6f7e8d9c4",
      "resolution": "rerun"
    }
  ]
}
```

**Request Fields**:
- `triggerSource` (optional, string): Source that triggered the run (e.g., "manual", "api", "schedule", "retry")
- `initialInput` (optional, object): Initial input data for the workflow
- `parentRunId` (optional, string): Parent run ID for retry scenarios
- `resolvedDependencies` (optional, array): Dependency resolution for retry (requires `parentRunId`)
  - `nodeId` (required, string): Node ID to resolve
  - `resolution` (required, string): Resolution strategy (`reuse_previous_output`, `rerun`)
  - `sourceNodeRunId` (optional, string): Source node run ID when reusing output (required when resolution is `reuse_previous_output`)

**Response**: `202 Accepted`
```json
{
  "runId": "507f1f77bcf86cd799439020",
  "workflowDefinitionId": "507f1f77bcf86cd799439011",
  "status": "pending",
  "triggerSource": "manual",
  "startedAt": "2024-01-25T10:00:00Z",
  "message": "Workflow run queued successfully"
}
```

**Error**:
- `400` Invalid workflow ID or invalid request body
- `404` Workflow not found
- `500` Internal server error

**Note**: Run executes asynchronously, returns 202 immediately

---

### 7. List Workflow Runs

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs`

**Query Parameters**:
```typescript
{
  status?: string;          // Status filter: pending | running | completed | failed
  page?: number;            // Page number (default: 1)
  perPage?: number;         // Items per page (default: 20, max: 100)
}
```

**Response**: `200 OK`
```json
{
  "runs": [
    {
      "id": "507f1f77bcf86cd799439020",
      "workflowDefinitionId": "507f1f77bcf86cd799439011",
      "status": "completed",
      "triggerSource": "manual",
      "startedAt": "2024-01-25T10:00:00Z",
      "finishedAt": "2024-01-25T10:05:30Z",
      "parentRunId": null,
      "errorSummary": null
    },
    {
      "id": "507f1f77bcf86cd799439021",
      "workflowDefinitionId": "507f1f77bcf86cd799439011",
      "status": "failed",
      "triggerSource": "api",
      "startedAt": "2024-01-25T11:00:00Z",
      "finishedAt": "2024-01-25T11:02:15Z",
      "parentRunId": null,
      "errorSummary": "Node 'Validate Customer Data' failed: Invalid email format"
    }
  ],
  "pagination": {
    "total": 25,
    "page": 1,
    "perPage": 20,
    "totalPages": 2
  }
}
```

**Error**:
- `400` Invalid workflow ID
- `404` Workflow not found
- `500` Internal server error

---

### 8. Get Workflow Run Detail

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs/{run_id}`

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439020",
  "workflowDefinitionId": "507f1f77bcf86cd799439011",
  "status": "completed",
  "triggerSource": "manual",
  "startedAt": "2024-01-25T10:00:00Z",
  "finishedAt": "2024-01-25T10:05:30Z",
  "initialInput": {
    "customerId": "cust-123",
    "email": "customer@example.com"
  },
  "finalOutput": {
    "status": "success",
    "accountId": "acc-456"
  },
  "errorSummary": null,
  "definitionSnapshot": {
    "name": "Customer Onboarding Workflow",
    "description": "Automated workflow for new customer onboarding",
    "nodes": [...]
  },
  "parentRunId": null,
  "resolvedDependencies": [],
  "nodeRuns": [
    {
      "id": "507f1f77bcf86cd799439030",
      "workflowRunId": "507f1f77bcf86cd799439020",
      "nodeId": "node-1",
      "nodeName": "Validate Customer Data",
      "status": "completed",
      "attempt": 0,
      "inputSnapshot": {
        "customerId": "cust-123",
        "email": "customer@example.com"
      },
      "outputSnapshot": {
        "valid": true
      },
      "error": null,
      "startedAt": "2024-01-25T10:00:05Z",
      "finishedAt": "2024-01-25T10:00:10Z"
    }
  ]
}
```

**Error**:
- `400` Invalid workflow ID or run ID
- `404` Workflow or run not found
- `500` Internal server error

---

## Error Response Format

All endpoints return errors in the following format:

```json
{
  "detail": {
    "error": "resource_not_found",
    "message": "Workflow not found"
  }
}
```

**Common Error Codes**:
- `authentication_required`: Not authenticated
- `invalid_request`: Validation error
- `resource_not_found`: Workflow or run not found
- `duplicate_entry`: Workflow name already exists
- `internal_error`: Internal server error
- `database_error`: Database operation failed

---

## Data Models

### WorkflowNode

```typescript
{
  id: string;                    // Node ID (UUID)
  name: string;                  // Node name
  nodeType: string;              // step | parallel | loop | condition | router
  executorKey?: string;          // Required for step nodes only
  config: object;                // Node configuration
  children: WorkflowNode[];      // Child nodes for container nodes
  conditionCel?: string;         // CEL expression for condition/router
  loopConfig?: LoopConfig;       // Loop configuration for loop nodes
}
```

### LoopConfig

```typescript
{
  maxIterations: number;         // Max iterations (min: 1)
  endConditionCel?: string;      // CEL expression for loop termination
}
```

### WorkflowRunStatus

- `pending`: Run is queued
- `running`: Run is in progress
- `completed`: Run completed successfully
- `failed`: Run failed

### NodeRunStatus

- `pending`: Node execution is queued
- `running`: Node is executing
- `completed`: Node completed successfully
- `failed`: Node execution failed
- `skipped`: Node was skipped

### ResolvedDependencyResolution

- `reuse_previous_output`: Reuse output from previous run
- `rerun`: Re-execute the node

---

## Naming Conventions

- All field names use **camelCase** in API requests and responses
- MongoDB document field names use **snake_case** internally
- Response models use `response_model_by_alias=True` for automatic conversion

---
