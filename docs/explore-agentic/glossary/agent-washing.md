# Agent washing

*Also known as: AI agent washing · 7 min · Updated April 17, 2026 · Author Michael Clough · Reviewed by Ryo Hang*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/glossary/agent-washing/](https://www.exploreagentic.ai/glossary/agent-washing/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Definition

<strong>Agent washing</strong> is Gartner's term for products and features marketed as "agentic AI" that do not meet the definitional threshold of an agent, namely systems that observe, reason, and act with meaningful autonomy and the ability to revise their own plan. Chatbots with workflow buttons, RPA with a language-model UI, and fixed-pipeline assistants all get relabeled as agents in response to customer demand. On June 25, 2025, Gartner published a press release forecasting that over 40 percent of agentic AI projects will be cancelled by end of 2027, and estimated that only about 130 of the thousands of vendors claiming to sell agentic AI are building real agents <a href="#cite-1" class="cite-ref">[1]</a>.

## Where the term came from

Gartner coined "agent washing" in 2024, in the same way it earlier coined "cloud washing" and "AI washing": to describe the pattern of vendors rebranding existing products (AI assistants, robotic process automation (RPA), chatbots) to match whichever category currently commands procurement budget. The pattern is predictable; the damage is specific. Buyers evaluate products on the assumption that the category word means something, and when it does not, the procurement conversation is poisoned for everyone.

On June 25, 2025, Gartner published a press release forecasting that over 40% of agentic AI projects will be cancelled by end of 2027, citing escalating costs, unclear business value, and inadequate risk controls <a href="#cite-1" class="cite-ref">[1]</a>. Analyst Anushree Verma is quoted in the release: "Most agentic AI projects right now are early stage experiments or proof of concepts that are mostly driven by hype and are often misapplied." A January 2025 Gartner poll of 3,412 webinar attendees found only 19% of organizations had made significant investments in agentic AI; 42% described themselves as conservative, and 31% were in wait-and-see mode <a href="#cite-1" class="cite-ref">[1]</a>.

The pattern was reinforced by Gartner's April 2026 Hype Cycle for Agentic AI, which placed AI agent development platforms at the Peak of Inflated Expectations with a 2–5 year time to mainstream adoption <a href="#cite-2" class="cite-ref">[2]</a>. GenAI itself entered the Trough of Disillusionment in the same cycle.

> The question isn't whether it's an agent. The question is whether it can change its mind without asking permission.

## How to spot it in a demo

Five tells that consistently separate shipping agent products from rules engines with a language-model wrapper. Four or more firing together, in our experience, is decisive.

1. **The demo runs the same three happy-path queries every time** — Ask for the failure-mode log. Shipped products keep one; prototypes do not. If the seller cannot produce a list of the last twenty things that went wrong and how the agent handled them, it is not a shipping product.
2. **No public eval suite exists** — Not a blog post. A numbered, versioned suite with a changelog and a nightly run. If the answer is "our customers do that internally," the vendor has no quality signal of their own.
3. **Replanning requires a human click** — Watch the state machine during the demo. If every branch advances only when the user clicks, it is a workflow with natural-language input, not an agent. Real agents reconsider mid-execution when new information arrives.
4. **Tool failures become user errors** — Ask what happens when a tool returns a 500, times out, or produces an unparseable response. An agent should retry, substitute, or escalate. A washed product shows the stack trace to the user.
5. **Pricing is per seat, not per outcome** — Outcome-priced agents exist: you pay per resolved ticket, per completed workflow, per successful transaction. Seat-priced agents are usually chatbots with a project manager. It is not dispositive, but it is a tell.

## Why buyers keep falling for it

The obvious explanation, procurement teams chasing keywords, is only half the story. The other half is that the category genuinely lacks a clear definitional line. Russell and Norvig's taxonomy (simple reflex, model-based, goal-based, utility-based, learning agents), which is more than twenty years old, remains the clearest reference <a href="#cite-3" class="cite-ref">[3]</a>. Most vendors positioning themselves as "agentic" are building goal-based agents with a utility function bolted on, but the marketing uses the word "autonomous" as though it meant something stronger.

The fix, as a buyer, is to stop arguing about whether something is an agent and start asking whether it does the two things that matter: can it change its plan mid-execution without being told to, and does it own the outcome metric rather than the click-through metric. If both answers are yes, the semantic argument is moot. If either is no, you are buying a workflow. IBM's 2025 Cost of a Data Breach report is a useful pressure test: the same governance gaps that enable shadow AI enable agent washing, because neither category gets scrutinised when leadership is chasing the buzzword <a href="#cite-4" class="cite-ref">[4]</a>.

## Further reading on this site

- The Agentic AI pillar (/agentic-ai) expands the five tells into a full argument, including what the honest list of production agentic workloads looks like in April 2026.
- Our Jarvis-inclusive comparisons flag where any product, including our own, does not meet the agent threshold for a given workflow.

## See Also

- [Agentic AI pillar](../pillars/agentic-ai.md)
- [Guardian agent](guardian-agent.md)
- [Agent observability](agent-observability.md)
- [Shadow AI](shadow-ai.md)

## FAQ

**Q: What is agent washing?**

Gartner's term for products marketed as "agentic AI" that do not meet the threshold of an agent. The threshold is short: a system that observes, reasons, and acts with autonomy, and can revise its own plan. Washed products usually cannot do the last bit. Chatbots with workflow buttons. RPA with a language-model UI. Fixed-pipeline assistants that dress up natural-language input as planning. Gartner coined the term in 2024, in the same way it earlier coined "cloud washing" and "AI washing." Same pattern, different buzzword.

**Q: What percentage of agentic AI projects will fail?**

Over 40%. Gartner's June 25, 2025 forecast has that many agentic AI projects canceled by end of 2027. The reasons are the usual three: escalating costs, unclear business value, inadequate risk controls. Same release added a number that changed how buyers shortlist. Of the thousands of vendors positioning themselves in agentic AI, Gartner estimated only about 130 are real. A 98% noise-to-signal ratio, give or take, which is the reason procurement teams keep getting burned.

**Q: How do I spot agent washing in a vendor demo?**

Five tells. Watch for them in the demo. The demo runs the same three happy-path queries every time and has no failure-mode log. No public eval suite exists, which means the vendor has no quality signal of their own. Replanning requires a human click, which means it's a workflow with language-model input, not an agent. Tool failures surface as stack traces to the user, not as agent retries. Pricing is per seat, not per outcome. Four or more firing together is decisive in our experience.

**Q: Is every 'AI agent' product agent-washed?**

No. Real agents exist and ship. The cleanest screen is two questions. Can the product change its plan mid-execution without being told to? And does the vendor price on outcomes, meaning resolved tickets or completed workflows, rather than seats? Customer support, ticket triage, reconciliation, structured document review: vendors in those categories tend to publish the outcome numbers. Agent-washed products, by and large, cannot. That is the whole sorting test.

**Q: How does agent washing relate to hype-cycle dynamics?**

Tightly. Gartner's April 2026 Hype Cycle for Agentic AI put AI agent development platforms at the Peak of Inflated Expectations. GenAI itself had already slid into the Trough of Disillusionment. Those two positions together make agent washing inevitable. Procurement budgets chase the peak-of-hype category. Vendors relabel existing products to match. It is the same play that produced cloud washing in 2010 and AI washing in 2023, and it will produce the same outcome: a lot of cancelled contracts in 2027.

**Q: What is the Russell and Norvig agent taxonomy?**

The five-tier taxonomy from Russell and Norvig's 'Artificial Intelligence: A Modern Approach', Chapter 2. Five categories. Simple reflex. Model-based reflex. Goal-based. Utility-based. Learning agents. Most products positioned as 'agentic' in 2026 are goal-based agents with a utility function bolted on, which is fine. The problem is the marketing word 'autonomous,' which is stronger than the engineering supports. The taxonomy is more than twenty years old and still the clearest reference.

## Citations

1. **Gartner** — Gartner Predicts Over 40% of Agentic AI Projects Will Be Canceled by End of 2027 — https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027 [accessed 2026-04-17] *(June 25, 2025 press release; identifies agent washing and the ~130 real vendors; January 2025 3,412-respondent poll data.)* { #cite-1 }
2. **Gartner** — What the 2026 Hype Cycle for Agentic AI Reveals — https://www.gartner.com/en/articles/hype-cycle-for-agentic-ai [accessed 2026-04-17] *(April 2026 Hype Cycle; AI agent development platforms at Peak of Inflated Expectations; GenAI in Trough of Disillusionment.)* { #cite-2 }
3. **Pearson** — Russell & Norvig: Artificial Intelligence: A Modern Approach — https://aima.cs.berkeley.edu/ [accessed 2026-04-17] *(Canonical textbook; Chapter 2 agent taxonomy.)* { #cite-3 }
4. **IBM** — Cost of a Data Breach Report 2025 — https://www.ibm.com/reports/data-breach [accessed 2026-04-17] *(97% of shadow-AI-linked breaches lacked AI access controls, the same governance gap that enables agent washing.)* { #cite-4 }
5. **BigDATAwire** — Gartner Predicts Over 40% of Agentic AI Projects Will Be Canceled by End of 2027 — https://www.hpcwire.com/bigdatawire/this-just-in/gartner-predicts-over-40-of-agentic-ai-projects-will-be-canceled-by-end-of-2027/ [accessed 2026-04-17] *(Industry reporting summary of the Gartner forecast.)* { #cite-5 }
6. **RCR Wireless** — Gartner: More than 40% of agentic AI projects will fail by 2027 — https://www.rcrwireless.com/20250627/business/agentic-ai-gartner [accessed 2026-04-17] *(Second industry report on the June 2025 Gartner press release.)* { #cite-6 }
7. **Outreach** — Agent washing exposed: Why 40% of AI projects fail in 2025 — https://www.outreach.io/resources/blog/agent-washing-ai-projects-fail-guide [accessed 2026-04-17] *(Vendor analysis of agent-washing patterns.)* { #cite-7 }
8. **XMPRO** — Gartner's 40% Agentic AI Failure Prediction Exposes a Core Architecture Problem — https://xmpro.com/gartners-40-agentic-ai-failure-prediction-exposes-a-core-architecture-problem/ [accessed 2026-04-17] *(Architecture-centric analysis of agent-washing failure modes.)* { #cite-8 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/glossary/agent-washing/](https://www.exploreagentic.ai/glossary/agent-washing/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
