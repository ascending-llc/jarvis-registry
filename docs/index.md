<div align="center">
<img src="img/jarvis_vertical_logo_w_text_light_bkg.svg" alt="Jarvis Registry Logo" width="2000" height="480" style="width:100%;height:auto;">
</div>

**Connect any AI copilot, custom chatbot, or autonomous agent to your enterprise tools — through a single, secure gateway with protocol-compliant [MCP](https://ascendingdc.com/jarvis-ai/mcp-gateway/), [Agent](https://ascendingdc.com/jarvis-ai/agent-gateway/), and [Skill](https://ascendingdc.com/jarvis-ai/skill-gateway/) Gateway support, built-in identity governance, and full observability.**

---

## What is Jarvis Registry?

**Jarvis Registry** is an open-source, enterprise-grade orchestration platform for **[MCP (Model Context Protocol)](https://ascendingdc.com/jarvis-ai/mcp-gateway/), [Agent](https://ascendingdc.com/jarvis-ai/agent-gateway/), and [Skill](https://ascendingdc.com/jarvis-ai/skill-gateway/)** Gateways built by [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/). It solves one of the hardest problems in enterprise AI: giving AI copilots, custom chatbots, and autonomous agents **secure, governed access** to internal tools and data — without fragmented integrations or security blind spots.

Jarvis Registry acts as a **centralized control plane** for tool calling and orchestration that sits between your AI clients (copilots, custom chatbots, IDEs, agents) and your enterprise MCP servers and skills. Every request is authenticated against your Identity Provider (Keycloak, Amazon Cognito, or Microsoft Entra ID) and checked against fine-grained ACL policies — before a single tool is invoked.

Whether you are building custom chatbots, plugging your favorite AI copilot (Claude, OpenAI, or Jarvis Chat) into internal APIs, orchestrating fleets of autonomous A2A agents, or federating tools and skills across cloud environments, Jarvis Registry lets your engineers **focus on the user experience** while the platform handles **secure tool discovery, calling, and orchestration** with built-in governance, discoverability, and auditability.

---

## See It in Action

<div align="center">
<a class="yt-facade" href="https://www.youtube.com/watch?v=EUqWc_mAaXs" target="_blank" rel="noopener noreferrer" aria-label="Watch Jarvis Registry demo on YouTube (opens in new tab)">
  <img src="https://img.youtube.com/vi/EUqWc_mAaXs/hqdefault.jpg" alt="Jarvis Registry demo video thumbnail" width="560" height="315" loading="lazy">
  <span class="yt-facade__play"></span>
</a>
</div>

---

## What It Does

| Capability | Description |
|---|---|
| [**Gateway & Proxy**](FEATURES.md#1-gateway-proxy) | Single authenticated entry point for all AI clients and agents — supports [MCP](https://ascendingdc.com/jarvis-ai/mcp-gateway/) transports (SSE, Streamable HTTP), [Agent](https://ascendingdc.com/jarvis-ai/agent-gateway/) transports (JSON-RPC 2.0 over HTTP, HTTP+JSON), and [Skill](https://ascendingdc.com/jarvis-ai/skill-gateway/) Gateway routing, rate limiting, and policy enforcement flow directly from the Registry |
| [**Registry**](FEATURES.md#2-registry) | Compliance enforcement layer for [MCP servers](https://ascendingdc.com/jarvis-ai/mcp-gateway/) and [Agent Registry](https://ascendingdc.com/jarvis-ai/agent-registry/) — validates AgentCard schema, MCP tool declarations, and transport compliance (JSON-RPC 2.0, HTTP+JSON); tracks Agent spec version per agent and stores custom discovery paths and auth prerequisites; the single source of truth the gateway derives every invocation decision from |
| [**AI Copilot & Custom Chatbot Integration**](FEATURES.md#1-gateway-proxy) | Connect any AI client to enterprise tools — standard copilots (Claude Desktop, Cursor, GitHub Copilot, VS Code), LLM-powered custom chatbots, and web apps. Engineers focus on UX/chat experience; Jarvis Registry handles all tool calling, skill selection, and orchestration. |
| [**Skill & Context-Based Discovery**](FEATURES.md#4-skill-context-based-discovery) | Semantic search over MCP servers, A2A agents, and skills by description and tags so AI clients find and invoke the right tool at runtime without manual configuration |
| [**A2A Agent Workflow**](FEATURES.md#5-a2a-agent-workflow-orchestration) | Register and manage autonomous agents; orchestrator agents coordinate worker agents through the same secure gateway |
| [**Identity & Access Management**](FEATURES.md#3-identity-access-management) | Governance enforcement layer that sits above your IdP (Keycloak, Cognito, Entra ID, Okta) — manages per-agent OAuth 2.0/OIDC auth prerequisites, Client Credentials (M2M) flows, and RBAC mappings, then propagates the enforced policy to the gateway |
| [**Fine-Grained Access Control**](FEATURES.md#3-identity-access-management) | ACL engine enforces scope-based, role-based permissions down to the individual tool level |
| [**Audit & Observability**](FEATURES.md#6-observability-with-opentelemetry) | Full request logging, OpenTelemetry tracing, and Prometheus metrics |

---

## Architecture Overview

<div align="center">
<img src="./img/overall-architecture.jpg" alt="Jarvis Registry Architecture Overview — AI copilots, custom chatbots, and A2A agents connecting through Jarvis Registry's discovery, orchestration, and security layers to enterprise MCP servers, with IdP and observability integrations" style="width:100%;height:auto;">
</div>

## Built by ASCENDING Inc

Jarvis Registry is developed and maintained by [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/). For more information about Jarvis AI and our broader AI platform:

- **Website**: [ascendingdc.com/jarvis-ai](https://ascendingdc.com/jarvis-ai/)
- **Jarvis Registry Product Page**: [ascendingdc.com/jarvis-ai/jarvis-registry](https://ascendingdc.com/jarvis-ai/jarvis-registry)
- **Governed AI Layer**: [ascendingdc.com/jarvis-ai/governed-ai](https://ascendingdc.com/jarvis-ai/governed-ai/)
- **Explore Agentic**: [exploreagentic.ai](https://exploreagentic.ai/) — the field guide to enterprise agentic AI, published by ASCENDING
- **YouTube**: [ASCENDING Inc Channel](https://www.youtube.com/channel/UCi5_sn38igXkk-4hsR0JGtw)
- **LinkedIn**: [ASCENDING Inc](https://www.linkedin.com/company/ascendingllc/mycompany/)
- **GitHub**: [ascending-llc/jarvis-registry](https://github.com/ascending-llc/jarvis-registry)
