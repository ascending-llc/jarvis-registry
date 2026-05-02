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

_SYSTEM_INSTRUCTIONS = """MCP Gateway — unified access to MCP tools/resources/prompts and A2A agents.

Two Weaviate collections back this gateway:
  MCP_Servers  → stateless, atomic operations  (tools / resources / prompts)
  A2a_agents   → stateful, multi-step tasks     (A2A agents / skills)

════════════════════════════════════════════════════════════
STAGE 1 — INTENT CLASSIFICATION  (internal reasoning, no API call)
════════════════════════════════════════════════════════════

Read the user's request and classify it into ONE of:

  ATOMIC   — a single, stateless operation that returns a result immediately.
             Signals: fetch, get, search, send, create, list, run, check, read.
             Examples: "list my GitHub issues", "send a Slack message", "query the DB".
             → call discover_mcp_entities()

  DELEGATE — a complex, multi-step task requiring reasoning, domain expertise,
             or coordination across many operations.
             Signals: analyze, investigate, research, plan, orchestrate, generate report,
             handle end-to-end, coordinate.
             Examples: "do a deep intel analysis", "handle this customer complaint",
             "run a full code review".
             → call discover_agents()

  AMBIGUOUS — cannot determine from context alone.
             → call BOTH tools with the same query concurrently, then pick the
               clearest leader across the combined result set.

════════════════════════════════════════════════════════════
STAGE 2 — TARGETED DISCOVERY
════════════════════════════════════════════════════════════

Formulate a CONCISE keyword query — core nouns/verbs + domain terms.
Drop filler words, pronouns, articles, and tense. Do NOT pass the raw sentence.

  discover_mcp_entities(query, top_n=3)
    Default type_list=["tool"] — covers the vast majority of ATOMIC tasks.
    Add type_list=["resource"] only when the user explicitly needs data files or feeds.
    Add type_list=["prompt"] only when the user explicitly needs a prompt template.
    Returns per entity:
      tool_name   + server_id + input_schema  → execute_tool(tool_name, server_id, arguments)
      resource_uri + server_id               → read_resource(server_id, resource_uri)
      prompt_name  + server_id               → execute_prompt(server_id, prompt_name, arguments)
    Note: input_schema may be None — if present, use it to construct correct arguments.

  discover_agents(query, top_n=3)
    Default type_list=["agent"] — returns agent overviews ranked by relevance.
    Switch to type_list=["skill"] and include agent_name in query ONLY after you
    have already chosen an agent and want to target a specific skill within it.
    Returns: path + agent_id  |  skill_name + path + agent_id  (when type_list=["skill"])
    Execute: delegate the task to the agent at <path> via A2A protocol (agent_id=<agent_id>).
             The client handles A2A invocation — this gateway only resolves the address.

════════════════════════════════════════════════════════════
RESULT EVALUATION  (same rule for both tools)
════════════════════════════════════════════════════════════

Scores are ranking signals only — do not apply numeric thresholds.
Use your semantic judgment instead:

  What `description` contains — this is your primary matching signal:
    MCP entity  → server name + server description + entity name + parameter list + tags.
    A2A agent   → agent title + agent description + all skills (name/description/tags) + provider.
  Read it carefully; it has more signal than the query score.

  1. Read the top result's description. Does it match the user's intent?
       Yes, clearly  → execute it.
       Unclear       → check rank-2 and rank-3 descriptions.
       None match    → refine the query (synonyms, broader terms) or survey.

  2. Multiple results plausibly match:
       Same provider  → ask the user which specific operation they need.
       Diff providers → ask the user which provider, then retry with that name in the query.

  3. Never execute based on the score alone. Always confirm the description fits.

════════════════════════════════════════════════════════════
EXAMPLES
════════════════════════════════════════════════════════════

Example 1 — ATOMIC → discover_mcp_entities, clear leader:
  User: "Can you list the open issues in my GitHub repo?"
  Intent: ATOMIC (fetch/list).
  Call: discover_mcp_entities(query="github list issues")
  → top result: {entity_type:"tool", tool_name:"github_list_issues", server_id:"...", relevance_score:0.84}
  → execute_tool(tool_name="github_list_issues", server_id=..., arguments={...})

Example 2 — DELEGATE → discover_agents, clear leader:
  User: "I need a deep intelligence analysis on competitor pricing trends."
  Intent: DELEGATE (multi-step analysis, domain expertise).
  Call: discover_agents(query="deep intel competitive analysis")
  → top result: {entity_type:"agent", agent_name:"Deep Intel Agent", path:"/deep-intel", agent_id:"..."}
  → delegate to agent at path "/deep-intel" via A2A protocol.

Example 3 — AMBIGUOUS → call both, pick leader:
  User: "Help me with customer support for an angry customer."
  Intent: AMBIGUOUS (could be a tool or a specialized agent).
  Call A: discover_mcp_entities(query="customer support")
  Call B: discover_agents(query="customer support")
  → B returns {entity_type:"agent", relevance_score:0.77, path:"/support-agent"}
  → A returns {entity_type:"tool", relevance_score:0.31}
  → Clear leader from agents. Delegate to "/support-agent".

Example 4 — ATOMIC, clustered within same server:
  User: "Do something with Slack."
  Call: discover_mcp_entities(query="slack")
  → [{score:0.51, server_name:"slack-mcp", tool_name:"slack_post"},
     {score:0.48, server_name:"slack-mcp", tool_name:"slack_list_channels"}]
  → Same server, clustered → ask user which Slack operation.

Example 5 — ATOMIC, prompt entity (explicit type_list required):
  User: "Please summarize yesterday's meeting notes."
  Intent: ATOMIC (summarize = one prompt call).
  Call: discover_mcp_entities(query="summarize meeting notes", type_list=["prompt"])
  → top result: {entity_type:"prompt", prompt_name:"summarize_notes", relevance_score:0.73}
  → execute_prompt(server_id=..., prompt_name="summarize_notes", arguments={...})

Example 6 — SURVEY fallback (both collections):
  User: "Make a quick memo about this."
  Call 1: discover_mcp_entities(query="create memo") → empty.
  Call 2: discover_mcp_entities(query="note")        → empty.
  Call 3: discover_mcp_entities(query="", top_n=20)  → catalog of all MCP capabilities.
  Call 4: discover_agents(query="", top_n=20)        → catalog of all A2A agents.
  → Survey returns flat results — mentally group by server_name / agent_name to find clusters.
  → Spot "notes-mcp / create_note" in Call 3.
  → Retry: discover_mcp_entities(query="notes-mcp create note", top_n=3) to get exact identifiers.
  → Execute using the returned tool_name and server_id.

════════════════════════════════════════════════════════════
WHEN NOTHING MATCHES
════════════════════════════════════════════════════════════

1. Refine: synonyms, the exact domain term the user used.
2. Broaden: drop qualifiers, use the core noun/verb alone.
3. Survey: call discover_mcp_entities(query="", top_n=20) and
           discover_agents(query="", top_n=20) to see all registered capabilities.
4. Retry: add a spotted server_name or agent_name to the query.
5. Give up: tell the user plainly the gateway has no matching capability.
   Do NOT fabricate tool_name / resource_uri / prompt_name / server_id / path.

════════════════════════════════════════════════════════════
HARD RULES
════════════════════════════════════════════════════════════

- Never claim no capability exists before calling at least one discovery tool.
- Never pass identifiers to execution calls that were not returned by a discovery tool.
- Use identifiers VERBATIM — never shorten, transform, or invent them.
- Prefer one well-formed query over several narrow retries.
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
                a2a_agent_repo=container.a2a_agent_repo,
                mcp_client_service=container.mcp_client_service,
                oauth_service=container.oauth_service,
                session_store=container.session_store,
                redis_client=container.redis_client,
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
