# NIST AI Risk Management Framework

*Also known as: NIST AI RMF, AI RMF 1.0 · 7 min · Updated April 17, 2026 · Author Merve Tengiz · Reviewed by Gloria Qian Zhang*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/glossary/nist-ai-rmf/](https://www.exploreagentic.ai/glossary/nist-ai-rmf/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Definition

The <strong>NIST AI Risk Management Framework</strong> (AI RMF 1.0) is a voluntary risk-management framework published by the US National Institute of Standards and Technology on January 26, 2023, as NIST AI 100-1 <a href="#cite-1" class="cite-ref">[1]</a>. It gives organizations a common vocabulary and a four-function working model (GOVERN, MAP, MEASURE, MANAGE) for identifying and managing AI risk across the lifecycle. Not certifiable. But federal agencies, critical-infrastructure operators, and most US-based enterprises treat it as the default reference anyway. Which is where the teeth actually are.

## The four functions, plainly

The framework is organised around four functions <a href="#cite-2" class="cite-ref">[2]</a>. They are not sequential; they run in parallel across the AI lifecycle, and the standard explicitly describes GOVERN as cross-cutting, operating alongside the other three at all times.

1. **GOVERN** — Cultivate a risk culture inside the organization. Establish policies, accountability structures, roles, and a process for mapping, measuring, and managing AI risk. Applies across the whole lifecycle.
2. **MAP** — Identify the context and categorise the risks of an AI system: what it is, who it affects, where it is deployed, what could go wrong. MAP comes first in the workflow but is not done once.
3. **MEASURE** — Analyse, assess, benchmark, and monitor AI risks and their impacts. Quantitative where possible, qualitative where necessary.
4. **MANAGE** — Allocate resources, prioritise risks, and respond (accept, transfer, avoid, or mitigate). Includes incident response and the communication pathways back to GOVERN.

## The documents, in order

*The NIST AI RMF document family, as of April 2026*

| Date | Document |
| --- | --- |
| January 26, 2023 | AI RMF 1.0 (NIST AI 100-1): the foundational framework. Introduced the four functions and the voluntary adoption model [1]. |
| July 26, 2024 | NIST AI 600-1, the Generative AI Profile. Applied AI RMF to generative models, flagging risks (hallucination, data leakage, CBRN, dangerous capabilities) that the 2023 base document did not name explicitly. Focused on four priority considerations: Governance, Content Provenance, Pre-deployment Testing, and Incident Disclosure [3]. |
| December 2025 | Draft NIST Cybersecurity Framework Profile for AI (NIST IR 8596 iprd): cross-walks AI RMF to NIST CSF 2.0 [5]. |
| April 7, 2026 | Concept note for an AI RMF Profile on Trustworthy AI in Critical Infrastructure. Addresses the 16 critical-infrastructure sectors (energy, water, health care, finance, transportation, etc.) and aligns AI risk with OT/ICS resilience and legacy-system constraints [4]. |

## How it relates to ISO/IEC 42001

AI RMF and ISO 42001 are complementary, not alternatives. ISO 42001 defines the management system: what structure, roles, and processes your organization needs. AI RMF defines the risk work you do inside the system, namely how you actually map, measure, and manage specific AI risks.

In practice, organizations running both produce a combined control-mapping document. It takes about two days once the ISO 42001 scope is set. Regulators and auditors increasingly ask for both citations side by side.

## How teams actually use it

- As a vocabulary. The four-function language lets governance, legal, engineering, and procurement talk about the same risks without re-inventing taxonomy every meeting.
- As an audit anchor. Even when certification is not the goal, pointing to AI RMF controls is the cheapest way to make an external auditor comfortable.
- As a procurement filter. Public-sector and federally-adjacent buyers increasingly require vendors to reference AI RMF functions in their risk documentation.
- As an onboarding curriculum. New governance hires can learn the AI RMF functions in a week and immediately contribute to a risk map.

> **If you are starting a governance program**: Read the AI RMF 1.0 core document first (about 48 pages, free). Then read the Generative AI Profile (NIST AI 600-1) if you ship generative features. Then, if relevant, the Critical Infrastructure concept note. ISO 42001 is the next layer; do it after you have a working AI RMF mapping, not before.

## See Also

- [ISO/IEC 42001](iso-42001.md)
- [AI Governance pillar](../pillars/ai-governance.md)
- [Shadow AI](shadow-ai.md)
- [Agent observability](agent-observability.md)

## FAQ

**Q: What is the NIST AI Risk Management Framework?**

A voluntary framework, published as NIST AI 100-1 on January 26, 2023. It gives organizations a common vocabulary and a four-function working model (GOVERN, MAP, MEASURE, MANAGE) for identifying and managing AI risk across the lifecycle. Not certifiable. But federal agencies, critical-infrastructure operators, and most US-based enterprises treat it as the default reference. Voluntary on paper. Defaults drive procurement conversations in practice.

**Q: What are the four functions of the NIST AI RMF?**

Four functions. GOVERN: cross-cutting risk culture, policies, accountability. MAP: identify context, categorise risks. MEASURE: analyse, assess, benchmark, monitor. MANAGE: allocate resources, prioritise, respond (accept, transfer, avoid, mitigate). Not sequential. They run in parallel across the AI lifecycle, and GOVERN operates alongside the other three at all times. That last point is the one teams miss on their first pass.

**Q: What is the NIST AI 600-1 Generative AI Profile?**

NIST AI 600-1, released July 26, 2024. A cross-sectoral profile of the AI RMF for generative AI, pursuant to Executive Order 14110. It adapts the four functions to GAI-specific risks the 2023 base document did not name: hallucination, data leakage, CBRN concerns, disinformation. Four priority considerations. Governance. Content Provenance. Pre-deployment Testing. Incident Disclosure. The last one is the one that makes legal teams read the document twice.

**Q: Is there a NIST AI RMF profile for critical infrastructure?**

Yes, in progress. NIST released a concept note on April 7, 2026 for an AI RMF Profile on Trustworthy AI in Critical Infrastructure. Scope is big. All 16 critical-infrastructure sectors: energy, water, health care, finance, transportation, and the rest. The profile will tailor AI risk management to the operational realities of those sectors. Legacy systems. Physically distributed assets. Resourcing constraints. NIST is forming a Community of Interest to drive the work. If your organization touches any of the 16 sectors, get involved early; the authors listen.

**Q: Is NIST AI RMF certifiable?**

No. Voluntary framework, no certification scheme, no auditor, no certificate. The closest certifiable standard is ISO/IEC 42001. Most organizations running both use them in the same sentence: ISO 42001 provides the certifiable management system, NIST AI RMF provides the risk-assessment work that happens inside it. NIST publishes an explicit ISO 42001 crosswalk to support the pairing. About two days of work to map the two once ISO scope is set.

**Q: How is NIST AI RMF used in federal procurement?**

Heavily. US federal procurement and critical-infrastructure vendor evaluations increasingly require vendors to reference AI RMF functions in their risk documentation. No AI RMF-aligned governance program, no federal contract; that's the short version. The friction shows up earliest in public-sector and federally-adjacent enterprise sales. By the time it reaches the CFO it is a line item in sales velocity, not a policy document.

## Citations

1. **NIST** — AI RMF 1.0: Artificial Intelligence Risk Management Framework (NIST AI 100-1) — https://nvlpubs.nist.gov/nistpubs/ai/nist.ai.100-1.pdf [accessed 2026-04-17] *(Canonical January 26, 2023 PDF. 48 pages; defines the four-function core.)* { #cite-1 }
2. **NIST** — AI Risk Management Framework — https://www.nist.gov/itl/ai-risk-management-framework [accessed 2026-04-17] *(NIST AI RMF landing page; links to playbook, profiles, and ISO 42001 crosswalk.)* { #cite-2 }
3. **NIST** — NIST AI 600-1: Generative Artificial Intelligence Profile — https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf [accessed 2026-04-17] *(July 26, 2024 release. Pursuant to EO 14110; four priority considerations: Governance, Content Provenance, Pre-deployment Testing, Incident Disclosure.)* { #cite-3 }
4. **NIST** — Concept Note: AI RMF Profile on Trustworthy AI in Critical Infrastructure — https://www.nist.gov/programs-projects/concept-note-ai-rmf-profile-trustworthy-ai-critical-infrastructure [accessed 2026-04-17] *(April 7, 2026 concept note; invites feedback from critical-infrastructure community.)* { #cite-4 }
5. **NIST** — Draft NIST Guidelines Rethink Cybersecurity for the AI Era (IR 8596) — https://www.nist.gov/news-events/news/2025/12/draft-nist-guidelines-rethink-cybersecurity-ai-era [accessed 2026-04-17] *(December 2025 draft Cybersecurity Framework Profile for AI; crosswalks AI RMF to CSF 2.0.)* { #cite-5 }
6. **NIST** — AI RMF Playbook — https://www.nist.gov/itl/ai-risk-management-framework/nist-ai-rmf-playbook [accessed 2026-04-17] *(Living document with suggested actions for each AI RMF subcategory.)* { #cite-6 }
7. **Wiley Rein LLP** — NIST Releases AI Risk Management Framework, Expected to Be a Critical Tool for Trustworthy AI Deployment — https://www.wileyconnect.com/nist-releases-ai-risk-management-framework-expected-to-be-a-critical-tool-for-trustworthy-ai-deployment [accessed 2026-04-17] *(Legal analysis of AI RMF 1.0 on publication; context for federal procurement impact.)* { #cite-7 }
8. **Industrial Cyber** — NIST develops Trustworthy AI in Critical Infrastructure Profile — https://industrialcyber.co/nist/nist-develops-trustworthy-ai-in-critical-infrastructure-profile-to-align-risk-resilience-and-infrastructure-security/ [accessed 2026-04-17] *(Coverage of the April 2026 critical-infrastructure profile concept note.)* { #cite-8 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/glossary/nist-ai-rmf/](https://www.exploreagentic.ai/glossary/nist-ai-rmf/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
