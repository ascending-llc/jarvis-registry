# Model Context Protocol (MCP)

*Also known as: MCP · 7 min · Updated April 17, 2026 · Author Alexander · Reviewed by Ryo Hang*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/glossary/model-context-protocol/](https://www.exploreagentic.ai/glossary/model-context-protocol/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Definition

<strong>Model Context Protocol (MCP)</strong> is an open JSON-RPC 2.0 specification that standardizes how language-model clients exchange context and invoke tools on external servers. Anthropic released the first draft on November 25, 2024 <a href="#cite-1" class="cite-ref">[1]</a>, and donated the project to the Agentic AI Foundation under the Linux Foundation on December 9, 2025 <a href="#cite-2" class="cite-ref">[2]</a>. As of March 2026, combined Python and TypeScript SDK downloads passed 97 million per month <a href="#cite-3" class="cite-ref">[3]</a>.

## Where MCP came from

Anthropic open-sourced MCP on November 25, 2024, with reference servers for Google Drive, Slack, GitHub, Git, Postgres, and Puppeteer, and SDKs in Python, TypeScript, C#, and Java <a href="#cite-1" class="cite-ref">[1]</a>. Block and Apollo were the first two named enterprise adopters. The framing was narrow: end the bilateral integration problem where every AI client re-invented its own way to call tools.

Adoption moved faster than the launch deck predicted. By April 2025 monthly SDK downloads had crossed 8 million. By March 2026 the number had reached 97 million, a 970x lift in eighteen months <a href="#cite-3" class="cite-ref">[3]</a>. OpenAI added MCP support in March 2025, Microsoft in July 2025, and AWS through Bedrock AgentCore in October 2025 <a href="#cite-4" class="cite-ref">[4]</a>.

On December 9, 2025, Anthropic announced it would donate MCP to the newly formed Agentic AI Foundation (AAIF), a directed fund under the Linux Foundation co-founded with Block and OpenAI <a href="#cite-2" class="cite-ref">[2]</a>. Supporting members include Google, Microsoft, AWS, Cloudflare, Bloomberg, and Intuit <a href="#cite-5" class="cite-ref">[5]</a>. For CISOs who had quietly flagged single-vendor governance as a procurement risk, that was the threshold event.

## The three roles in the spec, and the fourth that emerged

The specification names three participants. A <strong>client</strong> renders the interaction to the user. Examples include Claude Desktop, Cursor, ChatGPT, Microsoft Copilot, Visual Studio Code, and roughly three hundred others as of April 2026. A <strong>host</strong> runs the model and orchestrates the tool-call loop. A <strong>server</strong> exposes data, prompts, or tools through a JSON-RPC 2.0 surface <a href="#cite-6" class="cite-ref">[6]</a>.

Production deployments added a fourth role the spec does not describe: the <strong>gateway</strong>. A gateway sits between many clients and many servers, authenticates each call, enforces per-tool policy, logs the traffic, and brokers enterprise credentials so the user never pastes a Snowflake PAT into a prompt. AWS ships one as AgentCore Gateway; Cloudflare ships one as MCP Server Portals. We cover the pattern in the MCP Gateway entry.

## Authorization, briefly

The auth surface is the part that moved the most. An MCP server acts as an OAuth 2.1 resource server; the MCP client is an OAuth 2.1 client making protected-resource requests on behalf of the user. Three revisions mattered for procurement: June 2025 split the MCP server from the authorization server and introduced Protected Resource Metadata under RFC 9728; November 2025 made PKCE mandatory and replaced ad-hoc client registration with Client ID Metadata Documents; March 15, 2026 made RFC 8707 resource indicators mandatory to prevent token mis-redemption between tools <a href="#cite-7" class="cite-ref">[7]</a>.

This term is often misused. For example, vendors claiming "MCP auth" that have not implemented RFC 8707 resource indicators are a full spec revision behind as of April 2026. The short due-diligence question: does the server validate the resource indicator on every token?

## Why enterprise buyers care

Before MCP, integration was bilateral. After MCP, tooling is portable: the same GitHub server works in Claude, Cursor, and ChatGPT. That shifts the vendor-lock conversation away from the integration layer (where it was always a dead weight) toward the model and platform layers, where honest procurement discussions can happen.

The Nerq Q1 2026 census indexed 17,468 MCP servers across registries, though only 12.9% scored "high trust" on documentation, maintenance, and reliability <a href="#cite-8" class="cite-ref">[8]</a>. The practical reading: a long tail exists, curation remains a real problem, and programs that vet servers against the AAIF governance process run into fewer surprises.

> **Read the pillar**: The long-form version of this entry (four roles in production, the complete auth timeline, gateway vendor comparisons) is the MCP pillar at /mcp. It carries the same numbered citations this entry does.

## See Also

- [MCP Gateway](mcp-gateway.md)
- [Agentic RAG](agentic-rag.md)
- [Agent observability](agent-observability.md)
- [MCP pillar](../pillars/mcp.md)

## FAQ

**Q: What is the Model Context Protocol (MCP)?**

The wire protocol that lets an AI client call tools and read data from external servers over JSON-RPC 2.0. Anthropic released the first draft on November 25, 2024. Three roles in the spec. Client. Server. Host. Auth is OAuth 2.1. Governance moved to the Agentic AI Foundation, a Linux Foundation directed fund, on December 9, 2025. The supporting members read like a list of companies that rarely sign the same document: Anthropic, OpenAI, Block, Google, Microsoft, AWS, Cloudflare, Bloomberg. Which is the main reason CISOs stopped flagging it as a single-vendor risk.

**Q: Who created MCP and who governs it today?**

Anthropic, on November 25, 2024. Single-vendor origin. CISOs flagged that quietly as a procurement risk for most of 2025, and they were right to. December 9, 2025 closed the objection. Anthropic donated the project to the Agentic AI Foundation (AAIF), a directed fund under the Linux Foundation co-founded with Block and OpenAI. Day-to-day technical direction still sits with the maintainers, guided by the Specification Enhancement Proposal (SEP) process. The neutral-governance conversation now passes the laugh test, which is all most procurement committees were asking for.

**Q: How is MCP different from a REST API or a plugin system?**

A REST API is a bespoke HTTP contract, one per product. A plugin system is vendor-specific by design. MCP is narrower and more useful. A model-facing tool-calling JSON-RPC protocol. Published spec, OAuth 2.1 authorization, SDKs in Python, TypeScript, C#, and Java. The practical payoff is portability. The same GitHub MCP server runs under Claude, ChatGPT, Cursor, Microsoft Copilot, and Visual Studio Code without per-client glue code. That glue is the part that used to eat most of the integration budget.

**Q: How many MCP servers and clients exist?**

The Nerq Q1 2026 census indexed 17,468 servers across registries. Only 12.9% scored high-trust on documentation and maintenance; a long tail exists, and curation is still a real problem. Client support spans roughly 300 products. Claude. ChatGPT. Cursor. Gemini. Microsoft Copilot. Visual Studio Code. Visual Studio 2026 Insiders. Combined Python and TypeScript SDK downloads hit 97 million per month in March 2026, a 970x lift from April 2025's 8 million.

**Q: Does MCP require OAuth?**

For any server exposing protected resources, yes. The current spec treats MCP servers as OAuth 2.1 resource servers. Three revisions shaped what that means in practice. June 2025 split the MCP server from the authorization server under RFC 9728. November 2025 made PKCE mandatory. March 15, 2026 made RFC 8707 resource indicators mandatory. That last one has teeth: the resource parameter must appear in both authorization and token requests, so a token minted for one server cannot be replayed against another. The due-diligence question writes itself. Does your server validate the resource indicator on every token? If the answer hedges, the answer is no.

**Q: What is the difference between MCP and RAG?**

Different layers of the stack. RAG (Retrieval-Augmented Generation) is a pattern for grounding a model's answer in retrieved documents. MCP is a wire protocol for how the model calls tools at all. Orthogonal, not competing. Most production systems end up running both. The MCP server exposes the vector store or SQL engine. The RAG strategy decides how the agent uses it. Pipe on one side, decision-making on top of the pipe. The full side-by-side is at /comparisons/mcp-vs-rag.

## Citations

1. **Anthropic** — Introducing the Model Context Protocol — https://www.anthropic.com/news/model-context-protocol [accessed 2026-04-17] *(November 25, 2024 announcement; reference servers (Drive, Slack, GitHub, Git, Postgres, Puppeteer) and SDK matrix.)* { #cite-1 }
2. **Anthropic** — Donating the Model Context Protocol and establishing the Agentic AI Foundation — https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation [accessed 2026-04-17] *(December 9, 2025 donation announcement.)* { #cite-2 }
3. **Model Context Protocol Blog** — One Year of MCP: November 2025 Spec Release — https://blog.modelcontextprotocol.io/posts/2025-11-25-first-mcp-anniversary/ [accessed 2026-04-17] *(Adoption figures: 97M monthly SDK downloads as of March 2026, 970x lift in 18 months.)* { #cite-3 }
4. **AWS** — Amazon Bedrock AgentCore is now generally available — https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/ [accessed 2026-04-17] *(October 13, 2025 GA announcement; AgentCore Gateway supports MCP server targets.)* { #cite-4 }
5. **Linux Foundation** — Linux Foundation Announces the Formation of the Agentic AI Foundation (AAIF) — https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation [accessed 2026-04-17] *(Press release listing founding projects (MCP, goose, AGENTS.md) and supporting members.)* { #cite-5 }
6. **Model Context Protocol** — Specification, 2025-03-26 revision — https://modelcontextprotocol.io/specification/2025-03-26 [accessed 2026-04-17] *(Canonical spec; defines JSON-RPC 2.0 message shape and the client/server/host roles.)* { #cite-6 }
7. **Model Context Protocol** — Authorization specification (current draft) — https://modelcontextprotocol.io/specification/draft/basic/authorization [accessed 2026-04-17] *(OAuth 2.1 resource-server model; PKCE mandatory; RFC 8707 resource indicators mandatory from March 15, 2026.)* { #cite-7 }
8. **Model Context Protocol Blog** — The 2026 MCP Roadmap — https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/ [accessed 2026-04-17] *(Roadmap for transports, agent communication, governance maturation, enterprise readiness.)* { #cite-8 }
9. **TechCrunch** — OpenAI, Anthropic, and Block join new Linux Foundation effort to standardize the AI agent era — https://techcrunch.com/2025/12/09/openai-anthropic-and-block-join-new-linux-foundation-effort-to-standardize-the-ai-agent-era/ [accessed 2026-04-17] *(December 9, 2025 reporting on the AAIF formation.)* { #cite-9 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/glossary/model-context-protocol/](https://www.exploreagentic.ai/glossary/model-context-protocol/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
