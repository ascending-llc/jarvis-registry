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
  per_page?: number;        // Items per page (default: 20, max: 100)
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
      "protocol_version": "1.0",
      "tags": ["code", "review"],
      "num_skills": 5,
      "enabled": true,
      "status": "active",
      "permissions": {
        "VIEW": true,
        "EDIT": true,
        "DELETE": true,
        "SHARE": true
      },
      "author": "507f1f77bcf86cd799439012",
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-20T15:45:00Z"
    }
  ],
  "pagination": {
    "total": 150,
    "page": 1,
    "per_page": 20,
    "total_pages": 8
  }
}
```

**Note:**
- Uses `AgentListItem` schema for list items
- All field names use snake_case convention

---

### 2. Get Agent Statistics

**Endpoint**: `GET /api/v1/agents/stats`

**Response**: `200 OK`
```json
{
  "total_agents": 150,
  "enabled_agents": 120,
  "disabled_agents": 30,
  "by_status": {
    "active": 130,
    "inactive": 15,
    "error": 5
  },
  "by_transport": {
    "HTTP+JSON": 100,
    "JSONRPC": 30,
    "GRPC": 20
  },
  "total_skills": 450,
  "average_skills_per_agent": 3.0
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
  "protocol_version": "1.0",
  "capabilities": {
    "streaming": true,
    "push_notifications": false
  },
  "skills": [
    {
      "id": "code-analysis",
      "name": "Code Analysis",
      "description": "Analyze code quality",
      "tags": ["analysis"],
      "input_modes": ["text/plain"],
      "output_modes": ["application/json"]
    }
  ],
  "security_schemes": {
    "bearer": {
      "type": "http",
      "scheme": "bearer"
    }
  },
  "preferred_transport": "HTTP+JSON",
  "default_input_modes": ["text/plain"],
  "default_output_modes": ["application/json"],
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
  "well_known": {
    "enabled": true,
    "url": "https://example.com/.well-known/agent-card.json",
    "last_sync_at": "2024-01-20T12:00:00Z",
    "last_sync_status": "success",
    "last_sync_version": "1.0.0"
  },
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-20T15:45:00Z"
}
```

**Note:**
- Uses `AgentDetailResponse` schema
- All field names use snake_case convention

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
  "protocol_version": "1.0",
  "capabilities": {
    "streaming": true,
    "push_notifications": false
  },
  "skills": [
    {
      "id": "code-analysis",
      "name": "Code Analysis",
      "description": "Analyze code quality",
      "tags": ["analysis"],
      "input_modes": ["text/plain"],
      "output_modes": ["application/json"]
    }
  ],
  "security_schemes": {},
  "preferred_transport": "HTTP+JSON",
  "default_input_modes": ["text/plain"],
  "default_output_modes": ["application/json"],
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
  "protocol_version": "1.0",
  "capabilities": {
    "streaming": true,
    "push_notifications": false
  },
  "skills": [...],
  "security_schemes": {},
  "preferred_transport": "HTTP+JSON",
  "default_input_modes": ["text/plain"],
  "default_output_modes": ["application/json"],
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
  "well_known": null,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
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
  "protocol_version": "1.0",
  "capabilities": {...},
  "skills": [...],
  "security_schemes": {...},
  "preferred_transport": "HTTP+JSON",
  "default_input_modes": ["text/plain"],
  "default_output_modes": ["application/json"],
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
  "well_known": {...},
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-20T15:45:00Z"
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
  "protocol_version": "1.0",
  "capabilities": {...},
  "skills": [...],
  "security_schemes": {...},
  "preferred_transport": "HTTP+JSON",
  "default_input_modes": ["text/plain"],
  "default_output_modes": ["application/json"],
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
  "well_known": {...},
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-20T15:45:00Z"
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
  "agent_id": "507f1f77bcf86cd799439011",
  "agent_name": "Code Review Agent",
  "skills": [
    {
      "id": "code-analysis",
      "name": "Code Analysis",
      "description": "Analyze code quality",
      "tags": ["analysis", "quality"],
      "input_modes": ["text/plain"],
      "output_modes": ["application/json"]
    }
  ],
  "total_skills": 1
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
  "sync_status": "success",
  "synced_at": "2024-01-20T15:45:00Z",
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
