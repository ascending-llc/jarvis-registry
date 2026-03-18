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
      "numSkills": 5,
      "enabled": true,
      "status": "active",
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

**Error**: `404` Agent not found, `403` Access denied

---

### 4. Create Agent

**Endpoint**: `POST /api/v1/agents`

**Request Body**:
```json
{
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
  "securitySchemes": {},
  "preferredTransport": "HTTP+JSON",
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["application/json"],
  "provider": {
    "organization": "AI Labs",
    "url": "https://ailabs.com"
  },
  "tags": ["code", "review"],
  "enabled": false
}
```

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
  "tags": ["code", "review"],
  "status": "active",
  "enabled": false,
  "permissions": {
    "VIEW": true,
    "EDIT": true,
    "DELETE": true,
    "SHARE": true
  },
  "author": "507f1f77bcf86cd799439012",
  "wellKnown": null,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-15T10:30:00Z"
}
```

**Note:**
- Uses `AgentDetailResponse` schema (not a separate create response)
- Automatically grants OWNER permission to creator
- ACL resource type is `ResourceType.AGENT`

**Error**: `400` Validation error, `409` Path already exists

---

### 5. Update Agent

**Endpoint**: `PATCH /api/v1/agents/{agent_id}`

**Request Body** (all fields optional):
```json
{
  "name": "Updated Agent Name",
  "description": "Updated description",
  "version": "1.1.0",
  "skills": [...],
  "tags": ["new", "tags"],
  "enabled": true
}
```

**Response**: `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "path": "/code-reviewer",
  "name": "Updated Agent Name",
  "description": "Updated description",
  "url": "https://example.com/agents/code-reviewer",
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
- Uses `AgentDetailResponse` schema (not a separate update response)
- Returns complete agent details after update

**Error**: `404` Agent not found, `403` Access denied

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
