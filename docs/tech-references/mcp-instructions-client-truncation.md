# MCP instructions and client truncation

MCP exposes server guidance in the initialization `instructions` field, tool
descriptions, and tool results. The host decides how much of each reaches the
model. Receiving a complete protocol response is not proof that the host included
all of it in a model request.

## Claude clients

The [Claude Code MCP authoring documentation](https://code.claude.com/docs/en/mcp#tool-search-for-mcp-server-authors)
documents these default character limits:

| Surface | Current default | Earlier versions |
|---|---:|---|
| Server instructions | 4,096 | 2,048 before v2.1.296 |
| Tool descriptions loaded up front | 4,096 | 2,048 before v2.1.296 |
| Tool descriptions loaded through tool search | 16,384 | 2,048 before v2.1.295 |

The [v2.1.296 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.296)
also record the increase from 2,048 to 4,096 characters. Configuration overrides
and exceptions documented by the client mean these are not MCP protocol limits.
Registry targets 2,048 characters per tool description to support the older
up-front path too.

The AS-1944 v2 investigation supplied by the maintainer reports a 4,096-character
limit for Claude Desktop. That finding is separate from the Claude Code sources
above; this change does not include a new Desktop measurement.

Tool results have a different budget. Claude Code documents a warning above
10,000 tokens and a default maximum of 25,000 tokens; long text can also be
persisted to a file or truncated. Results are therefore not guaranteed to reach
the model whole. Registry's short situational recovery hints are far below these
thresholds. See [MCP output limits and warnings](https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings).

## How the AS-1944 change accounts for this

- Keep initialization instructions at 887 UTF-8 bytes. The discovery-before-refusal
  rule is on the 150-character first line. Tests retain the 1,000-byte overall
  budget and 250-character first-line budget.
- Register all tools with `inspect.cleandoc` descriptions. Remove redundant
  examples and hanging indentation in the source rather than silently clipping
  text at runtime. Tests enforce a 2,048-character limit on every advertised
  description and keep discovery guidance in its first 250 characters.
- Return recovery guidance when it is needed: stale server IDs require
  rediscovery and new paired identifiers; disabled tools must not be retried;
  empty searches suggest synonyms, broader queries, a bounded survey, and the
  other discovery type when appropriate.
- Keep successful empty searches distinct from errors: `isError=False` and
  `structuredContent={"result": []}`. Preserve non-empty output and the discovery
  output schemas. Real in-memory MCP tests verify the published schemas.
- Remove the redundant guide prompt. Do not add a guide tool or a manual loading
  skill. Authentication and consent handling retain their existing guidance.

Lengths below compare the pre-v2 descriptions with the revised text. Counts are
characters, not tokens; the final column is the description advertised by the
tool registration path.

| Tool | Before: raw docstring | Before: `cleandoc` only | After: advertised description |
|---|---:|---:|---:|
| `discover_servers` | 2,020 | 1,836 | 1,807 |
| `discover_agents` | 2,119 | 1,903 | 1,886 |
| `execute_tool` | 927 | 749 | 586 |
| `read_resource` | 2,210 | 1,880 | 712 |
| `execute_agent` | 2,739 | 2,403 | 1,066 |
| `execute_prompt` | 3,089 | 2,679 | 788 |

## Other clients

### Codex

The [official MCP server authoring guide](https://developers.openai.com/plugins/build/mcp-server)
states that Codex passes initialization instructions to the model and recommends
putting essential guidance in the first 512 characters. Earlier AS-1944 testing
observed the complete Registry initialization text on the tested native MCP
connection. Neither observation is a guarantee that every Codex integration path
or context-budget decision preserves arbitrary text unchanged.

### VS Code / GitHub Copilot Chat

Environment tested on 2026-10-28:

| Component | Version or selection |
|---|---|
| VS Code | 1.141.0 |
| Built-in GitHub Copilot Chat | 0.69.0 |
| Agent path | Local, agent mode within VS Code Chat |
| Model selection | Auto, routed to MAI-Code-1.1-Flash (`mai-code-1-flash-tertiary`) |

The temporary local probe used the real Registry app factory and six registered
tools, with inert backend dependencies. Initialization instructions and the
`discover_servers` description contained numbered markers every 100 characters,
each followed by a random verification token. Both started at 10,100 characters;
`inspect.cleandoc` reduced the advertised tool description to 10,099 characters.
A control run shortened the initialization instructions to 800 characters while
retaining the same tool-description length, then restarted the MCP server.
Production instructions were not changed.

Fresh chat sessions asked for the highest visible `[I...]` instruction marker and
`[T...]` tool-description marker, together with their verification tokens. The
prompts did not reveal expected values. No probe files were attached, and Copilot
neither read files nor called tools. The model requests were also inspected through
**Developer: Show Chat Debug View → Show Raw Request Body**, so the conclusions
do not depend solely on the model's description of its context.

| Check | Result |
|---|---|
| Local MCP initialization response | 10,100 characters; final `[I10000]` marker present |
| Local MCP discovery-tool description | 10,099 characters after `cleandoc`; final `[T10000]` marker present |
| 10,100-character instructions in the model request | No numbered instruction markers present; the model reported `I: absent` |
| 800-character instruction control in the model request | No numbered instruction markers present; the model again reported `I: absent` |
| Tool description in both model requests | Exact match to the complete 10,099-character advertised description; the model correctly quoted `[T10000]` and its corresponding random verification token in both runs |
| Instructions cut-off | No threshold established: the instructions were omitted at both tested lengths |
| Up-front tool-description cut-off | No truncation observed through 10,099 characters; the maximum supported length was not measured |

Installed-source inspection finds a generic path from stored `serverInstructions`
through `tool.source.instructions` to Copilot's `McpToolInstructions` prompt
component. The [Microsoft component source](https://github.com/microsoft/vscode-copilot-chat/blob/main/src/extension/prompts/node/agent/defaultAgentInstructions.tsx)
illustrates that path. Its existence does not establish that every runtime model
path includes those instructions; the captured requests above show the behavior
of the tested path without establishing why it omitted them.

This result supports putting essential discovery guidance in tool descriptions
instead of relying exclusively on initialization instructions. Recovery hints in
tool results are covered by the Registry tests; this client experiment did not
invoke tools or measure delivery of their results.

These observations apply to the tested versions, Local agent path, and selected
model. They do not establish a universal Copilot limit or verify Claude Desktop's
discovery behavior. Copilot CLI and the separate Copilot SDK agent path remain
outside this validation.
