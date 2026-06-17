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
      "id": "wf-demo-id",
      "name": "Customer Onboarding Workflow",
      "description": "Automated workflow for new customer onboarding",
      "numNodes": 5,
      "enabled": false,
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

Every node in the response always carries the full set of optional container fields
(`children`, `trueSteps`, `falseSteps`, `choices`) populated as empty lists when the
node type does not use them. This lets clients access any field without null checks.

```json
{
  "id": "wf-demo-id",
  "name": "Customer Onboarding Workflow",
  "description": "Automated workflow for new customer onboarding",
  "canvas": {
    "viewport": { "x": 0, "y": 0, "zoom": 1 }
  },
  "nodes": [
    {
      "id": "node-1",
      "name": "Validate Customer Data",
      "nodeType": "step",
      "position": { "x": 80, "y": 220 },
      "executorKey": "data-validator",
      "a2aPool": [],
      "stepConfig": {
        "maxRetries": 3,
        "onError": "retry",
        "backoffBaseSeconds": 2.0,
        "backoffMaxSeconds": 30.0
      },
      "config": {
        "validationRules": ["email", "phone"]
      },
      "children": [],
      "trueSteps": [],
      "falseSteps": [],
      "choices": [],
      "conditionCel": null,
      "loopConfig": null
    },
    {
      "id": "node-2",
      "name": "Parallel Processing",
      "nodeType": "parallel",
      "executorKey": null,
      "a2aPool": [],
      "stepConfig": null,
      "config": {},
      "children": [
        {
          "id": "node-2-1",
          "name": "Send Welcome Email",
          "nodeType": "step",
          "position": { "x": 360, "y": 160 },
          "executorKey": "email-sender",
          "a2aPool": [],
          "stepConfig": null,
          "config": {
            "template": "welcome"
          },
          "children": [],
          "trueSteps": [],
          "falseSteps": [],
          "choices": [],
          "conditionCel": null,
          "loopConfig": null
        },
        {
          "id": "node-2-2",
          "name": "Create User Account",
          "nodeType": "step",
          "position": { "x": 360, "y": 300 },
          "executorKey": null,
          "a2aPool": ["account-creator-v1", "account-creator-v2"],
          "stepConfig": {
            "maxRetries": 5,
            "onError": "retry",
            "backoffBaseSeconds": 1.0,
            "backoffMaxSeconds": 60.0
          },
          "config": {},
          "children": [],
          "trueSteps": [],
          "falseSteps": [],
          "choices": [],
          "conditionCel": null,
          "loopConfig": null
        }
      ],
      "trueSteps": [],
      "falseSteps": [],
      "choices": [],
      "conditionCel": null,
      "loopConfig": null
    },
    {
      "id": "node-3",
      "name": "Route by Customer Type",
      "nodeType": "condition",
      "position": { "x": 640, "y": 220 },
      "executorKey": null,
      "a2aPool": [],
      "stepConfig": null,
      "config": {},
      "children": [],
      "trueSteps": [
        {
          "id": "node-3-t1",
          "name": "Enterprise Provisioning",
          "nodeType": "step",
          "position": { "x": 920, "y": 160 },
          "executorKey": "mcp-enterprise-provisioner",
          "a2aPool": [],
          "stepConfig": null,
          "config": {},
          "children": [],
          "trueSteps": [],
          "falseSteps": [],
          "choices": [],
          "conditionCel": null,
          "loopConfig": null
        }
      ],
      "falseSteps": [
        {
          "id": "node-3-f1",
          "name": "Standard Provisioning",
          "nodeType": "step",
          "position": { "x": 920, "y": 300 },
          "executorKey": "mcp-standard-provisioner",
          "a2aPool": [],
          "stepConfig": null,
          "config": {},
          "children": [],
          "trueSteps": [],
          "falseSteps": [],
          "choices": [],
          "conditionCel": null,
          "loopConfig": null
        }
      ],
      "choices": [],
      "conditionCel": "input.customerType == 'enterprise'",
      "loopConfig": null
    }
  ],
  "enabled": false,
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
  "canvas": {
    "viewport": { "x": 0, "y": 0, "zoom": 1 }
  },
  "nodes": [
    {
      "name": "Validate Customer Email",
      "nodeType": "step",
      "position": { "x": 80, "y": 220 },
      "executorKey": "mcp-email-validator",
      "stepConfig": {
        "maxRetries": 3,
        "onError": "retry",
        "backoffBaseSeconds": 2.0,
        "backoffMaxSeconds": 30.0
      },
      "config": {
        "validationRules": ["format", "domain", "mx_record"],
        "allowedDomains": ["company.com", "partner.com"]
      }
    },
    {
      "name": "Check Customer Type",
      "nodeType": "condition",
      "position": { "x": 360, "y": 220 },
      "conditionCel": "input.customerType == 'enterprise'",
      "trueSteps": [
        {
          "name": "Enterprise Onboarding Path",
          "nodeType": "parallel",
          "position": { "x": 640, "y": 160 },
          "children": [
            {
              "name": "Send Welcome Email",
              "nodeType": "step",
              "position": { "x": 920, "y": 120 },
              "executorKey": "mcp-email-sender",
              "stepConfig": {
                "maxRetries": 2,
                "onError": "skip"
              },
              "config": {
                "template": "enterprise_welcome",
                "fromAddress": "onboarding@company.com"
              }
            },
            {
              "name": "Create Premium Account",
              "nodeType": "step",
              "a2aPool": [
                "account-manager-v1",
                "account-manager-v2",
                "account-manager-fallback"
              ],
              "stepConfig": {
                "maxRetries": 5,
                "onError": "retry",
                "backoffBaseSeconds": 1.0,
                "backoffMaxSeconds": 60.0
              },
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
        }
      ],
      "falseSteps": [
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
- `canvas` (required, object): Frontend canvas metadata
  - `viewport` (required, object): Canvas viewport state
    - `x` (optional, number): Viewport x offset (default: 0)
    - `y` (optional, number): Viewport y offset (default: 0)
    - `zoom` (optional, number): Viewport zoom level (default: 1, must be > 0)
- `nodes` (required, array): At least one root node required
  - `id` (optional, string): Node ID (auto-generated if not provided)
  - `name` (required, string): Node name
  - `nodeType` (required, string): Node type (`step`, `parallel`, `loop`, `condition`, `router`)
  - `position` (optional, object): Node position on the frontend canvas
    - `x` (optional, number): Node x coordinate (default: 0)
    - `y` (optional, number): Node y coordinate (default: 0)
  - `executorKey` (optional for `step` nodes, string): MCP tool name or A2A agent name (required if `a2aPool` is not provided)
  - `a2aPool` (optional for `step` nodes, array): A2A agent pool (max 5 agents, alternative to `executorKey`)
  - `stepConfig` (optional for `step` nodes, object): Step-level retry and error handling configuration
    - `maxRetries` (optional, number): Maximum number of retries (default: 0, min: 0)
    - `onError` (optional, string): Error handling strategy: `fail`, `skip`, or `retry` (default: `fail`)
    - `backoffBaseSeconds` (optional, number): Base wait time for first retry in seconds (default: 1.0, must be > 0)
    - `backoffMaxSeconds` (optional, number): Maximum wait time for any retry in seconds (default: 60.0, must be > 0)
  - `config` (optional, object): Node configuration
  - `children` (optional, array): Child nodes — used by `parallel` and `loop` nodes only
  - `trueSteps` (optional, array): Steps executed when CONDITION evaluator is true (CONDITION nodes, ≥ 1 required)
  - `falseSteps` (optional, array): Steps executed when CONDITION evaluator is false (CONDITION nodes, optional)
  - `choices` (optional, array): Named choices for ROUTER nodes (≥ 2 required); each entry is a `RouterChoice` object
    - `name` (required, string): Choice name — must match the value returned by the router's `conditionCel` selector
    - `steps` (required, array): One or more `WorkflowNode` steps executed sequentially when this choice is selected
  - `conditionCel` (optional, string): CEL expression for condition/router nodes
    - Condition: returns bool; available variables: `input`, `previous_step_content`, `previous_step_outputs`, `additional_data`, `session_state`
    - Router: returns a choice name string; additional variable: `step_choices` (list of all choice names)
  - `loopConfig` (optional, object): Loop configuration
    - `maxIterations` (required, number): Maximum iterations (min: 1)
    - `endConditionCel` (optional, string): CEL expression for loop termination

**Validation Rules**:
- `step` nodes must have either `executorKey` or `a2aPool` (but not both) and no `children` / `trueSteps` / `falseSteps` / `choices`
- `parallel` nodes must have at least 2 `children` and no `executorKey` / `trueSteps` / `falseSteps` / `choices`
- `condition` nodes must have non-empty `trueSteps` (optional `falseSteps`) and `conditionCel`; `children` and `choices` are forbidden
- `loop` nodes must have `loopConfig` and at least 1 child; `trueSteps` / `falseSteps` / `choices` are forbidden
- `router` nodes must have at least 2 `choices` with unique names and `conditionCel`; `children` and `trueSteps` / `falseSteps` are forbidden
- Each `RouterChoice` must have a non-empty `steps` list
- `condition` and `router` nodes must not define `stepConfig` (it is meaningful only for `step` nodes)

**Response**: `201 Created`
```json
{
  "id": "wf-demo-id",
  "name": "Customer Onboarding Workflow",
  "description": "Automated workflow for new customer onboarding",
  "canvas": {
    "viewport": { "x": 0, "y": 0, "zoom": 1 }
  },
  "nodes": [...],
  "enabled": false,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-15T10:30:00Z"
}
```

**Important Notes**:
- **Workflows are always created with `enabled: false`** for safety (similar to server registration)
- The `enabled` field cannot be set during creation - it is automatically set to `false`
- After creating a workflow, you must explicitly enable it using the Toggle Workflow endpoint (`POST /workflows/{id}/toggle`) before it can be triggered
- This two-step process (create → enable) ensures workflows are reviewed and verified before execution

**Error**:
- `400` Validation error (invalid node structure, duplicate node names in router, unknown `executorKey`, or unresolvable `a2aPool` agent path)
- `500` Internal server error

---

### 4. Update Workflow

**Endpoint**: `PUT /api/v1/workflows/{workflow_id}`

**Request Body** (all fields optional):
```json
{
  "name": "Customer Onboarding Workflow v2",
  "description": "Updated workflow with phone verification and improved enterprise features",
  "canvas": {
    "viewport": { "x": -120, "y": 40, "zoom": 0.8 }
  },
  "nodes": [
    {
      "name": "Validate Customer Email",
      "nodeType": "step",
      "position": { "x": 80, "y": 220 },
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
      "choices": [
        {
          "name": "enterprise",
          "steps": [
            {
              "name": "Enterprise Onboarding",
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
            }
          ]
        },
        {
          "name": "business",
          "steps": [
            {
              "name": "Business Onboarding",
              "nodeType": "step",
              "executorKey": "a2a-business-onboarding-agent",
              "config": {
                "accountType": "business",
                "trialDays": 30,
                "features": ["api_access", "priority_support"]
              }
            }
          ]
        },
        {
          "name": "standard",
          "steps": [
            {
              "name": "Standard Onboarding",
              "nodeType": "step",
              "executorKey": "a2a-standard-onboarding-agent",
              "config": {
                "accountType": "standard",
                "trialDays": 14,
                "features": ["basic_support"]
              }
            }
          ]
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
- `canvas` (object): Update frontend canvas metadata
- `nodes` (array): Update workflow nodes (follows same structure and validation as create)
- `enabled` (boolean): Update workflow enabled status

**Response**: `200 OK`
```json
{
  "id": "wf-demo-id",
  "name": "Updated Workflow Name",
  "description": "Updated description",
  "canvas": {
    "viewport": { "x": -120, "y": 40, "zoom": 0.8 }
  },
  "nodes": [...],
  "enabled": false,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Error**:
- `400` Validation error or invalid workflow ID (invalid node structure, duplicate node names in router, unknown `executorKey`, or unresolvable `a2aPool` agent path)
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

### 6. Toggle Workflow Status

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/toggle`

**Description**: Enable or disable a workflow.

**Business Rules**:
- Workflows are created with `enabled: false` by default for safety
- **Disabled workflows cannot be triggered** - you must enable them first using this endpoint
- Disabling a workflow does not affect already running workflow runs
- Similar to server toggle endpoint behavior

**Use Cases**:
- Enable workflow after creation and verification
- Temporarily disable a workflow for maintenance or debugging
- Prevent workflow execution without deleting the workflow definition
- Control workflow availability in production environments

**Request Body**:
```json
{
  "enabled": true
}
```

**Request Fields**:
- `enabled` (required, boolean): `true` to enable the workflow, `false` to disable it

**Response**: `200 OK`
```json
{
  "id": "wf-demo-id",
  "name": "Customer Onboarding Workflow",
  "description": "Automated workflow for new customer onboarding",
  "canvas": {
    "viewport": { "x": 0, "y": 0, "zoom": 1 }
  },
  "nodes": [...],
  "enabled": true,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Error**:
- `400` Invalid workflow ID or validation error
- `404` Workflow not found
- `500` Internal server error

---

### 7. Trigger Workflow Run

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
  "parentRunId": "run-demo-id",
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
  "runId": "run-demo-id",
  "workflowDefinitionId": "wf-demo-id",
  "status": "pending",
  "triggerSource": "manual",
  "startedAt": "2024-01-25T10:00:00Z",
  "message": "Workflow run queued successfully"
}
```

**Error**:
- `400` Invalid workflow ID, invalid request body, or **workflow is disabled** (must enable workflow first using toggle endpoint)
- `404` Workflow not found
- `500` Internal server error

**Important Notes**:
- Run executes asynchronously, returns 202 immediately
- **Workflow must be enabled before triggering** - disabled workflows will return a 400 error with message "Workflow is disabled. Please enable the workflow before triggering a run."
- Use the Toggle Workflow endpoint (`POST /workflows/{id}/toggle`) to enable the workflow before triggering

---

### 8. List Workflow Runs

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs`

**Query Parameters**:
```typescript
{
  status?: string;          // Status filter: pending | running | paused | completed | failed | cancelled
  page?: number;            // Page number (default: 1)
  perPage?: number;         // Items per page (default: 20, max: 100)
}
```

**Response**: `200 OK`
```json
{
  "runs": [
    {
      "id": "run-demo-id",
      "workflowDefinitionId": "wf-demo-id",
      "status": "completed",
      "triggerSource": "manual",
      "startedAt": "2024-01-25T10:00:00Z",
      "finishedAt": "2024-01-25T10:05:30Z",
      "parentRunId": null,
      "errorSummary": null,
      "nodeRuns": [
        {
          "id": "node-run-demo-id",
          "workflowRunId": "run-demo-id",
          "nodeId": "node-1",
          "nodeName": "Validate Customer Data",
          "status": "completed",
          "attempt": 1,
          "inputSnapshot": null,
          "outputSnapshot": {
            "valid": true
          },
          "error": null,
          "startedAt": "2024-01-25T10:00:05Z",
          "finishedAt": "2024-01-25T10:00:10Z"
        }
      ]
    },
    {
      "id": "run-demo-id-2",
      "workflowDefinitionId": "wf-demo-id",
      "status": "failed",
      "triggerSource": "api",
      "startedAt": "2024-01-25T11:00:00Z",
      "finishedAt": "2024-01-25T11:02:15Z",
      "parentRunId": null,
      "errorSummary": "Node 'Validate Customer Data' failed: Invalid email format",
      "nodeRuns": [
        {
          "id": "node-run-demo-id-2",
          "workflowRunId": "run-demo-id-2",
          "nodeId": "node-1",
          "nodeName": "Validate Customer Data",
          "status": "failed",
          "attempt": 1,
          "inputSnapshot": null,
          "outputSnapshot": null,
          "error": "Invalid email format",
          "startedAt": "2024-01-25T11:00:05Z",
          "finishedAt": "2024-01-25T11:00:10Z"
        }
      ]
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

### 8b. List Child Runs

List runs spawned from a parent run via **node rerun**, **replay**, or **retry**. Each child run carries `parentRunId == run_id` and a `triggerSource` of `node_rerun`, `replay`, or `retry`. Use this to build a run-lineage / history view in the UI. Results are ordered newest-first.

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs/{run_id}/children`

**Query Parameters**:
```typescript
{
  page?: number;            // Page number (default: 1)
  perPage?: number;         // Items per page (default: 20, max: 100)
}
```

**Response**: `200 OK` — same shape as **List Workflow Runs**, filtered to children of `run_id`.
```json
{
  "runs": [
    {
      "id": "child-run-id",
      "workflowDefinitionId": "wf-demo-id",
      "status": "completed",
      "triggerSource": "replay",
      "startedAt": "2024-01-25T12:00:00Z",
      "finishedAt": "2024-01-25T12:00:30Z",
      "parentRunId": "run-demo-id",
      "errorSummary": null,
      "nodeRuns": []
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

**Error**:
- `400` Invalid workflow or run ID
- `404` Workflow or parent run not found
- `500` Internal server error

---

### 9. Get Workflow Run Detail

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs/{run_id}`

**Response**: `200 OK`
```json
{
  "id": "run-demo-id",
  "workflowDefinitionId": "wf-demo-id",
  "workflowVersion": 2,
  "status": "awaiting_approval",
  "triggerSource": "manual",
  "startedAt": "2024-01-25T10:00:00Z",
  "finishedAt": null,
  "initialInput": {
    "customerId": "cust-123",
    "email": "customer@example.com"
  },
  "finalOutput": null,
  "errorSummary": null,
  "definitionSnapshot": {
    "name": "Customer Onboarding Workflow",
    "description": "Automated workflow for new customer onboarding",
    "canvas": {
      "viewport": { "x": 0, "y": 0, "zoom": 1 }
    },
    "nodes": [...]
  },
  "parentRunId": null,
  "resolvedDependencies": [],
  "nodeRuns": [
    {
      "id": "node-run-demo-id",
      "workflowRunId": "run-demo-id",
      "nodeId": "node-1",
      "nodeName": "Validate Customer Data",
      "status": "completed",
      "attempt": 1,
      "inputSnapshot": {"customerId": "cust-123"},
      "outputSnapshot": {"valid": true},
      "error": null,
      "startedAt": "2024-01-25T10:00:05Z",
      "finishedAt": "2024-01-25T10:00:10Z"
    }
  ],
  "pendingRequirements": [
    {
      "schemaVersion": 1,
      "stepId": "node-2",
      "stepName": "Send Welcome Email",
      "stepType": "step",
      "requiresConfirmation": true,
      "requiresUserInput": false,
      "requiresOutputReview": false,
      "requiresRouteSelection": false,
      "confirmationMessage": "Send welcome email to cust-123?",
      "isPostExecution": false,
      "confirmed": null,
      "timeoutAt": "2024-01-25T11:00:00Z",
      "onTimeout": "cancel",
      "onReject": "skip",
      "retryCount": 0
    }
  ]
}
```

**Notes**:
- `workflowVersion`: the WorkflowDefinition version snapshot this run is replaying against (HITL v2)
- `pendingRequirements`: non-empty iff `status == "awaiting_approval"`. Each element is one
  HITL gate awaiting decision; the frontend renders a decision UI per element (see
  [StepRequirementSummary](#steprequirementsummary)).

**Error**:
- `400` Invalid workflow ID or run ID
- `404` Workflow or run not found
- `500` Internal server error

---

### 10. Resolve HITL Requirement (Approve / Reject / Edit / etc.)

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/runs/{run_id}/approve`

HITL v2: resolve one pending requirement on a run holding at an HITL gate. The
endpoint accepts a 5-way decision aligned 1:1 with agno's `StepRequirement`
methods (`confirm` / `reject` / `edit` / `set_user_input` / `set_selected_choices`).

**Request Body**:
```json
{
  "stepId": "node-2",
  "resolution": "confirm",
  "feedback": null,
  "editedOutput": null,
  "userInput": null,
  "selectedChoices": null
}
```

**Fields**:
- `stepId` (required): the `pendingRequirements[].stepId` from GET /runs/{id}
- `resolution` (required): one of:
  - `confirm` — approve the gate; run resumes from the held step
  - `reject` — reject; node fails per `onReject` policy (skip / cancel / retry / else_branch)
  - `edit` — accept with modifications (output_review only); replaces `step_output` with `editedOutput`
  - `user_input` — provide collected form values (matching `userInputSchema`); requires `userInput`
  - `route_select` — choose router branch(es); requires `selectedChoices`
- `feedback` (optional): explanatory text on rejection (passed to agno when `onReject=retry`)
- `editedOutput` (required iff resolution=`edit`): replacement output to use downstream
- `userInput` (required iff resolution=`user_input`): form values matching schema
- `selectedChoices` (required iff resolution=`route_select`): chosen router choice name(s)

**Response**: `200 OK`
```json
{
  "runId": "run-demo-id",
  "status": "running",
  "resolvedStepId": "node-2",
  "message": "Requirement resolved as confirm; run resuming"
}
```

**Resolution × Requirement Type Compatibility**:

The backend validates that the chosen `resolution` matches the requirement's
capability flags (returns 400 on mismatch):

| Resolution      | Requires (on the pending requirement)              |
|-----------------|----------------------------------------------------|
| `confirm`       | always valid                                       |
| `reject`        | always valid                                       |
| `edit`          | `requiresOutputReview=true` AND `isPostExecution=true` |
| `user_input`    | `requiresUserInput=true`                           |
| `route_select`  | `requiresRouteSelection=true`                      |

**Status Codes**:

| Code | Meaning |
|------|---------|
| 200  | Decision accepted; `continue_run` triggered in the background |
| 400  | Resolution/requirement mismatch, or missing required field (editedOutput/userInput/selectedChoices) |
| 403  | Caller lacks `workflows-control` scope or VIEW permission on the workflow |
| 404  | Workflow, run, or step_id not found (incl. already-resolved requirement) |
| 409  | Run not in `awaiting_approval` (already resolved by someone else, timed out, completed) |
| 500  | Internal server error |

**Concurrency**: Decisions on different `stepId` values within the same run are
independent (a single run may have multiple pending requirements, e.g. parallel
branches). Concurrent decisions on the *same* `stepId` are resolved atomically via
MongoDB `array_filters` — the loser receives 409. The frontend should disable the
decision button after click and refetch on 409 to display the winning decision.

**Resume Flow**:
The HTTP response returns immediately after the decision is persisted. The actual
workflow resumption (`acontinue_run`) happens in a background task on whichever
pod handles it (CAS-protected so only one wins). Frontend should poll
`GET /runs/{run_id}` to observe state transitions.

---

### 11. List Node Runs

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs/{run_id}/nodes`

**Description**: Return all `NodeRun` records for a given workflow run, including full I/O snapshots. Results are ordered by `startedAt` ascending.

**Required Permission**: VIEW on the workflow (enforced via resource ACL).

**Required Scope**: `workflows-read`

**Response**: `200 OK`
```json
{
  "runId": "run-demo-id",
  "workflowId": "wf-demo-id",
  "nodeRuns": [
    {
      "id": "nr-demo-id",
      "nodeId": "node-1",
      "nodeName": "Validate Customer Data",
      "workflowRunId": "run-demo-id",
      "status": "completed",
      "attempt": 1,
      "inputSnapshot": {
        "customerEmail": "john.doe@company.com"
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

**Notes**:
- `inputSnapshot` / `outputSnapshot` may be `null` for runs created before snapshot capture was introduced.
- Returns an empty `nodeRuns` list (not 404) if the run exists but has not yet started any nodes.

**Error**:
- `400` Invalid workflow ID or run ID
- `403` Caller lacks VIEW permission on the workflow
- `404` Workflow or run not found
- `500` Internal server error

---

### 12. Get Node Run Detail

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs/{run_id}/nodes/{node_run_id}`

**Description**: Return the full detail of a single `NodeRun` by its ID, including I/O snapshots.

**Required Permission**: VIEW on the workflow (enforced via resource ACL).

**Required Scope**: `workflows-read`

**Response**: `200 OK`
```json
{
  "id": "nr-demo-id",
  "nodeId": "node-1",
  "nodeName": "Validate Customer Data",
  "workflowRunId": "run-demo-id",
  "status": "completed",
  "attempt": 1,
  "inputSnapshot": {
    "customerEmail": "john.doe@company.com"
  },
  "outputSnapshot": {
    "valid": true
  },
  "error": null,
  "startedAt": "2024-01-25T10:00:05Z",
  "finishedAt": "2024-01-25T10:00:10Z"
}
```

**Error**:
- `400` Invalid workflow ID, run ID, or node run ID
- `403` Caller lacks VIEW permission on the workflow
- `404` Workflow, run, or node run not found
- `500` Internal server error

---

### 13. Rerun Single Node

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/runs/{run_id}/nodes/{node_id}/rerun`

**Description**: Rerun a single top-level step node in isolation. All upstream nodes are replayed from their cached `output_snapshot`; downstream nodes do **not** run. A new child `WorkflowRun` (with `trigger_source="node_rerun"`) is created and returned immediately.

**Required Permission**: VIEW on the workflow (enforced via resource ACL).

**Required Scope**: `workflows-control`

**Request Body**: Empty (no fields required).

**Path Parameters**:
- `workflow_id`: Parent workflow definition ID
- `run_id`: Source run ID (must be in a terminal state: `completed` or `failed`)
- `node_id`: The `WorkflowNode.id` (from the workflow definition) to rerun

**Response**: `202 Accepted`
```json
{
  "run_id": "child-run-id",
  "status": "pending",
  "message": "Node 'node-1' rerun queued as run child-run-id"
}
```

**Business Rules**:
- Only **top-level step nodes** are supported. Nodes nested inside `parallel`, `condition`, or `router` containers return `400 "Nested node rerun is not supported."`.
- The source run must be in a terminal state (`completed` or `failed`). Passing a still-running or paused run returns `400`.
- Input to the target node is taken from the previous node's `output_snapshot` in the source run.
- The child run executes asynchronously; poll `GET /runs/{child_run_id}` to observe completion.

**Error**:
- `400` Invalid IDs, non-terminal source run, or nested node targeted
- `403` Caller lacks VIEW permission on the workflow
- `404` Workflow, run, or node ID not found
- `500` Internal server error

---

### 14. Replay Workflow Run

**Endpoint**: `POST /api/v1/workflows/{workflow_id}/runs/{run_id}/replay`

**Description**: Re-execute a workflow run from scratch using the same `initial_input` as the source run. Unlike `/retry`, replay does not reuse any cached node outputs — all nodes execute fresh. Uses the **current live workflow definition** (not the snapshot), so any definition updates since the original run are picked up.

**Required Permission**: VIEW on the workflow (enforced via resource ACL).

**Required Scope**: `workflows-control`

**Request Body**: None.

**Response**: `202 Accepted`
```json
{
  "run_id": "new-run-id",
  "status": "pending",
  "message": "Replay queued as run new-run-id"
}
```

**Business Rules**:
- The new run is a child run linked via `parent_run_id` so run lineage is traceable in the UI.
- `trigger_source` is set to `"replay"`.
- The source run's `initial_input` is forwarded verbatim; no additional input is accepted.
- The new run executes asynchronously; poll `GET /runs/{new_run_id}` to observe completion.

**Difference from `/retry`**:

| | `/retry` | `/replay` |
|---|---|---|
| Creates | Child run (linked via `parent_run_id`) | Child run (linked via `parent_run_id`) |
| Nodes | Selective: some cached, some re-executed | All nodes re-executed |
| Definition | Uses source run's `definition_snapshot` | Uses **current live** definition |
| Input | Same `initial_input` | Same `initial_input`
| Definition | Uses source run's `definition_snapshot` | Uses **current live** definition |
| Input | Same `initial_input` | Same `initial_input` |

**Error**:
- `400` Invalid workflow ID or run ID
- `403` Caller lacks VIEW permission on the workflow
- `404` Workflow or source run not found
- `500` Internal server error

---

## Internal Data Flow — Control Endpoints

The five control endpoints (`/pause`, `/resume`, `/cancel`, `/retry`, `/approve`)
look similar from the outside but take different internal paths and therefore
have different latency / persistence characteristics.  The frontend should not
treat them as interchangeable.

### `/pause`, `/resume`, `/cancel`

**Path**: route → `WorkflowControlService.send_*` → MongoDB write + in-process
`DirectiveQueue.put(...)` → HTTP 200 returned.

- The wait-loop wrapper inside the runner picks the directive up at the next
  step boundary (≤ 2 s under normal load), or on the next Mongo poll (≤ 60 s)
  if the queue is missed.
- Persistent: even if the pod owning the queue dies, the directive survives in
  `WorkflowRun.pending_directive` and is honored when the run is next observed.
- `cancel` additionally calls `agno.run.cancel.acancel_run(run_id)` via the
  `MongoBackedCancellationManager`, so any agno-internal code path that checks
  `raise_if_cancelled` also stops.

### `/retry`

**Path**: route → `WorkflowControlService.send_retry` → builds a *child*
`WorkflowRun` with `resolved_dependencies` describing which nodes replay from
cached outputs vs. re-execute → `asyncio.create_task(runner.run(child_run_id))`
→ HTTP 200 returned immediately with the **child run's** ID.

- The original run is unchanged.
- The child run starts at `PENDING` and proceeds normally.
- Background task runs on the pod that handled the HTTP request.

### `/nodes/{node_id}/rerun`

**Path**: route → `WorkflowControlService.rerun_single_node` → validates the
target node is a top-level step (400 if nested) → builds a child `WorkflowRun`
with `resolved_dependencies` that replays every node *before* the target from
cached outputs → starts `runner.run(..., stop_after_node_id=<target>)` so only
nodes up to and including the target are compiled/executed → HTTP 202 returned immediately.

- `trigger_source` is set to `"node_rerun"`.
- Only top-level step nodes are supported; returns 400 for nested nodes.
- The child run is linked to the source via `parent_run_id`.

### `/replay`

**Path**: route → `WorkflowControlService.replay_run` → reads `initial_input`
from the source run → creates a *child* `WorkflowRun` (linked via `parent_run_id`)
with the same `initial_input` and the current live workflow definition →
`asyncio.create_task(runner.run(new_run_id))` → HTTP 202 returned immediately.

- `trigger_source` is set to `"replay"`.
- Uses the live definition, not the source run's `definition_snapshot`.
- All nodes execute fresh — no cached outputs are reused.

### `/approve` (HITL resolution)

**Path**: route → `WorkflowControlService.resolve_requirement`:

1. Validates the run is in `AWAITING_APPROVAL` and the `stepId` matches an
   unresolved entry in `pending_requirements`.
2. Atomically writes the decision into `pending_requirements[stepId]` using
   MongoDB `array_filters` (three-layer concurrency protection: top-level
   `status` filter, per-element `confirmed is None` filter, and
   `modified_count == 0 → 409`).
3. Fires `asyncio.create_task(runner.continue_run(...))` to resume on the
   same pod.

`continue_run` then CAS-transitions the run from `AWAITING_APPROVAL` to
`RUNNING`, rebuilds the agno Workflow from `definition_snapshot`, hydrates the
decided requirements, and calls `workflow.acontinue_run(...)`.  agno restores
the persisted `WorkflowRunOutput` from `agno_workflow_sessions` and continues
execution.

**Frontend implications**:

- HTTP 200 returns *before* the resume completes — the status returned in the
  response body may still be `awaiting_approval` for a brief moment.  Poll
  `GET /workflows/{id}/runs/{run_id}` to observe the actual transition.
- Resume latency depends on the agno workflow's next step (LLM call, tool
  call, etc.); the `/approve` HTTP itself is fast (~20 ms).
- If multiple requirements are pending on the same run, decide them one at a
  time; agno only proceeds when *all* outstanding requirements on the current
  step are resolved.

### Failure modes shared across endpoints

If the pod that started a background `continue_run` / `runner.run` task dies
mid-execution, the run stays at `RUNNING` past its expected duration.  An
operator currently has to mark such runs `FAILED` manually (e.g. via mongosh)
— automated orphan-run recovery is tracked as a follow-up.

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

### WorkflowCanvas

```typescript
{
  viewport: {
    x: number;                    // Canvas viewport x offset
    y: number;                    // Canvas viewport y offset
    zoom: number;                 // Canvas viewport zoom level
  };
}
```

### WorkflowNode

```typescript
{
  id: string;                    // Node ID (UUID)
  name: string;                  // Node name
  nodeType: string;              // step | parallel | loop | condition | router
  position: {
    x: number;                   // Node x coordinate on the canvas
    y: number;                   // Node y coordinate on the canvas
  };
  executorKey?: string;          // MCP tool name or A2A agent name (required for step nodes if a2aPool is not provided)
  a2aPool?: string[];            // A2A agent pool (max 5 agents, alternative to executorKey for step nodes)
  stepConfig?: StepConfig;       // Step-level retry and error handling configuration (step nodes only)
  config: object;                // Node configuration
  children: WorkflowNode[];      // Child nodes for parallel and loop nodes only
  trueSteps: WorkflowNode[];     // Sequential steps for the true branch of a condition node (≥ 1 required)
  falseSteps: WorkflowNode[];    // Sequential steps for the false branch of a condition node (optional)
  choices: RouterChoice[];       // Named choices for router nodes (≥ 2 required)
  conditionCel?: string;         // CEL expression for condition/router nodes
  loopConfig?: LoopConfig;       // Loop configuration for loop nodes
  humanReview?: HumanReview;     // HITL v2: per-node Human-In-The-Loop configuration; see HumanReview type
}
```

> **No legacy `require_approval` shim**: the path-A `require_approval` /
> `approval_timeout_seconds` fields have been removed. They are **not** accepted,
> auto-migrated, or upgraded on read — with the current `APIBaseModel` config
> (`extra` at Pydantic's default `ignore`), unknown legacy fields are silently
> ignored. New clients must use `humanReview` exclusively. See
> [Backward Compatibility](#backward-compatibility--legacy-require_approval-fields).

### HumanReview

Per-node HITL configuration (translated 1:1 to agno's `HumanReview`).
Field × node-type compatibility is enforced server-side; sending an unsupported
combination returns 400.

```typescript
{
  requiresConfirmation: boolean;        // step / steps / loop / router / condition — pause before/after exec for approval
  confirmationMessage?: string;
  requiresUserInput: boolean;           // step / router only — collect form values before exec
  userInputMessage?: string;
  userInputSchema?: UserInputField[];
  requiresOutputReview: boolean;        // step / router only — review/edit/reject output after exec
  outputReviewMessage?: string;
  requiresIterationReview: boolean;     // loop only — review after each iteration
  iterationReviewMessage?: string;
  onReject: 'skip' | 'cancel' | 'retry' | 'else_branch';   // else_branch is condition-only
  timeoutSeconds?: number;              // pause timeout; null = use run-level default
  onTimeout: 'approve' | 'skip' | 'cancel';
}
```

**Node-type × field compatibility matrix**:

| Field                     | step | steps | condition | loop | router | parallel |
|---------------------------|:----:|:-----:|:---------:|:----:|:------:|:--------:|
| requiresConfirmation      | ✅   | ✅    | ✅        | ✅   | ✅     | ❌       |
| requiresUserInput         | ✅   | —     | —         | —    | ✅     | ❌       |
| requiresOutputReview      | ✅   | —     | —         | —    | ✅     | ❌       |
| requiresIterationReview   | —    | —     | —         | ✅   | —      | ❌       |
| onReject = else_branch    | —    | —     | ✅ only   | —    | —      | ❌       |

PARALLEL nodes reject any HITL field (agno itself forbids it because parallel
branches execute concurrently and cannot be individually paused).

### UserInputField

```typescript
{
  name: string;
  fieldType: 'string' | 'number' | 'boolean' | 'array';
  description?: string;
  required: boolean;
  defaultValue?: unknown;
}
```

### StepRequirementSummary

Returned inside `WorkflowRun.pendingRequirements` (see Get Workflow Run Detail
response). Each element represents one HITL gate awaiting user decision. The
frontend chooses which decision UI to render based on the `requires*` flags.

```typescript
{
  schemaVersion: number;                // 1 for current HITL v2 layout
  stepId: string;
  stepName?: string;
  stepIndex?: number;
  stepType?: 'step' | 'loop' | 'router' | 'condition' | 'steps';

  // Capability flags — drive which UI variant to render
  requiresConfirmation: boolean;
  requiresUserInput: boolean;
  requiresOutputReview: boolean;
  requiresRouteSelection: boolean;

  // User-facing prompts
  confirmationMessage?: string;
  userInputMessage?: string;
  userInputSchema?: PendingUserInputField[];   // runtime payload — NOT the authoring UserInputField
  outputReviewMessage?: string;
  availableChoices?: string[];          // for router selection
  allowMultipleSelections: boolean;

  // Post-execution review (output_review only)
  stepOutput?: object;
  isPostExecution: boolean;

  // Decision state (null until user resolves)
  confirmed?: boolean;
  rejectionFeedback?: string;
  editedOutput?: unknown;
  userInput?: object;
  selectedChoices?: string[];

  // Retry + timeout
  retryCount: number;
  maxRetries?: number;
  timeoutAt?: string;                   // ISO8601
  onTimeout: 'approve' | 'skip' | 'cancel';
  onReject: 'skip' | 'cancel' | 'retry' | 'else_branch';
}
```

### PendingUserInputField

The `userInputSchema` inside a `StepRequirementSummary` is **not** the authoring
[`UserInputField`](#userinputfield). It is the live agno runtime payload
(`StepRequirement.to_dict()`), so its shape differs deliberately:

- `fieldType` is passed through verbatim as agno's Python type name
  (`"str" | "int" | "float" | "bool" | "list" | "dict"`), **not** coerced into
  the authoring `'string' | 'number' | 'boolean' | 'array'` set.
- Carries agno's `value` / `allowedValues` instead of the authoring `defaultValue`.
- `required` defaults to `true` (agno's default).

```typescript
{
  name: string;
  fieldType?: string;            // agno type name: "str" | "int" | "float" | "bool" | "list" | "dict"
  description?: string;
  required: boolean;             // defaults to true
  value?: unknown;               // value collected so far (null until the user submits)
  allowedValues?: unknown[];     // optional allow-list for validation
}
```

### RouterChoice

```typescript
{
  name: string;                  // Choice name — must match what the router's conditionCel selector returns
  steps: WorkflowNode[];         // Sequential steps to execute when this choice is selected (≥ 1 required)
}
```

### StepConfig

```typescript
{
  maxRetries: number;            // Maximum number of retries (default: 0, min: 0)
  onError: string;               // Error handling: fail | skip | retry (default: fail)
  backoffBaseSeconds: number;    // Base wait time for first retry (default: 1.0, must be > 0)
  backoffMaxSeconds: number;     // Maximum wait time for any retry (default: 60.0, must be > 0)
}
```

### LoopConfig

```typescript
{
  maxIterations: number;         // Max iterations (min: 1)
  endConditionCel?: string;      // CEL expression for loop termination
}
```

### NodeRunDetail

Full detail of a single node execution, including I/O snapshots. Returned by endpoints 11 and 12.

```typescript
{
  id: string;                    // NodeRun document ID
  nodeId: string;                // WorkflowNode.id from the workflow definition
  nodeName: string;              // Human-readable step name
  workflowRunId: string;         // Parent WorkflowRun ID
  status: string;                // NodeRunStatus value (see below)
  attempt: number;               // 1-based attempt counter (0 = not yet started)
  inputSnapshot: object | null;  // Input fed to the executor; null for legacy runs
  outputSnapshot: object | null; // Output produced by the executor; null if not yet complete
  error: string | null;          // Last error message if the node failed; otherwise null
  startedAt: string | null;      // ISO8601; null if not yet started
  finishedAt: string | null;     // ISO8601; null if not yet terminal
}
```

### NodeRunListResponse

Returned by `GET /workflows/{id}/runs/{run_id}/nodes`.

```typescript
{
  runId: string;                 // WorkflowRun ID
  workflowId: string;            // WorkflowDefinition ID
  nodeRuns: NodeRunDetail[];     // All NodeRuns for the run, ordered by startedAt ascending
}
```

---

## Tree-Shaped Workflow Example (Multi-Step Branches)

Both `condition` and `router` nodes support sequential multi-step branches. The
canonical motivating example — node A followed by a CONDITION B that runs
`C → E → G` on the true branch and `D → F → H` on the false branch — is expressed
as:

```json
{
  "name": "Tree-Shaped Workflow",
  "nodes": [
    {
      "name": "A",
      "nodeType": "step",
      "executorKey": "tool-a"
    },
    {
      "name": "B",
      "nodeType": "condition",
      "conditionCel": "input.routeToTrue == true",
      "trueSteps": [
        { "name": "C", "nodeType": "step", "executorKey": "tool-c" },
        { "name": "E", "nodeType": "step", "executorKey": "tool-e" },
        { "name": "G", "nodeType": "step", "executorKey": "tool-g" }
      ],
      "falseSteps": [
        { "name": "D", "nodeType": "step", "executorKey": "tool-d" },
        { "name": "F", "nodeType": "step", "executorKey": "tool-f" },
        { "name": "H", "nodeType": "step", "executorKey": "tool-h" }
      ]
    }
  ]
}
```

Routers follow the same pattern with named multi-step choices:

```json
{
  "name": "Multi-Step Router",
  "nodes": [
    {
      "name": "research-router",
      "nodeType": "router",
      "conditionCel": "input.strategy",
      "choices": [
        {
          "name": "tech",
          "steps": [
            { "name": "hn-research",  "nodeType": "step", "executorKey": "hackernews-agent" },
            { "name": "deep-dive",    "nodeType": "step", "executorKey": "analysis-agent" }
          ]
        },
        {
          "name": "general",
          "steps": [
            { "name": "web-research", "nodeType": "step", "executorKey": "web-agent" }
          ]
        }
      ]
    }
  ]
}
```

Notes on the selector semantics:

- The router's `conditionCel` must return a string that matches one of the choice
  names (e.g. `"tech"` or `"general"` above).
- Each `RouterChoice` is compiled into a named agno `Steps` container regardless of
  whether it has one or many inner steps, so the selector contract does not change
  when a choice grows from one step to several.

---

### WorkflowRunStatus

- `pending`: Run is queued
- `running`: Run is in progress
- `paused`: Run is paused by user directive (POST /pause); awaiting RESUME
- `awaiting_approval`: Run is holding at one or more HITL gates; awaiting decision via POST /approve
- `completed`: Run completed successfully
- `failed`: Run failed
- `cancelled`: Run was cancelled

### NodeRunStatus

- `pending`: Node execution is queued
- `running`: Node is executing
- `awaiting_approval`: Node is at an HITL gate (mirrored from WorkflowRun.status for UI highlighting)
- `completed`: Node completed successfully
- `failed`: Node execution failed
- `skipped`: Node was skipped
- `cancelled`: Node execution was cancelled

### ResolvedDependencyResolution

- `reuse_previous_output`: Reuse output from previous run
- `rerun`: Re-execute the node

---

## Naming Conventions

- All field names use **camelCase** in API requests and responses
- MongoDB document field names use **snake_case** internally
- Response models use `response_model_by_alias=True` for automatic conversion

---

## HITL v2 Notes (Backward Compatibility & Internal Fields)

This section documents implementation-level details introduced by the HITL v2
migration (agno-native HumanReview). Frontend clients generally do not need
these details, but they're documented here for operators and integration
partners.

### Backward Compatibility — Legacy `require_approval` Fields

The path-A `require_approval: bool` and `approval_timeout_seconds: int` fields on
WorkflowNode have been **removed** from the v2 API/model.

Current behavior is:

- There is **no request/model compatibility shim** for legacy `requireApproval`,
  `require_approval`, or `approval_timeout_seconds` fields — no auto-migration
  and no validator rejects them.
- Clients must send the v2 `humanReview` structure instead of the removed legacy
  fields.
- With the current `APIBaseModel` configuration (`extra` left at the Pydantic
  default of `ignore`), unknown legacy fields are **silently ignored** rather
  than rejected or auto-migrated.
- Pre-v2 MongoDB documents containing these legacy fields are **not**
  transparently upgraded by the API/model layer; operators reading older data
  should not expect `humanReview` to be synthesized automatically.

If compatibility handling is added in the future, this section must be updated to
document the exact request-validation and document-migration behavior actually
implemented.

### Internal WorkflowRun Fields (Not API-Exposed)

The following fields exist on the persisted `WorkflowRun` document but are not
returned in API responses. They are documented here for operators reading
MongoDB directly or building admin tooling.

| Field                                  | Type   | Purpose |
|----------------------------------------|--------|---------|
| `triggering_user_id`                   | string | User ID captured at trigger time. Used (with `triggering_username` / `triggering_scopes`) to re-mint a short-lived service JWT for downstream MCP/A2A executor calls during HITL resume. The raw bearer token is **never** persisted — storing it would be useless since it expires during the pause. |
| `triggering_username`                  | string | Username captured at trigger time; carried into the re-minted resume JWT identity |
| `triggering_scopes`                    | array  | Scopes captured at trigger time; copied into the re-minted resume JWT |
| `pending_requirements`                 | array  | Serialized agno `StepRequirement` objects awaiting user decision; populated when `arun()` returns `is_paused=True`. **Surfaced to clients as `pendingRequirements`** in run-detail responses. |
| `pending_directive`                    | enum   | Runtime intervention signal for pause/resume/cancel/retry (separate from HITL decisions, which go via `/approve`) |
| `paused_at`                            | datetime | Set when run enters `paused` via user `/pause` directive (separate from HITL `awaiting_approval`) |

### Known Limitations

- **Authorization can drift during long HITL pauses**: resume does not reuse the
  original bearer token (which would have expired anyway). Instead it re-mints a
  short-lived service JWT from the captured trigger identity (`triggering_user_id`
  / `triggering_username` / `triggering_scopes`). If the user's account state,
  roles, or scopes change before they resolve the pause, downstream MCP/A2A calls
  may be authorized differently than at trigger time. Runs with no captured
  `triggering_user_id` (e.g. script-driven) get an empty token, so auth-required
  steps return 401 (visible in `nodeRuns[].error`).
- **agno cancel edge case ([#7929](https://github.com/agno-agi/agno/issues/7929) OPEN)**:
  cancel signals may not propagate through certain `acontinue_run +
  external_execution` code paths inside agno. Our wait-loop wrapper's CANCEL
  branch is the safety net (checks `pending_directive` before each step
  attempt); dual-signal sync mitigates but does not 100% eliminate the race.
- **HITL `on_reject=retry` attempt count**: agno's internal HITL retry does not
  flow into our `NodeRun.attempt` field. The UI's attempt counter reflects
  wait-loop retries only.

---
