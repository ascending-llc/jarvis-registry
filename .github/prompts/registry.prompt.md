---
alwaysApply: true
---
This MCP Gateway provides unified access to registered MCP servers through centralized discovery and execution.

KEY CAPABILITIES:
- Discover tools, resources, prompts, or full server documents across registered MCP servers
- Execute downstream MCP tools through a unified proxy
- Access downstream resources and prompts through the same registry
- Route requests with the server's configured authentication and connection settings

GLOBAL WORKFLOW RULES:
1. If you do not already have a suitable tool for the user's request, call `discover_servers` first.
2. Do not respond that you lack capability until you have attempted discovery.
3. If a native fetch or direct access attempt fails with authentication, permission, or access errors, fall back to `discover_servers`.
4. Prefer `type_list=["tool"]` first. Use `type_list=["server"]` only when you need a full server document to inspect all capabilities on that server.

AUTHENTICATION ELICITATION RULES:
- If `execute_tool` returns an authentication challenge, OAuth URL, or elicitation request, do NOT fall back to `discover_servers`. The server and tool are already known.
- Inform the user that authentication is required and present the auth URL or prompt if provided by the tool response.
- Wait for the user to confirm they have completed authentication (e.g., "done", "authenticated", "completed").
- After confirmation, immediately retry `execute_tool` with the exact same `tool_name`, `server_id`, and `arguments` as before — do not re-discover.
- Only fall back to `discover_servers` on auth failure if you do not already have a valid `server_id` and `tool_name`.

WHEN TO FALL BACK TO DISCOVERY:
- Private repository or API access fails
- Authentication or authorization fails (401, 403, permission denied) AND you do not already have a `server_id` and `tool_name` — if you do, retry `execute_tool` directly after auth completes (see AUTHENTICATION ELICITATION RULES)
- A specialized external service is likely needed
- The user asks what capabilities exist for a domain or service

TOKEN-EFFICIENT DISCOVERY:
- `type_list=["tool"]`: default and preferred for executable tools
- `type_list=["resource"]`: for data sources or URIs
- `type_list=["prompt"]`: for reusable prompt workflows
- `type_list=["server"]`: only when you need the full Mongo-style server document

CRITICAL RESULT INTERPRETATION RULE:
- Treat discovery results as full server documents only when `type_list` is exactly `["server"]`.
- In every other case, including `type_list=["tool"]`, treat each returned item as a directly usable result for execution purposes.

EXECUTION RULES:
- `execute_tool` always runs exactly one downstream MCP tool.
- The `tool_name` parameter of the `execute_tool` call must always be the final downstream MCP tool name.
- If the previous discovery call used exactly `type_list=["server"]`, first inspect the `$.config.toolFunctions` field of the server document, choose one tool entry, and pass that chosen entry's `mcpToolName` as `tool_name`. Only if `mcpToolName` is missing may you fall back to that tool entry's key or name.
- In every other discovery case, pass the returned `tool_name` unchanged into the `tool_name` parameter of the `execute_tool` call.
- Pair the chosen `tool_name` with the matching `server_id` from the same discovery result or chosen server document.

EXAMPLES:
- Weather or current events → `discover_servers(query="weather forecast", type_list=["tool"])`
- Web search → `discover_servers(query="web search news", type_list=["tool"])`
- Stock prices → `discover_servers(query="financial data stock market", type_list=["tool"])`
- Explore full capabilities of a server domain → `discover_servers(query="github", type_list=["server"])`
- Access failure on a protected service → `discover_servers(query="<service> authenticated", type_list=["tool"])`

SERVER-DOCUMENT EXAMPLE:
- If `discover_servers(..., type_list=["server"])` returns a server whose `$.config.toolFunctions` contains:
  - `add_numbers_mcp_minimal_mcp_iam -> mcpToolName="add_numbers"`
  - `greet_mcp_minimal_mcp_iam -> mcpToolName="greet"`
- Then first choose the single tool entry that matches the task.
- To execute the add tool, call `execute_tool(tool_name="add_numbers", server_id="<server id>", arguments={...})`.
- To execute the greet tool, call `execute_tool(tool_name="greet", server_id="<server id>", arguments={...})`.

TOOL-RESULT EXAMPLE:
- If discovery returns `{"tool_name": "tavily_search", "server_id": "abc123", ...}`, call `execute_tool(tool_name="tavily_search", server_id="abc123", arguments={...})`.
