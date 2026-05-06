<div align="center">
<img src="docs/img/jarvis_vertical_logo_w_text_light_bkg.svg" alt="Jarvis Registry Logo" width="100%">

**Connect any AI copilot or autonomous agent to your enterprise tools — through a single, secure MCP gateway with built-in identity, access control, and full observability.**

[![License](https://img.shields.io/github/license/ascending-llc/jarvis-registry?style=flat)](https://github.com/ascending-llc/jarvis-registry/blob/main/LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/ascending-llc/jarvis-registry?style=flat&logo=github)](https://github.com/ascending-llc/jarvis-registry/releases)

[🚀 Quick Start](#quick-start) | [📖 Documentation](https://jarvisregistry.com/) | [🎬 Demo](https://youtu.be/EUqWc_mAaXs) | [🌐 Website](https://ascendingdc.com/jarvis-ai/jarvis-registry/)

</div>

---

## What is Jarvis Registry?

**Jarvis Registry** is an open-source, enterprise-grade **MCP (Model Context Protocol) and A2A Agent Gateway and Workflow Orchestration platform** built by [ASCENDING Inc](https://ascendingdc.com/jarvis-ai/).

It solves one of the hardest problems in enterprise AI: giving AI copilots and autonomous agents **secure, governed access** to internal tools and data — without fragmented integrations or security blind spots.

| Capability | Description |
|---|---|
| **MCP Gateway & Reverse Proxy** | Single authenticated entry point for all AI clients and agents using MCP over SSE or Streamable HTTP |
| **AI Copilot Integration** | Connect Cursor, Claude Desktop, GitHub Copilot, VS Code, and any MCP-compatible copilot to enterprise tools |
| **A2A Agent Orchestration** | Register and manage autonomous agents; orchestrator agents coordinate worker agents through the same secure gateway |
| **Identity & Access Management** | OAuth 2.0/OIDC with Keycloak, Amazon Cognito, and Microsoft Entra ID — no custom auth code needed |
| **Fine-Grained Access Control** | ACL engine enforces scope-based, role-based permissions down to the individual tool level |
| **Skill & Context-Based Discovery** | Semantic search over skills, descriptions, and tags so agents and copilots find the right MCP server or A2A agent at runtime |
| **Audit & Observability** | Full request logging, OpenTelemetry tracing, and Prometheus metrics |

---

<div align="center">
  <a href="https://youtu.be/EUqWc_mAaXs">
    <img src="https://img.youtube.com/vi/EUqWc_mAaXs/maxresdefault.jpg" alt="Watch Jarvis Registry Demo on YouTube" width="640" />
  </a>
  <p><em>▶ Watch the demo — click to open on YouTube</em></p>
</div>

---

## Quick Start

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

See the full [Get Started guide](docs/quick-start.md) for detailed instructions.

## Documentation

Full documentation is available at **[jarvisregistry.com](https://jarvisregistry.com)**:

| Section | Description |
|---|---|
| [Why Use Registry](https://jarvisregistry.com/FEATURES/) | Benefits, use cases, and competitive advantages |
| [Get Started](https://jarvisregistry.com/quick-start/) | Installation, configuration, and first run |
| [Core Features](https://jarvisregistry.com/features/auth-server/) | IDP integration, MCP/A2A registry, federation |
| [Architecture & Design](https://jarvisregistry.com/design/security-design/) | Security, RBAC, ACL, agent workflow, federation |
| [Project](https://jarvisregistry.com/CONTRIBUTING/) | Contributing, license, and code of conduct |

---

## Deployment

Cloud-native deployment guides are available for:

- **AWS** — EKS
- **Azure** — AKS
- **GCP** — GKE
- **Docker Compose** — Full local stack in under 5 minutes

---

## Contributing

We welcome contributions! Please read our [Contributing Guide](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md) before submitting pull requests.

For security vulnerabilities, contact [support@ascendingdc.com](mailto:support@ascendingdc.com) — do **not** open a public issue.

---

## Acknowledgments

Jarvis Registry builds upon the excellent foundational work of the [agentic-community/mcp-gateway-registry](https://github.com/agentic-community/mcp-gateway-registry) project. We are grateful to those contributors for establishing the core MCP gateway patterns that made this enterprise evolution possible.

---

## License

Licensed under the Apache 2.0 License — see the [LICENSE](LICENSE) file for details.

---

## Star History

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=ascending-llc/jarvis-registry&type=Date)](https://star-history.com/#ascending-llc/jarvis-registry&Date)

</div>

---

<div align="center">

**⭐ Star this repository if it helps your organization!**

[Get Started](docs/quick-start.md) | [Documentation](https://jarvisregistry.com/) | [Website](https://ascendingdc.com/jarvis-ai/) | [YouTube](https://www.youtube.com/channel/UCi5_sn38igXkk-4hsR0JGtw) | [LinkedIn](https://www.linkedin.com/company/ascendingllc/mycompany/)

</div>
