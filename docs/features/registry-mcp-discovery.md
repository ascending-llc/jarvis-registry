# Discovering Registry capabilities

Jarvis Registry exposes tools for discovering MCP operations and A2A agents. Before reporting that a requested external capability is unavailable, an assistant should try the relevant discovery tool when permitted. Ordinary writing, explanation, calculation, and text editing do not require discovery. Discovery does not authorize an action: user restrictions, permissions, and execution approvals still apply.

The gateway sends compact instructions in the MCP initialization response. Detailed recovery guidance is returned in tool results when a search finds no matches, a server ID is stale, or a tool is disabled. The compact initialization text has an application budget of 1,000 UTF-8 bytes, with the capability-refusal rule on the first line within 250 characters. Tool descriptions are limited to 2,048 characters after removing docstring indentation. These are application budgets for client compatibility, not MCP protocol limits.

## Choose the discovery tool

- Use `discover_servers` for tools, resources, or prompts: fetching data, querying an API, sending a message, or another concrete operation.
- Use `discover_agents` when the requested task benefits from delegation to a specialized A2A agent.
- When either could fit, query both and compare their descriptions. Their relevance scores come from different searches and are not comparable.
- When the request needs both data retrieval and delegation, retrieve the requested real data first, then pass that output to the agent.

Start with concise keywords, not the user's full sentence, and `top_n=3`. Match the returned descriptions to the user's intent; do not impose a numeric relevance threshold. If several plausible operations or providers remain, ask the user for the missing choice. For matches from different providers, retry with the chosen provider's name in the query.

## Discover and execute an operation

For “List the open issues in my GitHub repository”:

```python
results = discover_servers(query="github list open issues", top_n=3)
# Select a matching result by description, then use that same result's values.
execute_tool(
    tool_name=selected["tool_name"],
    server_id=selected["server_id"],
    arguments={"owner": user_owner, "repo": user_repo, "state": "open"},
)
```

These snippets illustrate tool calls, not a standalone Python client. The selected tool's actual schema controls argument names and values. Never invent identifiers, schemas, required input values, or retrieved data. Ask for missing required inputs. Use identifiers verbatim and pair `tool_name` with the `server_id` from the same result. If `input_schema` is absent, consult the returned parameter description rather than assuming a schema.

`discover_servers` searches tools by default. Select other entity types explicitly when needed:

```python
# A registered prompt template, when the user requests one.
discover_servers(query="meeting notes summary template", type_list=["prompt"])
execute_prompt(
    server_id=selected["server_id"],
    prompt_name=selected["prompt_name"],
    arguments=prompt_arguments,
)

# A registered resource, when the user needs a data file or feed.
discover_servers(query="release notes feed", type_list=["resource"])
read_resource(server_id=selected["server_id"], resource_uri=selected["resource_uri"])
```

For an ambiguous request such as “Do something with Slack,” inspect discovery results and ask which operation the user intends before executing one.

## Delegate a task or combine retrieval with delegation

For “Analyze competitor pricing trends”:

```python
discover_agents(query="competitive pricing analysis", top_n=3)
execute_agent(
    agent_id=selected_agent["agent_id"],
    message={"parts": [{"kind": "text", "text": task_description}]},
)
```

Agent messages use a `parts` array, not a bare string. Use text for natural-language instructions, data parts for structured input, and file parts when appropriate. Follow the selected agent's documented input requirements. Search `type_list=["skill"]` with the agent name only after choosing an agent and needing a specific skill.

For “Pull the last 30 days of GitHub commits and have an agent analyze the trends,” first discover and execute the matching commit retrieval tool. Then discover an appropriate agent and pass the actual tool result:

```python
execute_agent(
    agent_id=selected_agent["agent_id"],
    message={
        "parts": [
            {"kind": "text", "text": "Analyze these GitHub commits for trends."},
            {"kind": "data", "data": {"commits": retrieved_commits}},
        ]
    },
)
```

Do not replace the retrieval step with a fabricated summary. If the task type is unclear, such as handling a customer complaint, discover both MCP operations and agents and choose by their descriptions rather than comparing scores across the two result sets.

## No match and failure recovery

A successful search with no relevant match differs from an error. Authentication failures, denied access, and service failures mean the capability could not be checked or used; they do not prove that no capability exists.

If the initial search has no relevant match, use these fallbacks:

1. Refine with a synonym or broader domain term.
2. Survey using `query="", top_n=20`. If the appropriate capability type is unclear, survey both `discover_servers` and `discover_agents`. The result count is limited; it is not proof that every registered capability was inspected.
3. If a survey suggests a relevant server or agent, include its name in a targeted query to retrieve matching capabilities and exact identifiers.

Use an exact identifier from a matching result; never guess one. Avoid repeated identical searches and report no matching result if these fallbacks do not find a suitable capability. Do not repeat the fallback sequence indefinitely.

For execution failures, read the actual error:

- A stale or missing server/agent ID requires rediscovery before another execution attempt. Never repeat a known stale ID.
- A disabled tool must not be retried. Choose another matching discovered candidate or report that the tool is unavailable.
- An authentication or consent failure requires surfacing the supplied authorization instructions and waiting for the user to complete them. Do not repeatedly rediscover or execute to bypass authorization.
- A transient invocation failure may permit a retry or another matching discovered candidate, within the user's authorization. Do not replay actions with uncertain side effects blindly.

Limit execution retries to two per user request. This execution retry limit does not cap the discovery fallbacks above. If failures persist, report what could not be completed and the observed reason.
