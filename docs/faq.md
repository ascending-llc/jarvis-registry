# Frequently Asked Questions (FAQ)

## General

### What is Jarvis Registry?

Jarvis Registry is an open-source, enterprise-grade **MCP (Model Context Protocol) and A2A Agent Gateway and Workflow Orchestration platform** built by [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/). It gives AI copilots and autonomous agents secure, governed access to internal tools and data through a single control plane.

→ Full overview: [What is Jarvis Registry?](index.md)

### How is Jarvis Registry different from other MCP frameworks?

Most MCP frameworks focus on protocol implementation. Jarvis Registry adds the enterprise layer on top: identity governance, fine-grained ACL enforcement down to the tool level, A2A agent orchestration, and full observability — all through a single gateway.

→ See the full feature comparison: [Why Use Jarvis Registry?](FEATURES.md)

### What is the difference between the MCP Gateway and A2A Agent Gateway?

The **MCP Gateway** is the authenticated entry point for AI copilots (Cursor, Claude Desktop, GitHub Copilot) to reach your MCP servers. The **A2A Agent Gateway** is the entry point for autonomous agents communicating over JSON-RPC 2.0 or HTTP+JSON — both share the same control plane, identity governance, and ACL enforcement.

→ Details: [Gateway & Proxy](FEATURES.md#1-gateway-proxy) · [A2A Agent Workflow](FEATURES.md#5-a2a-agent-workflow-orchestration)

### What is A2A Agent Orchestration?

A2A Agent Orchestration is Jarvis Registry's model for coordinating autonomous agents. An **orchestrator agent** decomposes work and delegates to **worker agents**, each registered in the registry with their own skills, ACL policies, and transport configuration. The gateway enforces permissions on every agent-to-agent call.

→ See: [A2A Agent Workflow Orchestration](FEATURES.md#5-a2a-agent-workflow-orchestration) · [A2A Agent Registry](features/a2a-registry.md)

---

## Getting Started

### How do I install Jarvis Registry?

→ Follow the [Quick Start Guide](quick-start.md) — the full stack runs in under 5 minutes with Docker Compose.

### What are the deployment options?

Jarvis Registry supports Docker Compose for local development and Kubernetes (EKS, AKS, GKE) for production.

→ See: [Deployment Guide](deployment-guide.md)

---

## Features

### Which Identity Providers (IdP) are supported?

Keycloak (open-source), Amazon Cognito, and Microsoft Entra ID (formerly Azure AD).

→ Setup guides: [Keycloak](keycloak-integration.md) · [Cognito](cognito.md) · [Entra ID](entra-id-setup.md)

### How does the ACL engine work?

Jarvis Registry uses a two-layer authorization model: **scopes** control which API endpoints are accessible, and **ACLs** enforce fine-grained per-resource permissions (VIEW, EDIT, DELETE) down to the individual MCP tool level.

→ Full design: [RBAC & Scope System](design/scopes.md) · [Identity & Access Management](FEATURES.md#3-identity-access-management)

### Which AI copilots can connect?

Any MCP-compatible client: Cursor, Claude Desktop, GitHub Copilot, VS Code, Windsurf, and others.

→ See: [AI Copilot Integration](FEATURES.md#1-gateway-proxy)

### How does Skill & Context-Based Discovery work?

Jarvis Registry provides semantic search over MCP server and A2A agent metadata (skills, descriptions, tags) so agents and copilots can find the right tool at runtime.

→ See: [Skill & Context-Based Discovery](FEATURES.md#4-skill-context-based-discovery)

### What observability features are available?

Full request logging, OpenTelemetry distributed tracing, and Prometheus metrics — compatible with Grafana, Datadog, and any standard observability stack.

→ See: [Observability with OpenTelemetry](FEATURES.md#6-observability-with-opentelemetry)

---

## Integration

### How do I register an MCP server?

→ See: [MCP Server Registry](features/mcp-registry.md)

### How do I register an A2A agent?

→ See: [A2A Agent Registry](features/a2a-registry.md) · [A2A Agent Management](a2a-agent-management.md)

---

## Configuration

### What configuration files do I need?

The main files are `.env` (runtime config, copy from `.env.example`), `oauth2_providers.yml` (IdP settings), and `scopes.yml` (RBAC scope mappings).

→ Full reference: [Configuration Reference](configuration.md)

### How do I manage ACL permissions?

No configuration files needed. ACLs are managed directly from the registry UI via the **Share** dialog on each MCP server or A2A agent. You can search for users or groups by name or email, assign roles (Owner, Editor, Viewer), and toggle public access — all from the interface.

→ See: [Identity & Access Management](FEATURES.md#3-identity-access-management) · [RBAC & Scope System Design](design/scopes.md)

---

## Troubleshooting

### Jarvis Registry won't start

Check your `.env` for missing IdP credentials, verify Docker is running, and confirm ports 80/443 are free.

```bash
docker compose logs
```

→ See: [Quick Start Guide — Troubleshooting](quick-start.md)

### MCP clients can't connect

Verify your IdP credentials and token, confirm ACL rules permit the user/scope, and check network access to the gateway endpoint.

```bash
docker compose logs registry | grep -i acl
```

→ See: [Authentication and Authorization Guide](auth.md)

### A2A agents are not coordinating

Confirm the agent is registered and that orchestrator-to-worker ACL permissions are in place.

```bash
docker compose logs registry | grep -i agent
```

→ See: [A2A Agent Workflow](FEATURES.md#5-a2a-agent-workflow-orchestration) · [A2A Agent Management](a2a-agent-management.md)

---

## Help & Resources

- **Documentation**: [jarvisregistry.com](https://jarvisregistry.com)
- **Demo Video**: [YouTube](https://youtu.be/EUqWc_mAaXs)
- **Website**: [ASCENDING Inc — Jarvis Registry](https://ascendingdc.com/jarvis-ai/jarvis-registry/)
- **Bug Reports & Feature Requests**: [Open a GitHub Issue](https://github.com/ascending-llc/jarvis-registry/issues)
- **Enterprise & Business Support**: [Contact ASCENDING Inc](https://ascendingdc.com/jarvis-ai/jarvis-registry/) or email [support@ascendingdc.com](mailto:support@ascendingdc.com)
- **Contributing**: [Contributing Guide](CONTRIBUTING.md)
- **Security Vulnerabilities**: Email [support@ascendingdc.com](mailto:support@ascendingdc.com) — do not open a public GitHub issue
