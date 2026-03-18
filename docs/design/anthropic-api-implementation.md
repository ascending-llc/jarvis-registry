# Anthropic MCP Registry API - Implementation Guide

> **Note**: The Anthropic API version (v0.1) is defined in `registry/core/config.py` as `Settings.anthropic_api_version`. All code references the shared settings object rather than hardcoding the version string.

---

## Overview

This implementation provides full compatibility with the [Anthropic MCP Registry REST API v0.1 specification](https://github.com/modelcontextprotocol/registry), enabling seamless integration with MCP ecosystem tools and downstream applications.

### Key Features

- ✅ **3 REST API endpoints** for server discovery
- ✅ **JWT Bearer token authentication** via Keycloak
- ✅ **Cursor-based pagination** for server lists
- ✅ **Permission-based filtering** using MCP scopes
- ✅ **Complete Pydantic models** matching Anthropic spec
- ✅ **Automatic data transformation** from internal format

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Client (Authorization: Bearer <JWT>)                        │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP Request
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ Nginx (:80/:443)                                            │
│  └─ /v0.1/* location                                          │
│     └─ auth_request /validate  ────────────────┐            │
└────────────────────┬───────────────────────────┼────────────┘
                     │                            │
                     │                            ▼
                     │              ┌─────────────────────────┐
                     │              │ Auth Server (:8888)     │
                     │              │  - Validates JWT        │
                     │              │  - Checks Keycloak      │
                     │              │  - Returns headers      │
                     │              └─────────────┬───────────┘
                     │                            │
                     │ ◄──────────────────────────┘
                     │ X-User, X-Scopes, X-Username
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ Registry FastAPI (:7860)                                    │
│  ├─ nginx_proxied_auth() - Reads headers                   │
│  ├─ registry_routes.py - API endpoints                           │
│  ├─ server_service - Data access                           │
│  └─ transform_service - Format conversion                  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
            Anthropic Schema Response
```

---

## File Structure

### New Files

| File | Purpose |
|------|---------|
| `registry/core/config.py` | Anthropic API settings (`anthropic_api_version`, `anthropic_server_namespace`, limits) |
| `registry/schemas/anthropic_schema.py` | 9 Pydantic models for Anthropic spec |
| `registry/services/transform_service.py` | Data transformation between formats |
| `registry/api/registry_routes.py` | 3 REST endpoints with JWT auth |
| `tests/unit/api/test_registry_routes.py` | API endpoint tests |
| `tests/unit/services/test_transform_service.py` | Transformation tests |
| `docs/design/anthropic-api-test-commands.md` | 20 test scenarios with curl |

### Modified Files

| File | Changes |
|------|---------|
| `registry/main.py` | Registered v0.1 router |
| `registry/auth/dependencies.py` | Added `nginx_proxied_auth()` function |
| `docker/nginx_rev_proxy_*.conf` | Added `/v0.1/` location with auth validation |
| `.gitignore` | Added `tests/reports/` |

---

## Settings Configuration

Anthropic API configuration is centralized in `registry/core/config.py`:

```python
class Settings(BaseSettings):
    anthropic_api_version: str = "v0.1"
    anthropic_server_namespace: str = "io.mcpgateway"
    anthropic_api_default_limit: int = 100
    anthropic_api_max_limit: int = 1000
```

**Usage**: Import with `from registry.core.config import settings`

---

## API Endpoints

### 1. List Servers

```
GET /v0.1/servers?cursor={cursor}&limit={limit}
```

**Purpose**: List all MCP servers the authenticated user can access.

**Query Parameters**:
- `cursor` (optional): Pagination cursor from previous response
- `limit` (optional): Results per page (1-1000, default 100)

**Response**: `ServerList` with pagination metadata

**Example**:
```bash
curl "http://localhost/v0.1/servers?limit=5" \
  -H "Authorization: Bearer $TOKEN"
```

### 2. List Server Versions

```
GET /v0.1/servers/{serverName:path}/versions
```

**Purpose**: List all available versions for a specific server.

**URL Parameters**:
- `serverName`: URL-encoded name (e.g., `io.mcpgateway%2Fatlassian`)

**Response**: `ServerList` (currently single version per server)

**Important**: Note `:path` route converter to handle `/` in server names.

**Example**:
```bash
curl "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions" \
  -H "Authorization: Bearer $TOKEN"
```

### 3. Get Server Version Details

```
GET /v0.1/servers/{serverName:path}/versions/{version}
```

**Purpose**: Get detailed information for a specific server version.

**URL Parameters**:
- `serverName`: URL-encoded name (e.g., `io.mcpgateway%2Fatlassian`)
- `version`: Version string (use `latest` for current version)

**Response**: `ServerResponse` with full server details

**Example**:
```bash
curl "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/latest" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Authentication Flow

### 1. JWT Bearer Token Validation

**Client → Nginx**:
```
GET /v0.1/servers
Authorization: Bearer eyJhbGci...
```

**Nginx → Auth Server** (`/validate` endpoint):
```
GET /validate
X-Authorization: Bearer eyJhbGci...
X-Original-URL: http://localhost/v0.1/servers
```

**Auth Server Processing**:
1. Validates JWT signature using Keycloak JWKS
2. Checks expiration, issuer (3-tier validation), audience
   - Tries external URL: `https://mcpgateway.ddns.net/realms/mcp-gateway`
   - Tries internal URL: `http://keycloak:8080/realms/mcp-gateway`
   - Tries localhost URL: `http://localhost:8080/realms/mcp-gateway`
3. Extracts user info: `preferred_username`, `groups`, `scope`
4. Maps Keycloak groups to MCP scopes

**Auth Server → Nginx** (response headers):
```
X-User: service-account-mcp-gateway-m2m
X-Username: service-account-mcp-gateway-m2m
X-Scopes: mcp-servers-unrestricted/read mcp-servers-unrestricted/execute
X-Auth-Method: keycloak
```

**Nginx → FastAPI**:
```
GET /v0.1/servers
X-User: service-account-mcp-gateway-m2m
X-Scopes: mcp-servers-unrestricted/read mcp-servers-unrestricted/execute
Authorization: Bearer eyJhbGci...
```

### 2. nginx Configuration

**Critical Setup** in `/v0.1/` location block:

```nginx
location /v0.1/ {
    # Authenticate via auth-server
    auth_request /validate;

    # Capture auth server response headers
    auth_request_set $auth_user $upstream_http_x_user;
    auth_request_set $auth_username $upstream_http_x_username;
    auth_request_set $auth_scopes $upstream_http_x_scopes;
    auth_request_set $auth_method $upstream_http_x_auth_method;

    # Forward to FastAPI with auth context
    proxy_pass http://127.0.0.1:7860/v0.1/;
    proxy_set_header X-User $auth_user;
    proxy_set_header X-Username $auth_username;
    proxy_set_header X-Scopes $auth_scopes;
    proxy_set_header X-Auth-Method $auth_method;
    proxy_set_header Authorization $http_authorization;
}
```

**Key Fix**: `/validate` endpoint must forward `Authorization` as `X-Authorization`:
```nginx
location = /validate {
    proxy_pass http://auth-server:8888/validate;
    # CRITICAL: Read from $http_authorization (client's Authorization header)
    proxy_set_header X-Authorization $http_authorization;
}
```

### 3. FastAPI Authentication Dependency

**Function**: `nginx_proxied_auth()` in `registry/auth/dependencies.py`

**Supports Two Modes**:
1. **JWT Flow** (primary): Reads nginx headers from auth validation
2. **Cookie Flow** (fallback): Reads session cookies for backward compatibility

```python
def nginx_proxied_auth(
    request: Request,
    session: Cookie = None,
    x_user: Header = None,
    x_username: Header = None,
    x_scopes: Header = None,
    x_auth_method: Header = None,
) -> Dict[str, Any]:
    # Try nginx headers first (JWT Bearer token)
    if x_user or x_username:
        username = x_username or x_user
        scopes = x_scopes.split() if x_scopes else []

        # Map scopes to groups
        if 'mcp-servers-unrestricted/read' in scopes:
            groups = ['mcp-registry-admin']
        else:
            groups = ['mcp-registry-user']

        # Get accessible servers from scopes
        accessible_servers = get_user_accessible_servers(scopes)

        return {
            'username': username,
            'groups': groups,
            'scopes': scopes,
            'accessible_servers': accessible_servers,
            'is_admin': 'mcp-registry-admin' in groups,
            # ... more fields
        }

    # Fallback to session cookie
    return enhanced_auth(session)
```

---

## Permission Checks

### Scope-Based Access Control

**IMPORTANT**: v0.1 API uses `accessible_servers` (MCP scopes), NOT `accessible_services` (UI scopes).

```python
# CORRECT - Check against accessible_servers
accessible_servers = user_context.get("accessible_servers", [])
if server_name not in accessible_servers:
    raise HTTPException(404, "Server not found")
```

**Why**:
- `accessible_services` = UI-level services ("auth_server", "mcpgw")
- `accessible_servers` = MCP server names ("atlassian", "currenttime")
- M2M tokens have MCP scopes but no UI scopes

### User Context Structure

```python
{
    "username": "service-account-mcp-gateway-m2m",
    "groups": ["mcp-registry-admin"],
    "scopes": [
        "mcp-servers-unrestricted/read",
        "mcp-servers-unrestricted/execute",
        "mcp-servers-restricted/read",
        "mcp-servers-restricted/execute"
    ],
    "auth_method": "keycloak",
    "provider": "keycloak",
    "accessible_servers": [
        "atlassian", "currenttime", "fininfo",
        "mcpgw", "realserverfaketools", "sre-gateway"
    ],
    "accessible_services": [],  # Empty for M2M tokens
    "is_admin": True,
    "can_modify_servers": False
}
```

---

## Data Transformation

### Namespace Convention

**Internal Format**: `/atlassian`, `/currenttime/`
**Anthropic Format**: `io.mcpgateway/atlassian`, `io.mcpgateway/currenttime`

**Implementation** (`transform_service.py`):

```python
def _create_server_name(server_info: Dict[str, Any]) -> str:
    path = server_info.get("path", "")
    clean_path = path.strip("/")
    namespace = settings.anthropic_server_namespace
    return f"{namespace}/{clean_path}"
```

### Server Detail Transformation

```python
def transform_to_server_detail(server_info: Dict[str, Any]) -> ServerDetail:
    # Create Anthropic-format name
    name = _create_server_name(server_info)

    # Build package with transport config
    transport = _create_transport_config(server_info)
    package = Package(
        registryType="mcpb",
        identifier=name,
        version="1.0.0",
        transport=transport,
        runtimeHint="docker"
    )

    # Add internal metadata
    namespace = settings.anthropic_server_namespace
    meta = {
        f"{namespace}/internal": {
            "path": server_info.get("path"),
            "is_enabled": server_info.get("is_enabled"),
            "health_status": server_info.get("health_status"),
            "num_tools": server_info.get("num_tools"),
            "tags": server_info.get("tags", []),
            "license": server_info.get("license", "N/A")
        }
    }

    return ServerDetail(name=name, packages=[package], meta=meta, ...)
```

### Response Structure

```json
{
  "server": {
    "name": "io.mcpgateway/atlassian",
    "description": "Atlassian",
    "version": "1.0.0",
    "title": "Atlassian",
    "packages": [
      {
        "registryType": "mcpb",
        "identifier": "io.mcpgateway/atlassian",
        "version": "1.0.0",
        "transport": {
          "type": "streamable-http",
          "url": "http://atlassian-server:8005/mcp/"
        },
        "runtimeHint": "docker"
      }
    ],
    "_meta": {
      "io.mcpgateway/internal": {
        "path": "/atlassian",
        "is_enabled": true,
        "health_status": "healthy",
        "num_tools": 42,
        "tags": ["Atlassian", "Jira", "Confluence"],
        "license": "MIT"
      }
    }
  },
  "_meta": {
    "io.mcpgateway/registry": {
      "last_checked": "2025-10-12T19:25:09.378358+00:00",
      "health_status": "healthy"
    }
  }
}
```

---

## Pagination

### Cursor-Based Implementation

**Algorithm** (`transform_service.py`):

```python
def transform_to_server_list(
    servers_data: List[Dict[str, Any]],
    cursor: Optional[str] = None,
    limit: Optional[int] = None
) -> ServerList:
    # Apply defaults
    limit = limit or settings.anthropic_api_default_limit
    limit = min(limit, settings.anthropic_api_max_limit)

    # Sort alphabetically for consistency
    sorted_servers = sorted(servers_data, key=lambda s: _create_server_name(s))

    # Find cursor position
    start_index = 0
    if cursor:
        for idx, server in enumerate(sorted_servers):
            if _create_server_name(server) == cursor:
                start_index = idx + 1
                break

    # Slice page
    end_index = start_index + limit
    page_servers = sorted_servers[start_index:end_index]

    # Determine next cursor
    has_more = end_index < len(sorted_servers)
    next_cursor = _create_server_name(sorted_servers[end_index - 1]) if has_more else None

    # Transform and return
    return ServerList(
        servers=[transform_to_server_response(s) for s in page_servers],
        metadata=PaginationMetadata(nextCursor=next_cursor, count=len(page_servers))
    )
```

**Example Flow**:
```
Page 1: GET /v0.1/servers?limit=3
← Returns: servers A, B, C with nextCursor="C"

Page 2: GET /v0.1/servers?cursor=C&limit=3
← Returns: servers D, E, F with nextCursor="F"

Page 3: GET /v0.1/servers?cursor=F&limit=3
← Returns: servers G, H with nextCursor=null (end)
```

---

## Critical Implementation Details

### 1. Route Path Parameters

**Problem**: Server names contain `/` which breaks FastAPI routing.

**Solution**: Use `:path` converter in route definition.

```python
# WRONG - Returns 404 for io.mcpgateway/atlassian
@router.get("/servers/{serverName}/versions")

# CORRECT - Captures full path including /
@router.get("/servers/{serverName:path}/versions")
```

**Why**: FastAPI URL-decodes before routing. `io.mcpgateway%2Fatlassian` becomes `io.mcpgateway/atlassian`, which looks like extra path segments without `:path`.

### 2. Trailing Slash Handling

**Problem**: Some servers have trailing slashes (`/currenttime/`), some don't (`/atlassian`).

**Solution**: Try both forms when looking up servers.

```python
# Construct path from server name
lookup_path = "/" + decoded_name.replace(expected_prefix, "")

# Try with and without trailing slash
server_info = server_service.get_server_info(lookup_path)
if not server_info:
    server_info = server_service.get_server_info(lookup_path + "/")

# Use actual path from server_info for health checks
path = server_info.get("path", lookup_path)  # Has correct trailing slash
health_data = health_service._get_service_health_data(path)
```

**Why**: Health data is indexed by exact path. Wrong path returns `"unknown"` status.

### 3. Namespace Constant Usage

**All occurrences** of hardcoded `"io.mcpgateway"` replaced with shared settings:

```python
from registry.core.config import settings

namespace = settings.anthropic_server_namespace
expected_prefix = f"{namespace}/"  # "io.mcpgateway/"
```

**Files using setting**:
- `registry/api/registry_routes.py` - Validates server name format
- `registry/services/transform_service.py` - Creates names and metadata keys

---

## Testing

### Generate Token

```bash
# Generate fresh credentials (tokens expire after 5 minutes)
./generate_creds.sh

# Load token
export TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)

# Verify token loaded
echo "Token: ${TOKEN:0:50}..."
```

### Test Endpoints

```bash
# 1. List servers with pagination
curl "http://localhost/v0.1/servers?limit=5" \
  -H "Authorization: Bearer $TOKEN" | jq

# 2. List versions for a server (note %2F = /)
curl "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions" \
  -H "Authorization: Bearer $TOKEN" | jq

# 3. Get specific version details
curl "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/latest" \
  -H "Authorization: Bearer $TOKEN" | jq

# 4. Test pagination
curl "http://localhost/v0.1/servers?limit=2" \
  -H "Authorization: Bearer $TOKEN" | jq '.metadata'
# Get nextCursor and use it:
curl "http://localhost/v0.1/servers?cursor=io.mcpgateway%2Fcurrenttime&limit=2" \
  -H "Authorization: Bearer $TOKEN" | jq
```

### Comprehensive Test Suite

See [docs/design/anthropic-api-test-commands.md](anthropic-api-test-commands.md) for 20 test scenarios.

---

## Common Issues & Solutions

### Issue: 404 on versions endpoint

**Symptom**: `GET /v0.1/servers/io.mcpgateway%2Fatlassian/versions` returns 404

**Cause**: Missing `:path` in route parameter

**Solution**: Ensure route uses `{serverName:path}` not `{serverName}`

### Issue: Health data shows "unknown"

**Symptom**: `health_status: "unknown"`, `last_checked: null`

**Cause**: Trailing slash mismatch in path lookup

**Solution**: Use `server_info.get("path")` for health checks, not constructed path

### Issue: Empty server list

**Symptom**: `{"servers": [], "metadata": {"count": 0}}`

**Cause**: Checking `accessible_services` instead of `accessible_servers`

**Solution**: Use `user_context["accessible_servers"]` for permission checks

### Issue: 401 Unauthorized

**Symptom**: `{"detail": "Token has expired"}`

**Cause**: JWT token expired (5 minute lifetime)

**Solution**: Run `./generate_creds.sh` to get fresh token

### Issue: Token not forwarded

**Symptom**: Auth server logs show `Authorization=False`

**Cause**: nginx using `$http_x_authorization` instead of `$http_authorization`

**Solution**: Update `/validate` location to use `$http_authorization`

---

## Schema Compliance

**OpenAPI Spec**: https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/openapi.yaml

**Pydantic Models** (`registry/schemas/anthropic_schema.py`):
- ✅ `ServerList` - Paginated server list
- ✅ `ServerResponse` - Single server with metadata
- ✅ `ServerDetail` - Complete server information
- ✅ `Package` - Distribution package details
- ✅ `Transport` - Union of transport types
- ✅ `Repository` - Source code repository info
- ✅ `PaginationMetadata` - Cursor and count
- ✅ `ErrorResponse` - Error details

**Field Aliases**: Pydantic handles `_meta` fields with `Field(alias="_meta")`

---

## Next Steps

1. ✅ **JWT Authentication** - Fully implemented
2. ✅ **Permission Filtering** - Uses MCP scopes
3. ✅ **Health Data** - Includes status and last checked
4. ✅ **Pagination** - Cursor-based with configurable limits
5. 🔄 **Read-Only API Access** - Optional: Create dedicated M2M client with minimal scopes (see `.scratchpad/registry-api-readonly-access.md`)
6. 🔄 **Rate Limiting** - Future: Add per-client rate limits
7. 🔄 **Caching** - Future: Cache server list responses

---

## References

- **Issue**: [#175 - Support Anthropic MCP Registry REST API v0](https://github.com/agentic-community/mcp-gateway-registry/issues/175)
- **OpenAPI Spec**: https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/openapi.yaml
- **API Guide**: https://github.com/modelcontextprotocol/registry/blob/main/docs/guides/consuming/use-rest-api.md
- **Test Commands**: [anthropic-api-test-commands.md](anthropic-api-test-commands.md)
- **Progress Notes**: [.scratchpad/anthropic-api-v0-jwt-auth-progress.md](../../.scratchpad/anthropic-api-v0-jwt-auth-progress.md)
