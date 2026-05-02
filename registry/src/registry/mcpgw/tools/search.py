import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field

from registry_pkgs.models.enums import A2AEntityType, MCPEntityType

from ...api.v1.search_routes import SearchRequest, search_entities_impl
from ...auth.dependencies import UserContextDict
from ...core.exceptions import InternalServerException
from ..core.types import McpAppContext

logger = logging.getLogger(__name__)


async def _run_search(
    ctx: Context[ServerSession, McpAppContext],
    query: str,
    top_n: int,
    search_type: str,
    type_list: list[str],
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
        result = await search_entities_impl(
            search_request,
            user_context,
            mcp_server_repo=lifespan_context.mcp_server_repo,
            a2a_agent_repo=lifespan_context.a2a_agent_repo,
        )
        entities = result.get("results", [])
        logger.info("✅ Found %d result(s) for query='%s'", len(entities), query)
        return entities

    except Exception:
        logger.exception("Vector search failed (type_list=%s, query='%s')", type_list, query)
        raise InternalServerException("entity discovery failed")


# ============================================================================
# Tool Factory
# ============================================================================


def get_tools() -> list[tuple[str, Callable]]:

    async def discover_mcp_entities(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description=(
                    "Concise keywords describing the atomic operation you need "
                    "(e.g. 'github list issues', 'send slack message', 'search web'). "
                    "Pass '' with top_n=20 to survey all available MCP capabilities."
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
    ) -> list[dict[str, Any]]:
        """Search the MCP_Servers collection for tools, resources, or prompts.

        Use this when the user needs an ATOMIC, stateless operation that completes in a single
        step: fetching data, running code, sending a message, querying an API, etc.

        Each result contains:
        - entity_type: 'tool' | 'resource' | 'prompt'
        - relevance_score: ranking signal only — verify the description, not the score
        - description: full embedded context — server name + server description + entity name
          + parameter list + tags. This is the primary signal; read it to confirm a match.
        - server_id + server_name: required for all execution calls
        - tool_name    (entity_type='tool')     → execute_tool(tool_name, server_id, arguments)
        - input_schema (entity_type='tool')     → use this dict to construct correct arguments;
                                                  may be None if the server omitted it
        - resource_uri (entity_type='resource') → read_resource(server_id, resource_uri)
        - prompt_name  (entity_type='prompt')   → execute_prompt(server_id, prompt_name, arguments)

        Evaluating results: read each result's description to confirm it matches the
        user's intent. If the top result fits, execute it. If multiple results plausibly
        fit, ask the user to clarify. If none fit, refine the query or call with
        query='' top_n=20 to survey all capabilities — then group mentally by server_name
        and retry with that server name in the query to get exact identifiers."""
        return await _run_search(ctx, query, top_n, "hybrid", type_list)

    async def discover_agents(
        ctx: Context[ServerSession, McpAppContext],
        query: Annotated[
            str,
            Field(
                min_length=0,
                max_length=512,
                description=(
                    "Concise keywords describing the complex task or domain you want to delegate "
                    "(e.g. 'deep intel analysis', 'customer support', 'code review'). "
                    "Pass '' with top_n=20 to survey all registered A2A agents."
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
    ) -> list[dict[str, Any]]:
        """Search the A2a_agents collection for A2A agents or their individual skills.

        Use this when the user needs a COMPLEX, multi-step task that requires reasoning,
        domain expertise, or coordination across multiple operations — and is best handled
        by delegating to a specialized autonomous agent via the A2A protocol.

        Each result contains:
        - entity_type: 'agent' | 'skill'
        - relevance_score: compare RELATIVELY across results
        - description: embedded agent context including capabilities, skills, and card info
        - agent_id + agent_name: agent identity
        - path: registry path used to invoke the agent via A2A protocol
        - skill_name (entity_type='skill'): the specific skill to target

        Execution:
        - Delegate the task to the agent at <path> using the A2A protocol (agent_id=<agent_id>).
        - If entity_type='skill', target skill_name=<skill_name> within that agent.

        Evaluating results: read each result's description to confirm the agent's
        domain matches the user's task — do not rely on the score alone. If the top
        result fits, delegate to it. If multiple agents plausibly fit, ask the user
        which domain they prefer. If none fit, broaden the query or call with
        query='' top_n=20 to survey all registered agents."""
        return await _run_search(ctx, query, top_n, "hybrid", type_list)

    return [
        ("discover_mcp_entities", discover_mcp_entities),
        ("discover_agents", discover_agents),
    ]
