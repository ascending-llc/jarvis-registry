# Workflow Orchestration

Jarvis Registry workflow orchestration lets you compose A2A agents, MCP tools, and skills into multi-step pipelines that complete complex tasks — with conditional logic, parallel execution, and human approval gates built in.

---

## Composing Agents, Skills, and MCP Tools

A workflow step can invoke any resource registered in Jarvis Registry:

- **A2A Agent step**: delegate a subtask to a worker agent (AgentCore, Azure AI Foundry, or self-hosted) and use its output as input to the next step
- **MCP Tool step**: invoke a specific tool, prompt, or resource from any registered MCP server
- **Skill step**: describe the capability needed in natural language — the Skill Gateway resolves the best-matched agent or MCP server at runtime

Steps are chained by passing outputs forward. The gateway handles transport negotiation, auth prerequisites, and ACL enforcement at each hop transparently.

---

## Workflow Logic

**Sequential steps** are the default — each step runs after the previous one completes and its output is available.

**Parallel execution**: steps that don't depend on each other can run concurrently. The workflow waits for all parallel branches to complete before merging results into the next step.

```
Step 1 (fetch data)
  ├── Step 2a (analyze sentiment)   ─┐
  └── Step 2b (extract entities)    ─┤ parallel
                                     ↓
                              Step 3 (summarize)
```

**Conditional branching**: route execution to different steps based on the output of a previous step — for example, escalate to a senior agent if a confidence score falls below a threshold, or skip a step if a resource is already cached.

**Approval gates**: pause a workflow and require explicit human or system approval before proceeding. The workflow holds state until the gate is resolved — approved, rejected, or timed out — then continues or terminates accordingly.

**Retry and fallback**: steps can be configured with retry limits and fallback targets — if the primary agent or tool fails, the workflow reroutes to an alternative without failing the whole pipeline.

---

## Next Steps

- [AgentCore Federation](agentcore-federation.md) — Importing Amazon Bedrock AgentCore agents for use in workflows
- [Azure AI Foundry Federation](foundry-ai-federation.md) — Importing Azure AI Foundry agents for use in workflows
- [Skill Gateway](skill-gateway.md) — How skills are resolved at workflow runtime
- [MCP Gateway](mcp-gateway.md) — Tool and resource access within workflow steps
