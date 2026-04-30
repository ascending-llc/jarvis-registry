# MCP Gateway

*Also known as: MCP proxy, Agent gateway, MCP federation layer · 7 min · Updated April 17, 2026 · Author Mehrdad Faqiri · Reviewed by Gloria Qian Zhang*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/glossary/mcp-gateway/](https://www.exploreagentic.ai/glossary/mcp-gateway/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Definition

An <strong>MCP Gateway</strong> is the proxy that sits between MCP clients and a cluster of MCP servers. It authenticates the caller. It evaluates per-tool policy. It logs the invocation. It holds the enterprise credential so the user never pastes a token. The MCP spec does not define the role; it grew up in production during 2025. The category went public in Q4 2025 when AWS Bedrock AgentCore Gateway went GA on October 13, 2025 <a href="#cite-1" class="cite-ref">[1]</a>. Nobody asked for the pattern. Everyone running more than three servers ended up building it anyway.

## Why the category exists

Three servers is the threshold. Past that, the same four questions arrive on the CISO's desk in sequence. Who is allowed to call which tool, under what conditions? Where is every tool call logged? How do we rate-limit? And the one that actually wakes people up: how do we stop handing user-level credentials to a model at inference time?

The gateway pattern answers all four in one place. Inbound auth (OAuth 2.1 with RFC 8707 resource indicators, per the March 2026 MCP spec). Policy evaluated against request context. Outbound call forwarded with the gateway's own credentials. Full exchange logged. Solo.io's 2025 writeup puts it bluntly: direct-to-server OAuth is "a non-starter for enterprise" at any meaningful scale <a href="#cite-2" class="cite-ref">[2]</a>. Which is roughly what we've watched every team discover on their own, usually around server number five.

## The vendor landscape in April 2026

The serious options divide into three tiers: hyperscaler-native, network-edge, and independent.

*Named MCP gateway offerings, April 2026*

| Vendor | Tier | What ships |
| --- | --- | --- |
| AWS Bedrock AgentCore Gateway | Hyperscaler | GA October 13, 2025. Zero-code MCP tool creation from Lambda and OpenAPI; ingress + egress auth; Semantic Tool Selection; 1-click integrations with Salesforce, Slack, Jira, Asana, Zendesk. |
| Cloudflare MCP Server Portals | Network edge | Runs MCP traffic across Cloudflare's global points-of-presence. Collapses many servers into two portal tools, and Cloudflare reports 99.9% token reduction via its Code Mode work [3]. |
| Kong Enterprise MCP Gateway | Independent (API-gateway extension) | API gateway that added MCP support; v3.14 added A2A (Agent-to-Agent) support in April 2026. Natural fit for teams already running Kong [4]. |
| Composio | Independent (integration-led) | 500+ pre-built SaaS integrations exposed as MCP. Strong on breadth; weaker on RBAC and compliance audit trails than governance-first options [5]. |
| Bifrost | Independent (performance-led) | Go-based; published 11-microsecond overhead at 5,000 req/s. Dual role as LLM gateway and MCP gateway in a single binary [6]. |
| Zuplo | Independent (TypeScript-first) | Edge-deployed across 300+ PoPs; combines API gateway, AI gateway, and MCP support. Programmable in TypeScript [7]. |
| Red Hat MCP Gateway, Lunar.dev, Portkey, MintMCP | Independent | Smaller but active. Portkey and Lunar.dev publish the most detailed policy-as-code documentation of the independents [8]. |

## What a serious gateway has

- Per-tool authorization policies, versioned and reviewable like any other policy artefact. AgentCore uses Cedar; Kong uses its declarative plugin config; Lunar and Portkey publish OPA-compatible rules.
- Centralized credential brokering. The gateway holds the Snowflake PAT, the Jira token, the SharePoint secret. The user never pastes them; the model never receives them in clear text.
- Per-tool observability: latency, error rate, call volume, traceable to a conversation ID and, ideally, a business outcome. OpenTelemetry semantic conventions for GenAI are the emerging baseline.
- Egress controls for regulated workloads: region-locked traffic, PII redaction, free-form text filtering. This is the chokepoint that proves PII never left a region.
- Shadow-MCP detection. Cloudflare Gateway runs multi-layer scans for unauthorized MCP server usage from 2026, worth asking about in any procurement cycle.
- A2A (Agent-to-Agent) support. Kong shipped this in v3.14 (April 2026); any gateway without it will be a quarter behind for the rest of the year.

## How to evaluate one in 2026

Four questions, asked in order. Show me the policy-as-code API. The DSL or schema that expresses "this caller, calling this tool, under these conditions, against that server." Show me the published latency budget (Bifrost's 11µs overhead at 5k req/s is the current public number to beat). Show me what happens when the tool is itself another MCP server, the composition case where several independents still quietly fall over. Show me customer references running the gateway in front of more than twenty servers. Scale pain doesn't show up until that point.

The category is often mis-sold. Several products marketed as "MCP gateways" in Q4 2025 were LLM proxies with MCP logging grafted on. MintMCP and Integrate.io flagged the mis-labelling in their 2026 guides <a href="#cite-5" class="cite-ref">[5]</a><a href="#cite-9" class="cite-ref">[9]</a>. The sorting test is crude and effective. If the product cannot describe per-tool policy enforcement in writing, it is a proxy. Not a gateway.

> **Explore Agentic editorial note**: We maintain an informal tracker of roughly fifteen MCP gateway vendors. The evaluation framework we use on client work is: policy-as-code, credential brokering, per-tool observability, egress enforcement, and documented behaviour when a tool is itself a federated MCP server. A gateway that lands four of five is deployable today.

## See Also

- [Model Context Protocol](model-context-protocol.md)
- [Agent observability](agent-observability.md)
- [Shadow AI](shadow-ai.md)
- [MCP pillar](../pillars/mcp.md)

## FAQ

**Q: What is an MCP gateway?**

The grown-up proxy between your MCP clients and your MCP servers. Auth-aware. Observability-aware. Policy-aware. Every call gets authenticated, policy gets evaluated per tool, the invocation gets logged, and the enterprise credential stays on the gateway so nobody pastes a Snowflake PAT into a prompt. Egress control lives in the same place. The MCP spec does not define the role, a fact that surprises people. It grew up in production during 2025 after teams ran headfirst into the reality that direct-to-server OAuth does not scale past a handful of servers.

**Q: Do I need an MCP gateway?**

Past three servers in the same environment, almost always yes. Centralized per-tool auth. Full tool-call logs. Credentials held server-side so users never paste tokens into prompts. Egress rules for regulated data. Below three servers, native OAuth 2.1 will carry you. For a while. By server number five you will have rebuilt most of a gateway yourself in anger, which is the tax. We've watched teams pay it on six separate engagements and stopped finding it funny.

**Q: Which MCP gateway should I use in 2026?**

Shortest honest answer: whichever one procurement can get through fastest. In practice that means the gateway from the cloud you already buy from. Two cloud-native defaults. AWS Bedrock AgentCore Gateway (GA October 13, 2025) and Cloudflare MCP Server Portals. The independent shortlist. Kong. Composio. Bifrost. Zuplo. Lunar.dev. Portkey. Then run every candidate through five questions: policy-as-code API, credential brokering, per-tool observability, egress enforcement, and what happens when the target is itself a federated MCP server. Four of five is shippable. Five of five is rare.

**Q: Is an MCP gateway the same as an API gateway?**

No. The difference matters more than the marketing admits. An API gateway routes and authorizes HTTP requests between services. Full stop. An MCP gateway speaks the protocol itself: tool discovery, tool invocation, resource access, prompt sampling. Policy runs at the per-tool level, not the endpoint level. Two lineages converged on the category. Kong and Zuplo came up as API gateways and bolted on MCP support later. AgentCore, Bifrost, and Lunar were MCP-native from day one. The pick is boring: choose the lineage that matches your operational muscle memory.

**Q: How does an MCP gateway handle credentials?**

Two separate flows. That is the whole point. The gateway holds the enterprise credentials: Snowflake PATs, Jira API tokens, SharePoint secrets. It attaches them to the outbound call. The user never pastes them. The model never sees them. Inbound, the gateway authenticates the caller with OAuth 2.1 and PKCE, plus RFC 8707 resource indicators from March 15, 2026. Splitting inbound user auth from outbound system auth is the single biggest security win over direct-to-server MCP. Nothing else is close.

**Q: What is Agent-to-Agent (A2A) support?**

An emerging protocol for agents to discover and invoke other agents. Complementary to MCP, not a replacement. Kong shipped A2A in v3.14 (April 2026). The rest of the field is catching up through the rest of the year. The practical buyer question is short: does your gateway treat a federated agent as just another MCP-style target? Yes, and you are positioned for the next protocol wave. No, and you are budgeting a rework for late 2027. Whether you know it yet or not.

## Citations

1. **AWS** — Introducing Amazon Bedrock AgentCore Gateway — https://aws.amazon.com/blogs/machine-learning/introducing-amazon-bedrock-agentcore-gateway-transforming-enterprise-ai-agent-tool-development/ [accessed 2026-04-17] *(AWS launch post. Zero-code MCP tool creation, ingress + egress auth, Semantic Tool Selection, MCP target type.)* { #cite-1 }
2. **Solo.io** — MCP Authorization is a Non-Starter for Enterprise — https://www.solo.io/blog/mcp-authorization-is-a-non-starter-for-enterprise [accessed 2026-04-17] *(Analysis of why direct-to-server OAuth does not scale in regulated environments.)* { #cite-2 }
3. **Cloudflare** — Scaling MCP adoption: our reference architecture for enterprise MCP deployments — https://blog.cloudflare.com/enterprise-mcp/ [accessed 2026-04-17] *(MCP Server Portals; Code Mode collapses many servers into two portal tools; shadow-MCP detection.)* { #cite-3 }
4. **Kong** — Introducing Kong's Enterprise MCP Gateway for Production-Ready AI — https://konghq.com/blog/product-releases/enterprise-mcp-gateway [accessed 2026-04-17] *(Kong Enterprise MCP Gateway launch; A2A support added in v3.14 (April 2026).)* { #cite-4 }
5. **MintMCP** — 7 top MCP gateways for enterprise AI infrastructure (2026) — https://www.mintmcp.com/blog/enterprise-ai-infrastructure-mcp [accessed 2026-04-17] *(Vendor review including Composio, Bifrost, Kong, AgentCore, Zuplo; notes the "proxy vs gateway" mis-labelling.)* { #cite-5 }
6. **Maxim AI** — Best MCP Gateway in 2026: How Bifrost Cuts Token Usage by 50% — https://www.getmaxim.ai/articles/best-mcp-gateway-in-2026-how-bifrost-cuts-token-usage-by-50/ [accessed 2026-04-17] *(Bifrost benchmarks: 11µs overhead at 5,000 requests per second; dual LLM+MCP gateway.)* { #cite-6 }
7. **Zuplo** — How to Choose the Best AI Gateway (2026 Buyer's Guide) — https://zuplo.com/learning-center/best-ai-gateway-buyers-guide [accessed 2026-04-17] *(Zuplo product + buyer's guide; edge deployment across 300+ points of presence.)* { #cite-7 }
8. **Red Hat Developer** — Advanced authentication and authorization for MCP Gateway — https://developers.redhat.com/articles/2025/12/12/advanced-authentication-authorization-mcp-gateway [accessed 2026-04-17] *(OPA-style policy enforcement and credential-brokering patterns applied to MCP gateways.)* { #cite-8 }
9. **Integrate.io** — Best MCP Gateways and AI Agent Security Tools (2026) — https://www.integrate.io/blog/best-mcp-gateways-and-ai-agent-security-tools/ [accessed 2026-04-17] *(2026 roundup; useful cross-check of vendor feature claims.)* { #cite-9 }
10. **AWS (docs)** — Amazon Bedrock AgentCore Gateway: developer guide — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html [accessed 2026-04-17] *(Canonical documentation for AgentCore Gateway, target types (Lambda, OpenAPI, Smithy, MCP servers).)* { #cite-10 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/glossary/mcp-gateway/](https://www.exploreagentic.ai/glossary/mcp-gateway/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
