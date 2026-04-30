# Agentic AI, minus the agent-washing

> A field guide to autonomous agents in 2026: the five tells that separate real agentic AI from a rebadged chatbot, the runtimes that matter (Strands, Microsoft Agent Framework, LangGraph, CrewAI, OpenAI Agents SDK), and the workloads where agents earn their keep.

*Pillar · Agentic AI · 18 minutes · Updated April 17, 2026 · Author Michael Clough · Reviewed by Ryo Hang*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/agentic-ai/](https://www.exploreagentic.ai/agentic-ai/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Intro

The phrase <strong>agentic AI</strong> covers too much ground. It is a research agenda, a product category, a budget line, and (most of the time in vendor decks) last year's chatbot with a new deck cover. Gartner put numbers on the noise in June 2025: roughly 130 of the several thousand vendors claiming agentic capability ship anything that qualifies, and over 40% of agentic AI projects are forecast to be cancelled by the end of 2027 <a href="#cite-1" class="cite-ref">[1]</a>. That is the backdrop for everything below.

This pillar walks the category from the definition down to the runtimes. The useful distinction is not agent versus not-agent. It is between systems that can rewrite their own plan mid-execution and systems that cannot. That line predicts which workloads get automated, which earn their seven-figure platform bills, and which keep a human in the loop for reasons no amount of tool-use fine-tuning will fix <a href="#cite-2" class="cite-ref">[2]</a>.

## TL;DR

- An agent is a loop that observes, reasons, acts, and can replan without a human click. Everything else is a workflow with a language model attached.
- Gartner estimates only about 130 of the thousands of self-described agentic vendors are real; over 40% of agentic AI projects will be cancelled by end of 2027 on cost, unclear value, or inadequate risk controls <a href="#cite-1" class="cite-ref">[1]</a>.
- The runtime field consolidated in 2025. LangGraph 1.0 (Oct 2025), AWS Strands Agents 1.0 (July 15, 2025), Microsoft Agent Framework public preview (Oct 1, 2025) and 1.0 GA (April 3, 2026), OpenAI Agents SDK (March 2025), and CrewAI now cover roughly every serious enterprise procurement <a href="#cite-3" class="cite-ref">[3]</a><a href="#cite-4" class="cite-ref">[4]</a><a href="#cite-5" class="cite-ref">[5]</a>.
- Agents are earning their keep in bounded, evidence-rich workflows. Salesforce's own Agentforce deployment handled 1.5M+ support cases and generated $1.7M of new sales pipeline in year one <a href="#cite-6" class="cite-ref">[6]</a>.
- Guardian agents (AI systems that supervise other AI systems) will hold 10-15% of the agentic market by 2030 per Gartner <a href="#cite-7" class="cite-ref">[7]</a>. The category is real; the marketing around it is still early.

## Stats

- **~130 / thousands** — real agentic vendors vs. claimants (Gartner, Jun 2025) *(Anushree Verma, Gartner Sr Director Analyst, on agent washing. Source [1].)*
- **Jul 15, 2025** — AWS Strands Agents 1.0 shipped *(Production-ready multi-agent orchestration with Swarms, Graphs, Agents-as-Tools, Handoffs. Source [4].)*
- **Apr 3, 2026** — Microsoft Agent Framework 1.0 GA *(AutoGen + Semantic Kernel convergence; public preview was October 1, 2025. Source [5].)*
- **10-15% by 2030** — guardian-agent share of agentic market *(Gartner's June 11, 2025 prediction. Source [7].)*

## 01. What qualifies as an agent

Strip the marketing. An agent is a loop: a system that observes state, decides the next action with some freedom of choice, and can revise that decision when new information arrives. The last clause is where most <em>agentic</em> products fail the definition. They replan only when a human clicks a button, which makes them workflow engines with a nicer frontend.

Russell and Norvig formalised the taxonomy a quarter-century ago: simple reflex, model-based, goal-based, utility-based, learning. The terms survive because they still map onto shipping code. Most enterprise deployments in April 2026 are <strong>goal-based agents with a utility function bolted on</strong>: a planning model that scores candidate actions, a tool-use harness that can retry or substitute a tool, and a memory store that lets the agent carry context across turns without asking the user to paste it back.

The tell that matters in a procurement meeting is simpler than the taxonomy suggests. Ask the vendor what happens when step three of a five-step plan fails. If the answer is <em>the user gets an error</em>, it is not an agent. If the answer is <em>the planner re-scores, picks an alternative tool, and continues</em>, it is. Write that into the evaluation rubric before the demo starts.

> The question is not whether it is an agent. The question is whether it can change its mind without asking permission.

## 02. The runtime field consolidated faster than expected

As of April 2026, five runtimes cover most serious enterprise procurement. <strong>LangGraph</strong> is the LangChain team's stateful graph runtime; its 1.0 release landed in October 2025 and the LangGraph Platform is generally available for long-running deployments, with nearly 400 companies running agents on it through beta <a href="#cite-3" class="cite-ref">[3]</a>. <strong>AWS Strands Agents</strong> shipped 1.0 on July 15, 2025 with four orchestration patterns (Swarms, Graphs, Agents-as-Tools, Handoffs) and model-agnostic support across Bedrock, Anthropic, Ollama, Meta, and LiteLLM providers <a href="#cite-4" class="cite-ref">[4]</a>.

<strong>Microsoft Agent Framework</strong> entered public preview on October 1, 2025 and shipped 1.0 GA on April 3, 2026, explicitly the convergence of AutoGen and Semantic Kernel into one SDK, with A2A and MCP interop baked in <a href="#cite-5" class="cite-ref">[5]</a>. <strong>OpenAI's Agents SDK</strong> launched in March 2025 alongside the Responses API, which the company now recommends over Chat Completions for new work; the older Assistants API is deprecated with a sunset date of August 26, 2026 <a href="#cite-8" class="cite-ref">[8]</a>. <strong>CrewAI</strong> is the independent holdout; its AMP suite and Flows architecture power a claimed 1.4 billion agentic automations at customers including PwC, IBM, Capgemini, and NVIDIA <a href="#cite-9" class="cite-ref">[9]</a>.

Worth noticing: the hyperscalers converged on open-source SDKs with commercial runtimes, not closed platforms. That is a different industry structure than we had in early 2025, and it changes the exit-cost calculation on every procurement.

*Production agent runtimes, April 2026. Status and source links verified via vendor documentation.*

| Runtime | Backing org | Milestone | Strength |
| --- | --- | --- | --- |
| LangGraph | LangChain | 1.0 Oct 2025; Platform GA | Stateful graphs, durable execution, strong OSS community |
| AWS Strands Agents | AWS Open Source | 1.0 GA Jul 15, 2025 | Model-agnostic, native Bedrock AgentCore integration |
| Microsoft Agent Framework | Microsoft | Preview Oct 1, 2025; 1.0 GA Apr 3, 2026 | AutoGen + Semantic Kernel, .NET + Python, MCP/A2A |
| OpenAI Agents SDK | OpenAI | Mar 2025; Responses API default | Built-in web_search, file_search, computer_use, MCP |
| CrewAI + AMP | CrewAI Inc. | Enterprise-GA; on-prem + cloud | Role-based multi-agent, explicit enterprise tenancy |

## 03. Where agents are earning their keep

The honest list of production-grade agentic workloads is shorter than the conference circuit suggests, and long enough to justify a category. What they share: narrow schemas, structured audit trails, and an outcome metric that closes the loop without a human reviewer in the inner path.

Salesforce's own year-one Agentforce deployment is the clearest public scorecard. The service agent handled more than 1.5 million support requests (the majority resolved without humans) while the SDR agent worked over 43,000 leads and generated $1.7 million in new sales pipeline <a href="#cite-6" class="cite-ref">[6]</a>. Across the broader Agentforce base (18,000+ customers in 124 countries), Salesforce reports over $100 million in annualised cost savings and a 34% productivity lift. Those are vendor figures, but the per-workload specificity of the breakdown is the part to read.

The counter-example every 2026 steering committee cites is <strong>Klarna</strong>. The company claimed its AI assistant had done the work of 700 customer-service agents in 2024; by mid-2025 it began rehiring humans after edge cases, emotional interactions, and multi-step resolutions dragged satisfaction scores down <a href="#cite-10" class="cite-ref">[10]</a>. The lesson is not that agents fail. It is that agents fail on exactly the workloads where <em>complexity</em> is uncorrelated with <em>volume</em>. Pick the wrong workload and scale becomes the enemy.

*Agentic workloads with documented production deployment. Sources: Salesforce Agentforce metrics page, ServiceNow + Moveworks combined customer library, public case studies.*

| Workload | Why it works | Guardrail in place |
| --- | --- | --- |
| IT ticket triage and routing | Narrow schema, strong priors | Human approval on tier 2+ escalation |
| Invoice reconciliation | Structured inputs, complete audit trail | Threshold-gated autonomous close |
| Compliance document review | Repetitive, low stakes per item | Spot-check sampling at 7-10% |
| Sales-lead enrichment & routing | Tolerant of imperfect decisions | Outcome metric closes the loop |
| L1 internal HR/IT support | Bounded intents, logged ground truth | Escalation on confidence drop |

## 04. Five tells of agent washing

Gartner's Anushree Verma named the phenomenon in June 2025. <em>Agent washing</em> is workflow automation, RPA, or chat UX rebranded as agentic capability, and it is the reason Gartner forecasts 40%+ of agentic AI projects to be cancelled by the end of 2027 <a href="#cite-1" class="cite-ref">[1]</a>. The good news: the tells are observable in a forty-minute demo if you know what to look for.

We screen every vendor against the five below. Four or more and the product is a rules engine with a language model bolted on. A review team should agree on scoring before the demo; drift on the <em>replanning</em> criterion alone can turn a 2/5 into a 4/5 depending on who was watching.

- **The demo uses the same three happy-path queries every run.** — Ask for the failure-mode log. Shipped products keep one; prototypes do not.
- **No eval suite is publicly documented.** — Not a blog post. A numbered suite, versioned, with a changelog and a nightly run posted to a channel.
- **Replanning requires a human click.** — Watch the state machine. If every branch needs user intent to advance, it is a workflow with conversation on top.
- **Tool failures surface as user-facing errors.** — An agent that cannot retry, substitute, or escalate a failing tool is not reasoning about tool use in any meaningful sense.
- **Pricing is per seat, not per successful outcome.** — Outcome-priced agents exist; Agentforce is explicit about it. Seat-priced ones are usually chatbots with a project manager.

## 05. The economics, written by finance not marketing

Serious agentic deployments are not cheap. Industry commentary through 2025 put enterprise first-year program cost in the seven-to-eight-figure range, once you add platform licence, integration partner, and the redirected staff time buyers routinely forget. The programs that survive into year two share one trait. They measure savings against one specific instrumented workflow and report the number to the CFO, on time, without adjustment. The ones that do not, do not renew.

Productivity-minute arithmetic (<em>30 minutes saved per employee per week</em>) is how the first wave embarrassed itself. The March 10, 2025 ServiceNow acquisition of Moveworks at $2.85 billion is a useful data point on what the market pays for a working agentic tier at the employee layer; the deal closed on December 15, 2025 <a href="#cite-11" class="cite-ref">[11]</a>. Futurum's 1H 2026 enterprise-AI reports and CXToday's Agentforce coverage both show the vocabulary shift: <em>direct financial impact</em> is displacing <em>productivity gains</em> in analyst write-ups, which mirrors what buyers now demand in procurement.

Our editorial position, for the record: measure one workflow, instrument the before and after, and refuse to scale the program until you can show a defensible dollar number on the first. Teams that skip the measurement step do not fail at procurement. They fail at renewal.

> **Further reading on this site**: The [AI-agent ROI playbook](../playbooks/ai-agent-roi.md) lays out the CFO-ready framework with a spreadsheet and three questions finance will ask at first review. Agent observability, the category Datadog and LangSmith are building, has its own [glossary entry](../glossary/agent-observability.md). For the plumbing most of these agents rely on, see our [MCP pillar](mcp.md).

## 06. Observability is the part that keeps them running

A production agent without observability is a prototype that pages on-call. The category matured fast in 2025. LangSmith added end-to-end native OpenTelemetry support to its SDK, letting teams pipe agent traces into Datadog, Grafana, Jaeger, or any OTel-compliant backend <a href="#cite-12" class="cite-ref">[12]</a>. AgentCore on AWS and Foundry observability on Azure ship with the same OTel spec so traces cross cloud boundaries without translation.

The specific signals that matter are unglamorous. Per-tool latency and per-tool error rate tell you whether the tool harness is the bottleneck. Per-step outcome flags tell you whether the planner is choosing good actions. Replan counts per conversation tell you whether the agent is thrashing. If a vendor cannot show you all three on a default dashboard, the observability story is a slide deck.

> **Editorial judgment**: Agent observability is where the next wave of procurement pain is hiding. The platform teams we have seen ship cleanly in 2026 all negotiated observability into the initial contract rather than retrofitting it in year two. That one clause has a bigger effect on year-two renewal economics than any other single line item: more than licence discount, more than integration credits. Put it in the RFP.

## FAQ

**Q: What is agentic AI, in one paragraph?**

An agent is a loop. Observe, reason, act, revise the plan mid-run when new information shows up. That last clause is where most products fail the definition. The procurement test is shorter than any taxonomy: can it replan without a human click? Gartner ran the numbers in June 2025 and they were brutal. Roughly 130 vendors meet the bar out of the thousands who claim it <a href="#cite-1" class="cite-ref">[1]</a>. Over 40% of agentic projects will be cancelled by end of 2027, usually on cost, unclear value, or risk controls nobody thought about until the security review (the part buyers forget). Everything else is a workflow with a language model glued on.

**Q: Which agent runtimes should an enterprise team evaluate in 2026?**

Five names cover most procurement. LangGraph. AWS Strands Agents. Microsoft Agent Framework. OpenAI Agents SDK. CrewAI. LangGraph 1.0 shipped in October 2025 with the Platform GA behind it <a href="#cite-3" class="cite-ref">[3]</a>. Strands hit 1.0 on July 15, 2025 with four orchestration patterns (Swarms, Graphs, Agents-as-Tools, Handoffs) <a href="#cite-4" class="cite-ref">[4]</a>. Microsoft Agent Framework went to public preview October 1, 2025 and reached 1.0 GA on April 3, 2026; the convergence of AutoGen and Semantic Kernel into one SDK <a href="#cite-5" class="cite-ref">[5]</a>. OpenAI's Agents SDK launched March 2025 alongside the Responses API, now the default for new work <a href="#cite-8" class="cite-ref">[8]</a>. CrewAI is the independent holdout, still the procurement winner where role-based multi-agent on-prem is the requirement <a href="#cite-9" class="cite-ref">[9]</a>. The pattern we see most: one framework per cloud, federate the rest via MCP.

**Q: What is agent washing and how do I spot it?**

Agent washing is RPA with a chat window. Or workflow automation with LLM garnish. Gartner's Anushree Verma named it in mid-2025 and the term stuck <a href="#cite-1" class="cite-ref">[1]</a>. Five tells, all watchable inside a forty-minute demo. The demo reuses three happy-path queries every run. No public eval suite exists (a blog post does not count). Replanning needs a human click. Tool failures come back as user-facing errors. Pricing is per seat, not per outcome. Four or more and the product is a rules engine with a language model bolted on the front. Score the rubric before the demo starts; drift on the <em>replanning</em> criterion is the part that flips a 2/5 into a 4/5 depending on who was watching.

**Q: Where do agents earn their keep?**

Bounded workflows. Narrow schemas. Logged ground truth. IT ticket triage. Invoice reconciliation. Compliance document review. Sales-lead enrichment. L1 internal HR/IT support. Those are the ones paying back. Salesforce's year-one Agentforce run is the clearest public scorecard we have: 1.5M+ support cases handled (most without humans), 43,000+ leads worked, $1.7M of new sales pipeline <a href="#cite-6" class="cite-ref">[6]</a>. Klarna is the counter-example every 2026 steering committee cites. Volume without complexity is automatable. Complexity without volume is not. Klarna mis-read the line and started rehiring humans by mid-2025 <a href="#cite-10" class="cite-ref">[10]</a>, a caution the buyers who picked the wrong workload now rehearse on every slide.

**Q: What are guardian agents, and are they real?**

A guardian watches another agent and can veto it. That is the one-line version. Gartner projects the category will hold 10 to 15% of the agentic market by 2030 <a href="#cite-7" class="cite-ref">[7]</a>. Products shipping today are narrow, which is fine. Prompt-injection guardrails. Output-schema validators. Policy-check agents that refuse disallowed tool combinations. Evaluate one by asking what happens when the guardian gets it wrong. A guardian with only false positives is an annoyance. A guardian with only false negatives is a liability. The procurement question buyers forget to ask, every time.

**Q: How does agentic AI relate to MCP and RAG?**

Three layers of the same stack, not competitors. MCP is the wire protocol an agent uses to call tools and fetch context: the plumbing. See our [MCP pillar](mcp.md). RAG is a retrieval pattern for grounding a model in a document corpus. <em>Agentic RAG</em> places that retrieval inside a planning loop so the agent decides what to fetch next — see the [agentic RAG glossary entry](../glossary/agentic-rag.md). Most production systems we review run all three. An agent on top. MCP underneath. RAG as one of several tools the agent can reach for. The part that surprises first-time buyers: the architecture converges within a year of serious deployment. Implementation trade-offs live in our [MCP vs RAG comparison](../comparisons/mcp-vs-rag.md).

## Citations

1. **Gartner** — Gartner Predicts Over 40% of Agentic AI Projects Will Be Canceled by End of 2027 — https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027 [accessed 2026-04-17] *(June 25, 2025 press release. Quotes Anushree Verma, Sr Director Analyst. Source for the 40% projection and context on agent washing and vendor count.)* { #cite-1 }
2. **Russell & Norvig** — Artificial Intelligence: A Modern Approach (agent taxonomy) — https://aima.cs.berkeley.edu/ [accessed 2026-04-17] *(Canonical textbook reference for simple-reflex / model-based / goal-based / utility-based / learning agent taxonomy.)* { #cite-2 }
3. **LangChain** — LangGraph Platform is now Generally Available — https://www.langchain.com/blog/langgraph-platform-ga [accessed 2026-04-17] *(LangGraph Platform GA announcement. Nearly 400 companies deployed agents during beta; source for durable execution and production posture.)* { #cite-3 }
4. **AWS Open Source Blog** — Introducing Strands Agents 1.0: Production-Ready Multi-Agent Orchestration Made Simple — https://aws.amazon.com/blogs/opensource/introducing-strands-agents-1-0-production-ready-multi-agent-orchestration-made-simple/ [accessed 2026-04-17] *(July 15, 2025 Strands Agents 1.0 release. Four orchestration patterns (Swarms, Graphs, Agents-as-Tools, Handoffs) and contributing partners (Accenture, Anthropic, Langfuse, mem0.ai, Meta, PwC, Ragas.io, Tavily).)* { #cite-4 }
5. **Microsoft** — Introducing Microsoft Agent Framework — https://azure.microsoft.com/en-us/blog/introducing-microsoft-agent-framework/ [accessed 2026-04-17] *(October 1, 2025 public preview announcement; AutoGen + Semantic Kernel convergence. 1.0 GA followed on April 3, 2026.)* { #cite-5 }
6. **Salesforce** — From Pilot to Playbook: What We Learned from Our First Year Using Agentforce — https://www.salesforce.com/news/stories/first-year-agentforce-customer-zero/ [accessed 2026-04-17] *(Customer Zero year-one numbers: 1.5M+ support cases handled, 43,000+ leads worked, $1.7M new sales pipeline.)* { #cite-6 }
7. **Gartner** — Gartner Predicts that Guardian Agents will Capture 10-15% of the Agentic AI Market by 2030 — https://www.gartner.com/en/newsroom/press-releases/2025-06-11-gartner-predicts-that-guardian-agents-will-capture-10-15-percent-of-the-agentic-ai-market-by-2030 [accessed 2026-04-17] *(June 11, 2025 Gartner press release. Definition of guardian agents and market-share projection for 2030.)* { #cite-7 }
8. **OpenAI** — New tools for building agents (Responses API and Agents SDK) — https://openai.com/index/new-tools-for-building-agents/ [accessed 2026-04-17] *(March 2025 launch of Responses API, Agents SDK, and built-in tools (web_search, file_search, computer_use). Assistants API deprecation sunset: August 26, 2026.)* { #cite-8 }
9. **CrewAI** — CrewAI Enterprise and the AMP suite — https://crewai.com/ [accessed 2026-04-17] *(CrewAI homepage; referenced for claimed 1.4B agentic automations and enterprise customer list (PwC, IBM, Capgemini, NVIDIA).)* { #cite-9 }
10. **CNBC** — Klarna CEO says AI helped company shrink workforce by 40% — https://www.cnbc.com/2025/05/14/klarna-ceo-says-ai-helped-company-shrink-workforce-by-40percent.html [accessed 2026-04-17] *(May 14, 2025 CNBC coverage of Klarna workforce reduction; subsequent reporting on Klarna's pilot to rehire human agents for complex interactions.)* { #cite-10 }
11. **TechCrunch** — ServiceNow buys Moveworks for $2.85B to grow its AI portfolio — https://techcrunch.com/2025/03/10/servicenow-buys-moveworks-for-2-85b-to-grow-its-ai-portfolio/ [accessed 2026-04-17] *(March 10, 2025 acquisition announcement. Closed December 15, 2025 per Moveworks and ServiceNow press releases.)* { #cite-11 }
12. **LangChain** — Introducing End-to-End OpenTelemetry Support in LangSmith — https://blog.langchain.com/end-to-end-opentelemetry-langsmith/ [accessed 2026-04-17] *(LangSmith native OTel support; interoperates with Datadog, Grafana, Jaeger, and other OTel-compliant backends.)* { #cite-12 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/agentic-ai/](https://www.exploreagentic.ai/agentic-ai/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
