# Why Use Jarvis Registry?

Jarvis Registry is the enterprise control plane for AI — connecting copilots, autonomous agents, and enterprise tools through a single, secure, and observable gateway. It handles both [MCP (Model Context Protocol)](https://exploreagentic.ai/mcp/) servers and [A2A agents](https://exploreagentic.ai/agentic-ai/) under a unified control plane, so you don't need separate infrastructure for tool access and agent-to-agent communication.

---

## 1. Gateway & Proxy

A single authenticated entry point that handles every transport your MCP servers and agents speak — without requiring separate infrastructure per protocol.

**For MCP servers and AI copilots:**

- **SSE Transport**: Server-Sent Events for streaming tool responses to copilots and IDEs (Cursor, Claude Desktop, GitHub Copilot, VS Code)
- **Streamable HTTP**: Bidirectional MCP transport compatible with the latest MCP specification, supporting long-running tool calls
- **Single Endpoint**: One URL gives every MCP-compatible client access to your entire enterprise tool catalog — no per-tool configuration

**For A2A agents:**

- **JSON-RPC 2.0 over HTTP**: The primary inter-agent transport; compatible with AWS AgentCore Runtime and standard A2A clients
- **HTTP+JSON**: REST-style A2A transport for agents running on standard web stacks (ALB, API Gateway, Azure Front Door)
- **Transport Negotiation**: The gateway reads transport constraints from the Registry per agent and routes accordingly — no caller needs to know which transport a target agent supports

**Enforcement that flows from the Registry:**

- Routing, rate limiting, and access policy are derived from Registry metadata, not hardcoded gateway config
- Transport mismatches (e.g. a caller requesting gRPC against an HTTP/1.1-backed agent) are caught before the request is forwarded

---

## 2. Registry

The Registry is the compliance enforcement layer — not just a catalog. Both MCP servers and A2A agents are validated on registration and their metadata drives every gateway decision at runtime. This is the distinction between a registry that stores entries and one that actually enforces protocol compliance.

**For MCP servers:**

- **Tool Declaration Validation**: Validates that each registered MCP server's tool manifest is complete — required fields, input schemas, and capability declarations are all checked on registration, not at first use
- **Transport Compliance**: Verifies that declared MCP transports (SSE, Streamable HTTP) match the actual server capabilities before the server is made discoverable
- **Version Tracking**: Tracks the MCP specification version each server is registered against, so clients can safely negotiate capabilities at runtime

**For A2A agents:**

- **AgentCard Schema Validation**: On registration, validates the AgentCard against the A2A spec — required fields (name, description, url, capabilities), transport declarations, and authentication metadata must all be correct; a partially filled AgentCard is a misconfigured agent, not a registered one
- **Transport Declaration Accuracy**: Flags transport claims that are impossible given the agent's runtime (e.g. gRPC claimed on an HTTP/1.1 stack)
- **A2A Spec Version per Agent**: Tracks whether each agent is operating under A2A v0.3 or v1.0 — critical for safely routing v1.0 callers to legacy agents without silent payload mismatches
- **Custom Discovery Paths**: Stores non-standard AgentCard paths (e.g. Azure AI Foundry serves `agentCard/v0.3` rather than `/.well-known/agent-card.json`) and surfaces them to callers before discovery is attempted
- **Auth Prerequisites**: Records which auth patterns each agent requires — pre-configured IdP JWTAuthorizers (as with AWS AgentCore), RBAC role assignments (as with Azure Foundry), or standard OAuth 2.0 Client Credentials — and makes this available before the first invocation

**Platform-native runtime federation:**

Jarvis Registry federates agents across AWS AgentCore Runtime, Azure AI Foundry Agent Service, and self-hosted A2A runtimes in a single searchable catalog. Each platform makes different choices around transport, AgentCard discovery paths, and auth prerequisites that break standard A2A assumptions — the Registry stores those per-runtime differences and surfaces them to callers, so no custom client code is needed per target platform. Agents from all three origins are discoverable, routable, and governed by the same ACL policies through the same gateway.

**Gateway propagation:**

Registry data without gateway enforcement is documentation. Every constraint stored in the Registry — transport, auth, spec version, discovery path, platform runtime — is propagated to the gateway so that invocation policy is always derived from the same source of truth as registration.

---

## 3. Identity & Access Management

A governance enforcement layer that sits above your IdP — not a replacement for it. Jarvis Registry manages the auth complexity that neither the A2A spec nor platform-native registries handle automatically, and propagates enforced policy to the gateway.

**IdP integration (Keycloak, Amazon Cognito, Microsoft Entra ID):**

- **OAuth 2.0/OIDC**: Centralized token validation, refresh, and vending for both human users and AI agents against your existing IdP
- **JWT Token Management**: Validates tokens at the gateway before any tool or agent is invoked — every request is authenticated
- **Multi-Provider**: Supports Keycloak, Cognito, and Entra ID in the same deployment; useful for multi-cloud agent fleets

**Machine-to-machine (M2M) auth for agents:**

- **Client Credentials Flow**: Manages OAuth 2.0 Client Credentials for service-to-service agent calls, including token acquisition and rotation
- **Per-Agent Auth Mapping**: Stores which IdP configuration, pre-registered client, or RBAC role assignment is required for each agent — the setup that AWS AgentCore and Azure Foundry both require as manual out-of-band steps is tracked and enforced here
- **Auth Pattern Awareness**: Records whether an agent requires M2M client credentials, user-delegated access, or both — information that is absent from the AgentCard spec itself

**Access control:**

- **Role-Based Access Control (RBAC)**: Assign permissions by role, group, or service principal — for both human users and agents acting as callers
- **Fine-Grained ACL**: Enforce access policies down to the individual MCP tool or A2A agent capability — not just the server or agent boundary
- **Zero-Trust Posture**: Every request — from copilot or agent — is authenticated and authorized before reaching a backend tool or target agent; learn more about [AI governance frameworks](https://exploreagentic.ai/ai-governance/) that inform this design

---

## 4. Skill & Context-Based Discovery

Agents and copilots find the right MCP server or A2A agent at runtime — without hardcoded routing or static tool lists.

- **Semantic Search**: Vector-powered search matches natural language queries to MCP servers and A2A agents by skill, description, and declared capabilities — see [Enterprise RAG architecture](https://exploreagentic.ai/enterprise-rag/) for the retrieval patterns that underpin this feature
- **MCP Server Discovery**: Surfaces the right MCP server for a given tool need, respecting ACL so agents only see servers they are authorized to call
- **A2A Agent Discovery**: Resolves registered agents by capability, including custom discovery paths and auth prerequisites the caller needs before invoking — no caller needs to know Foundry serves a non-standard AgentCard path
- **Tag & Skill Filtering**: Multi-dimensional filtering by capability, domain, transport, or A2A spec version
- **Hybrid Search**: Combines semantic similarity and exact tag matching for precision and recall

---

## 5. A2A Agent Workflow Orchestration

Visibility and control over complex, multi-agent operations — across agents registered on different runtimes (AgentCore, Azure Foundry, or self-hosted).

- **Orchestrator–Worker Model**: Orchestrator agents delegate tasks to worker agents through the same secure gateway, with consistent auth and ACL applied at every hop
- **Cross-Runtime Coordination**: Route agent tasks across agents hosted on AWS AgentCore, Azure AI Foundry, or any A2A-compliant runtime — the Registry holds the transport and auth metadata needed to reach each one correctly
- **Centralized Workflow Visibility**: Track which agents are running, what tools they invoked, which transport was used, and what outcomes they produced
- **Consistent Policy Enforcement**: ACL and RBAC apply uniformly across orchestrated workflows — no policy gaps between the orchestrator and its workers

---

## 6. Observability with OpenTelemetry

Full visibility into every request, tool call, and agent interaction — from copilot to tool response, and from orchestrator to worker agent.

- **OpenTelemetry Integration**: Distributed tracing across the full request path for both MCP tool calls and A2A agent invocations
- **Prometheus Metrics**: Request rates, latency, error rates, and token usage exposed as standard metrics per server, per agent, and per transport
- **Third-Party Compatible**: Works with Grafana, Jaeger, Datadog, AWS X-Ray, Azure Monitor, and any OTEL-compatible backend
- **Audit Logging**: Immutable record of every tool invocation and agent call with user or agent identity, timestamp, transport used, and outcome

---

## Deployment Options

Jarvis Registry is cloud-native and runs anywhere.

- **AWS**: Deploy on EKS, ECS, or EC2 with Cognito as the identity provider; integrates with AWS Lambda and API Gateway for serverless MCP backends; compatible with AgentCore Runtime for A2A agents
- **Azure**: Deploy on AKS with Microsoft Entra ID for enterprise SSO; supports Azure Container Apps and Azure Monitor for observability; bridges Azure AI Foundry agents with standard A2A discovery
- **GCP**: Deploy on GKE with Workload Identity; integrates with Cloud Run and Google Cloud Monitoring
- **Docker Compose**: Full local stack running in under 5 minutes — ideal for development and evaluation
- **On-Premises**: Kubernetes manifests and Helm support for air-gapped or private cloud environments

---

## Use Cases

| Use Case | How Jarvis Registry Helps |
|---|---|
| **Enterprise AI Copilot Rollout** | Give every developer a single, governed MCP endpoint for internal tools — no individual server setup, transport config, or custom auth code |
| **Autonomous Agent Fleets** | Register A2A agents from any runtime (AgentCore, Foundry, self-hosted) with validated AgentCards and enforced transport and auth constraints |
| **Multi-Cloud Agent Coordination** | Bridge agents across AWS, Azure, and GCP; the Registry holds the per-runtime transport and auth metadata so orchestrators don't need custom code per target |
| **A2A Spec Version Migration** | Track which agents are on v0.3 and which are on v1.0; prevent silent payload mismatches when routing callers across spec versions |
| **Regulated Industry Compliance** | Enforce audit trails, fine-grained ACL, and zero-trust access for AI workloads in finance, healthcare, and government |
| **Developer Productivity** | Semantic discovery means developers and agents find the right MCP server or A2A agent without reading docs or maintaining tool lists |

---

## Competitive Advantages

- **Protocol-Native for Both MCP and A2A**: Built for both protocols from the ground up — not retrofitted onto an existing API gateway
- **Registry as Compliance Layer**: Validates transport, schema, and auth on registration — not a catalog that stores whatever you put in it
- **Cross-Runtime Agent Support**: Works across AgentCore, Azure Foundry, and self-hosted A2A agents in the same Registry, with the per-runtime metadata the gateway needs to route correctly
- **Zero Vendor Lock-in**: Open-source, open architecture — bring your own IdP, vector store, and observability backend
- **Scales with Your Agents**: From a single team to hundreds of autonomous agents across multiple clouds, the same control plane handles it all
- **Open Source**: Community-driven with commercial support available from [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/) — see the [Jarvis Registry product page](https://ascendingdc.com/jarvis-ai/jarvis-registry) and [Explore Agentic](https://exploreagentic.ai/) for the research and field guides behind the platform
