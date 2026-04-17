from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from httpx import AsyncClient, Limits, Timeout
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..core.config import settings
from .core.event_store import InMemoryEventStore
from .core.types import McpAppContext
from .tools import proxied, search

if TYPE_CHECKING:
    from ..container import RegistryContainer

_SYSTEM_INSTRUCTIONS = """MCP Gateway: discover and execute tools, resources, and prompts from registered MCP servers.

DISCOVERY CHAIN:
1. Formulate a CONCISE keyword query from the user's intent — core nouns/verbs + domain terms, drop
   filler words, pronouns, and tense. Do NOT pass the user's raw sentence.
2. Call discover_entities(query). Default type_list=["tool","resource","prompt"] covers all intent
   shapes in a single round trip. Only narrow type_list when you are certain of the entity type.
3. Inspect results[].relevance_score and results[].description:
   - Relevance is RELATIVE, not absolute. Compare scores across the returned set — a clear leader
     is trustworthy; clustered scores mean the match is uncertain.
   - Always verify the top result's `description` actually matches the user's intent.
4. Execute the chosen entity immediately using identifiers from the result, verbatim.

EXAMPLES (query formulation + score interpretation):

Example 1 — clear leader, execute directly:
  User: "Can you help me find bugs in my GitHub repo?"
  Call: discover_entities(query="github issues")
  Returns: [{relevance_score:0.82, tool_name:"github_list_issues", description:"List issues in a repo"},
           {relevance_score:0.31, tool_name:"jira_search",        description:"Search JIRA tickets"}]
  -> Clear leader (0.82 vs 0.31). Call execute_tool(tool_name="github_list_issues", server_id=..., arguments={...}).

Example 2 — clustered scores, ask user to disambiguate:
  User: "send a notification about the incident"
  Call: discover_entities(query="send notification")
  Returns: [{relevance_score:0.44, tool_name:"slack_post"},
           {relevance_score:0.41, tool_name:"email_send"},
           {relevance_score:0.38, tool_name:"sms_send"}]
  -> Scores clustered. Ask the user which channel before executing.

Example 3 — non-tool intent, mixed type_list finds it in one call:
  User: "please summarize yesterday's meeting notes"
  Call: discover_entities(query="summarize meeting notes")
  Returns top result with entity_type="prompt", relevance_score 0.71, clearly leading.
  -> Call execute_prompt(prompt_name=..., server_id=..., arguments={...}).

EXECUTION (use identifiers verbatim; never transform, shorten, or invent them):
- Tool     -> execute_tool(tool_name=<result.tool_name>, server_id=<result.server_id>, arguments={...})
- Resource -> read_resource(server_id=<result.server_id>, resource_uri=<result.resource_uri>)
- Prompt   -> execute_prompt(server_id=<result.server_id>, prompt_name=<result.prompt_name>, arguments={...})

WHEN NO SUITABLE ENTITY IS FOUND (empty results, low scores, or no description matches intent):
1. Retry with REFINED keywords — synonyms or the domain term the user actually used
   (e.g. "github issues" instead of "bug tracker").
2. Retry with BROADER keywords — drop qualifiers, use the core noun/verb alone.
3. SURVEY fallback: call discover_entities(query="", top_n=20) to list registered capabilities when
   user phrasing may not match any registered name.
4. If nothing matches after the survey, tell the user plainly: the gateway has no registered
   capability for this request. Do NOT fabricate tool_name / resource_uri / prompt_name / server_id.

RULES:
- Never claim lack of capability before running discover_entities at least once.
- Never call execute_tool / read_resource / execute_prompt with identifiers not returned by discover_entities.
- Prefer one discovery call with good keywords over many narrow calls.
"""


def create_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> FastMCP[McpAppContext]:
    """
    Factory function to create a stateless FastMCP application instance.

    Returns:
        Configured FastMCP application instance
    """

    @asynccontextmanager
    async def mcp_lifespan(server: FastMCP) -> AsyncIterator[McpAppContext]:
        """Manage MCP application lifecycle with type-safe context."""

        container = container_provider()
        if container is None:
            raise RuntimeError("Registry container is not initialized")

        async with AsyncClient(
            timeout=Timeout(30.0, read=60.0),
            follow_redirects=True,
            limits=Limits(max_connections=100, max_keepalive_connections=20),
        ) as proxy_client:
            yield McpAppContext(
                proxy_client=proxy_client,
                server_service=container.server_service,
                mcp_server_repo=container.mcp_server_repo,
                mcp_client_service=container.mcp_client_service,
                oauth_service=container.oauth_service,
                session_store=container.session_store,
            )

    # Configure transport security settings from environment variables
    transport_security_settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.mcpgw_enable_dns_rebinding_protection,
        allowed_hosts=[host.strip() for host in settings.mcpgw_allowed_hosts.split(",") if host.strip()],
        allowed_origins=[origin.strip() for origin in settings.mcpgw_allowed_origins.split(",") if origin.strip()],
    )

    mcp = FastMCP(
        "JarvisRegistry",
        lifespan=mcp_lifespan,
        event_store=InMemoryEventStore(max_events_per_stream=50, max_streams=500),
        instructions=_SYSTEM_INSTRUCTIONS,
        transport_security=transport_security_settings,
    )

    return mcp


def create_gateway_mcp_app(*, container_provider: Callable[[], RegistryContainer | None]) -> FastMCP[McpAppContext]:
    """Create the FastMCP app and register all prompts/tools in one place."""
    mcp = create_mcp_app(container_provider=container_provider)
    register_prompts(mcp)
    register_tools(mcp)
    return mcp


# ============================================================================
# MCP Prompts - Guide AI Assistant Behavior (Claude, ChatGPT, etc.)
# ============================================================================


def register_prompts(mcp: FastMCP) -> None:
    """
    Register prompts for the MCP application.

    Args:
        mcp: FastMCP application instance
    """

    @mcp.prompt()
    def gateway_capabilities():
        """📚 Overview of MCP Gateway capabilities and available services.

        Use this prompt to understand what services and tools are available through the gateway.
        This is automatically invoked when you need to know what you can do.
        """
        return _SYSTEM_INSTRUCTIONS


# ============================================================================
# Tool Registration
# ============================================================================


def register_tools(mcp: FastMCP) -> None:
    """
    Register all tools for the MCP application.

    Args:
        mcp: FastMCP application instance
    """
    # Register entity discovery tools
    for tool_name, tool_func in search.get_tools():
        mcp.tool(name=tool_name)(tool_func)

    # Register registry API tools
    for tool_name, tool_func in proxied.get_tools():
        mcp.tool(name=tool_name)(tool_func)
