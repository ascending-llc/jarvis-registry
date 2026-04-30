# Governance, written by people who had to file the paperwork

> A working AI governance program in 2026: ISO 42001 certified vendors, the NIST AI RMF Generative AI Profile, EU AI Act enforcement dates, the OWASP LLM Top 10, and the governance platforms CISOs are actually procuring.

*Pillar · AI Governance · 22 minutes · Updated April 17, 2026 · Author Gloria Qian Zhang · Reviewed by Mehrdad Faqiri*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/ai-governance/](https://www.exploreagentic.ai/ai-governance/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Intro

AI governance is unfashionable, until the audit, when it becomes the only thing anyone talks about. As of April 2026 the audit is no longer hypothetical. The EU AI Act's enforcement regime for general-purpose AI providers took effect on August 2, 2025, with the full high-risk obligations and Commission enforcement powers landing on August 2, 2026 <a href="#cite-1" class="cite-ref">[1]</a>. ISO 42001, the first certifiable AI management standard, now has Anthropic (certified January 6, 2025), Microsoft Azure AI Foundry, AWS, Google, Snowflake, and KPMG in its issued-certificate column <a href="#cite-2" class="cite-ref">[2]</a><a href="#cite-3" class="cite-ref">[3]</a>.

This pillar is the working version of a mature program: three artefacts every CISO needs on file, the two standards that anchor most audits, what the EU AI Act asks of providers versus deployers, the OWASP LLM Top 10 as a threat model, and the governance platform landscape. The pattern we see consistently in programs that pass an audit: a policy with teeth, a practical inventory of in-use AI, and a steering group with the authority to decommission systems that fail evaluation <a href="#cite-4" class="cite-ref">[4]</a>.

## TL;DR

- Three artefacts or the audit fails: a written AI policy, a maintained AI inventory, and an eval cadence with named owners. Shortcut any of the three and the finding appears in writing.
- ISO 42001 (published Dec 2023) is the certifiable standard. Anthropic, Microsoft Azure AI Foundry, AWS, Google, Snowflake, and KPMG hold active certificates as of early 2026 <a href="#cite-2" class="cite-ref">[2]</a><a href="#cite-3" class="cite-ref">[3]</a>.
- NIST AI RMF is the US framework CISOs reference. NIST-AI-600-1, the Generative AI Profile (July 26, 2024), enumerates 13 generative-AI risks and 400+ actions <a href="#cite-5" class="cite-ref">[5]</a>.
- EU AI Act: GPAI obligations in force August 2, 2025; full high-risk regime and penalties from August 2, 2026 <a href="#cite-1" class="cite-ref">[1]</a>.
- Shadow AI is measurable, not theoretical. Gartner's 2025 CISO survey found 69% of organisations suspect or have evidence of employees using prohibited public GenAI tools; UpGuard put the worker figure above 80% <a href="#cite-6" class="cite-ref">[6]</a>.
- The OWASP LLM Top 10 (2025 edition) leads with prompt injection and expanded <em>excessive agency</em>, the explicit governance bridge into agentic AI <a href="#cite-7" class="cite-ref">[7]</a>.

## Stats

- **Jan 6, 2025** — Anthropic ISO 42001 certificate issued *(Certified by Schellman Compliance, LLC; first frontier lab on the standard. Source [2].)*
- **Aug 2, 2026** — EU AI Act high-risk + enforcement live *(Commission enforcement powers and penalties against GPAI providers begin. Source [1].)*
- **13 risks / 400+ actions** — NIST-AI-600-1 Generative AI Profile *(Published July 26, 2024; NIST public working group input from 2,500 participants. Source [5].)*
- **69% / >80%** — organisations / workers using unsanctioned GenAI *(Gartner 2025 CISO survey and UpGuard shadow-AI research. Source [6].)*

## 01. The three artefacts every program needs

A <strong>policy</strong> is not a deck. It is a written document, approved by the same committee that approves access-control policy, with named owners and a review cadence. It identifies prohibited use (employee data in public chat tools, for instance), high-risk use that requires steering-group approval, and the default path for low-risk use with logging. The policy that survives contact with reality fits on two pages and names a human for every decision.

An <strong>inventory</strong> is not a spreadsheet on the deputy's desktop. It is a maintained register, refreshed monthly, covering first-party AI, shadow AI, third-party AI embedded in SaaS tools, and models running inside acquired companies. Gartner's 2025 CISO survey put the share of organisations with suspected or confirmed prohibited GenAI use at 69%, and the inventory is how that number stops being a rumour <a href="#cite-6" class="cite-ref">[6]</a>.

An <strong>eval cadence</strong> is not a launch-day checklist. It is a recurring obligation on the owners of each system to rerun a defined suite (accuracy, safety, bias, prompt-injection, PII-leak) and to post the deltas. The NIST-AI-600-1 Generative AI Profile gives you the starter list of 13 risks and 400+ specific actions to evaluate against <a href="#cite-5" class="cite-ref">[5]</a>. Our editorial position: a program without <em>nightly</em> eval automation passes its first audit and fails its second.

> A policy without an inventory fails the audit. An inventory without a policy passes it. The non-negotiable one is the inventory.

## 02. ISO 42001 and the NIST AI RMF, side by side

<strong>ISO/IEC 42001:2023</strong> is the first certifiable AI management-system standard, published in December 2023. Certification has moved from nice-to-have to table stakes faster than most predicted. Anthropic received its certificate on January 6, 2025 from Schellman Compliance, LLC <a href="#cite-2" class="cite-ref">[2]</a>; Microsoft's Azure AI Foundry Models and Security Copilot followed, issued by Mastermind <a href="#cite-3" class="cite-ref">[3]</a>. AWS, Google, Snowflake, and KPMG hold active certificates. When a European regulator asks <em>who certifies your AI management system</em>, ISO 42001 is the answer that makes the follow-up short.

<strong>NIST AI RMF</strong> is the US voluntary framework most CISOs reference. The January 2023 core is paired with NIST-AI-600-1, the Generative AI Profile released July 26, 2024, which maps 13 concrete generative-AI risks (from CBRN information to data privacy to harmful bias) to more than 400 actions a developer or deployer can take <a href="#cite-5" class="cite-ref">[5]</a>. NIST published a concept note for a Critical-Infrastructure Profile on April 7, 2026, the next wave of sector-specific guidance.

The two do not conflict. ISO 42001 tells you what the management system looks like. NIST AI RMF tells you what risks to evaluate inside it. Enterprises running both build a combined control-mapping document. Two days of work. It pays for itself the first time a customer or auditor asks for evidence, the part procurement teams never plan for until the RFP lands.

*Anchoring AI standards in force, April 2026. Links point to canonical sources.*

| Standard | Publisher | Status | Scope |
| --- | --- | --- | --- |
| ISO/IEC 42001:2023 | ISO / IEC | Published Dec 2023; certifiable | AI management system (policy, controls, audit surface) |
| NIST AI RMF 1.0 + 600-1 Generative AI Profile | NIST | Core 2023; Gen-AI Profile Jul 26, 2024 | Voluntary risk framework; 13 gen-AI risks, 400+ actions |
| EU AI Act (Regulation 2024/1689) | European Union | GPAI in force Aug 2, 2025; full Aug 2, 2026 | Binding law; risk-tiered obligations on providers and deployers |
| OWASP LLM Top 10 (2025) | OWASP Gen AI Security Project | Published 2025 | Threat model: prompt injection, excessive agency, system-prompt leakage |

## 03. EU AI Act enforcement, the dates that matter

Regulation (EU) 2024/1689, the EU AI Act, entered into force August 1, 2024, but the compliance work is stepwise. <strong>August 2, 2025</strong> marked entry into application of the obligations for providers of general-purpose AI models and the institutional governance provisions <a href="#cite-1" class="cite-ref">[1]</a>. Any GPAI model placed on the EU market after that date falls under the new regime immediately.

<strong>August 2, 2026</strong> is the date governance teams should have circled in red. The full obligations for high-risk AI systems apply, and the European Commission's enforcement powers, including penalties against GPAI providers, begin. Models already on the market before August 2025 have until August 2, 2027 to conform, which is the grandfathering clause most deployers mis-read <a href="#cite-1" class="cite-ref">[1]</a>.

The practical effect for most enterprises: treat the procurement pipeline as the primary control. Require vendors to disclose whether their model falls under GPAI obligations, whether it is classified high-risk for your use case, and what evidence they will provide for your Annex IV technical documentation. That evidence package is the artefact that disappears first in a sloppy vendor selection and surfaces most expensively in an audit.

> **Practical procurement clause**: We have started recommending a one-paragraph addendum for every AI vendor contract signed after April 2026: the vendor warrants continued compliance with the EU AI Act obligations applicable to its classification, provides the technical documentation required under Annex IV on request, and notifies the deployer within 30 days of any change in classification. Legal teams push back. Security teams thank you in August.

## 04. Shadow AI is an inventory problem with measurable scale

Shadow AI, meaning employees using AI tools without IT approval, is the governance surface most programs under-estimate. Gartner's 2025 CISO survey found 69% of organisations suspect or have evidence of prohibited public GenAI use <a href="#cite-6" class="cite-ref">[6]</a>. UpGuard's research put worker-level usage above 80%, including nearly 90% of security professionals themselves. WalkMe's 2025 enterprise survey reported 78% of workers using unapproved AI with only 7.5% receiving extensive training. These are not fringe figures.

You cannot police shadow AI with a policy alone. You surface it with four inputs every CISO already has: SSO logs to enumerate SaaS tools with generative features, network telemetry to catch direct API traffic, procurement records filtered for AI-adjacent vocabulary, and a short amnesty window that asks employees to self-register. Every program we have reviewed in 2025-2026 found its largest exposure in one of those four channels, not in the clever leak the compliance team worried about.

- **Start with SSO logs** — Enumerate every SaaS tool with generative features. The top 200 cover most of the risk; the long tail mostly does not.
- **Add procurement records** — Cross-reference anything with 'AI', 'copilot', 'agent', 'assistant', or 'GPT' in purchases made in the last 24 months.
- **Run a one-week amnesty** — Ask employees to register tools they use with no consequence for historical use. One week beats a quarter of top-down discovery.
- **Refresh monthly, not annually** — Inventory decays. The annual refresh is theatre; monthly is the lowest cadence that survives contact with reality.
- **Instrument agentic tool calls via MCP gateway logs** — For agents, the gateway is the chokepoint: every tool call is observable. See our [MCP pillar](mcp.md) for the architecture.

## 05. The OWASP LLM Top 10 as a threat model

The OWASP Gen AI Security Project publishes the <strong>Top 10 for LLM Applications</strong>; the 2025 edition is the current baseline most red teams run against <a href="#cite-7" class="cite-ref">[7]</a>. Prompt injection (LLM01:2025) retains the top position, which tracks with every internal pen-test result we have seen. The expanded <em>Excessive Agency</em> entry (LLM06:2025) is the direct governance bridge into agentic AI. OWASP breaks it into excessive functionality, excessive permissions, and excessive autonomy, each with a distinct mitigation.

System Prompt Leakage is new in 2025 and worth a dedicated policy clause. RAG-related risks (vector and embedding weaknesses) earned a prominent position on the back of survey data showing 53% of companies relying on RAG and agentic pipelines rather than fine-tuning. Map each of the ten to a control in your ISO 42001 Statement of Applicability. That mapping is what auditors actually read.

- **LLM01 Prompt Injection** — Still top. Mitigate with input filtering, output constraints, and (for agents) tool-level authorization at the gateway.
- **LLM06 Excessive Agency** — Limit tool scope, enforce least-privilege credentials, require human-in-the-loop for high-impact actions.
- **LLM07 System Prompt Leakage (new)** — Assume the system prompt is extractable. Never place credentials or secrets in it.
- **LLM08 Vector and Embedding Weaknesses** — Poisoned embeddings and retrieval exploits. Validate ingestion sources; isolate retrieval by tenancy.
- **LLM09 Misinformation** — Groundedness eval on every production release. Tie to the eval cadence in chapter 01.

## 06. AI governance platforms: the procurable category

The platform category matured in 2025. Gartner's 2025 Market Guide for AI Governance Platforms named the serious vendors; the shortlist most CISOs now run is stable: <strong>Credo AI</strong> (Fast Company <em>Most Innovative 2026</em>) <a href="#cite-8" class="cite-ref">[8]</a>, <strong>Holistic AI</strong>, <strong>Monitaur</strong> (Forrester Strong Performer, Q3 2025) <a href="#cite-9" class="cite-ref">[9]</a>, and <strong>Fairly</strong>.

Buy what fits the programme you already have. If the driver is EU AI Act readiness, Credo AI and Holistic AI are the common landing spots. If the driver is regulated-industry audit rigour, Monitaur's record-first posture fits better. Seven-figure first-year program cost is typical for enterprise tenancies.

> **Further reading on this site**: Our [MCP pillar](mcp.md) covers the gateway layer. The [ISO 42001 glossary entry](../glossary/iso-42001.md) is the 900-word explainer. For observability, see the [agent observability](../glossary/agent-observability.md) entry.

## FAQ

**Q: What are the three artefacts of a working AI governance program?**

Three artefacts, nothing optional. A written <strong>policy</strong>. A maintained <strong>inventory</strong> of every AI in use, refreshed monthly. An <strong>eval cadence</strong> with named owners and a posted schedule. ISO 42001 names all three as clauses. NIST-AI-600-1 supplies the risk content that goes inside them <a href="#cite-4" class="cite-ref">[4]</a><a href="#cite-5" class="cite-ref">[5]</a>. Programs missing any one pass their first audit on paperwork. They fail the second. The auditor samples the inventory, finds the third-party AI nobody registered, and the finding lands in writing, the part steering committees always under-estimate.

**Q: Which enterprise AI vendors are ISO 42001 certified as of April 2026?**

A handful of names, and the list matters. Anthropic, certified January 6, 2025 by Schellman Compliance, LLC: first frontier lab on the standard <a href="#cite-2" class="cite-ref">[2]</a>. Microsoft Azure AI Foundry Models and Security Copilot, issued by Mastermind <a href="#cite-3" class="cite-ref">[3]</a>. AWS. Google. Snowflake. KPMG. OpenAI as a corporate entity had not published an ISO 42001 certificate as of this update, a gap European procurement teams notice even when the contracting team does not.

**Q: What are the key EU AI Act enforcement dates?**

Four dates, in order. Entry into force: August 1, 2024. GPAI obligations live: August 2, 2025. Full high-risk regime plus Commission enforcement powers: August 2, 2026. Grandfathering to August 2, 2027 for GPAI already on the market before August 2025 <a href="#cite-1" class="cite-ref">[1]</a>. That grandfathering clause is the one most deployers mis-read, the part legal surfaces six months late. Write August 2, 2026 in red on the war-room wall. It matters more than the August 1 entry-into-force people quote in decks.

**Q: How do ISO 42001 and NIST AI RMF fit together?**

They cover different layers of the same work. ISO 42001 defines the certifiable AI management system: structure, roles, documented processes, the audit surface. NIST AI RMF (paired with the 600-1 Generative AI Profile) supplies the risk content you evaluate inside that system on specific AI use cases <a href="#cite-5" class="cite-ref">[5]</a>. Teams running both produce one combined control-mapping document. About two days of work once the ISO 42001 scope is set. The part buyers do not expect: auditors and customers now ask for both citations side by side, not one or the other.

**Q: What is shadow AI and how widespread is it?**

Shadow AI is employee use of AI tools outside the IT approval path. Gartner's 2025 CISO survey found 69% of organisations suspect or have evidence of prohibited GenAI use. UpGuard research put worker-level usage above 80%, and nearly 90% of security professionals themselves <a href="#cite-6" class="cite-ref">[6]</a>. Not fringe numbers. The response is inventory, not prohibition. Start with SSO logs, add procurement records, run a one-week amnesty. The inventory you end up with is always bigger than the one the governance team guessed.

**Q: What does the OWASP LLM Top 10 (2025) prioritise?**

Prompt Injection (LLM01) holds the top slot. It tracks with every internal pen-test result we have seen. Excessive Agency (LLM06) splits into excessive functionality, permissions, and autonomy, the direct governance bridge into agentic AI. System Prompt Leakage is new as LLM07; assume the system prompt is extractable and never put credentials in it. Vector and embedding weaknesses earned prominence on RAG-adoption data (53% of companies now lean on retrieval over fine-tuning) <a href="#cite-7" class="cite-ref">[7]</a>. Map each entry to a control in your ISO 42001 Statement of Applicability. That mapping is what auditors read line by line.

## Citations

1. **European Commission** — Guidelines for providers of general-purpose AI models (EU AI Act timeline) — https://digital-strategy.ec.europa.eu/en/policies/guidelines-gpai-providers [accessed 2026-04-17] *(Canonical EU source. GPAI obligations in force Aug 2, 2025; full high-risk regime from Aug 2, 2026; grandfathering to Aug 2, 2027.)* { #cite-1 }
2. **Anthropic** — Anthropic achieves ISO 42001 certification for responsible AI — https://www.anthropic.com/news/anthropic-achieves-iso-42001-certification-for-responsible-ai [accessed 2026-04-17] *(Certificate effective January 6, 2025; issued by Schellman Compliance, LLC.)* { #cite-2 }
3. **Microsoft** — Azure AI Foundry Models and Security Copilot achieve ISO/IEC 42001:2023 certification — https://azure.microsoft.com/en-us/blog/microsoft-azure-ai-foundry-models-and-microsoft-security-copilot-achieve-iso-iec-420012023-certification/ [accessed 2026-04-17] *(Microsoft ISO 42001 announcement; issued by Mastermind.)* { #cite-3 }
4. **ISO** — ISO/IEC 42001:2023 AI Management system — https://www.iso.org/standard/42001 [accessed 2026-04-17] *(Canonical ISO catalogue page.)* { #cite-4 }
5. **NIST** — NIST-AI-600-1, AI RMF: Generative AI Profile — https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence [accessed 2026-04-17] *(Published July 26, 2024. 13 risks, 400+ actions.)* { #cite-5 }
6. **Cybersecurity Dive / UpGuard** — Shadow AI is widespread, and executives use it the most — https://www.cybersecuritydive.com/news/shadow-ai-employee-trust-upguard/805280/ [accessed 2026-04-17] *(UpGuard: 80%+ workers, 90%+ of security pros use unapproved AI. Gartner 2025 CISO survey: 69% of orgs.)* { #cite-6 }
7. **OWASP Gen AI Security Project** — OWASP Top 10 for LLM Applications 2025 — https://genai.owasp.org/llm-top-10/ [accessed 2026-04-17] *(2025 edition. LLM01 Prompt Injection, LLM06 Excessive Agency, LLM07 System Prompt Leakage (new).)* { #cite-7 }
8. **Credo AI** — Credo AI in Gartner Market Guide for AI Governance Platforms (2025) — https://www.credo.ai/blog/credo-ai-recognized-in-the-gartner-r-market-guide-for-ai-governance-platforms-2025 [accessed 2026-04-17] *(Gartner 2025 Market Guide; Fast Company Most Innovative 2026.)* { #cite-8 }
9. **Monitaur** — Monitaur: AI Governance for Regulated Industries — https://www.monitaur.ai/ [accessed 2026-04-17] *(Forrester Strong Performer, Q3 2025.)* { #cite-9 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/ai-governance/](https://www.exploreagentic.ai/ai-governance/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
