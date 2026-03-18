# Anthropic MCP Registry API Documentation

The MCP Gateway Registry implements the server listing and related APIs from the [Anthropic MCP Registry REST API](https://raw.githubusercontent.com/modelcontextprotocol/registry/refs/heads/main/docs/reference/api/openapi.yaml) specification (currently v0.1). Additional API endpoints will be added in future releases.

> **Note**: The Anthropic API version is defined in `registry/core/config.py` as `Settings.anthropic_api_version` for easy version management.

## Overview

This API provides programmatic access to the MCP server registry using standard REST endpoints with JWT authentication. The API respects user permissions - users only see servers they have access to based on their configured privileges.

## Authentication

The API uses JWT Bearer token authentication. You need to obtain a JWT token from the Keycloak authentication provider first.

### Generate JWT Token via UI (Admin Users)

1. **Login to the Registry Web Interface**
   - Navigate to your registry instance at `https://your-registry-domain/` or `http://localhost:7860/`
   - Login with your admin credentials

2. **Access Token Management**
   - After logging in, you should see the main dashboard
   - As an admin user, you have access to generate JWT tokens

3. **Generate JWT Token**
   - Click the "Generate JWT Token" button or navigate to the token generation page
   - The system will store your JWT tokens in files like `.oauth-tokens/mcp-registry-api-tokens-YYYY-MM-DD.json`
   - **Note**: Tokens have a short lifetime (typically 5-15 minutes) for security

### Token File Format

The token file typically contains:

```json
{
  "tokens": {
    "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": null,
    "token_type": "bearer",
    "expires_in": 300
  },
  "keycloak_url": "http://localhost:8080",
  "realm": "mcp-gateway",
  "client_id": "mcp-gateway-m2m"
}
```

**Note**: Refresh tokens are not provided for security reasons. If your token expires, generate a new one from the UI or ask your administrator to increase the access token timeout in Keycloak (Realm Settings → Tokens → Access Token Lifespan).

## API Endpoints

All endpoints are prefixed with the API version (currently `/v0.1`, defined in `registry/core/config.py` via `Settings.anthropic_api_version`) and require authentication via Bearer token.

### 1. List Servers

**Endpoint:** `GET /v0.1/servers`

Lists all MCP servers that the authenticated user has access to.

**Parameters:**
- `cursor` (optional): Pagination cursor from previous response
- `limit` (optional): Maximum number of items (1-1000, default: 100)

**Response:**
```json
{
  "servers": [
    {
      "name": "io.mcpgateway/atlassian",
      "description": "Atlassian Jira and Confluence integration",
      "version": "1.0.0",
      "vendor": "MCP Gateway"
    }
  ],
  "nextCursor": "eyJpZCI6ImF0bGFzc2lhbiJ9"
}
```

### 2. Get Server Versions

**Endpoint:** `GET /v0.1/servers/{server_name}/versions`

Lists all available versions for a specific server.

**Parameters:**
- `server_name`: URL-encoded server name (e.g., `io.mcpgateway%2Fatlassian`)

**Response:**
```json
{
  "versions": [
    {
      "version": "1.0.0",
      "description": "Latest stable version",
      "publishedAt": "2024-10-13T00:00:00Z"
    }
  ]
}
```

### 3. Get Server Version Details

**Endpoint:** `GET /v0.1/servers/{server_name}/versions/{version}`

Gets detailed information about a specific server version.

**Parameters:**
- `server_name`: URL-encoded server name
- `version`: Version identifier or "latest"

**Response:**
```json
{
  "name": "io.mcpgateway/atlassian",
  "version": "1.0.0",
  "description": "Atlassian Jira and Confluence integration",
  "vendor": "MCP Gateway",
  "sourceUrl": "https://github.com/mcpgateway/atlassian-mcp",
  "configuration": {
    "mcpVersion": "2024-11-05",
    "capabilities": {
      "tools": {},
      "resources": {}
    }
  }
}
```

## Using curl

You can test the API directly using curl:

```bash
# First, extract the access token from your token file
ACCESS_TOKEN=$(cat /path/to/your/token-file.json | jq -r '.tokens.access_token')

# List all servers you have access to
curl -X GET "http://localhost/v0.1/servers?limit=10" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json"

# Get versions for a specific server
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json"

# Get details for a specific server version
curl -X GET "http://localhost/v0.1/servers/io.mcpgateway%2Fatlassian/versions/latest" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

**Note**: Server names with slashes must be URL-encoded (e.g., `io.mcpgateway/atlassian` becomes `io.mcpgateway%2Fatlassian`).

## Using the Test Script

A complete test script is provided at `cli/test_anthropic_api.py` that demonstrates how to interact with the API programmatically.

### Basic Usage

```bash
# Run all tests with a token file
uv run python cli/test_anthropic_api.py --token-file /path/to/your/token-file.json

# Test specific endpoint
uv run python cli/test_anthropic_api.py \
  --token-file /path/to/your/token-file.json \
  --test list-servers \
  --limit 10

# Get details for a specific server
uv run python cli/test_anthropic_api.py \
  --token-file /path/to/your/token-file.json \
  --test get-server \
  --server-name io.mcpgateway/atlassian
```

### Additional Options

```bash
# Use with different registry instance
uv run python cli/test_anthropic_api.py \
  --token-file tokens.json \
  --base-url https://mcpgateway.ddns.net

# Enable debug logging
uv run python cli/test_anthropic_api.py \
  --token-file tokens.json \
  --debug
```

### Command Line Options

The test script supports the following options:

| Option | Description | Default |
|--------|-------------|---------|
| `--token-file` | Path to JWT token file (required) | - |
| `--base-url` | Registry API base URL | `http://localhost` |
| `--test` | Which test to run (all, list-servers, get-versions, get-server) | `all` |
| `--server-name` | Server name for specific tests | - |
| `--limit` | Number of servers to list | `5` |
| `--debug` | Enable debug logging | `false` |

## Example Python Code

Here's a minimal example of how to build your own client (you would obviously write your own code adapted to your needs):

```python
import requests
import json
from typing import Dict, Any, Optional

class MCPRegistryClient:
    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

    def list_servers(self, limit: int = 100, cursor: Optional[str] = None) -> Dict[str, Any]:
        """List all available MCP servers."""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        response = requests.get(
            f"{self.base_url}/v0.1/servers",
            headers=self.headers,
            params=params
        )
        response.raise_for_status()
        return response.json()

    def get_server_versions(self, server_name: str) -> Dict[str, Any]:
        """Get all versions for a specific server."""
        encoded_name = server_name.replace("/", "%2F")
        response = requests.get(
            f"{self.base_url}/v0.1/servers/{encoded_name}/versions",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def get_server_details(self, server_name: str, version: str = "latest") -> Dict[str, Any]:
        """Get detailed information about a server version."""
        encoded_name = server_name.replace("/", "%2F")
        response = requests.get(
            f"{self.base_url}/v0.1/servers/{encoded_name}/versions/{version}",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

# Usage example
def main():
    # Load token from file
    with open('/path/to/your/token-file.json', 'r') as f:
        token_data = json.load(f)

    access_token = token_data["tokens"]["access_token"]

    # Create client
    client = MCPRegistryClient("http://localhost", access_token)

    # List servers
    servers = client.list_servers(limit=10)
    print(f"Found {len(servers['servers'])} servers")

    # Get details for a specific server
    if servers["servers"]:
        server_name = servers["servers"][0]["name"]
        details = client.get_server_details(server_name)
        print(f"Server details: {json.dumps(details, indent=2)}")

if __name__ == "__main__":
    main()
```

## Token Lifetime Management

Tokens have a short lifetime (typically 5-15 minutes) for security. When your token expires:

1. **Generate a new token** from the UI (recommended approach)
2. **Or ask your administrator** to increase the access token timeout in Keycloak:
   - Navigate to: **Keycloak Admin Console → Realm Settings → Tokens → Access Token Lifespan**
   - Increase the value as needed for your automation or extended use cases

This approach is more secure than using refresh tokens and provides better audit trails.

## Error Handling

The API returns standard HTTP status codes:

- `200 OK`: Success
- `401 Unauthorized`: Invalid or expired token
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Server or version not found
- `500 Internal Server Error`: Server error

Error responses follow this format:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid or expired token"
  }
}
```

## Rate Limiting

The API may implement rate limiting. Check response headers for rate limit information:
- `X-RateLimit-Limit`: Maximum requests per time window
- `X-RateLimit-Remaining`: Remaining requests in current window
- `X-RateLimit-Reset`: When the rate limit window resets

## Security Considerations

1. **Token Storage**: Store JWT tokens securely and never commit them to version control
2. **Token Expiry**: Generate new tokens when needed or configure longer lifetimes in Keycloak
3. **HTTPS**: Always use HTTPS in production environments
4. **Access Control**: Tokens respect user permissions - users only see servers they have access to

## Support

For issues with the Anthropic Registry API implementation:

1. **Official Anthropic Registry API Specification**: [View the interactive API documentation](https://elements-demo.stoplight.io/?spec=https://raw.githubusercontent.com/modelcontextprotocol/registry/refs/heads/main/docs/reference/api/openapi.yaml) - This is the official Anthropic MCP Registry REST API specification that this implementation follows
2. Review the [authentication guide](./auth.md) for authentication setup
3. Examine the test script at `cli/test_anthropic_api.py` for working examples
4. Check server logs for detailed error information

The API is fully compatible with Anthropic's MCP Registry specification, so any client built for the official registry should work with this implementation.
