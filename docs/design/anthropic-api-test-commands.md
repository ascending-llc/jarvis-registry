# Anthropic Registry API Test Commands

> **Note**: The Anthropic API version is defined in `registry/constants.py` as `ANTHROPIC_API_VERSION` for easy version management.

## Overview

This document provides comprehensive curl commands to test all three endpoints of the Anthropic Registry API v0.1 implementation:

1. `GET /v0.1/servers` - List all MCP servers with pagination
2. `GET /v0.1/servers/{serverName}/versions` - List versions for a specific server
3. `GET /v0.1/servers/{serverName}/versions/{version}` - Get detailed info for a specific version

## Prerequisites

### 1. Start the MCP Gateway Registry

```bash
# Build and start all services
./build_and_run.sh

# Wait for services to be ready (check logs)
docker compose logs -f registry
```

### 2. Authentication Setup

The v0.1 API requires JWT authentication via Keycloak.

**Generate Fresh Token (Required)**

The ingress token expires regularly, so you must generate a new one before testing:

```bash
# Step 1: Generate fresh Keycloak credentials
credentials-provider/generate_creds.sh

# Step 2: Load the token from ingress.json
export TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)

# Step 3: Verify token was loaded
echo "Token loaded: ${TOKEN:0:50}..."
```

**Important Notes**:
- Tokens expire after 5 minutes - if you get authentication errors, regenerate with `./credentials-provider/generate_creds.sh`
- The `generate_creds.sh` script creates a new M2M token in `.oauth-tokens/ingress.json`
- This token has full access to all MCP servers (unrestricted + restricted scopes)
- **Other bot tokens** (like `bot-008`, `agent-finance-bot`) may have limited or no access to MCP servers depending on their Keycloak configuration. Use `ingress.json` for testing.

### 3. Base URL

The v0.1 API is accessible at:

- **API Endpoint**: `http://localhost/v0.1` or `https://localhost/v0.1`

**Authentication**: All endpoints require JWT Bearer token authentication via the `Authorization` header.

## Test Commands

### Test 1: List All Servers (Basic)

**Description**: Get the first page of servers with default pagination (100 items)

```bash
curl -X GET "http://localhost/v0.1/servers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "servers": [
    {
      "server": {
        "name": "io.mcpgateway/atlassian",
        "description": "...",
        "version": "1.0.0",
        "title": "Atlassian Server",
        "packages": [...],
        "_meta": {...}
      },
      "_meta": {...}
    },
    ...
  ],
  "metadata": {
    "nextCursor": "io.mcpgateway/some-server",
    "count": 100
  }
}
```

### Test 2: List Servers with Limit

**Description**: Get first 5 servers only

```bash
curl -X GET "http://localhost/v0.1/servers?limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**: ServerList with 5 items and pagination metadata

### Test 3: List Servers with Pagination

**Description**: Get the next page using cursor from previous response

```bash
# First, get the first page and extract the cursor
CURSOR=$(curl -s -X GET "http://localhost/v0.1/servers?limit=5" \
  -H "Authorization: Bearer $TOKEN" | jq -r '.metadata.nextCursor')

# Then fetch the next page
curl -X GET "http://localhost/v0.1/servers?cursor=$CURSOR&limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**: ServerList starting after the cursor position

### Test 4: List Servers with Maximum Limit

**Description**: Test the maximum limit (1000 items)

```bash
curl -X GET "http://localhost/v0.1/servers?limit=1000" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**: ServerList with up to 1000 items

### Test 5: List Server Versions

**Description**: Get all versions for the Atlassian server

```bash
# Note: Server name must be URL-encoded
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "servers": [
    {
      "server": {
        "name": "io.mcpgateway/atlassian",
        "description": "...",
        "version": "1.0.0",
        ...
      },
      "_meta": {...}
    }
  ],
  "metadata": {
    "nextCursor": null,
    "count": 1
  }
}
```

### Test 6: List Versions for Different Server

**Description**: Try with a different server (e.g., currenttime)

```bash
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fcurrenttime/versions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

### Test 7: Get Specific Version (latest)

**Description**: Get detailed information for the latest version of Atlassian server

```bash
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/latest" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "server": {
    "name": "io.mcpgateway/atlassian",
    "description": "...",
    "version": "1.0.0",
    "title": "Atlassian Server",
    "repository": null,
    "websiteUrl": null,
    "packages": [
      {
        "registryType": "mcpb",
        "identifier": "io.mcpgateway/atlassian",
        "version": "1.0.0",
        "transport": {
          "type": "streamable-http",
          "url": "http://atlassian:8005"
        },
        "runtimeHint": "docker"
      }
    ],
    "_meta": {
      "io.mcpgateway/internal": {
        "path": "/atlassian",
        "is_enabled": true,
        "health_status": "healthy",
        "num_tools": 5,
        "tags": ["atlassian", "jira", "confluence"],
        "license": "MIT"
      }
    }
  },
  "_meta": {
    "io.mcpgateway/registry": {
      "last_checked": "2025-10-12T18:00:00Z",
      "health_status": "healthy"
    }
  }
}
```

### Test 8: Get Specific Version (1.0.0)

**Description**: Get detailed information using explicit version number

```bash
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/1.0.0" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**: Same as Test 7 (we only support version 1.0.0 currently)

### Test 9: Invalid Version

**Description**: Try to access a non-existent version

```bash
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/2.0.0" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "detail": "Version 2.0.0 not found"
}
```

**Expected Status Code**: 404

### Test 10: Non-existent Server

**Description**: Try to access a server that doesn't exist

```bash
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fnon-existent/versions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "detail": "Server not found"
}
```

**Expected Status Code**: 404

### Test 11: Invalid Server Name Format

**Description**: Try to access a server with wrong namespace

```bash
curl -X GET "http://localhost/v0.1/servers/com.example%2Fserver/versions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "detail": "Server not found"
}
```

**Expected Status Code**: 404

### Test 12: Unauthorized Access

**Description**: Try to access API without authentication

```bash
curl -X GET "http://localhost/v0.1/servers" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "detail": "Not authenticated"
}
```

**Expected Status Code**: 401

### Test 13: Invalid Token

**Description**: Try to access API with invalid token

```bash
curl -X GET "http://localhost/v0.1/servers" \
  -H "Authorization: Bearer invalid_token_here" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**:
```json
{
  "detail": "Could not validate credentials"
}
```

**Expected Status Code**: 401

### Test 14: Permission-based Filtering (Non-admin User)

**Description**: Test that non-admin users only see servers they have access to

First, create a test user with limited permissions via the auth service, then:

```bash
# Get token for non-admin user
export USER_TOKEN=$(curl -s -X POST http://localhost:8888/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=testuser&password=testpass" | jq -r '.access_token')

# List servers as non-admin user
curl -X GET "http://localhost/v0.1/servers" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**: Only servers the user has access to (based on scopes.yml configuration)

### Test 15: Via Nginx Proxy (Production Path)

**Description**: Test the API through Nginx reverse proxy

```bash
curl -X GET "http://localhost/v0.1/servers?limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq
```

**Expected Response**: Same as Test 2, but routed through Nginx

### Test 16: Verbose Output with Headers

**Description**: See full HTTP response including headers

```bash
curl -v -X GET "http://localhost/v0.1/servers?limit=5" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" 2>&1 | grep -E "(< HTTP|< Content-Type|< X-)"
```

**Expected Headers**:
- `HTTP/1.1 200 OK`
- `Content-Type: application/json`

### Test 17: Test All Registered Servers

**Description**: Iterate through all servers and test version endpoint for each

```bash
# Get list of all servers
SERVERS=$(curl -s -X GET "http://localhost/v0.1/servers?limit=100" \
  -H "Authorization: Bearer $TOKEN" | jq -r '.servers[].server.name')

# Test each server
for server in $SERVERS; do
  echo "Testing server: $server"
  encoded_name=$(echo "$server" | sed 's/\//%2F/g')
  curl -s -X GET "http://localhost/v0.1/servers/$encoded_name/versions/latest" \
    -H "Authorization: Bearer $TOKEN" | jq -c '{name: .server.name, version: .server.version, status: "ok"}'
done
```

### Test 18: Performance Test - Large Pagination

**Description**: Test pagination performance with large result sets

```bash
# Time the request
time curl -s -X GET "http://localhost/v0.1/servers?limit=500" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq '.metadata.count'
```

**Expected**: Response should complete in < 2 seconds

### Test 19: Concurrent Requests

**Description**: Test API under concurrent load

```bash
# Run 10 concurrent requests
for i in {1..10}; do
  curl -s -X GET "http://localhost/v0.1/servers?limit=10" \
    -H "Authorization: Bearer $TOKEN" &
done
wait
echo "All concurrent requests completed"
```

### Test 20: Pretty Print Server Details

**Description**: Get nicely formatted output for a specific server

```bash
curl -s -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/latest" \
  -H "Authorization: Bearer $TOKEN" | jq '{
    name: .server.name,
    title: .server.title,
    description: .server.description,
    version: .server.version,
    transport_url: .server.packages[0].transport.url,
    num_tools: .server._meta."io.mcpgateway/internal".num_tools,
    health: ._meta."io.mcpgateway/registry".health_status
  }'
```

### Test 21: Permission-Based Filtering (Restricted vs Full Access)

**Description**: Verify that users with restricted permissions only see authorized servers

**Setup**: Create restricted bot account if it doesn't exist
```bash
# Check if test-restricted-bot already exists
if [ ! -f .oauth-tokens/test-restricted-bot.json ]; then
  echo "Creating test-restricted-bot..."

  # Load Keycloak admin password from .env
  export $(grep KEYCLOAK_ADMIN_PASSWORD .env | xargs)

  # Create restricted bot (only has access to restricted servers)
  uv run python ./api/registry_management.py user-create-m2m \
    --name test-restricted-bot \
    --groups 'mcp-servers-restricted'
else
  echo "test-restricted-bot already exists, skipping creation"
fi
```

**Test Commands**:
```bash
# Step 1: Refresh the restricted bot's token
./scripts/refresh_m2m_token.sh test-restricted-bot

# Step 2: Load the restricted bot's token
export TOKEN_RESTRICTED=$(jq -r '.access_token' .oauth-tokens/test-restricted-bot-token.json)

# Step 3: Test v0.1 API with restricted token - should see only ~3 servers
echo "=== Testing with RESTRICTED token ==="
curl -s "http://localhost/v0.1/servers" \
  -H "Authorization: Bearer $TOKEN_RESTRICTED" | jq '{
    total_servers: (.servers | length),
    server_names: [.servers[].server.name]
  }'

# Step 4: Load the full access token for comparison
export TOKEN_FULL=$(jq -r '.access_token' .oauth-tokens/ingress.json)

# Step 5: Test v0.1 API with full access token - should see all servers
echo ""
echo "=== Testing with FULL ACCESS token ==="
curl -s "http://localhost/v0.1/servers" \
  -H "Authorization: Bearer $TOKEN_FULL" | jq '{
    total_servers: (.servers | length),
    server_names: [.servers[].server.name]
  }'

# Step 6: Compare the difference
echo ""
echo "=== COMPARISON ==="
echo "Restricted bot sees: $(curl -s "http://localhost/v0.1/servers" -H "Authorization: Bearer $TOKEN_RESTRICTED" | jq '.servers | length') servers"
echo "Full access sees: $(curl -s "http://localhost/v0.1/servers" -H "Authorization: Bearer $TOKEN_FULL" | jq '.servers | length') servers"
```

**Expected Results**:
- **Restricted bot** (`mcp-servers-restricted` group): ~3 servers (currenttime, auth_server, mcpgw)
- **Full access** (`ingress.json` token): ~7+ servers (all servers including atlassian, fininfo, sre-gateway)

This demonstrates that the v0.1 API correctly enforces permission-based filtering based on Keycloak groups and MCP scopes!

---

## Verification Checklist

After running the tests, verify:

- [ ] All successful requests return 200 status code
- [ ] Pagination works correctly (cursor-based)
- [ ] Server name format follows `io.mcpgateway/{path}` convention
- [ ] All responses conform to Anthropic schema
- [ ] Authentication is required for all endpoints
- [ ] Non-admin users only see authorized servers (Test 21)
- [ ] Restricted users see only restricted servers (Test 21)
- [ ] Error responses include proper status codes (404, 401)
- [ ] Version "latest" and "1.0.0" both work
- [ ] Transport configuration includes correct proxy URLs
- [ ] Metadata includes health status and internal info
- [ ] URL encoding works for server names with special characters

## Schema Validation

To validate responses against the Anthropic OpenAPI specification:

```bash
# Download the official OpenAPI spec
curl -o /tmp/anthropic-openapi.yaml \
  https://raw.githubusercontent.com/modelcontextprotocol/registry/refs/heads/main/docs/reference/api/openapi.yaml

# Use a tool like openapi-spec-validator or similar
# (Requires installation: pip install openapi-spec-validator)
```

## Common Issues

### Issue 1: Token Expired

**Symptom**: 401 Unauthorized or "Token has expired" error

**Solution**: Generate fresh credentials and reload token
```bash
# Step 1: Generate new token
./generate_creds.sh

# Step 2: Reload the token
export TOKEN=$(jq -r '.access_token' .oauth-tokens/ingress.json)

# Step 3: Verify it works
curl -s -X GET "http://localhost/v0.1/servers?limit=1" \
  -H "Authorization: Bearer $TOKEN" | jq '.servers[0].server.name'
```

### Issue 2: URL Encoding

**Symptom**: 404 errors when server name contains `/`

**Solution**: Always URL-encode the server name
```bash
# Wrong: io.mcpgateway/server-name
# Correct: io.mcpgateway%2Fserver-name
```

### Issue 3: Empty Response

**Symptom**: `{"servers": [], "metadata": {"count": 0}}`

**Solution**: Check if servers are registered and enabled
```bash
# List server files
ls ~/mcp-gateway/servers/*.json

# Check registry logs
docker compose logs registry | grep -i "loading servers"
```

## Integration with Anthropic Tools

These endpoints are compatible with Anthropic MCP client tools. Example:

```python
import httpx

# Configure client
client = httpx.Client(
    base_url="http://localhost:7860",
    headers={"Authorization": f"Bearer {token}"}
)

# List servers
response = client.get("/v0.1/servers", params={"limit": 10})
servers = response.json()

# Get server details
server_name = servers["servers"][0]["server"]["name"]
encoded_name = server_name.replace("/", "%2F")
details = client.get(f"/v0.1/servers/{encoded_name}/versions/latest")
```

## Next Steps

After testing:

1. Document any issues found
2. Test with real MCP clients (Claude Desktop, etc.)
3. Verify compatibility with Anthropic's official registry clients
4. Performance testing with larger datasets
5. Security testing (SQL injection, XSS, etc.)

## References

- Issue #175: Support Anthropic MCP Registry REST API v0.1
- OpenAPI Spec: https://raw.githubusercontent.com/modelcontextprotocol/registry/refs/heads/main/docs/reference/api/openapi.yaml
- API Guide: https://github.com/modelcontextprotocol/registry/blob/main/docs/guides/consuming/use-rest-api.md
