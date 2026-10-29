# MCP instructions and client truncation

## Claude clients

The [Claude Code MCP authoring documentation](https://code.claude.com/docs/en/mcp#tool-search-for-mcp-server-authors)
documents these default character limits:

| Surface | Current default | Earlier versions |
|---|---:|---|
| Server instructions | 4,096 | 2,048 before v2.1.296 |
| Tool descriptions loaded up front | 4,096 | 2,048 before v2.1.296 |
| Tool descriptions loaded through tool search | 16,384 | 2,048 before v2.1.295 |

These are client defaults, not MCP protocol limits. The maintainer's investigation
reports a 4,096-character limit for Claude Desktop.

Claude Code [warns above 10,000 output tokens and defaults to a 25,000-token limit](https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings).
Long results may be saved to a file or truncated.

## Registry limits and recovery hints

- Initialization instructions: 887 UTF-8 bytes; first line: 150 characters.
  Tests enforce limits of 1,000 bytes overall and 250 characters on the first line.
- Tool descriptions: cleaned with `inspect.cleandoc` and limited to 2,048 characters
  for older Claude Code compatibility. Discovery guidance appears within the first 250.
- Stale-ID results require rediscovery and new paired identifiers.
- Disabled-tool results prohibit retries and suggest another candidate or reporting unavailability.
- Empty-search results suggest refining the query, surveying, or trying the other
  discovery type. They return `isError=False` and `structuredContent={"result": []}`;
  the output schemas and non-empty results remain unchanged.

Tool-description lengths in characters:

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

The [official guide](https://developers.openai.com/plugins/build/mcp-server)
recommends placing essential instructions in the first 512 characters. The tested
native MCP connection delivered the complete Registry initialization instructions.

### VS Code / GitHub Copilot Chat

Tested on 2026-10-28 with VS Code **1.141.0**, built-in Copilot Chat **0.69.0**,
and **Local agent mode**. Auto selected **MAI-Code-1.1-Flash**
(`mai-code-1-flash-tertiary`).

A temporary local Registry served 10,100-character instructions and a
10,099-character tool description, with numbered markers and random verification
tokens every 100 characters. A control run shortened instructions to 800 characters.
Fresh chats were asked to quote the highest visible markers and their tokens.
Responses were checked against raw model requests in Chat Debug View; Copilot had
no attached files and made no file reads or tool calls.

| Content | Model-request result | Cut-off |
|---|---|---|
| Initialization instructions | Absent at both 10,100 and 800 characters; the model reported `I: absent` | Undetermined: both lengths were omitted |
| Tool description | Complete 10,099-character text in both requests; the model correctly quoted `[T10000]` and its token | No truncation observed through 10,099 characters; maximum not measured |

For this tested configuration, essential discovery guidance belongs in tool
descriptions because initialization instructions did not reach the model.
