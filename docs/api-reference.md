# MCP Gateway Registry - Complete API Reference

This document provides a comprehensive overview of all 49 API endpoints available in the MCP Gateway Registry, organized by category with authentication requirements, request/response specifications, and OpenAPI documentation links.

## Table of Contents

1. [API Categories](#api-categories)
2. [Authentication Schemes](#authentication-schemes)
3. [A2A Agent Management APIs](#a2a-agent-management-apis)
4. [Anthropic MCP Registry API v0](#anthropic-mcp-registry-api-v0)
5. [Internal Server Management APIs](#internal-server-management-apis)
6. [Authentication & Login APIs](#authentication--login-apis)
7. [Health Monitoring APIs](#health-monitoring-apis)
8. [Discovery & Well-Known Endpoints](#discovery--well-known-endpoints)
9. [Utility Endpoints](#utility-endpoints)
10. [Response Codes & Error Handling](#response-codes--error-handling)
11. [OpenAPI Specifications](#openapi-specifications)

---

## API Categories

| Category | Count | Auth Method | Purpose |
|----------|-------|-------------|---------|
| A2A Agent Management | 8 | JWT Bearer Token | Agent registration, discovery, and management |
| Anthropic Registry API v0 (Servers) | 3 | JWT Bearer Token | Standard MCP server discovery via Anthropic API spec |
| Internal Server Management (UI) | 10 | Session Cookie | Dashboard and service management |
| Internal Server Management (Admin) | 12 | HTTP Basic Auth | Administrative operations and group management |
| Authentication & Login | 7 | OAuth2 + Session | User authentication and provider management |
| Health Monitoring | 3 | Session Cookie / None | Real-time health updates and statistics |
| Discovery | 1 | None (Public) | Public MCP server discovery |
| Utility | 2 | Session Cookie / Public | Current user info and service health |
| **TOTAL** | **46** | **Multiple** | **Full registry functionality** |

---

## Authentication Schemes

### 1. JWT Bearer Token (Nginx-Proxied Auth)

**Used by:** A2A Agent APIs, Anthropic Registry API v0

**How it works:**
- Client sends JWT token in `Authorization: Bearer <token>` header
- Nginx validates token via `/validate` endpoint against auth-server
- Auth-server validates token against Keycloak
- Token scopes determine user permissions

**Token Sources:**
- Keycloak M2M service account (`mcp-gateway-m2m`)
- User tokens generated via `/api/tokens/generate`

**Example:**
```bash
curl -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ..." \
  http://localhost/v0.1/agents
```

---

### 2. Session Cookie (Enhanced Auth)

**Used by:** UI Server Management, Health Monitoring (WebSocket), Auth status endpoints

**How it works:**
- User logs in via OAuth2 (Keycloak)
- Auth-server sets `mcp_gateway_session` cookie
- Browser automatically includes cookie in subsequent requests
- Registry validates cookie against auth-server

**Example:**
```bash
curl -b "mcp_gateway_session=<session_value>" \
  http://localhost/api/servers
```

---

### 3. HTTP Basic Auth

**Used by:** Internal Admin Endpoints

**How it works:**
- Credentials: `ADMIN_USER:ADMIN_PASSWORD` from environment
- Sent in `Authorization: Basic <base64>` header
- Used for internal mcpgw-server operations

**Example:**
```bash
curl -u admin:password http://localhost/api/internal/register \
  -d "service_path=/example"
```

---

### 4. Public (No Authentication)

**Used by:** Discovery endpoints, login page, OAuth2 providers list

**Endpoints:**
- `GET /.well-known/mcp-servers`
- `GET /api/auth/login`
- `GET /api/auth/providers`
- `GET /health`

---

## A2A Agent Management APIs

**File:** `registry/api/agent_routes.py`
**Route Prefix:** `/api`
**Authentication:** JWT Bearer Token (nginx_proxied_auth)

### 1. Register Agent

**Endpoint:** `POST /api/agents/register`

**Purpose:** Register a new A2A agent in the registry

**Authentication:** Requires `publish_agent` scope

**Request Body:**
```json
{
  "name": "string",
  "description": "string",
  "path": "/agent-name",
  "url": "https://example.com/agent",
  "version": "1.0.0",
  "provider": "anthropic|custom|other",
  "security_schemes": {
    "scheme_name": {
      "type": "bearer|api_key|oauth2|etc",
      "description": "string"
    }
  },
  "skills": [
    {
      "name": "skill_name",
      "description": "string",
      "input_schema": {}
    }
  ],
  "tags": "string, comma, separated",
  "visibility": "public|private|internal",
  "license": "MIT|Apache-2.0|etc"
}
```

**Response:** `201 Created`
```json
{
  "message": "Agent registered successfully",
  "agent": {
    "name": "string",
    "path": "/agent-name",
    "url": "https://example.com/agent",
    "num_skills": 5,
    "registered_at": "2025-11-01T04:53:56.228791+00:00",
    "is_enabled": false
  }
}
```

**Error Codes:**
- `409 Conflict` - Agent path already exists
- `422 Unprocessable Entity` - Validation error (invalid JSON, missing fields)
- `403 Forbidden` - User lacks `publish_agent` permission

---

### 2. List Agents

**Endpoint:** `GET /api/agents`

**Purpose:** List all agents, optionally filtered

**Authentication:** Optional (results filtered by user permissions)

**Query Parameters:**
- `query` (optional, string) - Search query string
- `enabled_only` (optional, boolean, default: false) - Show only enabled agents
- `visibility` (optional, string) - Filter by visibility level

**Response:** `200 OK`
```json
{
  "agents": [
    {
      "name": "string",
      "path": "/agent-name",
      "description": "string",
      "is_enabled": true,
      "total_count": 5
    }
  ]
}
```

---

### 3. Get Single Agent

**Endpoint:** `GET /api/agents/{path:path}`

**Purpose:** Get a single agent by path

**Authentication:** JWT Bearer Token required

**Path Parameter:**
- `path` - Agent path (e.g., `/code-reviewer`)

**Response:** `200 OK`
```json
{
  "name": "Code Reviewer Agent",
  "path": "/code-reviewer",
  "description": "string",
  "url": "https://example.com/agents/code-reviewer",
  "version": "1.0.0",
  "skills": [
    {
      "name": "review_code",
      "description": "string"
    }
  ],
  "is_enabled": true
}
```

**Error Codes:**
- `404 Not Found` - Agent doesn't exist
- `403 Forbidden` - User not authorized

---

### 4. Update Agent

**Endpoint:** `PUT /api/agents/{path:path}`

**Purpose:** Update an existing agent

**Authentication:** Requires `modify_service` permission and ownership

**Path Parameter:**
- `path` - Agent path

**Request Body:** Same as registration request

**Response:** `200 OK` with updated agent card

**Error Codes:**
- `404 Not Found` - Agent doesn't exist
- `403 Forbidden` - User lacks modify permission
- `422 Unprocessable Entity` - Validation error

---

### 5. Delete Agent

**Endpoint:** `DELETE /api/agents/{path:path}`

**Purpose:** Delete an agent from registry

**Authentication:** Requires admin permission or agent ownership

**Path Parameter:**
- `path` - Agent path

**Response:** `204 No Content`

**Error Codes:**
- `404 Not Found` - Agent doesn't exist
- `403 Forbidden` - User lacks delete permission

---

### 6. Toggle Agent Status

**Endpoint:** `POST /api/agents/{path:path}/toggle`

**Purpose:** Enable or disable an agent

**Authentication:** Requires `toggle_service` permission

**Path Parameter:**
- `path` - Agent path

**Query Parameter:**
- `enabled` (boolean) - True to enable, false to disable

**Response:** `200 OK`
```json
{
  "path": "/agent-name",
  "is_enabled": true,
  "message": "Agent enabled successfully"
}
```

**Error Codes:**
- `404 Not Found` - Agent doesn't exist
- `403 Forbidden` - User lacks toggle permission

---

### 7. Discover Agents by Skills

**Endpoint:** `POST /api/agents/discover`

**Purpose:** Find agents that match required skills

**Authentication:** Optional

**Request Body:**
```json
{
  "skills": ["skill1", "skill2"],
  "tags": ["optional", "filters"]
}
```

**Query Parameter:**
- `max_results` (optional, integer, default: 10, max: 100)

**Response:** `200 OK`
```json
{
  "agents": [
    {
      "path": "/agent-name",
      "name": "string",
      "relevance_score": 0.95,
      "matching_skills": ["skill1"]
    }
  ]
}
```

**Error Codes:**
- `400 Bad Request` - No skills provided

---

### 8. Discover Agents Semantically

**Endpoint:** `POST /api/agents/discover/semantic`

**Purpose:** Find agents using NLP semantic search (FAISS vector search)

**Authentication:** Optional

**Query Parameters:**
- `query` (required, string) - Natural language query (e.g., "Find agents that can analyze code")
- `max_results` (optional, integer, default: 10, max: 100)

**Response:** `200 OK`
```json
{
  "agents": [
    {
      "path": "/code-reviewer",
      "name": "Code Reviewer Agent",
      "relevance_score": 0.92,
      "description": "Analyzes code for issues..."
    }
  ]
}
```

**Error Codes:**
- `400 Bad Request` - Empty query
- `500 Internal Server Error` - Search error

---

## Anthropic MCP Registry API v0

This section implements the official [Anthropic MCP Registry API specification](https://github.com/modelcontextprotocol/registry) for standard server discovery and agent discovery using the same API patterns.

### MCP Servers (v0)

**File:** `registry/api/registry_routes.py`
**Route Prefix:** `/v0.1` (from `Settings.anthropic_api_version` in `registry/core/config.py`)
**Authentication:** JWT Bearer Token

#### 1. List MCP Servers

**Endpoint:** `GET /v0/servers`

**Purpose:** List all MCP servers with cursor-based pagination

**Query Parameters:**
- `cursor` (optional, string) - Pagination cursor from previous response
- `limit` (optional, integer, default: 100, max: 1000) - Max items per page

**Response:** `200 OK`
```json
{
  "servers": [
    {
      "id": "io.mcpgateway/example-server",
      "name": "Example Server",
      "description": "string",
      "homepage": "https://example.com",
      "resources": [
        {
          "uri": "example://resource",
          "mimeType": "text/plain"
        }
      ]
    }
  ],
  "_meta": {
    "pagination": {
      "hasMore": false,
      "nextCursor": null
    }
  }
}
```

---

#### 2. List Server Versions

**Endpoint:** `GET /v0/servers/{serverName:path}/versions`

**Purpose:** List all versions for a specific server

**Path Parameter:**
- `serverName` - URL-encoded reverse-DNS name (e.g., `io.mcpgateway%2Fexample-server`)

**Response:** `200 OK` with versions array (currently one version per server)

**Error Codes:**
- `404 Not Found` - Server not found or user lacks access

---

#### 3. Get Server Version Details

**Endpoint:** `GET /v0/servers/{serverName:path}/versions/{version}`

**Purpose:** Get detailed information about a specific server version

**Path Parameters:**
- `serverName` - URL-encoded server name
- `version` - Version string or `latest`

**Response:** `200 OK` with complete server details including tools

**Error Codes:**
- `404 Not Found` - Server/version not found or user lacks access

---

## Internal Server Management APIs

### UI Management Endpoints

**File:** `registry/api/server_routes.py`
**Route Prefix:** `/api`
**Authentication:** Session Cookie (enhanced_auth)

#### 1. Dashboard/Root

**Endpoint:** `GET /api/`

**Purpose:** Main dashboard showing services based on user permissions

**Query Parameters:**
- `query` (optional, string) - Search services

**Response:** HTML page with filtered service list

---

#### 2. Get Servers JSON

**Endpoint:** `GET /api/servers`

**Purpose:** Get servers data as JSON for React frontend

**Query Parameters:**
- `query` (optional, string)

**Response:** `200 OK`
```json
{
  "servers": [
    {
      "path": "/example",
      "name": "Example Server",
      "description": "string",
      "is_enabled": true,
      "health_status": "healthy"
    }
  ]
}
```

---

#### 3. Toggle Service

**Endpoint:** `POST /api/toggle/{service_path:path}`

**Purpose:** Enable/disable a service

**Authentication:** Requires `toggle_service` UI permission

**Form Parameters:**
- `enabled` (boolean)

**Response:** `200 OK` with new status

**Error Codes:**
- `404 Not Found` - Service doesn't exist
- `403 Forbidden` - User lacks toggle permission
- `500 Internal Server Error` - Toggle operation failed

---

#### 4. Register Service (UI)

**Endpoint:** `POST /api/register`

**Purpose:** Register new service via dashboard

**Authentication:** Requires `register_service` UI permission

**Form Parameters:**
- `name`, `description`, `path`, `proxy_pass_url`, `tags`, `num_tools`, `num_stars`, `is_python`, `license`

**Response:** `201 Created`

**Error Codes:**
- `400 Bad Request` - Service already exists
- `403 Forbidden` - User lacks register permission

---

#### 5. Edit Service Form

**Endpoint:** `GET /api/edit/{service_path:path}`

**Purpose:** Show edit form for service

**Authentication:** Requires `modify_service` UI permission

**Response:** HTML edit form

---

#### 6. Update Service

**Endpoint:** `POST /api/edit/{service_path:path}`

**Purpose:** Handle service edit submission

**Authentication:** Requires `modify_service` UI permission

**Form Parameters:** Same as register

**Response:** `303 See Other` (redirect to home)

---

#### 7. Token Generation Page

**Endpoint:** `GET /api/tokens`

**Purpose:** Show JWT token generation form

**Response:** HTML form

---

#### 8. Get Server Details

**Endpoint:** `GET /api/server_details/{service_path:path}`

**Purpose:** Get detailed server info by path or all servers

**Path Parameter:**
- `service_path` - Service path or `all`

**Response:** `200 OK` with server details

---

#### 9. Get Service Tools

**Endpoint:** `GET /api/tools/{service_path:path}`

**Purpose:** Get tools list for service

**Path Parameter:**
- `service_path` - Service path or `all`

**Response:** `200 OK`
```json
{
  "tools": [
    {
      "name": "tool_name",
      "description": "string",
      "inputSchema": {}
    }
  ]
}
```

**Error Codes:**
- `404 Not Found` - Service not found
- `400 Bad Request` - Service disabled
- `403 Forbidden` - User lacks access

---

#### 10. Refresh Service

**Endpoint:** `POST /api/refresh/{service_path:path}`

**Purpose:** Refresh service health and tools

**Authentication:** Requires `health_check_service` permission

**Response:** `200 OK` with refresh status

---

### Internal Admin Endpoints

**Authentication:** HTTP Basic Auth (admin credentials)

#### 11. Internal Register Service

**Endpoint:** `POST /api/internal/register`

**Purpose:** Internal service registration for mcpgw-server

**Form Parameters:** All registration parameters + `overwrite`, `auth_provider`, `auth_type`, `supported_transports`, `headers`, `tool_list_json`

**Response:** `201 Created` or `409 Conflict`

**Features:** Auto-enables services, updates scopes.yml

---

#### 12. Internal Remove Service

**Endpoint:** `POST /api/internal/remove`

**Form Parameters:** `service_path`

**Response:** `200 OK` or `404/500` error

---

#### 13. Internal Toggle Service

**Endpoint:** `POST /api/internal/toggle`

**Form Parameters:** `service_path`

**Response:** `200 OK` with new state

---

#### 14. Internal Healthcheck

**Endpoint:** `POST /api/internal/healthcheck`

**Response:** Health status for all servers

---

#### 15. Add Server to Groups

**Endpoint:** `POST /api/internal/add-to-groups`

**Form Parameters:**
- `server_name` - Server name
- `group_names` - Comma-separated group names

**Response:** `200 OK` with result

---

#### 16. Remove Server from Groups

**Endpoint:** `POST /api/internal/remove-from-groups`

**Form Parameters:** Same as add-to-groups

**Response:** `200 OK`

---

#### 17. Internal List Services

**Endpoint:** `GET /api/internal/list`

**Response:** `200 OK` with all services and health status

---

#### 18. Create Group

**Endpoint:** `POST /api/internal/create-group`

**Form Parameters:**
- `group_name`
- `description` (optional)
- `create_in_keycloak` (optional)

**Response:** `200 OK`

---

#### 19. Delete Group

**Endpoint:** `POST /api/internal/delete-group`

**Form Parameters:**
- `group_name`
- `delete_from_keycloak` (optional)
- `force` (optional)

**Response:** `200 OK`

**Note:** Prevents deletion of system groups

---

#### 20. List Groups

**Endpoint:** `GET /api/internal/list-groups`

**Query Parameters:**
- `include_keycloak` (default: true)
- `include_scopes` (default: true)

**Response:** `200 OK` with synchronized groups info

---

#### 21. Generate JWT Token

**Endpoint:** `POST /api/tokens/generate`

**Purpose:** Generate JWT token for authenticated user

**Request Body:**
```json
{
  "requested_scopes": ["optional", "scopes"],
  "expires_in_hours": 8,
  "description": "Token description"
}
```

**Response:** `200 OK`
```json
{
  "access_token": "string",
  "token_type": "Bearer",
  "expires_in": 28800,
  "refresh_token": "string (if enabled)",
  "scope": "space separated scopes"
}
```

---

#### 22. Admin Get Keycloak Token

**Endpoint:** `GET /api/admin/tokens`

**Purpose:** Admin-only endpoint to retrieve M2M tokens

**Authentication:** Admin users only

**Response:** `200 OK` with access token

**Error Codes:**
- `403 Forbidden` - Non-admin user
- `500 Internal Server Error` - Configuration error

---

## Authentication & Login APIs

**File:** `registry/auth/routes.py`
**Route Prefix:** `/api/auth`

### 1. Login Form

**Endpoint:** `GET /api/auth/login`

**Purpose:** Show login form with OAuth2 providers

**Query Parameters:**
- `error` (optional) - Error message

**Response:** HTML login form

---

### 2. OAuth2 Redirect

**Endpoint:** `GET /api/auth/auth/{provider}`

**Purpose:** Redirect to auth server for OAuth2 login

**Path Parameter:**
- `provider` - OAuth2 provider (e.g., `keycloak`, `cognito`)

**Response:** `302 Redirect` to auth server

---

### 3. OAuth2 Callback

**Endpoint:** `GET /api/auth/auth/callback`

**Purpose:** Handle OAuth2 callback

**Query Parameters:**
- `error` (optional)
- `details` (optional)

**Response:** `302 Redirect` to home or login with error

---

### 4. Login Submit (Form)

**Endpoint:** `POST /api/auth/login`

**Purpose:** Handle login form submission

**Form Parameters:**
- `username`
- `password`

**Response:** `302 Redirect` to home on success, `401` on failure

---

### 5. Logout (GET)

**Endpoint:** `GET /api/auth/logout`

**Purpose:** Handle logout via GET

**Response:** `302 Redirect` to login (clears session)

---

### 6. Logout (POST)

**Endpoint:** `POST /api/auth/logout`

**Purpose:** Handle logout via POST

**Response:** `302 Redirect` to login (clears session)

---

### 7. OAuth2 Providers List

**Endpoint:** `GET /api/auth/providers`

**Purpose:** Get available OAuth2 providers

**Authentication:** None (public)

**Response:** `200 OK`
```json
{
  "providers": [
    {
      "name": "keycloak",
      "display_name": "Keycloak",
      "icon": "keycloak"
    }
  ]
}
```

---

## Health Monitoring APIs

**File:** `registry/health/routes.py`
**Route Prefix:** `/api/health`

### 1. Health Status WebSocket

**Endpoint:** `WebSocket /api/health/ws/health_status`

**Purpose:** Real-time health status updates via WebSocket

**Authentication:** Session cookie required

**Messages:** Periodic health status broadcasts

**Features:**
- Authenticated connections only
- Ping/pong keep-alive
- Graceful disconnect handling

---

### 2. Health Status HTTP

**Endpoint:** `GET /api/health/ws/health_status`

**Purpose:** Get health status via HTTP (WebSocket fallback)

**Authentication:** None

**Response:** `200 OK` with health status JSON

---

### 3. WebSocket Statistics

**Endpoint:** `GET /api/health/ws/stats`

**Purpose:** Get WebSocket performance statistics

**Response:** `200 OK`
```json
{
  "active_connections": 5,
  "total_messages_sent": 1234,
  "uptime_seconds": 86400
}
```

---

## Discovery & Well-Known Endpoints

**File:** `registry/api/wellknown_routes.py`
**Route Prefix:** `/.well-known`
**Authentication:** None (public)

### MCP Servers Discovery

**Endpoint:** `GET /.well-known/mcp-servers`

**Purpose:** Public MCP server discovery for client tools

**Response:** `200 OK`
```json
{
  "servers": [
    {
      "id": "io.mcpgateway/example",
      "name": "Example Server",
      "description": "string",
      "mcp": {
        "transport": "streamable-http",
        "url": "https://gateway.example.com/example/"
      }
    }
  ],
  "_meta": {
    "registry": "MCP Gateway Registry",
    "updated_at": "2025-11-01T04:53:56Z"
  }
}
```

**Features:**
- Server filtering by enabled status
- Authentication info included
- Tools preview
- Public cache headers with configurable TTL

---

## Utility Endpoints

### 1. Current User Info

**Endpoint:** `GET /api/auth/me`

**Purpose:** Get current user information for React auth context

**Authentication:** Session cookie (enhanced_auth)

**Response:** `200 OK`
```json
{
  "username": "admin",
  "email": "admin@example.com",
  "auth_method": "oauth2",
  "provider": "keycloak",
  "scopes": ["mcp-registry-admin"],
  "groups": ["mcp-registry-admin", "mcp-servers-unrestricted"],
  "is_admin": true
}
```

---

### 2. Health Check

**Endpoint:** `GET /health`

**Purpose:** Simple health check for load balancers

**Authentication:** None (public)

**Response:** `200 OK`
```json
{
  "status": "healthy",
  "service": "mcp-gateway-registry"
}
```

---

## Response Codes & Error Handling

### Success Responses

| Code | Meaning | Use Case |
|------|---------|----------|
| `200 OK` | Successful GET/POST | Data retrieval, updates |
| `201 Created` | Resource created | Agent/server registration |
| `204 No Content` | Successful deletion | DELETE operations |
| `303 See Other` | Redirect after form | Form submissions (POST) |

### Client Error Responses

| Code | Meaning | Example |
|------|---------|---------|
| `400 Bad Request` | Invalid input | Missing required fields, invalid JSON |
| `401 Unauthorized` | Authentication failed | Missing/invalid JWT token |
| `403 Forbidden` | Permission denied | User lacks required scope |
| `404 Not Found` | Resource doesn't exist | Agent/server not found |
| `409 Conflict` | Resource conflict | Agent path already registered |
| `422 Unprocessable Entity` | Validation error | Invalid field values |

### Server Error Responses

| Code | Meaning | Example |
|------|---------|---------|
| `500 Internal Server Error` | Server error | Exception during processing |
| `502 Bad Gateway` | Upstream error | Auth server unreachable |
| `503 Service Unavailable` | Service down | Database unavailable |

### Error Response Format

```json
{
  "detail": "Human-readable error message",
  "error_code": "optional_error_code",
  "request_id": "unique_request_identifier"
}
```

---

## OpenAPI Specifications

### Access OpenAPI Specifications

FastAPI automatically generates OpenAPI (Swagger) specifications:

**Available Endpoints:**
- **OpenAPI JSON:** `GET /openapi.json`
- **Swagger UI:** `GET /docs`
- **ReDoc:** `GET /redoc`

**Local Access:**
```bash
curl http://localhost:7860/openapi.json
```

**Browser Access:**
- Swagger UI: http://localhost:7860/docs
- ReDoc: http://localhost:7860/redoc

### Generate Spec Files

To download and save OpenAPI specs:

```bash
# Get full OpenAPI spec as JSON
curl -s http://localhost:7860/openapi.json > openapi.json

# Filter for specific tags
curl -s http://localhost:7860/openapi.json | \
  jq '.paths | keys[] | select(contains("/agents"))' > agents-endpoints.json

# Generate Swagger YAML (requires conversion)
curl -s http://localhost:7860/openapi.json | \
  python3 -c "import sys, json, yaml; print(yaml.dump(json.load(sys.stdin)))" > openapi.yaml
```

### Using Generated Specs

1. **Code Generation:**
   ```bash
   # Generate Python client
   openapi-generator-cli generate -i openapi.json -g python -o ./python-client

   # Generate JavaScript client
   openapi-generator-cli generate -i openapi.json -g javascript -o ./js-client
   ```

2. **API Documentation:** Import into Postman, Insomnia, or other API tools

3. **Validation:** Use `openapi-spec-validator` to validate the spec

---

## Summary Table

| Category | Endpoints | Auth | Purpose |
|----------|-----------|------|---------|
| A2A Agents | 8 | JWT Bearer | Agent lifecycle management |
| Anthropic v0 (Servers) | 3 | JWT Bearer | Standard server discovery |
| Anthropic v0 (Agents) | 3 | JWT Bearer | Standard agent discovery |
| UI Management | 10 | Session Cookie | Dashboard operations |
| Admin Operations | 12 | HTTP Basic Auth | Administrative tasks |
| Authentication | 7 | OAuth2/Session | User login/logout |
| Health Monitoring | 3 | Session/None | Real-time status |
| Discovery | 1 | None | Public server discovery |
| Utility | 2 | Session/None | Helper endpoints |
| **TOTAL** | **49** | **Multiple** | **Full system coverage** |

---

## Quick Reference by Use Case

### I want to register an agent
- **Endpoint:** `POST /api/agents/register`
- **Auth:** JWT Bearer Token with `publish_agent` scope
- **Documentation:** See [A2A Agent Management APIs > Register Agent](#1-register-agent)

### I want to discover agents by capability
- **Endpoint:** `POST /api/agents/discover/semantic`
- **Auth:** Optional
- **Query:** Natural language query
- **Documentation:** See [A2A Agent Management APIs > Discover Agents Semantically](#8-discover-agents-semantically)

### I want to list all servers (Anthropic API format)
- **Endpoint:** `GET /v0/servers`
- **Auth:** JWT Bearer Token
- **Documentation:** See [Anthropic MCP Registry API v0 > List MCP Servers](#1-list-mcp-servers)

### I want to generate a JWT token
- **Endpoint:** `POST /api/tokens/generate`
- **Auth:** Session Cookie
- **Documentation:** See [Internal Server Management APIs > Generate JWT Token](#21-generate-jwt-token)

### I want to find servers I have access to
- **Endpoint:** `GET /api/servers`
- **Auth:** Session Cookie
- **Documentation:** See [Internal Server Management APIs > Get Servers JSON](#2-get-servers-json)

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2025-11-01 | 1.0 | Initial API reference documentation, 49 endpoints cataloged |
