<div align="center">
<img src="img/jarvis_vertical_logo_w_text_light_bkg.svg" alt="Jarvis Registry Logo" width="100%">

**Connect any AI copilot or autonomous agent to your enterprise tools — through a single, secure MCP gateway with built-in identity, access control, and full observability.**

</div>

---

## What is Jarvis Registry?

**Jarvis Registry** is an open-source, enterprise-grade **MCP (Model Context Protocol) Gateway and Registry** built by [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/). It solves one of the hardest problems in enterprise AI: giving AI copilots and autonomous agents **secure, governed access** to internal tools and data — without fragmented integrations or security blind spots.

Jarvis Registry acts as a **centralized control plane** that sits between your AI clients (copilots, IDEs, agents) and your enterprise MCP servers. Every request flows through NGINX, is authenticated against your Identity Provider (Keycloak, Amazon Cognito, or Microsoft Entra ID), and checked against fine-grained ACL policies — before a single tool is invoked.

Whether you are plugging GitHub Copilot into internal APIs, orchestrating fleets of autonomous A2A agents, or federating tools across cloud environments, Jarvis Registry gives you the **security, discoverability, and auditability** that enterprise deployments demand.

---

## See It in Action

<div align="center">
<iframe width="800" height="450" src="https://www.youtube.com/embed/EUqWc_mAaXs" title="Jarvis Registry Demo" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
</div>

---

## What It Does

| Capability | Description |
|---|---|
| **MCP Gateway & Reverse Proxy** | Single authenticated entry point (NGINX) for all AI clients and agents using MCP over SSE or Streamable HTTP |
| **AI Copilot Integration** | Connect Cursor, Claude Desktop, GitHub Copilot, VS Code, and any MCP-compatible copilot to enterprise tools |
| **A2A Agent Orchestration** | Register and manage autonomous agents; orchestrator agents coordinate worker agents through the same secure gateway |
| **Identity & Access Management** | OAuth 2.0/OIDC with Keycloak, Amazon Cognito, and Microsoft Entra ID — no custom auth code needed |
| **Fine-Grained Access Control** | ACL engine enforces scope-based, role-based permissions down to the individual tool level |
| **Dynamic Tool Discovery** | Semantic and tag-based search so agents find the right MCP tool at runtime |
| **Service Registry** | Centralized catalog of all registered MCP servers, tools, and agent capabilities |
| **Audit & Observability** | Full request logging, OpenTelemetry tracing, and Prometheus metrics |

---

## Architecture Overview

```mermaid
flowchart TB
    subgraph AIClients["AI Clients"]
        subgraph Copilots["AI Copilots & IDEs"]
            Cursor["Cursor / Claude Desktop"]
            GHCop["GitHub Copilot / VS Code"]
        end
        subgraph A2AOrch["A2A Agent Orchestration"]
            Orch["Orchestrator Agent"]
            Worker1["Worker Agent 1"]
            Worker2["Worker Agent 2"]
        end
    end

    subgraph JarvisGW["Jarvis Registry — MCP Gateway"]
        NGINX["NGINX Reverse Proxy\n(Single Entry Point)"]

        subgraph SecurityLayer["Security Layer"]
            AuthSrv["Auth Server\n(OAuth 2.0 / JWT)"]
            ACL["ACL Engine\n(Fine-Grained Access Control)"]
        end

        subgraph RegistryServices["Registry Services"]
            RegUI["Registry Web UI\n(Service Catalog)"]
            RegMCP["Registry MCP Server\n(Tool Discovery)"]
        end

        subgraph MCPServers["MCP Servers"]
            MCP1["MCP Server 1"]
            MCP2["MCP Server 2"]
            MCP3["MCP Server 3"]
        end
    end

    IdP["Identity Provider\n(Keycloak / Cognito / Entra ID)"]

    subgraph Enterprise["Enterprise Backends"]
        DB1[(Database)]
        IntAPI["Internal API"]
        CloudSvc["Cloud Services\n(AWS / Azure)"]
    end

    Cursor -->|"MCP (SSE / Streamable HTTP)"| NGINX
    GHCop -->|"MCP (SSE / Streamable HTTP)"| NGINX
    Orch -->|"A2A Protocol (MCP)"| NGINX
    Orch -- "orchestrate" --> Worker1
    Orch -- "orchestrate" --> Worker2
    Worker1 -->|"MCP (SSE / Streamable HTTP)"| NGINX
    Worker2 -->|"MCP (SSE / Streamable HTTP)"| NGINX

    NGINX -->|"1 · Validate Bearer Token"| AuthSrv
    AuthSrv <-->|"Verify Identity"| IdP
    AuthSrv -->|"2 · Enforce Permissions"| ACL
    NGINX -->|"3 · Tool Discovery"| RegMCP
    RegUI -.->|"Browse Catalog"| NGINX
    NGINX -->|"4 · Route Authenticated Request"| MCP1
    NGINX -->|"4 · Route Authenticated Request"| MCP2
    NGINX -->|"4 · Route Authenticated Request"| MCP3

    MCP1 --> DB1
    MCP2 --> IntAPI
    MCP3 --> CloudSvc

    classDef copilot fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef a2a fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    classDef nginx fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef security fill:#fff8e1,stroke:#e65100,stroke-width:2px
    classDef registry fill:#e8eaf6,stroke:#283593,stroke-width:2px
    classDef mcp fill:#fce4ec,stroke:#b71c1c,stroke-width:2px
    classDef idp fill:#ffebee,stroke:#c62828,stroke-width:2px
    classDef backend fill:#f1f8e9,stroke:#33691e,stroke-width:2px

    class Cursor,GHCop copilot
    class Orch,Worker1,Worker2 a2a
    class NGINX nginx
    class AuthSrv,ACL security
    class RegUI,RegMCP registry
    class MCP1,MCP2,MCP3 mcp
    class IdP idp
    class DB1,IntAPI,CloudSvc backend
```

---

## Quick Start

Get Jarvis Registry running locally in minutes:

```bash
# Clone the repository
git clone https://github.com/ascending-llc/jarvis-registry.git
cd jarvis-registry

# Copy and configure environment
cp .env.example .env
# Edit .env with your identity provider credentials

# Start all services
docker-compose up -d

# Open the registry UI
open http://localhost:7860
```

See the full [Get Started](quick-start.md) guide for detailed instructions.

---

## Built by ASCENDING Inc

Jarvis Registry is developed and maintained by [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/). For more information about Jarvis AI and our broader AI platform:

- **Website**: [ascendingdc.com/jarvis-ai](https://ascendingdc.com/jarvis-ai/)
- **YouTube**: [ASCENDING Inc Channel](https://www.youtube.com/channel/UCi5_sn38igXkk-4hsR0JGtw)
- **LinkedIn**: [ASCENDING Inc](https://www.linkedin.com/company/ascendingllc/mycompany/)
- **GitHub**: [ascending-llc/jarvis-registry](https://github.com/ascending-llc/jarvis-registry)
