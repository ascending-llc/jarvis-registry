import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from registry_pkgs.core.exceptions import InternalServerException
from registry_pkgs.models.enums import A2AEntityType, MCPEntityType

from ...auth.dependencies import UserContextDict
from ...services.search.service import SearchRequest
from ..core.types import McpAppContext
from ..tracing import DiscoveryKind, trace_discovery

logger = logging.getLogger(__name__)

_SURVEY_TOP_N = 20


@trace_discovery
async def _run_search(
    ctx: Context[ServerSession, McpAppContext],
    query: str,
    top_n: int,
    search_type: str,
    type_list: list[str],
    discovery_kind: DiscoveryKind,
) -> list[dict[str, Any]]:
    logger.info("🔍 Searching type_list=%s query='%s' top_n=%d", type_list, query, top_n)
    try:
        payload: dict[str, Any] = {
            "search_type": search_type,
            "type_list": type_list,
            "top_n": top_n,
        }
        if query:
            payload["query"] = query

        search_request = SearchRequest.model_validate(payload)
        user_context: UserContextDict = ctx.request_context.request.state.user  # type: ignore[union-attr]
        lifespan_context = ctx.request_context.lifespan_context
        result = await lifespan_context.search_service.search_entities(search_request, user_context)
        entities = result.get("results", [])
        logger.info("✅ Found %d result(s) for query='%s'", len(entities), query)
        return entities

    except Exception:
        logger.exception("Vector search failed (type_list=%s, query='%s')", type_list, query)
        raise InternalServerException("entity discovery failed")


def _empty_discovery_result(
    other_tool: str,
    *,
    query: str,
    top_n: int,
    unsearched_types: list[str],
) -> CallToolResult:
    # Offer only fallbacks that differ from the call that just came back empty.
    hints = ["No matching capability found."]
    if query:
        hints.append("Refine the query with synonyms or a broader term.")
    if query or top_n < _SURVEY_TOP_N:
        hints.append(f"Survey with query='' and top_n={_SURVEY_TOP_N}.")
    if unsearched_types:
        types = ", ".join(repr(entity_type) for entity_type in unsearched_types)
        hints.append(f"Entity types {types} were not searched; add them to type_list if they could fit.")
    hints.append(f"If the capability type is unclear, also try {other_tool}.")
    hints.append("Report no match only after these fallbacks.")
    return CallToolResult(
        isError=False,
        structuredContent={"result": []},
        content=[TextContent(type="text", text=" ".join(hints))],
    )


# ============================================================================
# Tool Factory
# ============================================================================


def get_tools() -> list[tuple[str, Callable]]:

    async def discover_servers(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description=(
                    "Concise keywords, not the user's full sentence, describing the atomic operation you need "
                    "(e.g. 'github list issues', 'send slack message', 'search web'). "
                    "Pass '' with top_n=20 to survey up to 20 accessible MCP capabilities."
                ),
            ),
        ] = "",
        top_n: Annotated[
            int,
            Field(ge=1, le=50, description="Max results to return. Default 3 for targeted search, 20 for survey."),
        ] = 3,
        type_list: Annotated[
            list[str],
            Field(
                description=(
                    "MCP entity types to search: 'tool', 'resource', 'prompt'. "
                    "Default is 'tool' — the most common case. "
                    "Add 'resource' or 'prompt' only when you are certain the user needs them."
                ),
            ),
        ] = Field(default_factory=lambda: [MCPEntityType.TOOL.value]),
    ) -> Annotated[CallToolResult, list[dict[str, Any]]]:
        """Discover MCP tools, resources, and prompts for a requested capability.

        Before claiming a needed external operation is unavailable, search here when permitted.
        Ordinary text-only work does not require discovery.

        Use this when the user needs an ATOMIC, stateless operation that completes in a single
        step: fetching data, running code, sending a message, querying an API, etc.

        Each result contains:
        - entity_type: 'tool' | 'resource' | 'prompt'
        - relevance_score: ranking signal only — verify the description, not the score
        - description: server name/description, entity name, parameters and tags; the primary matching signal
        - server_id: required for all execution calls; server_name: display and search context
        - tool_name (entity_type='tool') → execute_tool(tool_name, server_id, arguments)
        - input_schema (entity_type='tool'): use it to construct arguments; may be None if omitted
        - resource_uri (entity_type='resource') → read_resource(server_id, resource_uri)
        - prompt_name (entity_type='prompt') → execute_prompt(server_id, prompt_name, arguments)

        Evaluating results: read each result's description to confirm it matches the
        user's intent. If the top result fits, execute it. If multiple results plausibly
        fit, ask the user to clarify. For matches from different providers, ask which provider
        the user wants, then retry with that provider's name in the query.
        If none fit, refine the query or call with
        query='' top_n=20 to survey up to 20 accessible capabilities — then group by server_name
        and retry with that server name in the query to get exact identifiers.
        If it is unclear whether the task needs a tool or an agent, also use discover_agents.
        Scores from the two discovery tools are not comparable; judge descriptions instead
        of applying numeric thresholds. Avoid repeating identical searches."""
        # Annotated metadata preserves the list output schema while allowing a guided empty result.
        results = await _run_search(ctx, query, top_n, "hybrid", type_list, "mcp")
        if results:
            return results
        return _empty_discovery_result(
            "discover_agents",
            query=query,
            top_n=top_n,
            unsearched_types=[entity_type.value for entity_type in MCPEntityType if entity_type.value not in type_list],
        )

    async def discover_agents(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description=(
                    "Concise keywords, not the user's full sentence, describing the task or domain to delegate "
                    "(e.g. 'deep intel analysis', 'customer support', 'code review'). "
                    "Pass '' with top_n=20 to survey up to 20 accessible A2A agents."
                ),
            ),
        ] = "",
        top_n: Annotated[
            int,
            Field(ge=1, le=50, description="Max results to return. Default 3 for targeted search, 20 for survey."),
        ] = 3,
        type_list: Annotated[
            list[str],
            Field(
                description=(
                    "A2A entity types to search. Default 'agent' — finds the best agent for the task. "
                    "Switch to 'skill' (with the agent_name in the query) only after you have already "
                    "identified which agent to use and want to target a specific capability."
                ),
            ),
        ] = Field(default_factory=lambda: [A2AEntityType.AGENT.value]),
    ) -> Annotated[CallToolResult, list[dict[str, Any]]]:
        """Discover A2A agents or their skills for tasks that benefit from delegation.

        Before claiming a needed delegated capability is unavailable, search here when permitted.
        Ordinary text-only work does not require discovery.

        Use this when the user needs a COMPLEX, multi-step task that requires reasoning,
        domain expertise, or coordination across multiple operations — and is best handled
        by delegating to a specialized autonomous agent via the A2A protocol.
        When a task requests data retrieval followed by delegation, retrieve the real data
        through discovered MCP tools first, then include that output in the agent message.
        Do not substitute fabricated data or a fabricated summary for the requested input.

        Each result contains:
        - entity_type: 'agent' | 'skill'
        - relevance_score: ranking signal within these results, not a numeric match threshold
        - description: embedded agent context including capabilities, skills, and card info
        - agent_id + agent_name: agent identity
        - path: registry path used to invoke the agent via A2A protocol
        - skill_name (entity_type='skill'): the specific skill to target

        Execution:
        Call execute_agent(agent_id=<agent_id>, message=<payload>); its inputSchema defines
        the message structure (parts array with typed content).
        - If entity_type='skill', target the specific skill via the message payload.

        Evaluating results: read each result's description to confirm the agent's
        domain matches the user's task — do not rely on the score alone. If the top
        result fits, delegate to it. If multiple agents plausibly fit, ask the user
        which domain they prefer. If none fit, broaden the query or call with
        query='' top_n=20 to survey up to 20 accessible agents.
        If it is unclear whether the task needs an agent or a tool, also use discover_servers.
        Scores from the two discovery tools are not comparable; judge descriptions instead.
        Avoid repeating identical searches."""
        results = await _run_search(ctx, query, top_n, "hybrid", type_list, "a2a")
        if results:
            return results
        return _empty_discovery_result(
            "discover_servers",
            query=query,
            top_n=top_n,
            # Agent search already covers skills through the indexed agent card.
            unsearched_types=[],
        )

    return [
        ("discover_servers", discover_servers),
        ("discover_agents", discover_agents),
    ]
