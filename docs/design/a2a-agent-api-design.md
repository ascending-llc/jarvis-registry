# A2A Agent Management API


## API Route Prefix

```
/api/v1/agents
```

---

## API Endpoints

### 1. List Agents

**Endpoint**: `GET /api/v1/agents`

**Query Parameters**:
```typescript
{
  query?: string;           // Search keywords (name, description, tags, skills)
  status?: string;          // Status filter: active | inactive | error
  page?: number;            // Page number (default: 1)
  perPage?: number;         // Items per page (default: 20, max: 100)
}
```

**Response**: `200 OK`
```json
{
  "agents": [
    {
      "id": "507f1f77bcf86cd799439011",
      "path": "/code-reviewer",
      "name": "Code Review Agent",
      "description": "AI-powered code review assistant",
      "url": "https://example.com/agents/code-reviewer",
      "version": "1.0.0",
      "protocolVersion": "1.0",
      "tags": ["code", "review"],
      "numSkills": 2,
      "skills": [
        {
          "id": "code-analysis",
          "name": "Code Analysis",
          "description": "Analyze code quality and identify issues",
          "tags": ["analysis", "quality"],
          "inputModes": ["text/plain"],
          "outputModes": ["application/json"]
        },
        {
          "id": "bug-detection",
          "name": "Bug Detection",
          "description": "Detect potential bugs in code",
          "tags": ["bugs", "detection"],
          "inputModes": ["text/plain"],
          "outputModes": ["application/json"]
        }
      ],
      "enabled": true,
      "status": "active",
      "config": {
        "title": "Code Review Agent",
        "description": "AI-powered code review assistant",
        "type": "jsonrpc"
      },
      "permissions": {
        "VIEW": true,
        "EDIT": true,
        "DELETE": true,
        "SHARE": true
      },
      "author": "507f1f77bcf86cd799439012",
      "createdAt": "2024-01-15T10:30:00Z",
      "updatedAt": "2024-01-20T15:45:00Z"
    }
  ],
  "pagination": {
    "total": 150,
    "page": 1,
    "perPage": 20,
    "totalPages": 8
  }
}
```

**Note:**
- Uses `AgentListItem` schema for list items
- All field names use camelCase convention
- Each agent includes `config` field with user-provided metadata

---

### 2. Get Agent Statistics

**Endpoint**: `GET /api/v1/agents/stats`

**Response**: `200 OK`
```json
{
  "totalAgents": 150,
  "enabledAgents": 120,
  "disabledAgents": 30,
  "byStatus": {
    "active": 130,
    "inactive": 15,
    "error": 5
  },
  "byTransport": {
    "HTTP+JSON": 100,
    "JSONRPC": 30,
    "GRPC": 20
  },
  "totalSkills": 450,
  "averageSkillsPerAgent": 3.0
}
```

**Permission**: Admin only

---

### 3. Get Agent Detail

**Endpoint**: `GET /api/v1/agents/{agent_id}`

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "path": "/code-reviewer",
  "name": "Code Review Agent",
  "description": "AI-powered code review assistant",
  "url": "https://example.com/agents/code-reviewer",
  "version": "1.0.0",
  "protocolVersion": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "numSkills": 1,
  "skills": [
    {
      "id": "code-analysis",
      "name": "Code Analysis",
      "description": "Analyze code quality",
      "tags": ["analysis"],
      "inputModes": ["text/plain"],
      "outputModes": ["application/json"]
    }
  ],
  "securitySchemes": {
    "bearer": {
      "type": "http",
      "scheme": "bearer"
    }
  },
  "preferredTransport": "HTTP+JSON",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json"],
  "provider": {
    "organization": "AI Labs",
    "url": "https://ailabs.com"
  },
  "tags": ["code", "review"],
  "status": "active",
  "enabled": true,
  "config": {
    "title": "Code Review Agent",
    "description": "AI-powered code review assistant",
    "type": "jsonrpc"
  },
  "permissions": {
    "VIEW": true,
    "EDIT": true,
    "DELETE": true,
    "SHARE": true
  },
  "author": "507f1f77bcf86cd799439012",
  "wellKnown": {
    "enabled": true,
    "url": "https://example.com/.well-known/agent-card.json",
    "lastSyncAt": "2024-01-20T12:00:00Z",
    "lastSyncStatus": "success",
    "lastSyncVersion": "1.0.0"
  },
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Note:**
- Uses `AgentDetailResponse` schema
- All field names use camelCase convention
- Includes `config` field with user-provided metadata

**Error**: `404` Agent not found, `403` Access denied

---

### 4. Create Agent

**Endpoint**: `POST /api/v1/agents`

**Description**: Register a new A2A agent. Only 5 fields are required in the request body. All other agent information (version, capabilities, skills, etc.) is automatically fetched from the agent's `.well-known/agent-card.json` endpoint using the A2A SDK. The fetched agent card is stored as-is without modification in the `card` field.

**Request Body**:
```json
{
  "path": "/code-reviewer",
  "title": "Code Review Agent",
  "description": "AI-powered code review assistant",
  "url": "https://example.com/agents/code-reviewer",
  "type": "jsonrpc"
}
```

**Request Fields**:
- `path` (required, string): Unique registry path identifier (e.g., `/code-reviewer`)
- `title` (required, string): Display title for the agent in the registry (stored in `config.title`)
- `description` (optional, string): Description of the agent for the registry (stored in `config.description`)
- `url` (required, string): Agent endpoint URL - the agent card will be automatically fetched from `{url}/.well-known/agent-card.json`
- `type` (required, string): Transport type - must be one of: `jsonrpc`, `grpc`, `http_json`

**Data Storage Structure**:
- `card`: Original agent card from third-party URL (unmodified)
- `config`: Registry-specific configuration containing user-provided `title`, `description`, and `type`

**Auto-Fetch Behavior**:
1. System fetches agent card from `{url}/.well-known/agent-card.json` using A2A SDK
2. Validates the fetched agent card structure
3. **Stores the original card data without any modifications in the `card` field**
4. User-provided `title`, `description`, and `type` are stored separately in the `config` field
5. Enables wellKnown sync automatically for future updates
6. **Tags field**: Initialized as empty array `[]` - tags are registry-level metadata separate from skill tags, and can be managed manually if needed

**Response**: `201 Created`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "path": "/code-reviewer",
  "name": "Code Review Agent",
  "description": "AI-powered code review assistant",
  "url": "https://example.com/agents/code-reviewer",
  "version": "1.0.0",
  "protocolVersion": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [...],
  "securitySchemes": {},
  "preferredTransport": "HTTP+JSON",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json"],
  "provider": {
    "organization": "AI Labs",
    "url": "https://ailabs.com"
  },
  "tags": [],
  "status": "active",
  "enabled": false,
  "config": {
    "title": "Code Review Agent",
    "description": "AI-powered code review assistant",
    "type": "jsonrpc"
  },
  "permissions": {
    "VIEW": true,
    "EDIT": true,
    "DELETE": true,
    "SHARE": true
  },
  "author": "507f1f77bcf86cd799439012",
  "wellKnown": {
    "enabled": true,
    "url": "https://example.com/agents/code-reviewer",
    "lastSyncAt": "2024-01-15T10:30:00Z",
    "lastSyncStatus": "success",
    "lastSyncVersion": "1.0.0"
  },
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-15T10:30:00Z"
}
```

**Note:**
- Uses `AgentDetailResponse` schema (not a separate create response)
- Automatically grants OWNER permission to creator
- ACL resource type is `ResourceType.AGENT`
- Agent is created with `enabled: false` by default for safety
- All agent metadata is auto-fetched from the provided URL and stored in `card` field without modification
- User-provided configuration is stored separately in `config` field

**Error**:
- `400` Validation error, invalid transport type, or failed to fetch agent card from URL
- `409` Path already exists

---

### 5. Update Agent

**Endpoint**: `PATCH /api/v1/agents/{agent_id}`

**Description**: Update an existing agent. Multiple fields can be updated via this endpoint. When the `url` field is updated, all other agent information is automatically fetched from the new URL's `.well-known/agent-card.json` endpoint and stored as-is in the `card` field.

**Request Body** (all fields optional):
```json
{
  "path": "/new-code-reviewer",
  "title": "Updated Agent Name",
  "description": "Updated description",
  "url": "https://example.com/agents/new-code-reviewer",
  "type": "grpc",
  "enabled": true
}
```

**Request Fields** (all optional):
- `path` (string): Update the registry path
- `title` (string): Update the display title (stored in `config.title`)
- `description` (string): Update the description (stored in `config.description`)
- `url` (string): Update the agent endpoint URL
- `type` (string): Update the transport type - must be one of: `jsonrpc`, `grpc`, `http_json`
- `enabled` (boolean): Update the enabled state

**Auto-Fetch Behavior**:
1. **If `url` is updated**:
   - System fetches new agent card from `{new_url}/.well-known/agent-card.json`
   - **Stores the original card data without any modifications in the `card` field**
   - User-provided fields (`title`, `description`, `type`) are updated in the `config` field separately
   - wellKnown sync is updated with new URL and sync status

2. **If only `title`, `description`, or `type` is updated** (no URL change):
   - Only these fields are updated in the `config` field
   - No agent card re-fetch occurs
   - The `card` field remains unchanged

3. **If only `path` is updated**:
   - Registry path is updated (must be unique)
   - Agent card and config remain unchanged

4. **If only `enabled` is updated**:
   - Agent enabled state is toggled
   - No other fields are changed

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "path": "/new-code-reviewer",
  "name": "Updated Agent Name",
  "description": "Updated description",
  "url": "https://example.com/agents/new-code-reviewer",
  "version": "1.1.0",
  "protocolVersion": "1.0",
  "capabilities": {...},
  "skills": [...],
  "securitySchemes": {...},
  "preferredTransport": "HTTP+JSON",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json"],
  "provider": {...},
  "tags": ["new", "tags"],
  "status": "active",
  "enabled": true,
  "config": {
    "title": "Updated Agent Name",
    "description": "Updated description",
    "type": "grpc"
  },
  "permissions": {
    "VIEW": true,
    "EDIT": true,
    "DELETE": true,
    "SHARE": true
  },
  "author": "507f1f77bcf86cd799439012",
  "wellKnown": {
    "enabled": true,
    "url": "https://example.com/agents/new-code-reviewer",
    "lastSyncAt": "2024-01-20T15:45:00Z",
    "lastSyncStatus": "success",
    "lastSyncVersion": "1.1.0"
  },
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Note:**
- Uses `AgentDetailResponse` schema (not a separate update response)
- Returns complete agent details after update
- If URL is changed, automatically re-fetches all agent metadata from new URL and stores in `card` field
- User-provided configuration is stored separately in `config` field

**Error**:
- `400` Validation error, invalid transport type, or failed to fetch agent card from new URL
- `404` Agent not found
- `403` Access denied
- `409` New path conflicts with existing agent

**Permission**: Requires EDIT permission

---

### 6. Delete Agent

**Endpoint**: `DELETE /api/v1/agents/{agent_id}`

**Response**: `204 No Content`

**Error**: `404` Agent not found, `403` Access denied

**Permission**: Requires DELETE permission

**Note**: Deletes all associated ACL permission records when deleting Agent

---

### 7. Toggle Agent

**Endpoint**: `POST /api/v1/agents/{agent_id}/toggle`

**Request Body**:
```json
{
  "enabled": true
}
```

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "path": "/code-reviewer",
  "name": "Code Review Agent",
  "description": "AI-powered code review assistant",
  "url": "https://example.com/agents/code-reviewer",
  "version": "1.0.0",
  "protocolVersion": "1.0",
  "capabilities": {...},
  "skills": [...],
  "securitySchemes": {...},
  "preferredTransport": "HTTP+JSON",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json"],
  "provider": {...},
  "tags": ["code", "review"],
  "status": "active",
  "enabled": true,
  "config": {
    "title": "Code Review Agent",
    "description": "AI-powered code review assistant",
    "type": "jsonrpc"
  },
  "permissions": {
    "VIEW": true,
    "EDIT": true,
    "DELETE": true,
    "SHARE": true
  },
  "author": "507f1f77bcf86cd799439012",
  "wellKnown": {...},
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Note:**
- Uses `AgentDetailResponse` schema (not a separate toggle response)
- Returns complete agent details after toggle
- Includes `config` field with user-provided metadata

**Error**: `404` Agent not found, `403` Access denied

**Permission**: Requires EDIT permission

---

### 8. Get Agent Skills

**Endpoint**: `GET /api/v1/agents/{agent_id}/skills`

**Response**: `200 OK`
```json
{
  "agentId": "507f1f77bcf86cd799439011",
  "agentName": "Code Review Agent",
  "skills": [
    {
      "id": "code-analysis",
      "name": "Code Analysis",
      "description": "Analyze code quality",
      "tags": ["analysis", "quality"],
      "inputModes": ["text/plain"],
      "outputModes": ["application/json"]
    }
  ],
  "totalSkills": 1
}
```

**Error**: `404` Agent not found, `403` Access denied

**Permission**: Requires VIEW permission

---

### 9. Sync Well-Known

**Endpoint**: `POST /api/v1/agents/{agent_id}/wellknown`

**Response**: `200 OK`
```json
{
  "message": "Well-known configuration synced successfully",
  "syncStatus": "success",
  "syncedAt": "2024-01-20T15:45:00Z",
  "version": "1.0.0",
  "changes": [
    "Updated version to 1.0.0",
    "Added 2 new skills"
  ]
}
```

**Error**: `400` Well-known sync not enabled, `404` Agent not found or URL unreachable

**Permission**: Requires EDIT permission

---

### 10. Get Agent Card (Well-Known)

**Endpoint**: `GET /api/v1/agents/.well-known/agent-cards?url={agent_url}`

**Authentication**: JWT Bearer token required

**Description**: Fetch and validate agent card from a remote A2A agent URL. This endpoint is used during agent creation (before ID is assigned) to validate and preview the agent card from the provided URL. It uses the A2A SDK to fetch and validate the card from the remote agent's `.well-known/agent-card.json` endpoint.

**Query Parameters**:
- `url` (required, string): The base URL of the A2A agent to fetch card from (e.g., `https://example.com/agents/code-reviewer`)

**Example Request**:
```
GET /api/v1/agents/.well-known/agent-cards?url=https://example.com/agents/code-reviewer
Authorization: Bearer <token>
```

**Response**: `200 OK`
```json
{
  "name": "Code Review Agent",
  "description": "AI-powered code review assistant",
  "url": "https://example.com/agents/code-reviewer",
  "version": "1.0.0",
  "protocolVersion": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "preferredTransport": "HTTP+JSON",
  "provider": {
    "organization": "AI Labs",
    "url": "https://ailabs.com"
  },
  "skills": [
    {
      "id": "code-analysis",
      "name": "Code Analysis",
      "description": "Analyze code quality",
      "tags": ["analysis", "quality"]
    }
  ],
  "securitySchemes": {
    "bearer": {
      "type": "http",
      "scheme": "bearer"
    }
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json"]
}
```

**Features**:
- Fetches agent card from remote URL using A2A SDK's `A2ACardResolver`
- Validates agent card structure per A2A protocol (via a2a-sdk)
- No caching (for real-time validation during creation)
- 15-second timeout for remote requests
- Supports both with/without `.well-known` suffix in URL

**Headers**:
```
Content-Type: application/json
```

**Implementation**:
```python
# Fetch and validate from remote URL using SDK
async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
    resolver = A2ACardResolver(base_url=url, httpx_client=client)
    agent_card = await resolver.get_agent_card()

agent_card_data = agent_card.model_dump(mode="json", exclude_none=True, by_alias=True)
```

**Use Cases**:
- Pre-creation validation: Validate agent before registering
- Agent discovery: Preview agent capabilities from URL
- Frontend integration: Fetch agent details during creation flow
- URL validation: Verify the agent URL is accessible and valid

**Error Responses**:
- `400 Bad Request`: Invalid URL format
- `404 Not Found`: Agent card not found at the provided URL
- `500 Internal Server Error`: Failed to fetch or parse agent card from remote URL

**Permission**: No specific resource permission required (uses authenticated user context only)

---
