# Authentication and Authorization Guide

The MCP Gateway Registry provides enterprise-ready authentication and authorization using industry-standard OAuth 2.0 flows with fine-grained access control.

## Quick Navigation

**I want to...**
- [Manage A2A agents via CLI](#a2a-agent-management) → Agent Management
- [Build an AI agent with authentication](#quick-start-for-ai-agents) → Quick Start
- [Understand the authentication architecture](#authentication-architecture) → Architecture
- [Set up external service integration](#external-service-integration) → Egress Auth
- [Configure fine-grained permissions](#fine-grained-access-control-fgac) → FGAC
- [See all configuration options](#configuration-reference) → Reference

---

## A2A Agent Management

For managing A2A agents through the CLI using the `mcp-gateway-m2m` service account:

**See: [A2A Agent Management Guide](a2a-agent-management.md)**

Quick commands:
```bash
uv run python cli/agent_mgmt.py register cli/examples/code_reviewer_agent.json
uv run python cli/agent_mgmt.py list
uv run python cli/agent_mgmt.py get /code-reviewer
```

The `mcp-gateway-m2m` service account is automatically configured with full agent management permissions.

---

## Quick Start for AI Agents

Get your AI agent authenticated and running in 5 minutes.

### Prerequisites
- Keycloak service account credentials (provided by your administrator)
- Access to external services you want to integrate (optional)

### Step 1: Configure Environment

Create `credentials-provider/oauth/.env` with your credentials:

```bash
# Authentication Provider Selection
AUTH_PROVIDER=keycloak

# Keycloak Ingress Authentication (Required for MCP Gateway access)
KEYCLOAK_URL=https://mcpgateway.ddns.net
KEYCLOAK_REALM=mcp-gateway
KEYCLOAK_M2M_CLIENT_ID=agent-your-agent-name-m2m
KEYCLOAK_M2M_CLIENT_SECRET=your_keycloak_m2m_client_secret

# Alternative: Cognito (if AUTH_PROVIDER=cognito)
# AWS_REGION=us-east-1
# INGRESS_OAUTH_USER_POOL_ID=us-east-1_XXXXXXXXX
# INGRESS_OAUTH_CLIENT_ID=your_cognito_client_id
# INGRESS_OAUTH_CLIENT_SECRET=your_cognito_client_secret

# Alternative: Microsoft Entra ID (if AUTH_PROVIDER=entra)
# ENTRA_TENANT_ID=your-tenant-id-or-common
# ENTRA_CLIENT_ID=your-application-client-id
# ENTRA_CLIENT_SECRET=your-client-secret-value
# ENTRA_GRAPH_URL=https://graph.microsoft.com  # For sovereign clouds
# ENTRA_M2M_SCOPE=https://graph.microsoft.com/.default  # M2M scope

# Egress Authentication (Optional - for external services)
EGRESS_OAUTH_CLIENT_ID_1=your_external_provider_client_id
EGRESS_OAUTH_CLIENT_SECRET_1=your_external_provider_client_secret
EGRESS_OAUTH_REDIRECT_URI_1=http://localhost:9999/callback
EGRESS_PROVIDER_NAME_1=atlassian
EGRESS_MCP_SERVER_NAME_1=atlassian
```

**Pro Tip:** Use the example files as templates:
```bash
# Copy and customize the example configurations
cp credentials-provider/oauth/.env.example credentials-provider/oauth/.env
cp .env.example .env

# Edit with your actual credentials
```

### Step 2: Run OAuth Setup

```bash
cd credentials-provider
./generate_creds.sh

# Available options:
# ./generate_creds.sh --all              # Run all authentication flows (default)
# ./generate_creds.sh --ingress-only     # Only MCP Gateway authentication
# ./generate_creds.sh --egress-only      # Only external provider authentication
# ./generate_creds.sh --agentcore-only   # Only AgentCore token generation
# ./generate_creds.sh --provider google  # Specify provider for egress auth
# ./generate_creds.sh --verbose          # Enable debug logging

# This will:
# 1. Authenticate with Keycloak (M2M) or Cognito (M2M/2LO)
# 2. Optionally authenticate with external services (3LO)
# 3. Generate AgentCore tokens if configured
# 4. Generate Keycloak agent tokens if configured
# 5. Generate MCP client configurations
# 6. Add no-auth services to configurations
```

### Step 3: Use Generated Configuration

The script generates ready-to-use MCP client configurations:

**For VS Code** (`~/.vscode/mcp.json`):
```json
{
  "mcp": {
    "servers": {
      "mcpgw": {
        "url": "https://mcpgateway.ddns.net/mcpgw/mcp",
        "headers": {
          "X-Authorization": "Bearer {your_keycloak_jwt_token}",
          "X-Client-Id": "{agent_client_id}",
          "X-Keycloak-Realm": "mcp-gateway",
          "X-Keycloak-URL": "http://localhost:8080"
        }
      },
      "atlassian": {
        "url": "https://mcpgateway.ddns.net/atlassian/mcp",
        "headers": {
          "Authorization": "Bearer {atlassian_oauth_token}",
          "X-Atlassian-Cloud-Id": "{cloud_id}",
          "X-Authorization": "Bearer {your_keycloak_jwt_token}",
          "X-Client-Id": "{agent_client_id}",
          "X-Keycloak-Realm": "mcp-gateway",
          "X-Keycloak-URL": "http://localhost:8080"
        }
      }
    }
  }
}
```

**For AI Coding Assistants** (Create your token configuration using these actual patterns):

```json
{
  "mcpServers": {
    "mcpgw": {
      "type": "streamable-http",
      "url": "https://mcpgateway.ddns.net/mcpgw/mcp",
      "headers": {
        "X-Authorization": "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
        "X-Client-Id": "agent-ai-coding-assistant-m2m",
        "X-Keycloak-Realm": "mcp-gateway",
        "X-Keycloak-URL": "http://localhost:8080"
      },
      "disabled": false,
      "alwaysAllow": []
    },
    "atlassian": {
      "type": "streamable-http",
      "url": "https://mcpgateway.ddns.net/atlassian/mcp",
      "headers": {
        "Authorization": "Bearer eyJraWQiOiJhdXRoLmF0bGFzc2lhbi5jb20tQUNDRVNTLTk0ZTczYTkwLTUxYWQtNGFjMS1hOWFjLWU4NGUwNDVjNDU3ZCIsImFsZyI6IlJTMjU2In0...",
        "X-Atlassian-Cloud-Id": "923a213e-e930-4359-be44-f4b164d3f269",
        "X-Authorization": "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
        "X-Client-Id": "agent-ai-coding-assistant-m2m",
        "X-Keycloak-Realm": "mcp-gateway",
        "X-Keycloak-URL": "http://localhost:8080"
      },
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
```

**To Generate Your Own Tokens:**

1. **Create Keycloak Service Account** for your AI agent:
   ```bash
   # Run from the project root
   ./keycloak/setup/setup-agent-service-account.sh --agent-id ai-coding-assistant --group mcp-servers-unrestricted
   ```

2. **Generate Agent Token**:
   ```bash
   # Generate M2M token for your agent
   uv run python credentials-provider/keycloak/generate_tokens.py --agent-id ai-coding-assistant

   # Check generated token file
   cat .oauth-tokens/agent-ai-coding-assistant-m2m-token.json
   ```

3. **Create MCP Configuration**:
   ```bash
   # Run complete credential generation
   ./credentials-provider/generate_creds.sh --keycloak-only

   # Your configuration will be in:
   # - .oauth-tokens/mcp.json (for Claude Code/Roocode)
   # - .oauth-tokens/vscode_mcp.json (for VS Code)
   ```

**Important**: Use your actual generated tokens - the examples above are truncated for security.

### Step 4: Test Your Connection

```python
# Example: Using the MCP client with authentication
import json
import os
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient

# Method 1: Load configuration from ~/.vscode/mcp.json
def load_mcp_config_from_file():
    """Load MCP configuration from VS Code config file."""
    config_path = Path.home() / ".vscode" / "mcp.json"

    if not config_path.exists():
        # Fallback to oauth-tokens directory if VS Code config doesn't exist
        config_path = Path.cwd() / ".oauth-tokens" / "vscode_mcp.json"

    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
            # Extract the servers configuration
            return config.get("mcp", {}).get("servers", {})
    else:
        raise FileNotFoundError(f"MCP configuration not found at {config_path}")

# Method 2: Direct configuration (as shown in agent.py)
def create_mcp_client_direct(auth_token, user_pool_id, client_id, region):
    """Create MCP client with direct configuration."""
    auth_headers = {
        'X-Authorization': f'Bearer {auth_token}',
        'X-User-Pool-Id': user_pool_id,
        'X-Client-Id': client_id,
        'X-Region': region
    }

    return MultiServerMCPClient({
        "mcp_gateway": {
            "url": "https://mcpgateway.ddns.net/mcpgw/mcp",
            "transport": "sse",
            "headers": auth_headers
        }
    })

# Usage Example - Loading from config file
async def connect_with_config_file():
    # Load configuration from file
    servers_config = load_mcp_config_from_file()

    # Initialize MCP client with loaded configuration
    mcp_client = MultiServerMCPClient(servers_config)

    # Discover available tools (filtered by your permissions)
    tools = await mcp_client.get_tools()
    return tools

# Usage Example - Direct configuration (useful for agents)
async def connect_with_params(token, pool_id, client_id, region="us-east-1"):
    # Create client with parameters
    mcp_client = create_mcp_client_direct(token, pool_id, client_id, region)

    # Discover available tools
    tools = await mcp_client.get_tools()
    return tools
```

**That's it!** Your agent is now authenticated and can access MCP servers based on your assigned permissions.

### Integration with Agent Applications

The `agents/agent.py` file demonstrates how to integrate authentication in a production agent:

```python
# Example from agents/agent.py showing MultiServerMCPClient usage
from langchain_mcp_adapters.client import MultiServerMCPClient

# The agent can read auth parameters from multiple sources:
# 1. Command-line arguments (--client-id, --client-secret, etc.)
# 2. Environment variables (COGNITO_CLIENT_ID, etc.)
# 3. Configuration files (.env.agent, .env.user)
# 4. VS Code MCP config (~/.vscode/mcp.json)

# Current implementation in agent.py:
auth_headers = {
    'X-Authorization': f'Bearer {access_token}',
    'X-User-Pool-Id': args.user_pool_id,
    'X-Client-Id': args.client_id,
    'X-Region': args.region
}

client = MultiServerMCPClient({
    "mcp_registry": {
        "url": server_url,
        "transport": "sse",
        "headers": auth_headers
    }
})

# To enhance agent.py to read from ~/.vscode/mcp.json:
# Add a function to load config from file as a fallback
# when auth parameters are not provided via CLI or env vars
```

For a complete working example, see [`agents/agent.py`](../agents/agent.py) which implements:
- Multiple authentication methods (M2M, session cookies, JWT tokens)
- Dynamic token generation and refresh
- Comprehensive error handling and logging
- Integration with LangChain and Anthropic models

---

## Authentication Architecture

### Overview

The MCP Gateway Registry uses a comprehensive three-layer authentication system:

1. **Ingress Authentication (2LO)**: Gateway access using Keycloak or Amazon Cognito
2. **Egress Authentication (3LO)**: External service integration via OAuth providers
3. **Fine-Grained Access Control (FGAC)**: Permission management at method and tool level

### Key Concepts

- **Dual Token System**: AI agents carry BOTH ingress and egress tokens
- **Token Storage**: After authentication, tokens are stored locally and used by AI agents
- **Header Passing**: Both token sets are passed as headers from AI agent to gateway
- **Validation Points**:
  - Ingress tokens validated by gateway with Keycloak or Cognito
  - FGAC enforced at gateway level using group-based permissions
  - Egress tokens passed through to MCP servers for their validation

### High-Level Authentication Flow

```mermaid
sequenceDiagram
    participant User as User/Developer
    participant Agent as AI Agent
    participant Auth as Keycloak/Cognito<br/>(Ingress IdP)
    participant ExtIdP as External IdP<br/>(e.g., Atlassian)
    participant Gateway as MCP Gateway
    participant MCPServer as MCP Server

    Note over User,MCPServer: One-Time Setup

    User->>Cognito: 1. Authenticate (2LO)
    Cognito->>User: Ingress Token

    User->>ExtIdP: 2. Authenticate & Approve (3LO)
    Note right of User: "Allow AI agent to<br/>act on my behalf"
    ExtIdP->>User: Egress Token

    User->>Agent: 3. Configure with both tokens

    Note over Agent,MCPServer: Runtime (Every Request)

    Agent->>Gateway: 4. Request with dual tokens<br/>(Ingress + Egress headers)
    Gateway->>Cognito: 5. Validate ingress auth
    Gateway->>Gateway: 6. Apply FGAC
    Gateway->>MCPServer: 7. Forward with egress token
    MCPServer->>ExtIdP: 8. Validate egress auth
    MCPServer->>Agent: 9. Response (via Gateway)
```

### Complete End-to-End Flow (Detailed)

```mermaid
sequenceDiagram
    participant User as User/Developer
    participant Agent as AI Agent
    participant Browser as Browser
    participant Cognito as Amazon Cognito<br/>(Ingress IdP)
    participant ExtIdP as External IdP<br/>(e.g., Atlassian)
    participant Gateway as MCP Gateway
    participant AuthServer as Auth Server<br/>(in Gateway)
    participant MCPServer as MCP Server

    Note over User,MCPServer: PHASE 1: One-Time Setup & Authentication

    rect rgb(240, 248, 255)
        Note over User,Cognito: Step 1: Ingress Authentication (2LO/M2M)
        User->>Cognito: 1. Client Credentials Grant<br/>(client_id + client_secret)
        Cognito->>User: 2. JWT Token with scopes
        User->>User: 3. Store ingress token locally
    end

    rect rgb(255, 248, 220)
        Note over User,ExtIdP: Step 2: Egress Authentication (3LO)
        User->>Browser: 4. Initiate OAuth flow
        Browser->>ExtIdP: 5. Redirect to provider login
        User->>ExtIdP: 6. User authenticates
        User->>ExtIdP: 7. Reviews & approves permissions<br/>"Allow AI agent to act on my behalf"
        ExtIdP->>Browser: 8. Auth code redirect
        Browser->>User: 9. Capture auth code
        User->>ExtIdP: 10. Exchange code for tokens
        ExtIdP->>User: 11. Access & refresh tokens
        User->>User: 12. Store egress tokens locally
    end

    rect rgb(240, 255, 240)
        Note over User: Step 3: Configure AI Agent
        User->>User: 13. Create configuration with:<br/>- Server URLs<br/>- Ingress headers (X-Authorization, etc.)<br/>- Egress headers (Authorization, etc.)
    end

    Note over Agent,MCPServer: PHASE 2: Runtime - AI Agent Uses Tokens

    rect rgb(255, 240, 245)
        Note over Agent,MCPServer: Every MCP Request
        Agent->>Gateway: 14. MCP Request with headers:<br/>X-Authorization: Bearer {ingress_jwt}<br/>Authorization: Bearer {egress_jwt}<br/>X-User-Pool-Id, X-Client-Id, X-Region

        Gateway->>AuthServer: 15. Extract & validate ingress token
        AuthServer->>Cognito: 16. Verify JWT signature & claims
        Cognito->>AuthServer: 17. Token valid + user scopes

        AuthServer->>AuthServer: 18. Apply FGAC rules<br/>(Check tool/method permissions)

        Gateway->>MCPServer: 19. Forward request with egress headers:<br/>Authorization: Bearer {egress_jwt}

        MCPServer->>ExtIdP: 20. Validate egress token
        ExtIdP->>MCPServer: 21. Token valid + permissions

        MCPServer->>MCPServer: 22. Execute requested action<br/>(within approved scope)

        MCPServer->>Gateway: 23. Response
        Gateway->>Agent: 24. Response
        Agent->>User: 25. Display result
    end
```

### Authentication Types Explained

#### Two-Legged OAuth (2LO) - Ingress
- **Purpose**: Authenticate agents/users TO the MCP Gateway
- **Provider**: Amazon Cognito
- **Use Case**: M2M authentication for AI agents
- **Token Type**: JWT with embedded scopes

#### Three-Legged OAuth (3LO) - Egress
- **Purpose**: Authenticate FROM the Gateway to external services
- **Providers**: Atlassian, Google, GitHub, others
- **Use Case**: Access external APIs on behalf of users
- **Token Type**: Provider-specific OAuth tokens

---

## Token Header Mapping

### Headers sent from AI Agent to Gateway:
```json
{
  "headers": {
    // Ingress Authentication (for Gateway) - Keycloak
    "X-Authorization": "Bearer {keycloak_jwt_token}",
    "X-Client-Id": "{agent_client_id}",
    "X-Keycloak-Realm": "mcp-gateway",
    "X-Keycloak-URL": "http://localhost:8080",

    // OR Ingress Authentication (for Gateway) - Cognito
    "X-Authorization": "Bearer {cognito_jwt_token}",
    "X-User-Pool-Id": "{cognito_user_pool_id}",
    "X-Client-Id": "{cognito_client_id}",
    "X-Region": "{aws_region}",

    // Egress Authentication (for MCP Server) - Example: Atlassian
    "Authorization": "Bearer {atlassian_oauth_token}",
    "X-Atlassian-Cloud-Id": "{atlassian_cloud_id}"
  }
}
```

### Headers forwarded from Gateway to MCP Server:
```json
{
  "headers": {
    // Only egress headers are forwarded (provider-specific)
    "Authorization": "Bearer {external_provider_token}",
    "X-Atlassian-Cloud-Id": "{atlassian_cloud_id}"  // For Atlassian
    // OR other provider-specific headers as needed
  }
}
```

## Key Security Layers

### Layer 1: Ingress Authentication (2LO)
- **Purpose**: Controls who can access the MCP Gateway
- **Validation**: Gateway validates with Cognito
- **Headers**: X-Authorization, X-User-Pool-Id, X-Client-Id, X-Region
- **Methods**: Machine-to-Machine (M2M), User Authentication to Registry UI

### Layer 2: Fine-Grained Access Control (FGAC)
- **Purpose**: Controls which tools/methods within MCP servers can be accessed
- **Validation**: Applied at Gateway level after ingress auth
- **Based on**: User/agent scopes and permissions

### Layer 3: Egress Authentication (3LO)
- **Purpose**: Allows MCP servers to act on user's behalf with external services
- **Validation**: MCP server validates with its IdP (e.g., Atlassian)
- **Headers**: Authorization, provider-specific headers

## External Service Integration

### Supported Providers

- **Atlassian Cloud**: Jira, Confluence integration
- **Google**: Workspace, Gmail, Drive access
- **GitHub**: Repository and organization access
- **Others**: See [`agents/oauth/oauth_providers.yaml`](../agents/oauth/oauth_providers.yaml) for complete list

### Setting Up External Provider (Atlassian Example)

1. **Create Developer App**
   - Visit [developer.atlassian.com](https://developer.atlassian.com)
   - Create OAuth 2.0 (3LO) app
   - Set redirect URI: `http://localhost:8080/callback`

2. **Configure Credentials**
   ```bash
   # In credentials-provider/oauth/.env
   EGRESS_OAUTH_CLIENT_ID_1=your_atlassian_client_id
   EGRESS_OAUTH_CLIENT_SECRET_1=your_atlassian_client_secret
   EGRESS_OAUTH_REDIRECT_URI_1=http://localhost:8080/callback
   EGRESS_PROVIDER_NAME_1=atlassian
   EGRESS_MCP_SERVER_NAME_1=atlassian
   ```

3. **Run Authentication**
   ```bash
   cd credentials-provider
   ./generate_creds.sh  # This will handle both ingress and egress auth
   ```

---

## Fine-Grained Access Control (FGAC)

The FGAC system provides granular permissions for MCP servers, methods, and individual tools.

### Key Concepts

#### Scope Types

- **UI Scopes**: Registry management permissions
  - `mcp-registry-admin`: Full administrative access
  - `mcp-registry-user`: Limited user access

- **Server Scopes**: MCP server access
  - `mcp-servers-unrestricted/read`: Read all servers
  - `mcp-servers-unrestricted/execute`: Execute all tools
  - `mcp-servers-restricted/read`: Limited read access
  - `mcp-servers-restricted/execute`: Limited execute access

#### Methods vs Tools

The system distinguishes between:

- **MCP Methods**: Protocol operations (`initialize`, `tools/list`, `tools/call`)
- **Individual Tools**: Specific functions within servers

### Access Control Example

```yaml
# User can list tools but only execute specific ones
mcp-servers-restricted/execute:
  - server: fininfo
    methods:
      - tools/list        # Can list all tools
      - tools/call        # Can call tools
    tools:
      - get_stock_aggregates   # But only these specific tools
      - print_stock_data       # Not other tools in the server
```

### Common Scenarios

| Scenario | Can List Tools? | Can Execute? | Which Tools? |
|----------|----------------|--------------|--------------|
| Read-only user | ✅ Yes | ❌ No | N/A |
| Restricted execute | ✅ Yes | ✅ Yes | Only specified tools |
| Unrestricted admin | ✅ Yes | ✅ Yes | All tools |

For complete FGAC documentation, see [Fine-Grained Access Control](scopes.md).

---

## Implementation Guide

### Running OAuth Authentication

#### Complete Setup (Ingress + Egress)
```bash
cd agents/oauth
./oauth_creds.sh                    # Interactive mode
./oauth_creds.sh --provider atlassian --force  # Specific provider
```

#### Ingress Only (Gateway Access)
```bash
./oauth_creds.sh --ingress-only
# Or directly:
python ingress_oauth.py --verbose
```

#### Egress Only (External Services)
```bash
./oauth_creds.sh --egress-only --provider atlassian
# Or directly:
python egress_oauth.py --provider atlassian
```

### Authentication Headers Reference

Headers are automatically managed by the OAuth scripts, but here's what gets sent:

#### For MCP Gateway (Ingress)
| Header | Purpose | Example |
|--------|---------|---------|
| `X-Authorization` | JWT token for gateway | `Bearer eyJhbG...` |
| `X-User-Pool-Id` | Cognito pool identifier | `us-east-1_XXXXXXXXX` |
| `X-Client-Id` | Cognito client ID | `your_client_id` |
| `X-Region` | AWS region | `us-east-1` |

#### For External Services (Egress)
| Header | Purpose | Example |
|--------|---------|---------|
| `Authorization` | External service token | `Bearer ya29.a0...` |
| `X-Atlassian-Cloud-Id` | Atlassian instance | `1234-5678-9abc` |

---

## Configuration Reference

📋 **For complete configuration documentation, see [Configuration Reference](configuration.md)**

The Configuration Reference provides comprehensive documentation for all configuration files including:

- **Environment Variables**: `.env` files with complete parameter documentation
- **YAML Configurations**: All `.yml` and `.yaml` files with field descriptions
- **Example Files**: `.env.example` and `config.yaml.example` templates
- **Security Best Practices**: Credential management and file permissions
- **Troubleshooting**: Common configuration issues and solutions

### Quick Reference

| Configuration | Location | Purpose |
|---------------|----------|---------|
| **Main Environment** | `.env` | Core project settings and registry URLs |
| **OAuth Credentials** | `credentials-provider/oauth/.env` | Ingress/egress OAuth provider credentials |
| **AgentCore Config** | `credentials-provider/agentcore-auth/` | Amazon Bedrock AgentCore authentication |
| **OAuth Providers** | `auth_server/oauth2_providers.yml` | Web-based OAuth provider definitions |

### Key Configuration Files

#### OAuth Provider Configuration

Providers are configured in [`credentials-provider/oauth/oauth_providers.yaml`](../credentials-provider/oauth/oauth_providers.yaml):

```yaml
providers:
  atlassian:
    display_name: "Atlassian Cloud"
    auth_url: "https://auth.atlassian.com/authorize"
    token_url: "https://auth.atlassian.com/oauth/token"
    scopes:
      - "read:jira-work"
      - "write:jira-work"
    requires_cloud_id: true
```

### Scope Configuration

Access control is defined in [`auth_server/scopes.yml`](../auth_server/scopes.yml):

```yaml
group_mappings:
  mcp-registry-admin:
    - mcp-registry-admin
    - mcp-servers-unrestricted/read
    - mcp-servers-unrestricted/execute

mcp-servers-restricted/read:
  - server: currenttime
    methods:
      - tools/list
    tools:
      - current_time_by_timezone
```

### Generated Output Files

The OAuth scripts generate:

- **VS Code Config**: `~/.vscode/mcp.json` - Primary configuration for VS Code integration
- **Local VS Code Config**: `.oauth-tokens/vscode_mcp.json` - Local copy of VS Code config
- **Roocode Config**: `~/.roocode/mcp_servers.json` - Configuration for Roocode
- **Local Roocode Config**: `.oauth-tokens/mcp.json` - Local copy of Roocode config
- **Token Storage**: `.oauth-tokens/ingress.json`, `.oauth-tokens/egress.json` - Raw token data

#### Using Configuration Files in Your Code

The generated configuration files can be used directly with `MultiServerMCPClient`:

```python
# Option 1: Load from VS Code config location
config_path = Path.home() / ".vscode" / "mcp.json"

# Option 2: Load from local oauth-tokens directory
config_path = Path.cwd() / ".oauth-tokens" / "vscode_mcp.json"

# Parse and use the configuration
with open(config_path) as f:
    config = json.load(f)
    servers = config.get("mcp", {}).get("servers", {})
    client = MultiServerMCPClient(servers)
```

---

## Security Considerations

### Best Practices

1. **Token Storage**: Tokens stored with `600` permissions in `.oauth-tokens/`
2. **Environment Security**: Never commit `.env` files
3. **Scope Management**: Follow principle of least privilege
4. **Network Security**: HTTPS-only, PKCE where supported

### Token Lifecycle

- **Ingress tokens**: 1-hour expiry, auto-refresh via client credentials
- **Egress tokens**: Provider-specific, refresh tokens where available
- **Session management**: Handled automatically by OAuth scripts
- **Automated refresh service**: Background service monitors and refreshes all tokens

#### Token Refresh Service

The MCP Gateway includes an [Automated Token Refresh Service](token-refresh-service.md) that provides:

- **Continuous monitoring** of all OAuth tokens for expiration
- **Proactive refresh** before tokens expire (configurable 1-hour buffer)
- **Automatic MCP config generation** for coding assistants
- **Service discovery** for both OAuth and no-auth services
- **Background operation** with comprehensive logging

Start the token refresh service:
```bash
./start_token_refresher.sh
```

The service automatically generates MCP configurations for:
- **VS Code extensions** (`.oauth-tokens/vscode_mcp.json`)
- **Claude Code/Roocode** (`.oauth-tokens/mcp.json`)
- **Custom MCP clients** (standard configuration format)

---

## Troubleshooting

### Common Issues

**Cannot authenticate with Cognito**
- Verify credentials in `.env`
- Check user pool ID format
- Ensure client has proper Cognito configuration

**External provider authentication fails**
- Verify redirect URI matches provider configuration
- Check client ID/secret are correct
- Ensure required scopes are configured

**Permission denied for specific tools**
- Check your Cognito group memberships
- Verify scope mappings in `scopes.yml`
- Ensure tool names match exactly

---

## Testing and Validation

### MCP Gateway Testing Tools

Use the comprehensive testing script to validate your authentication setup:

```bash
# Test basic connectivity
./tests/mcp_cmds.sh basic

# Test MCP connectivity with authentication
./tests/mcp_cmds.sh ping

# List available tools (filtered by your permissions)
./tests/mcp_cmds.sh list

# Call specific tools
./tests/mcp_cmds.sh call debug_auth_context '{}'
./tests/mcp_cmds.sh call intelligent_tool_finder '{"natural_language_query": "quantum"}'

# Test against different gateway URLs
GATEWAY_URL=https://your-domain.com/mcp ./tests/mcp_cmds.sh ping
./tests/mcp_cmds.sh --url https://your-domain.com/mcp list
```

The testing script automatically:
- Detects localhost vs external URLs
- Loads appropriate authentication credentials from `.oauth-tokens/ingress.json`
- Handles MCP session establishment and authentication headers
- Provides clear error messages for debugging

### Credential Validation

```bash
# Validate all OAuth configurations
cd credentials-provider
./generate_creds.sh --verbose

# Test specific authentication flows
./generate_creds.sh --ingress-only --verbose    # Test MCP Gateway auth
./generate_creds.sh --egress-only --verbose     # Test external provider auth
./generate_creds.sh --agentcore-only --verbose  # Test AgentCore auth
```

### Authentication Flow Testing

1. **Ingress Authentication** (MCP Gateway access):
   ```bash
   python credentials-provider/oauth/ingress_oauth.py --verbose
   ```

2. **Egress Authentication** (External services):
   ```bash
   python credentials-provider/oauth/egress_oauth.py --provider atlassian --verbose
   ```

3. **AgentCore Token Generation**:
   ```bash
   python credentials-provider/agentcore-auth/generate_access_token.py --debug
   ```

---

## Setting Up Groups in Keycloak

This section provides step-by-step instructions for entry-level platform engineers to set up and manage groups in Keycloak for the MCP Gateway.

### Prerequisites Checklist

Before starting group setup:
- ✅ Keycloak is running (check: `docker-compose ps keycloak`)
- ✅ You have admin credentials (default: admin / your-configured-password)
- ✅ You can access the admin console at `https://your-domain/admin` or `http://localhost:8080/admin`
- ✅ The `mcp-gateway` realm exists (created by init-keycloak.sh)

### Understanding Groups in Keycloak

#### What are Groups?
Groups in Keycloak are collections of users that share common permissions. Think of them as departments in a company - all members inherit the same access rights.

#### Why Use Groups for MCP Gateway?
- **Simplified Management**: Assign permissions once to a group, not individually to each agent
- **Scalability**: Easy to add new agents with same permissions
- **Audit Trail**: Track which agents belong to which permission sets
- **Security**: Principle of least privilege - agents only get necessary access

#### MCP Gateway Group Structure
```
mcp-gateway (realm)
├── mcp-servers-unrestricted (group)
│   └── Full access to all MCP servers and tools
└── mcp-servers-restricted (group)
    └── Limited access to specific MCP servers and tools
```

### Step-by-Step: Creating Groups via Keycloak Admin Console

#### Step 1: Access the Admin Console

1. **Open your browser** and navigate to:
   - Local: `http://localhost:8080/admin`
   - Production: `https://mcpgateway.ddns.net/admin`

2. **Login with admin credentials**:
   ```
   Username: admin
   Password: <your-admin-password>
   ```

   > **Note**: If you don't know the password, check with your team lead or the person who set up Keycloak.

#### Step 2: Navigate to the Correct Realm

1. **Check current realm** - Look at the top-left dropdown
2. **Switch to `mcp-gateway` realm** if not already selected:
   - Click the realm dropdown
   - Select `mcp-gateway`

   > **Important**: Always ensure you're in the `mcp-gateway` realm, not the `master` realm!

#### Step 3: Create the Required Groups

1. **Navigate to Groups**:
   - In the left sidebar, click **Groups**
   - You'll see either existing groups or an empty list

2. **Create the first group** (`mcp-servers-unrestricted`):
   - Click the **Create group** button
   - Enter exactly: `mcp-servers-unrestricted`
   - Leave Parent as "none"
   - Click **Create**

3. **Create the second group** (`mcp-servers-restricted`):
   - Click **Create group** again
   - Enter exactly: `mcp-servers-restricted`
   - Leave Parent as "none"
   - Click **Create**

   > **Critical**: The group names must match EXACTLY (case-sensitive) for the auth server to recognize them!

#### Step 4: Verify Group Creation

1. **Check the groups list**:
   - Both groups should appear in the Groups list
   - They should be at the root level (no parent)

2. **Verify group paths**:
   - Click on each group
   - Check the path shows: `/mcp-servers-unrestricted` or `/mcp-servers-restricted`

### Assigning Service Accounts to Groups

Service accounts (used by AI agents) need to be added to groups to get permissions.

#### Method 1: Via User Management (Recommended for Individual Agents)

1. **Navigate to Users**:
   - Click **Users** in the left sidebar
   - Search for the service account (e.g., `agent-sre-agent-m2m`)

2. **Add to Group**:
   - Click on the service account name
   - Go to the **Groups** tab
   - Click **Join Group**
   - Select either:
     - `mcp-servers-unrestricted` for full access
     - `mcp-servers-restricted` for limited access
   - Click **Join**

3. **Verify Membership**:
   - The group should now appear in the user's group list
   - Shows the group path and when they joined

#### Method 2: Via Group Management (Good for Bulk Operations)

1. **Navigate to Groups**:
   - Click **Groups** in the left sidebar
   - Click on the target group

2. **Add Members**:
   - Go to the **Members** tab
   - Click **Add member**
   - Search for service accounts (type "agent" to see all)
   - Select the accounts you want to add
   - Click **Add**

### Group Configuration for Different Agent Types

#### Decision Tree for Group Assignment

```
Is this agent for...
│
├── Production/Critical Operations?
│   ├── Yes → mcp-servers-unrestricted
│   │   Examples: SRE agents, monitoring agents
│   │
│   └── No → Continue ↓
│
├── Customer-Facing or Limited Scope?
│   ├── Yes → mcp-servers-restricted
│   │   Examples: Travel assistants, chatbots
│   │
│   └── No → mcp-servers-restricted (default to restricted)
│
└── Development/Testing?
    └── Create a custom group or use mcp-servers-restricted
```

### Validating Your Group Setup

#### Quick Validation Checklist

1. **Groups exist in Keycloak** ✓
   ```bash
   # Check via admin console: Groups section should show both groups
   ```

2. **Service accounts have group membership** ✓
   ```bash
   # Check via admin console: Users → [service-account] → Groups tab
   ```

3. **Test token generation with groups** ✓
   ```bash
   # Generate a token for an agent
   cd credentials-provider
   python token_refresher.py --agent-id <agent-name>

   # Check the token contains groups
   cat .oauth-tokens/agent-<agent-name>.json | jq '.access_token' | \
     cut -d. -f2 | base64 -d | jq '.groups'

   # Should show: ["mcp-servers-unrestricted"] or ["mcp-servers-restricted"]
   ```

4. **Verify auth server recognizes groups** ✓
   ```bash
   # Test authentication with the token
   ./test-keycloak-mcp.sh --agent-id <agent-name>

   # Check auth server logs for group mapping
   docker-compose logs auth-server | grep -i "groups.*mapped"
   # Should see: "Mapped Keycloak groups ['mcp-servers-unrestricted'] to scopes..."
   ```

### Troubleshooting Group Issues

#### Issue: "Access forbidden" even though user is in group

**Symptoms**:
- Agent gets 403 Forbidden errors
- Logs show "Access denied" messages

**Solutions**:
1. **Verify exact group names**:
   ```bash
   # Groups must be named EXACTLY:
   # ✓ mcp-servers-unrestricted
   # ✗ mcp-servers-Unrestricted (wrong case)
   # ✗ mcp_servers_unrestricted (underscores instead of hyphens)
   ```

2. **Check group mapper configuration**:
   - Go to Clients → `mcp-gateway-m2m` → Client scopes
   - Check that "groups" mapper exists and is enabled

3. **Regenerate token** after group changes:
   ```bash
   python credentials-provider/token_refresher.py --agent-id <agent-name> --force
   ```

#### Issue: Groups not appearing in JWT token

**Symptoms**:
- Token doesn't contain groups claim
- Auth server can't map groups to scopes

**Solutions**:
1. **Ensure groups mapper is configured**:
   - Navigate to: Clients → `mcp-gateway-m2m` → Client scopes → Mappers
   - Should have a "groups" mapper
   - If missing, run: `./keycloak/setup/init-keycloak.sh`

2. **Check service account has "view-groups" role**:
   - Users → [service-account] → Role mappings
   - Should have "view-groups" client role

#### Issue: Can't create groups - "Forbidden" error

**Symptoms**:
- Admin console shows permission errors
- Can't create or modify groups

**Solutions**:
1. **Verify you're logged in as admin**:
   - Logout and login with the admin account
   - Not a regular user account

2. **Check you're in the correct realm**:
   - Must be in `mcp-gateway` realm to create groups there
   - Master realm admin can switch to any realm

### Best Practices for Group Management

1. **Naming Conventions**:
   - Use the exact names: `mcp-servers-unrestricted` and `mcp-servers-restricted`
   - Don't create variations or abbreviations

2. **Documentation**:
   - Keep a record of which agents are in which groups
   - Document why an agent needs unrestricted access

3. **Regular Audits**:
   - Monthly: Review group memberships
   - Quarterly: Audit unrestricted access needs
   - Remove agents that no longer need access

4. **Testing After Changes**:
   - Always regenerate tokens after group changes
   - Test agent access to verify permissions work

---

## Additional Resources

- [Complete Configuration Reference](configuration.md)
- [Amazon Cognito Setup Guide](cognito.md)
- [Complete Fine-Grained Access Control Documentation](scopes.md)
- [OAuth Provider Configurations](../credentials-provider/oauth/oauth_providers.yaml)
- [MCP Testing Tools](../tests/mcp_cmds.sh)
- [Source: Auth Server Implementation](../auth_server/server.py)
