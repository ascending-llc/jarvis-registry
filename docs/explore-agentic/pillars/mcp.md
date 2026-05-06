# Model Context Protocol, sixteen months in

> Anthropic open-sourced MCP on November 25, 2024. Sixteen months later it has 97 million monthly SDK downloads, a Linux Foundation home, and an OAuth 2.1 authorization spec that finally satisfies most CISOs. Field guide for platform teams shipping it now.

*Pillar · Model Context Protocol · 14 minutes · Updated April 17, 2026 · Author Alexander · Reviewed by Elias Saljuki*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/mcp/](https://www.exploreagentic.ai/mcp/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Intro

Open the modelcontextprotocol.io home page <a href="#cite-1" class="cite-ref">[1]</a> and you will not find a manifesto. You will find an RFC index, an SDK matrix in seven languages, and a list of 10,000+ public servers. Sixteen months after Anthropic announced it on November 25, 2024 <a href="#cite-2" class="cite-ref">[2]</a>, MCP has become what its early backers said it would: a JSON-RPC interface for letting models call tools and read data, governed in public, used by everyone with a chat product.

This pillar is the working version of the field map. It covers the four roles in production, the auth spec evolution that finally landed in March 2026 <a href="#cite-3" class="cite-ref">[3]</a>, the gateway category that AWS and Microsoft both shipped this year, and the questions a platform team should ask before its third MCP server.

## TL;DR

- MCP defines three protocol roles: client (the app), server (the tool/data surface), host (the model runtime). Gateways are a fourth, ops-driven role that every multi-server program ends up building or buying.
- 97 million monthly SDK downloads and over 10,000 active servers as of December 2025, per Anthropic and the Linux Foundation announcement <a href="#cite-4" class="cite-ref">[4]</a>.
- Donated to the Agentic AI Foundation under the Linux Foundation on December 9, 2025. Co-founded by Anthropic, Block, and OpenAI; backed by Google, Microsoft, AWS, Cloudflare, and Bloomberg.
- The OAuth 2.1 authorization model became fully usable for enterprise deployments in the November 2025 spec revision (PKCE mandatory, Client ID Metadata Documents) and tightened in March 2026 with mandatory RFC 8707 resource indicators <a href="#cite-3" class="cite-ref">[3]</a>.
- AWS Bedrock AgentCore went GA on October 13, 2025, with MCP server targets and an open-source AgentCore MCP server in nine regions <a href="#cite-5" class="cite-ref">[5]</a>. Microsoft Foundry MCP Server is in preview at mcp.ai.azure.com, Entra-authenticated <a href="#cite-6" class="cite-ref">[6]</a>.

## Stats

- **Nov 25, 2024** — Anthropic open-sources MCP *(Anthropic announcement of the spec, SDKs, and reference servers (Google Drive, Slack, GitHub, Postgres). Source [2].)*
- **Dec 9, 2025** — Donated to the Agentic AI Foundation *(Anthropic's announcement of the AAIF (Linux Foundation directed fund) co-founded with Block and OpenAI. Source [4].)*
- **97M / 10K** — Monthly SDK downloads / active servers *(Public adoption figures cited by Anthropic and the Linux Foundation as of December 2025.)*
- **2026-03-15** — Latest spec revision *(RFC 8707 resource indicators became mandatory to mitigate token mis-redemption. Source [3].)*

## 01. The three protocol roles, plus the one ops added

The spec defines three participants <a href="#cite-1" class="cite-ref">[1]</a>. A <strong>client</strong> renders the conversation or workflow to the user: Claude Desktop, Cursor, ChatGPT, Microsoft Copilot, and roughly two dozen others. A <strong>server</strong> exposes data, prompts, or tools through a JSON-RPC surface. A <strong>host</strong> runs the model and orchestrates tool calls. Any of the three can be swapped independently. In practice, enterprises rarely want all three swappable; the procurement value is in pinning two and varying one.

The emergent fourth role is the <strong>gateway</strong>. A gateway is an authorization- and observability-aware proxy that sits between a set of clients and a set of MCP servers. It enforces policy on every tool call, logs the call to a system the audit team can read, and brokers the credentials that the end user should never paste into a prompt. AWS shipped one as AgentCore Gateway <a href="#cite-5" class="cite-ref">[5]</a>. Cloudflare ships one as MCP Server Portals <a href="#cite-7" class="cite-ref">[7]</a>. Most platform teams that buy neither end up building one.

*The four roles in a production MCP deployment, April 2026*

| Role | Spec status | Responsibility | Who owns it |
| --- | --- | --- | --- |
| Client | Spec-defined | Renders the conversation, collects input, presents tool affordances | Product team or vendor |
| Host | Spec-defined | Runs the model; orchestrates the tool-call loop | Platform team |
| Server | Spec-defined | Implements the JSON-RPC surface; exposes data, prompts, tools | Domain team (HR, Finance, IT) |
| Gateway | Emergent | Authenticates, authorizes, logs, rate-limits across many servers | Platform + Security, jointly |

## 02. What shipped between November 2024 and April 2026

Twelve months of unusually orderly releases. The original Anthropic announcement landed November 25, 2024, with reference servers for Google Drive, Slack, GitHub, Git, Postgres, and Puppeteer, and SDKs in Python, TypeScript, C#, and Java <a href="#cite-2" class="cite-ref">[2]</a>. Block and Apollo were the first two named enterprise adopters in the same announcement.

In March 2025 the spec gained OAuth 2.1 authorization. In June 2025 it formally split MCP servers from authorization servers and required Protected Resource Metadata under RFC 9728 <a href="#cite-8" class="cite-ref">[8]</a>. In November 2025 it adopted Client ID Metadata Documents and made PKCE non-negotiable for every client. Each revision survived a public RFC; none broke the wire format.

On December 9, 2025, Anthropic announced the donation of MCP to a new Agentic AI Foundation under the Linux Foundation, co-founded with Block and OpenAI <a href="#cite-4" class="cite-ref">[4]</a>. Google, Microsoft, AWS, Cloudflare, Bloomberg, and Intuit signed on as supporting members. The neutral-governance question that some CISOs had been quietly raising in security reviews stopped being a question.

> The wire format barely moved in sixteen months. The auth surface changed almost every quarter, and that is the part that determined whether you could deploy this in a regulated environment.

## 03. Authorization, the part security review reads

If your security team has been asking for the auth model in writing, the answer in April 2026 is straightforward enough to pin to a slide. An MCP server acts as an OAuth 2.1 resource server. The MCP client is an OAuth 2.1 client making protected-resource requests on behalf of the user. The host runtime never sees long-lived credentials. <a href="#cite-3" class="cite-ref">[3]</a>

Three concrete things changed in the last six months that matter for procurement. First, the November 2025 spec made PKCE mandatory across all clients (not optional, not configurable). Second, Client ID Metadata Documents replaced ad hoc client registration, which is the change that lets you publish a single client manifest and have every MCP server you talk to validate it identically. Third, the March 15, 2026 revision mandated RFC 8707 resource indicators to prevent token mis-redemption (the attack class where a token issued for tool A is replayed against tool B). <a href="#cite-3" class="cite-ref">[3]</a>

If you are evaluating MCP servers from third parties, the short due-diligence list: do they implement Protected Resource Metadata under RFC 9728, do they enforce PKCE, and do they validate the resource indicator on every token. A vendor that cannot answer these three questions in writing has not read the current spec.

> **What this means in procurement**: When AWS shipped AgentCore Gateway GA in October 2025 with Cognito-backed OAuth, and Microsoft shipped Foundry MCP Server in preview hosted at mcp.ai.azure.com with Entra-only auth and on-behalf-of token flows, both were specifically aligning to the protocol-level auth spec, not inventing their own. That convergence is what made auth a procurement-answerable question instead of a research project.

## 04. Gateways: where most of the 2026 budget is going

Every platform team with more than three MCP servers ends up building or buying a gateway. The reasons are unexciting: per-tool authorization, a single point at which to log every call, a centralized rate-limit, and a hard boundary at which to enforce egress rules. The MCP spec does not require a gateway. Production deployment patterns do.

The vendor list grew quickly. AWS Bedrock AgentCore Gateway became GA on October 13, 2025, in nine regions, and now connects to MCP servers as named targets <a href="#cite-5" class="cite-ref">[5]</a>. Cloudflare's MCP Server Portals consolidate authorized servers behind a single endpoint <a href="#cite-7" class="cite-ref">[7]</a>. The independent landscape includes Kong, Composio, Bifrost, and Zuplo, plus a long tail of in-house gateways at companies that started before the products existed. Most options shipped between September 2025 and Q1 2026. Assume the comparison shifts every quarter.

- **Tool-level authorization, written as policy** — Which users, under which conditions, can invoke which tools on which servers. Versioned, reviewable, expressed as code (Cedar in AgentCore's case).
- **Credential brokering** — The gateway holds the Snowflake PAT, the Jira token, the SharePoint secret. The user never sees them; the model never receives them in clear text.
- **Call observability** — Per-tool latency, per-tool error rate, per-tool outcome flag. Traceable back to a conversation ID and, where possible, to a business event in the calling system.
- **Egress enforcement** — For regulated workloads, the gateway is the chokepoint that proves PII never left a region and free-form text was never exfiltrated through a tool call.

## 05. If your team is starting MCP work this quarter

Pick one workflow that already has a real metric. Stand up one MCP server for it. Front the server with whichever gateway your procurement team can get through fastest; that almost certainly means whichever cloud you already buy from, which means AgentCore Gateway on AWS or the Foundry MCP Server on Azure. Instrument every tool call with an outcome flag from the first deployment. The programs that scaled in 2025 looked like this. The ones that did not scaled the registry first and the use case second.

Two things to skip in the first sixty days. Do not try to publish a server catalog before you have a working green call graph for the first server. Do not write your own authorization layer; use the spec's OAuth 2.1 + RFC 8707 model and the gateway's enforcement of it. Both shortcuts seem like accelerators and almost always cost a quarter.

> **Further reading on this site**: Vendor evaluation rubric for MCP gateways: /glossary/mcp-gateway. Comparison of MCP and RAG (the two protocols people most often confuse): /comparisons/mcp-vs-rag. Field analysis of AgentCore vs Foundry as MCP hosting platforms: /insights/aws-agentcore-vs-azure-ai-foundry.

## FAQ

**Q: What is the Model Context Protocol (MCP)?**

MCP is an open JSON-RPC specification, originally released by Anthropic on November 25, 2024 <a href="#cite-2" class="cite-ref">[2]</a>, that lets a language-model client call tools and read data from an external server in a uniform way. It defines three roles: client (the app), server (the tool/data surface), and host (the model runtime). The protocol was donated to the Agentic AI Foundation under the Linux Foundation on December 9, 2025 <a href="#cite-4" class="cite-ref">[4]</a>.

**Q: Who governs MCP today?**

Since December 9, 2025, MCP is governed by the Agentic AI Foundation (AAIF), a directed fund hosted by the Linux Foundation. The AAIF was co-founded by Anthropic, Block, and OpenAI, with supporting members including Google, Microsoft, AWS, Cloudflare, Bloomberg, and Intuit <a href="#cite-4" class="cite-ref">[4]</a>. Day-to-day technical direction stays with the project's maintainers; the foundation handles funding, IP, and trademarks.

**Q: How does MCP authentication and authorization work?**

An MCP server acts as an OAuth 2.1 resource server; the MCP client is an OAuth 2.1 client making requests on behalf of the user. As of the March 15, 2026 spec revision, three things are mandatory: PKCE on every client, Protected Resource Metadata under RFC 9728, and resource indicators under RFC 8707 to prevent token mis-redemption between tools <a href="#cite-3" class="cite-ref">[3]</a>. Both AWS AgentCore Gateway and Microsoft Foundry MCP Server align to this spec rather than implementing custom auth.

**Q: Do I need an MCP gateway?**

Once you operate more than three MCP servers in the same environment, the answer is almost always yes. Gateways centralize per-tool authorization, log every tool call, broker enterprise credentials so the user never pastes tokens into prompts, and enforce egress rules for regulated data. AWS Bedrock AgentCore Gateway (GA October 13, 2025) <a href="#cite-5" class="cite-ref">[5]</a> and Cloudflare MCP Server Portals <a href="#cite-7" class="cite-ref">[7]</a> are the two cloud-native options. Independent vendors include Kong, Composio, Bifrost, and Zuplo.

**Q: How is MCP different from RAG?**

RAG (Retrieval-Augmented Generation) is a pattern for grounding a model's response in a retrieved document set; MCP is a wire protocol for letting a model call tools and fetch context. They solve different problems and are usually used together. A full comparison with implementation guidance is at /comparisons/mcp-vs-rag.

**Q: Which AI clients support MCP?**

As of December 2025, MCP has first-class client support across Claude (Anthropic), ChatGPT (OpenAI), Cursor, Gemini, Microsoft Copilot, Visual Studio Code, Visual Studio 2026 Insiders, and approximately twenty other clients <a href="#cite-4" class="cite-ref">[4]</a>. The protocol's monthly SDK downloads passed 97 million in the same window. Adoption is broad enough that vendor-lock concerns no longer block enterprise procurement.

## Citations

1. **Model Context Protocol** — Authorization specification (current) — https://modelcontextprotocol.io/specification/draft/basic/authorization [accessed 2026-04-17] *(Canonical spec home; SDK matrix and server registry index linked from this page.)* { #cite-1 }
2. **Anthropic** — Introducing the Model Context Protocol — https://www.anthropic.com/news/model-context-protocol [accessed 2026-04-17] *(Original Nov 25, 2024 announcement; lists initial reference servers (Drive, Slack, GitHub, Postgres) and SDKs (Python, TypeScript, C#, Java).)* { #cite-2 }
3. **dasroot.net (Maciej Kocon)** — The New MCP Authorization Specification — OAuth 2.1, Resource Indicators — https://dasroot.net/posts/2026/04/mcp-authorization-specification-oauth-2-1-resource-indicators/ [accessed 2026-04-17] *(Walkthrough of the March 15, 2026 spec revision and the RFC 8707 resource-indicators mandate.)* { #cite-3 }
4. **Anthropic** — Donating the Model Context Protocol and establishing the Agentic AI Foundation — https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation [accessed 2026-04-17] *(Dec 9, 2025 announcement; lists supporting members and the 97M / 10K adoption figures cited above.)* { #cite-4 }
5. **AWS** — Amazon Bedrock AgentCore is now generally available — https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/ [accessed 2026-04-17] *(October 13, 2025 GA announcement; nine-region availability and AgentCore Gateway MCP server targets.)* { #cite-5 }
6. **Microsoft** — Announcing Foundry MCP Server (preview) in the cloud — https://devblogs.microsoft.com/foundry/announcing-foundry-mcp-server-preview-speeding-up-ai-dev-with-microsoft-foundry/ [accessed 2026-04-17] *(Microsoft Foundry MCP Server preview at https://mcp.ai.azure.com with Entra ID OBO auth.)* { #cite-6 }
7. **Cloudflare** — Scaling MCP adoption: reference architecture for enterprise MCP deployments — https://blog.cloudflare.com/enterprise-mcp/ [accessed 2026-04-17] *(Cloudflare MCP Server Portals + the gateway-as-aggregation pattern.)* { #cite-7 }
8. **Auth0 (Okta)** — Model Context Protocol Spec Updates from June 2025 — All About Auth — https://auth0.com/blog/mcp-specs-update-all-about-auth/ [accessed 2026-04-17] *(June 2025 split of MCP servers from authorization servers; introduction of Protected Resource Metadata (RFC 9728).)* { #cite-8 }
9. **Linux Foundation** — Linux Foundation Announces the Formation of the Agentic AI Foundation (AAIF) — https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation [accessed 2026-04-17] *(Foundation press release; founding projects (MCP, goose, AGENTS.md) and supporting member list.)* { #cite-9 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/mcp/](https://www.exploreagentic.ai/mcp/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
