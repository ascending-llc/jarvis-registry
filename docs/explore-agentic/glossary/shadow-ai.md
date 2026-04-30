# Shadow AI

*Also known as: Unsanctioned AI, Bring-your-own AI · 7 min · Updated April 17, 2026 · Author Gloria Qian Zhang · Reviewed by Michael Clough*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/glossary/shadow-ai/](https://www.exploreagentic.ai/glossary/shadow-ai/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Definition

<strong>Shadow AI</strong> is the use of AI tools (most often consumer generative-AI services, but also SaaS-embedded AI features and model integrations built by individual teams) outside the visibility and control of the organization's IT, security, and governance functions. Every mature AI governance program discovers it. IBM's 2025 Cost of a Data Breach Report found that breaches involving shadow AI cost $670,000 more than standard incidents, with one in five organizations (20%) experiencing a shadow-AI-linked breach <a href="#cite-1" class="cite-ref">[1]</a>.

## What counts as shadow AI

The canonical case is an employee pasting sensitive data into ChatGPT from a personal account, but that is only the visible layer. The 2024 Microsoft and LinkedIn Work Trend Index reported that 78% of AI users brought their own AI to work, and 71% of office workers admitted using AI tools without IT approval <a href="#cite-2" class="cite-ref">[2]</a>. In every AI inventory exercise, three categories consistently surface, and each requires a different remediation path.

- Personal-account use of consumer AI services (ChatGPT, Claude, Gemini, Perplexity) with corporate data. Surfaces from DLP alerts and, occasionally, an amnesty program.
- SaaS tools that quietly added generative features in a recent release: Notion AI, Slack AI, Zoom AI Companion, Salesforce Einstein GPT, and dozens of smaller tools. Surfaces from procurement and SSO logs.
- Team-built integrations: a marketing analyst wiring OpenAI into a Google Sheet, a data engineer running a local llama.cpp on production data, an acquired subsidiary running its own models. Surfaces only from network telemetry and conversations.

## Why it is not just shadow IT

Shadow IT is an access-control problem: someone is using a tool without approval. Shadow AI adds two complications that make it materially harder. First, the data leaves: any conversation with an external model potentially trains on, caches, or logs the content you sent, depending on the provider's terms and your account tier. Second, the output comes back: an employee who drafts a contract clause in an unapproved model then pastes that clause into a real document, and nobody knows which clauses were machine-written.

That dual flow (data out, output in) is the part that breaks simple blocking strategies. Samsung's 2023 company-wide ChatGPT ban did not stop AI adoption; it pushed it underground and made the governance problem worse <a href="#cite-3" class="cite-ref">[3]</a>. The working approach is visibility first, policy second. IBM's 2025 data-breach report found that 97% of organizations with shadow-AI-linked breaches lacked proper AI access controls <a href="#cite-1" class="cite-ref">[1]</a>.

## How to surface it

1. **Start with SSO logs** — Enumerate every SaaS tool in the corporate identity catalog that has added a generative feature. The top 200 tools will cover the bulk of the exposure. This is a one-afternoon exercise that most organizations have never run.
2. **Cross-reference procurement records** — Search the last 24 months of procurement and expense data for strings like "AI", "copilot", "agent", "assistant", "LLM", and "GPT". Each hit is a candidate for the inventory.
3. **Review DLP egress telemetry** — Most enterprise DLP platforms now flag traffic to consumer AI services. Turn on the category and measure for 30 days. The volume is rarely what leadership expects.
4. **Run a one-week amnesty** — Ask employees to register the AI tools they actually use, without punishment. You will learn more in a week than in a quarter of top-down discovery.
5. **Refresh monthly, not annually** — Shadow AI inventories decay. The annual governance refresh is theatre; monthly is the lowest cadence that survives contact with reality.

## What a governance response looks like

Ban-everything approaches fail. Unregulated-everything approaches fail. The governance pattern that works, in practice, has three parts: provide a sanctioned alternative (usually an enterprise LLM with DLP, logging, and retention controls) so employees have a legitimate path; publish a plain-language use policy describing what data can and cannot leave the sanctioned alternative; and monitor for drift with the inventory process above.

Jarvis, like every serious enterprise LLM platform, exists partly to make the sanctioned-alternative story credible: DLP, RBAC, audit logs, and multi-model routing in one layer. That is not the point of this entry; we mention it only because we would be dishonest if we pretended the sanctioned-alternative slot was filled by a competitor on this site.

> **The single best habit**: Put shadow-AI inventory on the same monthly cadence as patch compliance. One owner, one dashboard, same meeting. Governance programs that treat it as a "project" rediscover the same surface area six months later; programs that treat it as a rolling obligation don't.

## See Also

- [AI Governance pillar](../pillars/ai-governance.md)
- [ISO/IEC 42001](iso-42001.md)
- [NIST AI RMF](nist-ai-rmf.md)
- [MCP Gateway](mcp-gateway.md)

## FAQ

**Q: What is shadow AI?**

The use of AI tools outside the visibility and control of IT, security, and governance. Consumer services: ChatGPT, Claude, Gemini, Perplexity. SaaS-embedded AI features. Team-built integrations a data engineer wired up on a Tuesday. Shadow IT, with two extra problems nobody budgeted for. The data leaves the organization (to provider servers, caches, or training sets). The machine-generated output returns into real documents with no audit trail. Both directions are a problem; the second one is the one that bites quietly.

**Q: How costly is shadow AI?**

Costly enough to change the conversation. IBM's 2025 Cost of a Data Breach Report put the average shadow-AI breach at $4.63 million. That's $670,000 above a standard incident. One in five organizations (20%) reported a shadow-AI-linked breach. Of those, 97% lacked proper AI access controls. The exposure skewed toward customer PII and intellectual property, which are the two categories that turn a breach into a regulator conversation.

**Q: How widespread is shadow AI?**

Widespread enough that "surface area" is the wrong question. The 2024 Microsoft and LinkedIn Work Trend Index put 78% of AI users bringing their own AI to work. 71% of office workers admitted to using AI tools without IT approval. More recent 2025 data pegs shadow-AI usage at around 37% of staff as a persistent corporate-security concern. Which means any governance program that assumes employees will wait for a sanctioned alternative is already six months behind.

**Q: Can I just block ChatGPT to stop shadow AI?**

No. Samsung tried a company-wide ChatGPT ban in 2023. The ban did not stop adoption. It pushed it onto personal devices, where DLP could not see any of it. The pattern that works in 2026 has three moves. Provide a sanctioned enterprise alternative (ChatGPT Enterprise, Microsoft Copilot, or an internal LLM platform). Publish a plain-language use policy people will actually read. Monitor for drift on the same cadence as patch compliance. Banning without an alternative is the shortest path to losing visibility entirely.

**Q: How do I surface shadow AI in my organization?**

Five steps, in order. Start with SSO logs; the top 200 SaaS tools cover most of the exposure. Cross-reference the last 24 months of procurement records for strings like 'AI', 'copilot', 'agent', 'assistant', 'LLM', 'GPT'. Turn on DLP egress telemetry to consumer AI services and measure for 30 days. Run a one-week amnesty where employees register what they actually use, no punishment. Then refresh monthly, not annually. Annual discovery is theatre; monthly is the lowest cadence that survives contact with reality.

**Q: What tools help detect shadow AI?**

Several, each solving a different slice. Microsoft Purview AI Hub is the most mature option for Microsoft 365 environments. Cloudflare Gateway's shadow-MCP scans, rolled out in 2026, extend detection to MCP servers specifically. Nudge Security. Torii. Vectra. Proofpoint. Each publishes shadow-AI-specific detection features, and the right answer is usually two of them, not one; the overlap is how you catch the thing the primary tool misses.

## Citations

1. **IBM** — Cost of a Data Breach Report 2025 — https://www.ibm.com/reports/data-breach [accessed 2026-04-17] *(Shadow AI added $670K to the average breach cost; 20% of organizations reported shadow-AI-linked breaches; 97% of those lacked AI access controls.)* { #cite-1 }
2. **Microsoft** — 2025 Annual Work Trend Index — https://news.microsoft.com/annual-work-trend-index-2025/ [accessed 2026-04-17] *(2024 Work Trend Index data: 78% of AI users bring their own AI; 71% use AI without IT approval.)* { #cite-2 }
3. **CloudEagle** — ChatGPT Enterprise Security: How To Govern Your AI in 2026 — https://www.cloudeagle.ai/blogs/chatgpt-enterprise-security [accessed 2026-04-17] *(Samsung 2023 company-wide ChatGPT ban case study.)* { #cite-3 }
4. **IBM Newsroom** — 13% Of Organizations Reported Breaches Of AI Models, 97% Lacked AI Access Controls — https://newsroom.ibm.com/2025-07-30-ibm-report-13-of-organizations-reported-breaches-of-ai-models-or-applications,-97-of-which-reported-lacking-proper-ai-access-controls [accessed 2026-04-17] *(July 30, 2025 press release.)* { #cite-4 }
5. **Microsoft (Edge Blog)** — Protect your enterprise from shadow AI: Announcements at RSAC 2026 — https://blogs.windows.com/msedgedev/2026/03/23/protect-your-enterprise-from-shadow-ai-and-more-announcements-at-rsac-2026/ [accessed 2026-04-17] *(March 23, 2026 Microsoft briefing on enterprise shadow-AI detection.)* { #cite-5 }
6. **Microsoft** — Microsoft Purview (AI Hub) — https://www.microsoft.com/en-us/security/business/microsoft-purview [accessed 2026-04-17] *(Purview AI Hub for SaaS-embedded AI discovery and DLP.)* { #cite-6 }
7. **Kiteworks** — How Shadow AI Costs Companies $670K Extra: IBM's 2025 Breach Report — https://www.kiteworks.com/cybersecurity-risk-management/ibm-2025-data-breach-report-ai-risks/ [accessed 2026-04-17] *(Analysis of shadow-AI breach cost premium.)* { #cite-7 }
8. **Nudge Security** — Shadow AI: The emerging security threat in IBM's 2025 Cost of a Data Breach Report — https://www.nudgesecurity.com/post/shadow-ai-the-emerging-security-threat-in-ibms-2025-cost-of-a-data-breach-report [accessed 2026-04-17] *(Practitioner analysis of shadow-AI governance gaps.)* { #cite-8 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/glossary/shadow-ai/](https://www.exploreagentic.ai/glossary/shadow-ai/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
