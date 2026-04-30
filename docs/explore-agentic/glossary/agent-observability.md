# Agent observability

*Also known as: AI agent tracing, LLM observability · 7 min · Updated April 17, 2026 · Author Kelvin Yu · Reviewed by Elias Saljuki*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/glossary/agent-observability/](https://www.exploreagentic.ai/glossary/agent-observability/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Definition

<strong>Agent observability</strong> makes an agentic AI system legible after the fact. State, decisions, tool calls: all captured, all replayable, all auditable. The vocabulary is borrowed from distributed-systems observability (traces, spans, W3C trace context), then bent around the non-determinism of language-model calls. That bending is the part that breaks every old observability assumption at once. OpenTelemetry's GenAI Semantic Conventions are the emerging open standard, with the AI agent convention based on Google's AI agent white paper <a href="#cite-1" class="cite-ref">[1]</a><a href="#cite-2" class="cite-ref">[2]</a>.

## What counts as observable

The OpenTelemetry GenAI Semantic Conventions define a minimum span shape for any instrumented agent: model name, request and response tokens, tool calls, latency, error state, and correlation IDs from W3C Trace Context <a href="#cite-3" class="cite-ref">[3]</a>. Agent-specific conventions (tasks, actions, memory, agent-to-agent communication) were drafted in 2025 and move through experimental status in 2026 <a href="#cite-2" class="cite-ref">[2]</a>. Framework conventions for CrewAI, AutoGen, LangGraph, and Semantic Kernel are in active development.

- Every input the agent saw, including retrieved context and tool outputs.
- Every decision the agent made, with the reasoning trace where available.
- Every tool call: name, parameters, result, latency, error state.
- The final output, tagged with a conversation ID, a user ID, and a business-outcome reference.
- A W3C trace context that binds multi-agent spans into a single observable workflow. This is the primitive that multi-agent nesting still breaks for most vendors <a href="#cite-4" class="cite-ref">[4]</a>.

## The vendor landscape in April 2026

Three platforms pulled away from the pack in 2026. LangSmith, the LangChain team's official platform. Arize AI, with the open-source Phoenix next to it. Braintrust, evaluation-first <a href="#cite-5" class="cite-ref">[5]</a>. Datadog added native OpenTelemetry GenAI Semantic Convention support in v1.37 and pulled LLM workloads into its existing APM footprint without asking <a href="#cite-6" class="cite-ref">[6]</a>. AWS Bedrock AgentCore Observability launched in 2025. Telemetry out in OpenTelemetry-compatible format, integrable with CloudWatch, Datadog, LangSmith, and Langfuse <a href="#cite-7" class="cite-ref">[7]</a>.

The honest read in April 2026. LangSmith wins if you live inside LangChain and LangGraph. Arize Phoenix wins if OpenTelemetry neutrality matters or your team has platform engineers to spare. Braintrust wins if evaluations should drive deployment. Nobody has obviously won across every use case. The per-span pricing unit is still unsettled, which means the category keeps moving for another two quarters.

The term is often misused. Vendors selling "AI observability" that only log prompt/response pairs are shipping log search, which is a different product. The sorting test is short. A real agent observability product emits OpenTelemetry-compatible spans with the GenAI conventions applied. It supports multi-agent trace nesting. And it lets you replay a production failure against a different model. Three of three, or call it something else.

## The metrics that matter

- Tool-call success rate, per tool, over time. Anything below 95% for a tool in production is a bug.
- Latency by phase: retrieval, reasoning, tool, generation. AgentCore Observability breaks these out natively; most vendors follow.
- Eval-suite pass rate, trended against every model and prompt change. Braintrust's trace-to-test pipeline is the clearest implementation of this pattern in 2026.
- User-reported failure rate, tied back to trace IDs. Without this linkage, support tickets are floating strings.
- Token usage per session (the unit that shows up on the cloud bill). AgentCore reports token usage, latency, session duration, and error rates as first-class metrics <a href="#cite-7" class="cite-ref">[7]</a>.
- Agent quality scores: correctness, helpfulness, safety, goal-success rate. Continuous evaluation against these is what separates observability from log collection.

## Why OpenTelemetry matters

Before OpenTelemetry GenAI conventions existed, every vendor defined its own span shape. Cross-vendor migration was expensive. Multi-vendor deployments were incoherent. The semantic conventions fix both problems at once: a CrewAI agent instrumented with OTel emits spans that Datadog, LangSmith, or Phoenix ingest identically. The AI Agent Application Conventions, experimental as of March 2026, extend this to agent tasks, memory, and agent-to-agent traffic <a href="#cite-2" class="cite-ref">[2]</a>.

For procurement in 2026, the single most useful question is one sentence long: does your vendor emit and ingest OTel GenAI Semantic Conventions natively? Yes is the word you want. Datadog. Phoenix. AgentCore. Langfuse. Safer bets than any vendor that answers "we have our own format and an exporter," which is the sentence that shows up right before a migration bill.

> **Further reading**: Observability does not work without governance. The AI Governance pillar at /ai-governance covers the policy side; the MCP Gateway entry at /glossary/mcp-gateway covers the tool-level observability questions that gateway products answer for the transport layer.

## See Also

- [Model Context Protocol](model-context-protocol.md)
- [MCP Gateway](mcp-gateway.md)
- [Agentic RAG](agentic-rag.md)
- [AI Governance pillar](../pillars/ai-governance.md)

## FAQ

**Q: What is agent observability?**

How you make an agent system legible after the fact. Instrument everything. Capture inputs, decisions, tool calls, outputs. The whole trace has to be inspectable and replayable. The vocabulary comes straight from distributed-systems observability (traces, spans, W3C Trace Context), then gets bent around the fact that language-model calls are not deterministic. That non-determinism breaks a lot of assumptions the old observability stacks baked in. The open standard is OpenTelemetry's GenAI Semantic Conventions. Everything else is a dialect.

**Q: How is agent observability different from LLM monitoring?**

LLM monitoring is a subset. Usually just prompts, responses, and latency for one model call, which is useful as far as it goes. Agent observability goes further. The full multi-step trajectory gets captured. Tool calls. Retrievals. Sub-agents. Reasoning traces. All as nested spans bound together by a shared trace context. The distinction matters because a single-call view cannot explain a multi-agent failure mode. Every team learns this on their first real production incident. Every single one.

**Q: What are OpenTelemetry GenAI Semantic Conventions?**

The open-standard schema for instrumenting generative-AI applications. Span attributes, metrics, event shapes: all defined, all ingestable by any compliant vendor. Agent-specific conventions hit experimental status in March 2026. Framework conventions are in active development for CrewAI, AutoGen, LangGraph, and Semantic Kernel. The short version: emit once, ingest anywhere, and the procurement question about vendor lock-in gets a lot quieter.

**Q: Which agent observability platform should I use?**

Three platforms pulled away from the pack in April 2026. LangSmith. Arize AI (with open-source Phoenix). Braintrust. LangSmith wins for LangChain/LangGraph-centric stacks. Phoenix wins if OpenTelemetry neutrality matters to you. Braintrust wins if evaluations should drive deployment decisions. Datadog and AgentCore Observability are the cloud-native picks for teams already on those platforms, and the answer there is the boring one: use what you already pay for.

**Q: What metrics matter most for agent observability?**

Six metrics do most of the work. Tool-call success rate, per tool. Latency by phase (retrieval, reasoning, tool, generation). Eval-suite pass rate per model and prompt change. User-reported failure rate, tied to trace IDs. Token usage per session, because that is the line-item on the cloud bill. Continuous agent-quality scores on correctness, helpfulness, safety, and goal success. Anything missing from that list and you are running log search, not observability.

**Q: Does AWS Bedrock AgentCore include observability?**

Yes. AgentCore Observability ships as a built-in component of the Bedrock AgentCore service. Telemetry goes out in OpenTelemetry-compatible format. Token usage, latency, session duration, and error rates surface in Amazon CloudWatch dashboards. Integrations are first-class with Datadog, LangSmith, and Langfuse. The one thing AWS does not sell here is an evaluation platform, so Braintrust or Phoenix sits beside it in most serious deployments.

## Citations

1. **OpenTelemetry** — Semantic conventions for generative AI systems — https://opentelemetry.io/docs/specs/semconv/gen-ai/ [accessed 2026-04-17] *(Canonical GenAI semantic conventions.)* { #cite-1 }
2. **OpenTelemetry** — Semantic Conventions for GenAI agent and framework spans — https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/ [accessed 2026-04-17] *(Agent-specific span conventions; based on Google's AI agent white paper.)* { #cite-2 }
3. **OpenTelemetry** — AI Agent Observability: Evolving Standards and Best Practices — https://opentelemetry.io/blog/2025/ai-agent-observability/ [accessed 2026-04-17] *(Overview of the GenAI SIG's work on agent observability standards.)* { #cite-3 }
4. **Uptrace** — OpenTelemetry for AI Systems: LLM and Agent Observability (2026) — https://uptrace.dev/blog/opentelemetry-ai-systems [accessed 2026-04-17] *(Practitioner walkthrough of OTel GenAI conventions and multi-agent tracing.)* { #cite-4 }
5. **Medium (Anudeep)** — LangSmith vs Arize vs Braintrust: The Definitive 2026 Comparison — https://anudeepsri.medium.com/langsmith-vs-arize-vs-braintrust-e397e4728a76 [accessed 2026-04-17] *(March 2026 side-by-side of the three leading observability platforms.)* { #cite-5 }
6. **Datadog** — Datadog LLM Observability natively supports OpenTelemetry GenAI Semantic Conventions — https://www.datadoghq.com/blog/llm-otel-semantic-convention/ [accessed 2026-04-17] *(Datadog v1.37 native OTel GenAI support.)* { #cite-6 }
7. **AWS** — Get started with AgentCore Observability — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-get-started.html [accessed 2026-04-17] *(AWS Bedrock AgentCore Observability; CloudWatch integration; quality metrics.)* { #cite-7 }
8. **W3C** — Trace Context: W3C Recommendation — https://www.w3.org/TR/trace-context/ [accessed 2026-04-17] *(W3C standard for propagating trace context across HTTP services.)* { #cite-8 }
9. **Arize AI** — Phoenix: open-source LLM observability — https://phoenix.arize.com/ [accessed 2026-04-17] *(Open-source observability platform using OpenInference on OTLP.)* { #cite-9 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/glossary/agent-observability/](https://www.exploreagentic.ai/glossary/agent-observability/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
