# Workflow Orchestration

Jarvis Registry workflow orchestration lets you compose A2A agents, MCP tools, and skills into multi-step pipelines that complete complex tasks — with conditional logic, parallel execution, and human approval gates.

<div style="text-align: center; margin: 1.5rem 0;">
  <iframe width="560" height="315" src="https://www.youtube.com/embed/vQkYLIabrZg?si=pleyYr-B4XZtg6_0" title="Workflow orchestration with Jarvis Registry" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
</div>

---

## Composing Agents, Skills, and MCP Tools

A workflow step can invoke any resource registered in Jarvis Registry:

- **A2A Agent step** — delegate a subtask to a worker agent (AgentCore, Azure AI Foundry, or self-hosted) and forward its output to the next step.
- **MCP Tool step** — invoke a specific tool, prompt, or resource from any registered MCP server and pass the response onward.
- **Skill step** — describe the capability needed in natural language; the Skill Gateway resolves the best-matched agent or MCP server at runtime.

Steps are chained by passing outputs forward. The gateway handles transport negotiation, auth prerequisites, and ACL enforcement at each hop.

---

## Workflow Logic

### Sequential steps

Sequential execution is the default: each step runs after the previous one completes and its output becomes available to the next step.

### Parallel execution

Steps that don't depend on each other can run concurrently. The workflow waits for all parallel branches to complete before merging results into the next step.

```
Step 1 (fetch data)
  ├── Step 2a (analyze sentiment)   ─┐
  └── Step 2b (extract entities)    ─┤ parallel
                                     ↓
                              Step 3 (summarize)
```

### Conditional branching

Route execution to different steps based on earlier outputs — for example, escalate to a senior agent if a confidence score falls below a threshold, or skip a step when cached data is available.

### Approval gates

Pause a workflow and require explicit human or system approval before proceeding. A gate may be configured with timeouts and escalation targets (approve, reject, auto-escalate).

### Retry and fallback

Configure retry limits, backoff, and fallback targets so workflows can recover from transient failures without failing the entire pipeline.

---

## Step configuration (common options)

- **Timeouts** — per-step execution timeout.
- **Retries** — retry policy (count, backoff, jitter).
- **Parallelism** — max concurrency for parallel branches.
- **Approval policy** — approvers, timeout, and escalation.
- **Fallbacks** — alternate targets or compensating actions on failure.

---

## Demo & external resources

- Product/demo pages on Ascending DC:
  - [A2A Agent Registry](https://ascendingdc.com/jarvis-ai/agent-registry/) — product demo and docs for agent registry and cataloging.
  - [Agent Gateway](https://ascendingdc.com/jarvis-ai/agent-gateway/) — overview of the agent gateway product and demo pages.
  - [MCP Gateway](https://ascendingdc.com/jarvis-ai/mcp-gateway/) — product page for the MCP gateway and integration examples.

---

## Next Steps

- [AgentCore Federation](agentcore-federation.md) — how AgentCore agents participate in workflows
- [Azure AI Foundry Federation](foundry-ai-federation.md) — importing Foundry agents for use in workflows
- [Skill Gateway](skill-gateway.md) — how skills are resolved at workflow runtime
- [MCP Gateway](mcp-gateway-registry.md) — tool and resource access within workflow steps
