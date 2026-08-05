# Why Use Jarvis Registry?

Jarvis Registry is the enterprise control plane for AI — connecting copilots, autonomous agents, and enterprise tools through a single, secure, and observable gateway. It handles both [MCP (Model Context Protocol)](https://exploreagentic.ai/mcp/) servers and [A2A agents](https://exploreagentic.ai/agentic-ai/) under a unified control plane, so you don't need separate infrastructure for tool access and agent-to-agent communication.

---

## 1. Registry

The Registry is the compliance enforcement layer — not just a catalog. Both MCP servers and A2A agents are validated on registration and their metadata drives every gateway decision at runtime. This is the distinction between a registry that stores entries and one that actually enforces protocol compliance.

**For MCP servers:**

- **Tool Declaration Validation**: Validates that each registered MCP server's tool manifest is complete — required fields, input schemas, and capability declarations are all checked on registration, not at first use
- **Transport Compliance**: Verifies that declared MCP transports (SSE, Streamable HTTP) match the actual server capabilities before the server is made discoverable; misconfigured transport declarations are rejected before the server is made discoverable
- **OAuth Egress**: Manages outbound OAuth credentials for MCP servers that call downstream protected APIs — token acquisition, rotation, and per-server credential mapping are handled centrally so individual servers don't carry credentials
- **Security Scanning**: Registered MCP servers are scanned for common security issues — exposed secrets, overly broad tool scopes, and missing input validation — surfaced as warnings or blocking violations depending on policy

**For A2A agents:**

- **AgentCard Schema Validation**: On registration, validates the AgentCard against the A2A spec — required fields (name, description, url, capabilities), transport declarations, and authentication metadata must all be correct; a partially filled AgentCard is a misconfigured agent, not a registered one
- **A2A Spec Compliance**: Enforces that each agent's declared capabilities, skill definitions, and input/output modes conform to the registered spec version — not just that the AgentCard parses, but that it is internally consistent. Tracks whether each agent is operating under A2A v0.3 or v1.0 — critical for safely routing v1.0 callers to legacy agents without silent payload mismatches
- **Transport Declaration Accuracy**: Flags transport claims that are impossible given the agent's runtime (e.g. gRPC claimed on an HTTP/1.1 stack); prevents callers from reaching agents over transports they don't actually support
- **Custom Discovery Paths**: Stores non-standard AgentCard paths (e.g. Azure AI Foundry serves `agentCard/v0.3` rather than `/.well-known/agent-card.json`) and surfaces them to callers before discovery is attempted

**Platform-native runtime federation:**

Jarvis Registry federates agents across AWS AgentCore Runtime, Azure AI Foundry Agent Service, and self-hosted A2A runtimes in a single searchable catalog. Each platform makes different choices around transport, AgentCard discovery paths, and auth prerequisites that break standard A2A assumptions — the Registry stores those per-runtime differences and surfaces them to callers, so no custom client code is needed per target platform. Agents from all three origins are discoverable, routable, and governed by the same ACL policies through the same gateway.

- **Azure AI Foundry Federation**: Import and govern agents hosted on Azure AI Foundry — handling non-standard AgentCard discovery paths, Entra ID RBAC prerequisites, and HTTP+JSON transport constraints automatically. Any MCP client or A2A orchestrator can invoke Foundry agents through Jarvis Registry without platform-specific client code. [Watch the demo →](https://ascendingdc.com/jarvis-ai/videos/azure-ai-foundry-federation-with-jarvis-registry-access-governed-agents-from-any-interface/?autoplay=1)

- **AWS AgentCore Federation**: Import and govern agents deployed on AWS AgentCore Runtime — resolving pre-configured JWTAuthorizer requirements, Bedrock-native transport constraints, and per-agent credential mapping. Orchestrators reach AgentCore agents through the same governed gateway as every other registered agent. [Watch the demo →](https://ascendingdc.com/jarvis-ai/videos/aws-agentcore-federation-with-jarvis-registry-access-governed-agents-from-any-interface/?autoplay=1)

---

## 2. MCP Gateway

Single authenticated entry point for AI copilots and MCP-compatible clients — handling discovery, access control, elicitation, and credential management centrally so individual MCP servers carry none of that complexity.

- **Unified Catalog**: Clients discover tools, prompts, and resources across all registered MCP servers through a single endpoint — no per-server configuration or manual tool list maintenance
- **ACL-Filtered Discovery**: Discovery results are scoped per caller identity — agents and copilots only see tools, prompts, and resources they are authorized to invoke; unauthorized entries are invisible, not just blocked
- **Transport Negotiation**: Supports SSE (Server-Sent Events) for streaming responses to copilots and IDEs (Cursor, Claude Desktop, GitHub Copilot, VS Code) and Streamable HTTP for bidirectional long-running tool calls — the gateway selects the right transport per client based on capability negotiation
- **Registry-Driven Enforcement**: Routing, rate limiting, and access policy are derived from Registry metadata, not hardcoded gateway config; policy changes in the Registry take effect immediately without gateway redeployment
- **Interactive Tool Flows**: The gateway implements the MCP elicitation spec natively — tools can request additional input from the user mid-invocation without the client needing custom elicitation handling
- **Token Encryption at Rest**: OAuth tokens and credentials stored for MCP server egress are encrypted at rest — no plaintext credentials in the database or config
- **Token Caching**: Validated inbound tokens are cached for their remaining TTL to avoid redundant IdP round-trips on every tool call; cache entries are invalidated on token revocation
- **Credential Isolation**: Per-server egress credentials are scoped and isolated — a compromised MCP server cannot access credentials belonging to another server

---

## 3. Agent Gateway

Single authenticated entry point for A2A agents — handling skill discovery, transport negotiation, and security scanning centrally so callers need no platform-specific client code per target runtime.

- **Agent & Skill Discovery**: Resolves registered A2A agents and their skills by capability, tags, and spec version — callers query the gateway to find the right agent for a task without knowing which runtime hosts it or which transport it speaks
- **Transport Negotiation**: Supports JSON-RPC 2.0 over HTTP (primary inter-agent transport, compatible with AWS AgentCore and standard A2A clients) and HTTP+JSON for agents on standard web stacks (ALB, API Gateway, Azure Front Door) — the gateway reads per-agent transport constraints from the Registry and routes accordingly; transport mismatches are caught before the request is forwarded
- **Security Scanning**: Registered agents are scanned for security issues on registration — misconfigured CORS policies, missing auth declarations, and overly permissive skill scopes are flagged before the agent is made discoverable
- **Registry-Driven Enforcement**: Routing, rate limiting, and ACL policy are derived from Registry metadata, not hardcoded gateway config; policy changes take effect immediately without gateway redeployment

---

## 4. Skill Gateway

The Skill Gateway is the organization-wide control plane for AI skills — managing how skills are defined, organized, discovered, and kept in sync with your source of truth. Callers never need to know where a skill lives, which transport it speaks, or which team owns it.

- **Organization Skill Management**: Define and organize skills across teams, domains, and environments from a central control plane — skills are versioned, tagged by capability and owner, and grouped into namespaces so large organizations can manage hundreds of skills without collision or sprawl
- **Skill Discovery**: Semantic vector search matches natural language queries to skills by description, tags, and declared capabilities — ACL-filtered so callers only see skills they are authorized to invoke; see [Enterprise RAG architecture](https://exploreagentic.ai/enterprise-rag/) for the retrieval patterns that underpin this feature
- **Skill Lifecycle Management**: Skills have explicit lifecycle states (draft, active, deprecated) — deprecated skills surface warnings to callers before they are removed, and active skills can be promoted or rolled back without gateway redeployment
- **Git Provider Sync**: Skills are synced bidirectionally with your Git provider (GitHub, GitLab, Bitbucket) — skill definitions live in version-controlled repositories and changes are reflected in the gateway automatically; pull requests, branch-based staging, and audit history flow from your existing Git workflow into the Skill Gateway without manual import steps

---

## 5. A2A Agent Workflow Orchestration

Visibility and control over complex, multi-agent operations — across agents registered on different runtimes (AgentCore, Azure Foundry, or self-hosted).

- **Orchestrator–Worker Model**: Orchestrator agents delegate tasks to worker agents through the same secure gateway, with consistent auth and ACL applied at every hop
- **Cross-Runtime Coordination**: Route agent tasks across agents hosted on AWS AgentCore, Azure AI Foundry, or any A2A-compliant runtime — the Registry holds the transport and auth metadata needed to reach each one correctly
- **Centralized Workflow Visibility**: Track which agents are running, what tools they invoked, which transport was used, and what outcomes they produced
- **Consistent Policy Enforcement**: ACL and RBAC apply uniformly across orchestrated workflows — no policy gaps between the orchestrator and its workers

---

## 6. Identity & Access Management

A governance enforcement layer that sits above your IdP — not a replacement for it. Jarvis Registry manages the auth complexity that neither the A2A spec nor platform-native registries handle automatically, and propagates enforced policy to the gateway.

**IdP integration (Keycloak, Amazon Cognito, Microsoft Entra ID):**

- **OAuth 2.0/OIDC**: Centralized token validation, refresh, and vending for both human users and AI agents against your existing IdP
- **JWT Token Management**: Validates tokens at the gateway before any tool or agent is invoked — every request is authenticated
- **Multi-Provider**: Supports Keycloak, Cognito, and Entra ID in the same deployment; useful for multi-cloud agent fleets

**Access control:**

- **Role-Based Access Control (RBAC)**: Assign permissions by role, group, or service principal — for both human users and agents acting as callers
- **Fine-Grained ACL**: Enforce access policies down to the individual MCP tool or A2A agent capability — not just the server or agent boundary
- **Zero-Trust Posture**: Every request — from copilot or agent — is authenticated and authorized before reaching a backend tool or target agent; learn more about [AI governance frameworks](https://exploreagentic.ai/ai-governance/) that inform this design

---

## 7. Observability with OpenTelemetry

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
| **Custom Chatbot Backends** | Custom-built chatbots connect to a single gateway endpoint to access all enterprise tools, prompts, and resources — no per-tool integration code, credentials, or discovery logic in the chatbot itself; ACL ensures each chatbot only sees what it is authorized to use |
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
