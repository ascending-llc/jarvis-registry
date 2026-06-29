"""Search service: all vector-search logic for MCP and A2A entities.

This service is the single home for discovery logic shared by:
- the HTTP ``POST /search`` route (structured servers/tools/agents/skills), and
- the mcpgw ``discover_mcp_entities`` / ``discover_agents`` tools (flat results).

It is intentionally decoupled from the API layer: route handlers and MCP tools
inject it and map its plain-dict results onto their own response shapes.
"""

import asyncio
import logging
import time

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from registry_pkgs.models.enums import A2AEntityType, MCPEntityType
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.vector.enum.enums import SearchType
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ...auth.dependencies import UserContextDict
from ...services.access_control_service import ACLService
from ...utils.otel_metrics import record_tool_discovery
from .base import VectorSearchService

logger = logging.getLogger(__name__)

type SearchEntityType = MCPEntityType | A2AEntityType

_MCP_SEMANTIC_TYPES = ("mcp_server", "tool")
_A2A_SEMANTIC_TYPES: dict[str, A2AEntityType] = {
    "a2a_agent": A2AEntityType.AGENT,
    "skill": A2AEntityType.SKILL,
}
_DEFAULT_SEMANTIC_TYPES = (*_MCP_SEMANTIC_TYPES, *_A2A_SEMANTIC_TYPES.keys())


class SearchRequest(BaseModel):
    """Flat discovery request used by the mcpgw tools."""

    query: str = Field(default="", min_length=0, max_length=512, description="Natural language query")
    top_n: int = Field(1, description="Number of results to return")
    search_type: SearchType = Field(default=SearchType.HYBRID, description="Type of search to perform")
    type_list: list[SearchEntityType] = Field(
        default_factory=lambda: list(MCPEntityType),
        description=(
            "Entity types to search. MCP supports 'tool', 'resource', 'prompt'. "
            "A2A supports 'agent', 'skill'. Default: all MCP entity types."
        ),
    )
    include_disabled: bool = Field(default=False, description="Include disabled results")


def _build_filters(include_disabled: bool, entity_types: list) -> dict[str, object]:
    """Build a vector-store filter dict from an entity-type list and the disabled flag."""
    filters: dict[str, object] = {"entity_type": entity_types}
    if not include_disabled:
        filters["enabled"] = True
    return filters


def _candidate_k(top_n: int) -> int:
    """Re-ranking candidate pool: 10x requested, floored at 50, capped at 100."""
    return min(max(top_n * 10, 50), 100)


class SearchService:
    """Encapsulates vector search across MCP servers/tools and A2A agents/skills."""

    def __init__(
        self,
        *,
        vector_service: VectorSearchService,
        mcp_server_repo: MCPServerRepository,
        a2a_agent_repo: A2AAgentRepository,
        acl_service: ACLService,
    ) -> None:
        self.vector_service = vector_service
        self.mcp_server_repo = mcp_server_repo
        self.a2a_agent_repo = a2a_agent_repo
        self.acl_service = acl_service

    async def _get_accessible_ids(
        self,
        user_context: UserContextDict,
        resource_type: str,
    ) -> list[str]:
        """Return resource IDs the user may VIEW, including PUBLIC entries.

        Passes None as user_id when the token carries no identity so that
        get_accessible_resource_ids still returns PUBLIC resources.
        """
        raw_user_id: str | None = user_context.get("user_id")
        try:
            user_id = PydanticObjectId(raw_user_id) if raw_user_id else None
        except (ValueError, TypeError):
            logger.warning("Invalid user_id format in context: %r — treating as anonymous", raw_user_id)
            user_id = None

        return await self.acl_service.get_accessible_resource_ids(
            user_id=user_id,
            resource_type=resource_type,
        )

    async def search_entities(self, search: SearchRequest, user_context: UserContextDict) -> dict[str, object]:
        """Run discovery against the correct Weaviate collection per entity type.

        - tool/resource/prompt -> MCP_Servers collection (mcp_server_repo)
        - agent/skill          -> A2a_agents collection (a2a_agent_repo)

        Results are merged and re-sorted by relevance_score before truncation to
        ``top_n``. Every document embeds its server/agent context, so no MongoDB
        lookup is required.

        ACL filtering is pushed into the Weaviate query so that ``top_n`` is
        respected at the database level, not post-hoc.
        """
        query = search.query.strip()
        top_n = search.top_n
        start_time = time.perf_counter()
        success = False
        search_results: list = []

        all_types = search.type_list
        mcp_types: list[MCPEntityType] = [t for t in all_types if isinstance(t, MCPEntityType)]
        a2a_types: list[A2AEntityType] = [t for t in all_types if isinstance(t, A2AEntityType)]

        logger.info(
            f"Entity search from user '{user_context.get('username', 'unknown')}': "
            f"query='{query}', top_n={top_n}, search_type={search.search_type}, "
            f"mcp_types={mcp_types}, a2a_types={a2a_types}"
        )

        try:
            results: list = []

            if mcp_types:
                allowed_server_ids = await self._get_accessible_ids(user_context, RegistryResourceType.MCP_SERVER.value)
                if allowed_server_ids:
                    results.extend(await self._search_mcp_documents(search, query, mcp_types, allowed_server_ids))
                else:
                    logger.info("User has no accessible MCP servers — skipping MCP search")

            if a2a_types:
                allowed_agent_ids = await self._get_accessible_ids(
                    user_context, RegistryResourceType.REMOTE_AGENT.value
                )
                if allowed_agent_ids:
                    try:
                        results.extend(await self._search_a2a_documents(search, query, a2a_types, allowed_agent_ids))
                    except RuntimeError as exc:
                        logger.warning("A2A vector search unavailable, skipping A2A results: %s", exc)
                else:
                    logger.info("User has no accessible A2A agents — skipping A2A search")

            # Re-sort merged results by relevance_score (desc) and cap at top_n
            results.sort(key=lambda r: r.get("relevance_score") or 0.0, reverse=True)
            results = results[:top_n]

            search_results = results
            logger.info(f"Found {len(search_results)} results (mcp={len(mcp_types) > 0}, a2a={len(a2a_types) > 0})")

            success = True

            return {
                "query": query,
                "type_list": search.type_list,
                "total": len(search_results),
                "results": search_results,
            }
        finally:
            duration = time.perf_counter() - start_time
            try:
                if mcp_types:
                    mcp_items = [r for r in search_results if isinstance(r.get("entity_type"), MCPEntityType)]
                    self._record_discovery(mcp_items, success, duration, str(search.search_type.value), len(mcp_items))
            except Exception as e:
                logger.warning(f"Failed to record tool discovery metric: {e}")

    async def _search_mcp_documents(
        self,
        search: SearchRequest,
        query: str,
        mcp_types: list[MCPEntityType],
        allowed_server_ids: list[str],
    ) -> list:
        filters = _build_filters(search.include_disabled, mcp_types)
        filters["server_id"] = {"$in": allowed_server_ids}
        if not query:
            return await self.mcp_server_repo.afilter(filters=filters, limit=search.top_n)
        return await self.mcp_server_repo.asearch_with_rerank(
            query=query,
            k=search.top_n,
            candidate_k=_candidate_k(search.top_n),
            search_type=search.search_type,
            filters=filters,
        )

    async def _search_a2a_documents(
        self,
        search: SearchRequest,
        query: str,
        a2a_types: list[A2AEntityType],
        allowed_agent_ids: list[str],
    ) -> list:
        filters = _build_filters(search.include_disabled, a2a_types)
        filters["agent_id"] = {"$in": allowed_agent_ids}
        if not query:
            return await self.a2a_agent_repo.afilter(filters=filters, limit=search.top_n)
        return await self.a2a_agent_repo.asearch_with_rerank(
            query=query,
            k=search.top_n,
            candidate_k=_candidate_k(search.top_n),
            search_type=search.search_type,
            filters=filters,
        )

    @staticmethod
    def _record_discovery(
        items: list,
        success: bool,
        duration: float,
        transport_type: str,
        tools_count: int,
    ) -> None:
        """Emit a tool-discovery metric per discovered server, or "registry" if none."""
        discovered_names = {item["server_name"] for item in items if isinstance(item, dict) and item.get("server_name")}
        for name in discovered_names or {"registry"}:
            record_tool_discovery(
                server_name=name,
                success=success,
                duration_seconds=duration,
                transport_type=transport_type,
                tools_count=tools_count,
            )

    async def semantic_search(
        self,
        query: str,
        user_context: UserContextDict,
        entity_types: list[str] | None = None,
        max_results: int = 10,
        include_disabled: bool = False,
    ) -> dict[str, list]:
        """Structured search returning MCP servers/tools and A2A agents/skills.

        MCP results come from ``vector_service.search_mixed`` (unchanged behaviour);
        A2A results come from ``a2a_agent_repo`` with ACL filtering applied via
        agent_id/$in. An A2A vector outage degrades gracefully.
        """
        query = query.strip()
        requested = entity_types or list(_DEFAULT_SEMANTIC_TYPES)
        mcp_types = [t for t in requested if t in _MCP_SEMANTIC_TYPES]
        a2a_types = [_A2A_SEMANTIC_TYPES[t] for t in requested if t in _A2A_SEMANTIC_TYPES]

        start_time = time.perf_counter()
        success = False
        servers: list = []
        tools: list = []

        logger.info(
            "Semantic search: query='%s', mcp_types=%s, a2a_types=%s, max=%s",
            query,
            mcp_types,
            [t.value for t in a2a_types],
            max_results,
        )

        try:
            mcp_result, a2a_result = await asyncio.gather(
                self._search_mcp_for_semantic(query, mcp_types, max_results, user_context),
                self._search_a2a_for_semantic(query, a2a_types, max_results, include_disabled, user_context),
                return_exceptions=True,
            )
            for result in (mcp_result, a2a_result):
                if isinstance(result, BaseException):
                    raise result

            (servers, tools), (agents, skills) = mcp_result, a2a_result
            success = True
            return {"servers": servers, "tools": tools, "agents": agents, "skills": skills}
        finally:
            duration = time.perf_counter() - start_time
            mcp_total = len(servers) + len(tools)
            try:
                if mcp_types:
                    self._record_discovery([*servers, *tools], success, duration, "semantic", mcp_total)
            except Exception as e:
                logger.warning(f"Failed to record tool discovery metric: {e}")

    async def _accessible_ids_for_semantic(
        self,
        user_context: UserContextDict,
        resource_type: str,
        label: str,
    ) -> list[str] | None:
        """Resolve ACL-allowed IDs for semantic search, or None if the user has none."""
        allowed_ids = await self._get_accessible_ids(user_context, resource_type)
        if not allowed_ids:
            logger.info("Semantic search: user has no accessible %s", label)
            return None
        return allowed_ids

    async def _search_mcp_for_semantic(
        self,
        query: str,
        mcp_types: list[MCPEntityType],
        max_results: int,
        user_context: UserContextDict,
    ) -> tuple[list, list]:
        """ACL-filtered MCP servers/tools for semantic_search. Degrades to empty on any failure."""
        if not mcp_types:
            return [], []
        allowed_server_ids = await self._accessible_ids_for_semantic(
            user_context, RegistryResourceType.MCP_SERVER.value, "MCP servers"
        )
        if allowed_server_ids is None:
            return [], []
        try:
            mcp_results = await self.vector_service.search_mixed(
                query=query,
                entity_types=mcp_types,
                max_results=max_results,
                allowed_server_ids=allowed_server_ids,
            )
        except Exception:
            logger.exception("MCP search failed unexpectedly")
            return [], []
        return mcp_results.get("servers", []), mcp_results.get("tools", [])

    async def _search_a2a_for_semantic(
        self,
        query: str,
        a2a_types: list[A2AEntityType],
        max_results: int,
        include_disabled: bool,
        user_context: UserContextDict,
    ) -> tuple[list, list]:
        """ACL-filtered A2A agents/skills for semantic_search. Degrades to empty on a vector outage."""
        if not a2a_types:
            return [], []
        allowed_agent_ids = await self._accessible_ids_for_semantic(
            user_context, RegistryResourceType.REMOTE_AGENT.value, "A2A agents"
        )
        if allowed_agent_ids is None:
            return [], []

        filters = _build_filters(include_disabled, a2a_types)
        filters["agent_id"] = {"$in": allowed_agent_ids}
        try:
            if query:
                docs = await self.a2a_agent_repo.asearch_with_rerank(
                    query=query,
                    k=max_results,
                    candidate_k=_candidate_k(max_results),
                    search_type=SearchType.HYBRID,
                    filters=filters,
                )
            else:
                docs = await self.a2a_agent_repo.afilter(filters=filters, limit=max_results)
        except RuntimeError as exc:
            logger.warning("A2A vector search unavailable, skipping A2A results: %s", exc)
            return [], []

        agents: list = []
        skills: list = []
        for doc in docs:
            entity_type = doc.get("entity_type")
            if entity_type == A2AEntityType.AGENT:
                agents.append(doc)
            elif entity_type == A2AEntityType.SKILL:
                skills.append(doc)
        return agents, skills
