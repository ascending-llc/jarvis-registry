# Frequently Asked Questions (FAQ)

## General

### What is Jarvis Registry?

Jarvis Registry is an enterprise-grade **MCP (Model Context Protocol) and A2A Agent Gateway and Workflow Orchestration platform**. It provides secure, governed access for AI copilots and autonomous agents to internal tools and data through a single gateway.

### How is Jarvis Registry different from other MCP frameworks?

Jarvis Registry focuses on **enterprise-grade security and governance**:

- **Identity & Access Management**: OAuth 2.0/OIDC integration (Keycloak, Cognito, Entra ID)
- **Fine-Grained Access Control**: ACL engine with scope-based and role-based permissions down to tool level
- **A2A Agent Orchestration**: Register and coordinate autonomous agents through secure gateway
- **Audit & Observability**: Full request logging, OpenTelemetry tracing, Prometheus metrics

Other MCP frameworks (like `mcp-use` or `nanobot`) focus on MCP server/client implementation. Jarvis Registry adds enterprise identity, access control, and observability layers.

### What's the difference between MCP Gateway and A2A Agent Orchestration?

- **MCP Gateway**: Provides authenticated entry point for AI clients (Cursor, Claude Desktop, GitHub Copilot) to connect to MCP servers
- **A2A Agent Orchestration**: Registers autonomous agents and coordinates their workflows (orchestrator agents coordinate worker agents)

Both use the same secure gateway with identity management and access control.

## Getting Started

### How do I install Jarvis Registry?

```bash
# Clone the repository
git clone https://github.com/ascending-llc/jarvis-registry.git
cd jarvis-registry

# Copy and configure environment
cp .env.example .env
# Edit .env with your identity provider credentials

# Setup Python Virtual Environment
uv sync --all-packages
source .venv/bin/activate

# Start all services
docker compose --profile full up -d

# Open the registry UI
open http://localhost:80
```

See the [Quick Start guide](docs/quick-start.md) for detailed instructions.

### What are the deployment options?

Jarvis Registry supports cloud-native deployment on:

- **AWS** — EKS
- **Azure** — AKS
- **GCP** — GKE
- **Docker Compose** — Full local stack in under 5 minutes

## Features

### What Identity Providers (IDP) are supported?

Jarvis Registry integrates with:

- **Keycloak** (open-source)
- **Amazon Cognito**
- **Microsoft Entra ID** (formerly Azure AD)

No custom auth code needed — configure your IDP in `.env` and Jarvis Registry handles authentication.

### What is the ACL engine?

The **Access Control List (ACL) engine** enforces permissions at multiple levels:

- **Scope-based**: Control which MCP servers/A2A agents are accessible
- **Role-based**: Define permissions by user roles (admin, developer, viewer)
- **Tool-level**: Fine-grained permissions down to individual MCP tools

ACLs ensure agents and copilots only access authorized tools and data.

### What is Skill & Context-Based Discovery?

Jarvis Registry provides **semantic search** over:

- Skills and capabilities
- Descriptions and metadata
- Tags and categories

This allows agents and copilots to discover the right MCP server or A2A agent at runtime based on their needs.

### What observability features are available?

Jarvis Registry provides:

- **Full request logging**: Track all MCP and A2A requests
- **OpenTelemetry tracing**: Distributed tracing across services
- **Prometheus metrics**: Real-time metrics for monitoring

Use your existing observability stack (Grafana, Datadog, etc.) to visualize Jarvis Registry metrics.

## Integration

### Which AI copilots can connect to Jarvis Registry?

Jarvis Registry supports any **MCP-compatible** copilot:

- **Cursor**
- **Claude Desktop**
- **GitHub Copilot**
- **VS Code** (with MCP extensions)
- **Windsurf**
- Any MCP client

### How do I register an MCP server?

MCP servers are registered in the registry configuration. See the [MCP Server Registration guide](docs/features/mcp-registry/) for details.

### How do I register an A2A agent?

A2A agents are registered with their capabilities, skills, and access requirements. Orchestrator agents coordinate worker agents through the gateway. See the [A2A Agent Orchestration guide](docs/features/a2a-registry/) for details.

## Configuration

### What environment variables are required?

Key environment variables (see `.env.example`):

- **IDP Configuration**: OAuth 2.0/OIDC settings for your identity provider
- **Database**: PostgreSQL connection for registry data
- **Redis**: For caching and session management
- **Docker**: Container orchestration settings

### How do I configure ACLs?

ACLs are defined in the registry configuration, specifying:

- Which users/roles can access which MCP servers
- Which tools are permitted for each scope
- Agent-level permissions for A2A orchestration

See the [ACL Configuration guide](docs/design/acl-design/) for details.

## Troubleshooting

### Jarvis Registry won't start

**Common causes:**

1. **Missing IDP configuration**: Ensure `.env` has valid OAuth 2.0/OIDC credentials
2. **Database connection failed**: Check PostgreSQL is running and accessible
3. **Port conflicts**: Ensure ports 80, 443, and Docker ports are available

**Solution:**

```bash
# Check Docker logs
docker compose logs

# Verify environment
cat .env | grep -E "IDP|DATABASE|REDIS"
```

### MCP clients can't connect

**Common causes:**

1. **Authentication failed**: Verify IDP credentials and tokens
2. **ACL blocking access**: Check ACL rules for the client/user
3. **Network/firewall issues**: Ensure gateway endpoint is accessible

**Solution:**

```bash
# Test MCP endpoint
curl -v http://localhost:80/mcp

# Check ACL logs
docker compose logs registry | grep -i acl
```

### A2A agents not coordinating

**Common causes:**

1. **Agent not registered**: Verify agent registration in registry
2. **ACL preventing coordination**: Check orchestrator-to-worker permissions
3. **Skill discovery failing**: Verify semantic search configuration

**Solution:**

```bash
# Check registered agents
curl http://localhost:80/api/agents

# Verify agent skills
curl http://localhost:80/api/agents/{agent_id}/skills
```

## Help & Resources

- **Documentation**: [jarvisregistry.com](https://jarvisregistry.com)
- **Demo Video**: [YouTube Demo](https://youtu.be/EUqWc_mAaXs)
- **Website**: [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/jarvis-registry/)
- **Contributing**: [Contributing Guide](CONTRIBUTING.md)
- **Security Issues**: [support@ascendingdc.com](mailto:support@ascendingdc.com) (do not open public issues)

---

*Last updated: 2026-05-16*