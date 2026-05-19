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
  "nodes": [
    {
      "id": "node-1",
      "name": "Validate Customer Data",
      "nodeType": "step",
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
      "conditionCel": "input.customerType == 'enterprise'",
      "trueSteps": [
        {
          "name": "Enterprise Onboarding Path",
          "nodeType": "parallel",
          "children": [
            {
              "name": "Send Welcome Email",
              "nodeType": "step",
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
- `nodes` (required, array): At least one root node required
  - `id` (optional, string): Node ID (auto-generated if not provided)
  - `name` (required, string): Node name
  - `nodeType` (required, string): Node type (`step`, `parallel`, `loop`, `condition`, `router`)
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
- `nodes` (array): Update workflow nodes (follows same structure and validation as create)

**Response**: `200 OK`
```json
{
  "id": "wf-demo-id",
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

### 8. Get Workflow Run Detail

**Endpoint**: `GET /api/v1/workflows/{workflow_id}/runs/{run_id}`

**Response**: `200 OK`
```json
{
  "id": "run-demo-id",
  "workflowDefinitionId": "wf-demo-id",
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
      "id": "node-run-demo-id",
      "workflowRunId": "run-demo-id",
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
- `paused`: Run is paused and waiting to resume
- `completed`: Run completed successfully
- `failed`: Run failed
- `cancelled`: Run was cancelled

### NodeRunStatus

- `pending`: Node execution is queued
- `running`: Node is executing
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
