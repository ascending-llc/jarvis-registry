# MCP Gateway Registry Management API

Command-line tools for managing users, groups, servers, and agents in the MCP Gateway Registry.

## API Specification

**Live OpenAPI Specification** (Always Up-to-Date):

Access the OpenAPI specification directly from your running registry instance:

- **Localhost**: `http://localhost/openapi.json`
- **Production**: `https://registry.us-east-1.example.com/openapi.json` (replace with your actual registry endpoint)

The live OpenAPI spec is auto-generated and always reflects the current API implementation.

**Reference OpenAPI Specification** (May Not Be Latest):

A reference copy is available at [openapi.json](openapi.json) for offline reference. However, this may not reflect the latest changes. Always use the live endpoint from your running registry for the most current API specification.

## Quick Start

### Local Development Testing

```bash
# 1. Start local services
docker-compose up -d

# 2. Generate credentials for localhost
cd credentials-provider
./generate_creds.sh
cd ..

# 3. Run management commands
uv run python api/registry_management.py --token-file .oauth-tokens/ingress.json <command>

# Example: Create a user
uv run python api/registry_management.py \
  --token-file .oauth-tokens/ingress.json \
  user-create-human \
  --username johndoe \
  --email john@example.com \
  --first-name John \
  --last-name Doe \
  --groups registry-user \
  --password MySecurePass123
```

### Production (AWS ECS Deployment)

```bash
# 1. Get M2M token from Keycloak (requires AWS credentials)
./api/get-m2m-token.sh \
  --aws-region us-east-1 \
  --keycloak-url https://keycloak.us-east-1.example.com \
  --output-file api/.token \
  registry-admin-bot

# 2. Run management commands against production
uv run python api/registry_management.py \
  --token-file api/.token \
  --registry-url https://registry.us-east-1.example.com \
  --aws-region us-east-1 \
  --keycloak-url https://keycloak.us-east-1.example.com \
  <command>

# Example: List all users in production
uv run python api/registry_management.py \
  --token-file api/.token \
  --registry-url https://registry.us-east-1.example.com \
  --aws-region us-east-1 \
  --keycloak-url https://keycloak.us-east-1.example.com \
  user-list
```

## Token Generation

### For Localhost
Use `credentials-provider/generate_creds.sh` which creates tokens for local Keycloak instance:

**Using generate_creds.sh (all services):**
```bash
cd credentials-provider && ./generate_creds.sh && cd ..
```
Token saved to: `.oauth-tokens/ingress.json`

**Using generate-agent-token.sh (specific M2M bot):**
```bash
# Generate token for default bot (mcp-gateway-m2m)
./keycloak/setup/generate-agent-token.sh

# Generate token for custom M2M bot
./keycloak/setup/generate-agent-token.sh lob1-bot
```
Tokens saved to: `.oauth-tokens/{agent-name}.json`

### For Production (AWS)
Use `api/get-m2m-token.sh` which retrieves tokens from AWS-deployed Keycloak:

**Default admin bot:**
```bash
./api/get-m2m-token.sh \
  --aws-region us-east-1 \
  --keycloak-url https://keycloak.us-east-1.example.com \
  --output-file api/.token \
  registry-admin-bot
```

**Custom M2M bot account:**
```bash
./api/get-m2m-token.sh \
  --aws-region us-east-1 \
  --keycloak-url https://keycloak.us-east-1.example.com \
  --output-file api/.token \
  lob1-bot
```

Token saved to: `api/.token`

**Notes:**
- `get-m2m-token.sh` is for AWS deployments only and requires AWS credentials
- It retrieves secrets from SSM Parameter Store
- You can specify any M2M service account name as the last argument
- The script automatically handles both `client-name` and `service-account-client-name` formats

## End-to-End Testing

### Test Localhost
```bash
./api/test-management-api-e2e.sh --token-file .oauth-tokens/ingress.json
```

### Test Production
```bash
./api/test-management-api-e2e.sh \
  --token-file api/.token \
  --registry-url https://registry.us-east-1.example.com \
  --aws-region us-east-1 \
  --keycloak-url https://keycloak.us-east-1.example.com
```

## Common Management Operations

### User Management

```bash
# Create human user
uv run python api/registry_management.py --token-file <token> \
  user-create-human \
  --username alice \
  --email alice@example.com \
  --first-name Alice \
  --last-name Smith \
  --groups engineering \
  --password SecurePass123

# Create M2M service account
uv run python api/registry_management.py --token-file <token> \
  user-create-m2m \
  --name service-bot \
  --groups engineering \
  --description "Automated service account"

# List users
uv run python api/registry_management.py --token-file <token> user-list

# Delete user
uv run python api/registry_management.py --token-file <token> \
  user-delete --username alice --force
```

### Group Management

```bash
# Create group
uv run python api/registry_management.py --token-file <token> \
  group-create \
  --name engineering \
  --description "Engineering team"

# List groups
uv run python api/registry_management.py --token-file <token> group-list

# Delete group
uv run python api/registry_management.py --token-file <token> \
  group-delete --name engineering --force
```

### Server Registration

```bash
# Register server from JSON config
uv run python api/registry_management.py --token-file <token> \
  register --config server-config.json

# List servers
uv run python api/registry_management.py --token-file <token> list

# Remove server
uv run python api/registry_management.py --token-file <token> \
  remove --path /my-server --force
```

### Agent Registration

```bash
# Register agent from JSON config
uv run python api/registry_management.py --token-file <token> \
  agent-register --config agent-config.json

# List agents
uv run python api/registry_management.py --token-file <token> agent-list

# Delete agent
uv run python api/registry_management.py --token-file <token> \
  agent-delete --path /my-agent --force
```

## Environment Summary

| Environment | Token Script | Registry URL | Keycloak URL |
|-------------|--------------|--------------|--------------|
| **Localhost** | `credentials-provider/generate_creds.sh` or `keycloak/setup/generate-agent-token.sh` | `http://localhost` (default) | `http://localhost:8080` (default) |
| **Production** | `api/get-m2m-token.sh --aws-region ... --keycloak-url ...` | `https://registry.us-east-1.example.com` | `https://keycloak.us-east-1.example.com` |

## Files

- `registry_management.py` - Main CLI for user/group/server/agent management
- `registry_client.py` - Python client library for Registry API
- `get-m2m-token.sh` - Get M2M tokens from AWS Keycloak (production only)
- `test-management-api-e2e.sh` - End-to-end test suite
- `.gitignore` - Excludes token files and temporary JSON files

## Requirements

- Python 3.11+ with `uv` package manager
- For production: AWS credentials with access to SSM Parameter Store
- For localhost: Running `docker-compose` stack with Keycloak

## Authentication

All commands require a valid JWT token:
- **Localhost**: Session-based tokens from `generate_creds.sh`
- **Production**: M2M client credentials from `get-m2m-token.sh`

Tokens are passed via `--token-file` parameter and must have appropriate scopes for the operations being performed.
