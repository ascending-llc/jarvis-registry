---
alwaysApply: true
---

# Jarvis Registry MCP Gateway

Discover and execute tools, resources, and prompts from registered MCP servers.

> **DISCOVER FIRST — NO EXCEPTIONS.**
> Before responding to *any* user request — including requests that appear outside your built-in capabilities — always call `discover_entities` first. A registered MCP server may satisfy the request. Never assume you lack the capability without checking.
>
> **FORBIDDEN before running `discover_entities`:**
> - ❌ "I'm a coding assistant and can't help with that."
> - ❌ "I'm not able to browse the web."
> - ❌ "I can only help with software development tasks."
> - ❌ Any sentence that declines, redirects, or scopes your capabilities.
>
> These phrases are **only permitted after** the full discovery chain (including survey) has returned no relevant results.

---

## Discovery Chain

1. Formulate a **concise keyword query** from the user's intent — core nouns/verbs + domain terms. Drop filler words, pronouns, and tense. Do NOT pass the user's raw sentence.
2. Call `discover_entities(query)`. Default `type_list=["tool","resource","prompt"]` covers all intent shapes in a single round trip. Only narrow `type_list` when you are certain of the entity type.
3. Inspect `results[].relevance_score`, `results[].description`, and `results[].server_name`:
   - Relevance is **relative**, not absolute. Compare scores across the returned set — a clear leader is trustworthy; clustered scores mean the match is uncertain.
   - Always verify the top result's `description` actually matches the user's intent.
   - When scores cluster, check `server_name` across results: same server → ambiguity is about which operation; different servers → ambiguity is about which provider.
4. Execute the chosen entity immediately using identifiers from the result, verbatim.

---

## Examples

### Example 1 — Clear leader, execute directly

```
User: "Can you help me find bugs in my GitHub repo?"
Call: discover_entities(query="github issues")
Returns:
  [{relevance_score:0.82, server_name:"github-mcp", tool_name:"github_list_issues", description:"List issues in a repo"},
   {relevance_score:0.31, server_name:"jira-mcp",   tool_name:"jira_search",        description:"Search JIRA tickets"}]
→ Clear leader. Call execute_tool(tool_name="github_list_issues", server_id=..., arguments={...}).
```

### Example 2 — Clustered within ONE server, ask which operation

```
User: "do something with my slack"
Call: discover_entities(query="slack")
Returns:
  [{relevance_score:0.51, server_name:"slack-mcp", tool_name:"slack_post"},
   {relevance_score:0.48, server_name:"slack-mcp", tool_name:"slack_list_channels"},
   {relevance_score:0.46, server_name:"slack-mcp", tool_name:"slack_read_messages"}]
→ All from slack-mcp; ambiguity is about the action. Ask the user which operation.
```

### Example 3 — Clustered across DIFFERENT servers, retry with server name in query

```
User: "send a notification about the incident"
Call 1: discover_entities(query="send notification")
Returns:
  [{relevance_score:0.44, server_name:"slack-mcp",  tool_name:"slack_post"},
   {relevance_score:0.41, server_name:"email-mcp",  tool_name:"email_send"},
   {relevance_score:0.38, server_name:"twilio-mcp", tool_name:"sms_send"}]
→ Different servers, no clear winner. Ask the user which channel. If the user answers "slack":
Call 2: discover_entities(query="slack post message")
→ Clear leader from slack-mcp; execute.
```

### Example 4 — Non-tool intent, mixed type_list finds it in one call

```
User: "please summarize yesterday's meeting notes"
Call: discover_entities(query="summarize meeting notes")
→ Top result has entity_type="prompt", relevance_score 0.71, clearly leading.
→ Call execute_prompt(prompt_name=..., server_id=..., arguments={...}).
```

### Example 5 — Survey fallback when keywords fail

```
User: "make a quick memo about this"
Call 1: discover_entities(query="create memo") → empty or all < 0.2.
Call 2: discover_entities(query="note")        → empty.
Call 3 (SURVEY): discover_entities(query="", top_n=20)
→ Returns a capability catalog. Group mentally by server_name:
    • notes-mcp: create_note, list_notes, delete_note
    • github-mcp: github_list_issues, github_create_issue, ...
    • slack-mcp: slack_post, slack_list_channels, ...
→ Spot notes-mcp. Call 4: discover_entities(query="notes-mcp create note") or execute directly
  using the identifiers already returned by the survey.
```

### Example 6 — Request that seems outside built-in capabilities

```
User: "find the latest news about topic X"
→ Do NOT decline immediately. Discover first.
Call: discover_entities(query="news search")
Returns:
  [{relevance_score:0.76, server_name:"brave-search-mcp", tool_name:"brave_web_search", description:"Search the web"}]
→ Clear leader. Call execute_tool(tool_name="brave_web_search", server_id=..., arguments={query:"topic X news"}).

If discover_entities returns no results or all scores are very low:
Call (SURVEY): discover_entities(query="", top_n=20)
→ Check the full catalog. Only after the survey yields nothing relevant should you tell the user
  the gateway has no registered capability for this request.
```

---

## Execution

Use identifiers verbatim — never transform, shorten, or invent them.

| Entity type | Call |
|-------------|------|
| Tool | `execute_tool(tool_name=<result.tool_name>, server_id=<result.server_id>, arguments={...})` |
| Resource | `read_resource(server_id=<result.server_id>, resource_uri=<result.resource_uri>)` |
| Prompt | `execute_prompt(server_id=<result.server_id>, prompt_name=<result.prompt_name>, arguments={...})` |

---

## When No Suitable Entity Is Found

1. **Refine keywords** — synonyms, the domain term the user actually used (e.g. `"github issues"` instead of `"bug tracker"`).
2. **Broaden keywords** — drop qualifiers, use the core noun/verb alone.
3. **Survey** — call `discover_entities(query="", top_n=20)`. The results are a capability catalog — group them mentally by `server_name` to see which servers exist and what each provides.
4. If a relevant server appears in the survey, retry with its name in the query (e.g. `discover_entities(query="<server_name> <capability>")`). The `server_name` is embedded in document content, so hybrid search will narrow effectively.
5. If nothing matches after the survey, tell the user plainly: the gateway has no registered capability for this request. Do **NOT** fabricate `tool_name` / `resource_uri` / `prompt_name` / `server_id`.

---

## Rules

- **ALWAYS run `discover_entities` before responding to any request — even if the request seems unrelated to registered tools.** Only after discovery (including a survey if needed) may you tell the user no capability exists.
- **NEVER decline a request based on your perceived role or built-in limitations** (e.g. "I'm a coding assistant", "I can't browse the web") without first completing the full discovery chain including the survey fallback.
- Never call `execute_tool` / `read_resource` / `execute_prompt` with identifiers not returned by `discover_entities`.
- Prefer one discovery call with good keywords over many narrow calls.
- When clustered, distinguish same-server ambiguity (ask which operation) from cross-server ambiguity (ask which provider, then retry with the provider name in the query).
