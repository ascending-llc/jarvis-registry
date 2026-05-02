import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from registry_pkgs.models.enums import A2AEntityType, MCPEntityType
from registry_pkgs.vector.enum.enums import SearchType
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ...auth.dependencies import CurrentUser
from ...core.telemetry_decorators import track_registry_operation
from ...deps import get_a2a_agent_repo, get_mcp_server_repo, get_vector_service
from ...schemas.case_conversion import APIBaseModel
from ...services.search.base import VectorSearchService
from ...utils.otel_metrics import record_tool_discovery

logger = logging.getLogger(__name__)

router = APIRouter()

EntityType = Literal["mcp_server", "tool", "a2a_agent"]


class MatchingToolResult(APIBaseModel):
    toolName: str
    description: str | None = None
    relevanceScore: float = Field(0.0, ge=0.0, le=1.0)
    matchContext: str | None = None


class ServerSearchResult(APIBaseModel):
    path: str
    serverName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    numTools: int = 0
    isEnabled: bool = False
    relevanceScore: float = Field(..., ge=0.0, le=1.0)
    matchContext: str | None = None
    matchingTools: list[MatchingToolResult] = Field(default_factory=list)


class ToolSearchResult(APIBaseModel):
    serverPath: str
    serverName: str
    toolName: str
    description: str | None = None
    relevanceScore: float = Field(..., ge=0.0, le=1.0)
    matchContext: str | None = None


class AgentSearchResult(APIBaseModel):
    agentId: str | None = None
    path: str
    agentName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    isEnabled: bool = False
    relevanceScore: float = Field(..., ge=0.0, le=1.0)
    matchContext: str | None = None


class SkillSearchResult(APIBaseModel):
    agentId: str | None = None
    agentPath: str
    agentName: str
    skillName: str
    description: str | None = None
    relevanceScore: float = Field(..., ge=0.0, le=1.0)
    matchContext: str | None = None


class SemanticSearchRequest(APIBaseModel):
    query: str = Field(..., min_length=1, max_length=512, description="Natural language query")
    entityTypes: list[EntityType] | None = Field(default=None, description="Optional entity filters")
    maxResults: int = Field(default=10, ge=1, le=50, description="Maximum results per entity collection")


class SemanticSearchResponse(APIBaseModel):
    query: str
    servers: list[ServerSearchResult] = Field(default_factory=list)
    tools: list[ToolSearchResult] = Field(default_factory=list)
    agents: list[AgentSearchResult] = Field(default_factory=list)
    totalServers: int = 0
    totalTools: int = 0


class AgentSemanticSearchRequest(APIBaseModel):
    query: str = Field(default="", min_length=0, max_length=512, description="Natural language query")
    entityTypes: list[A2AEntityType] | None = Field(
        default=None,
        description="A2A entity types to search: 'agent', 'skill'. Default: both.",
    )
    maxResults: int = Field(default=10, ge=1, le=50, description="Maximum results to return")
    includeDisabled: bool = Field(default=False, description="Include disabled agents in results")


class AgentSemanticSearchResponse(APIBaseModel):
    query: str
    agents: list[AgentSearchResult] = Field(default_factory=list)
    skills: list[SkillSearchResult] = Field(default_factory=list)
    totalAgents: int = 0
    totalSkills: int = 0


@router.post(
    "/search/semantic",
    response_model=SemanticSearchResponse,
    response_model_by_alias=True,
    summary="Unified semantic search for MCP servers and tools",
)
@track_registry_operation("search", resource_type="semantic")
async def semantic_search(
    request: Request,
    search_request: SemanticSearchRequest,
    vector_service: VectorSearchService = Depends(get_vector_service),
) -> SemanticSearchResponse:
    """
    Run a semantic search against MCP servers (and their tools) using FAISS embeddings.
    """
    if not request.state.is_authenticated:
        raise HTTPException(detail="Not authenticated", status_code=401)
    user_context = request.state.user
    start_time = time.perf_counter()
    success = False
    total_results = 0
    filtered_servers: list[ServerSearchResult] = []
    filtered_tools: list[ToolSearchResult] = []

    logger.info(
        "Semantic search requested by %s (entities=%s, max=%s)",
        user_context.get("username"),
        search_request.entityTypes or ["mcp_server", "tool"],
        search_request.maxResults,
    )

    try:
        try:
            raw_results = await vector_service.search_mixed(
                query=search_request.query,
                entity_types=search_request.entityTypes,
                max_results=search_request.maxResults,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.error("FAISS search service unavailable: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Semantic search is temporarily unavailable. Please try again later.",
            ) from exc

        for server in raw_results.get("servers", []):
            matching_tools = [
                MatchingToolResult(
                    toolName=tool.get("tool_name", ""),
                    description=tool.get("description"),
                    relevanceScore=tool.get("relevance_score", 0.0),
                    matchContext=tool.get("match_context"),
                )
                for tool in server.get("matching_tools", [])
            ]

            filtered_servers.append(
                ServerSearchResult(
                    path=server.get("path", ""),
                    serverName=server.get("server_name", ""),
                    description=server.get("description"),
                    tags=server.get("tags", []),
                    numTools=server.get("num_tools", 0),
                    isEnabled=server.get("is_enabled", False),
                    relevanceScore=server.get("relevance_score", 0.0),
                    matchContext=server.get("match_context"),
                    matchingTools=matching_tools,
                )
            )

        for tool in raw_results.get("tools", []):
            server_path = tool.get("server_path", "")
            server_name = tool.get("server_name", "")
            filtered_tools.append(
                ToolSearchResult(
                    serverPath=server_path,
                    serverName=server_name,
                    toolName=tool.get("tool_name", ""),
                    description=tool.get("description"),
                    relevanceScore=tool.get("relevance_score", 0.0),
                    matchContext=tool.get("match_context"),
                )
            )

        # Note: Legacy file-based agent search has been removed.
        # Use the new A2A Agent Management V1 API (/api/v1/agents) instead.
        filtered_agents: list[AgentSearchResult] = []

        success = True
        total_results = len(filtered_servers) + len(filtered_tools) + len(filtered_agents)

        return SemanticSearchResponse(
            query=search_request.query.strip(),
            servers=filtered_servers,
            tools=filtered_tools,
            agents=filtered_agents,
            totalServers=len(filtered_servers),
            totalTools=len(filtered_tools),
            totalAgents=len(filtered_agents),
        )
    finally:
        # Record tool discovery metrics per discovered server
        duration = time.perf_counter() - start_time
        try:
            discovered_names: set[str] = set()
            for srv in filtered_servers:
                if srv.server_name:
                    discovered_names.add(srv.server_name)
            for tl in filtered_tools:
                if tl.server_name:
                    discovered_names.add(tl.server_name)

            if discovered_names:
                for name in discovered_names:
                    record_tool_discovery(
                        server_name=name,
                        success=success,
                        duration_seconds=duration,
                        transport_type="semantic",
                        tools_count=total_results,
                    )
            else:
                record_tool_discovery(
                    server_name="registry",
                    success=success,
                    duration_seconds=duration,
                    transport_type="semantic",
                    tools_count=total_results,
                )
        except Exception as e:
            logger.warning(f"Failed to record tool discovery metric: {e}")


class ToolDiscoveryMatch(BaseModel):
    """A discovered tool with metadata for execution"""

    tool_name: str
    server_id: str
    server_path: str
    description: str | None = None
    input_schema: dict | None = None
    discovery_score: float = Field(..., ge=0.0, le=1.0)
    transport_type: str = "streamable-http"


class ToolDiscoveryResponse(BaseModel):
    """Response from tool discovery"""

    query: str
    total_matches: int
    matches: list[ToolDiscoveryMatch]


class SearchRequest(BaseModel):
    query: str = Field(default="", min_length=0, max_length=512, description="Natural language query")
    top_n: int = Field(1, description="Number of results to return")
    search_type: SearchType = Field(default=SearchType.HYBRID, description="Type of search to perform")
    type_list: list[MCPEntityType] | None = Field(
        default_factory=lambda: list(MCPEntityType),
        description="MCP entity types to search: 'tool', 'resource', 'prompt'. Default: all.",
    )
    include_disabled: bool = Field(default=False, description="Include disabled results")


def _build_mcp_filters(search: SearchRequest, mcp_types: list[MCPEntityType]) -> dict[str, object]:
    """Build vector-store filters for the MCP collection (uses 'enabled' key)."""
    filters: dict[str, object] = {"entity_type": mcp_types}
    if not search.include_disabled:
        filters["enabled"] = True
    return filters


def _build_a2a_filters(search: SearchRequest, a2a_types: list[A2AEntityType]) -> dict[str, object]:
    """Build vector-store filters for the A2A collection (uses 'is_enabled' key)."""
    filters: dict[str, object] = {"entity_type": a2a_types}
    if not search.include_disabled:
        filters["is_enabled"] = True
    return filters


async def _search_mcp_documents(
    search: SearchRequest,
    query: str,
    mcp_types: list[MCPEntityType],
    mcp_server_repo: MCPServerRepository,
) -> list:
    filters = _build_mcp_filters(search, mcp_types)
    if not query:
        return await mcp_server_repo.afilter(filters=filters, limit=search.top_n)
    return await mcp_server_repo.asearch_with_rerank(
        query=query,
        k=search.top_n,
        candidate_k=min(max(search.top_n * 10, 50), 100),
        search_type=search.search_type,
        filters=filters,
    )


async def _search_a2a_documents(
    search: SearchRequest,
    query: str,
    a2a_types: list[A2AEntityType],
    a2a_agent_repo: A2AAgentRepository,
) -> list:
    filters = _build_a2a_filters(search, a2a_types)
    if not query:
        return await a2a_agent_repo.afilter(filters=filters, limit=search.top_n)
    return await a2a_agent_repo.asearch_with_rerank(
        query=query,
        k=search.top_n,
        candidate_k=min(max(search.top_n * 10, 50), 100),
        search_type=search.search_type,
        filters=filters,
    )


async def search_entities_impl(
    search: SearchRequest,
    user_context: CurrentUser,
    *,
    mcp_server_repo: MCPServerRepository,
    a2a_agent_repo: A2AAgentRepository | None = None,
) -> dict[str, object]:
    """
    Shared discovery implementation for both FastAPI routes and MCP tools.

    Routes searches to the correct Weaviate collection based on entity type:
    - tool/resource/prompt -> MCP_Servers collection
    - agent/skill          -> A2a_agents collection

    Results are merged and re-sorted by relevance_score before truncation to top_n.
    Every document embeds its server/agent context so no MongoDB lookup is needed.
    """
    query = search.query
    top_n = search.top_n
    start_time = time.perf_counter()
    success = False
    results_count = 0
    search_results: list = []

    all_types = search.type_list or (list(MCPEntityType) + list(A2AEntityType))
    mcp_types: list[MCPEntityType] = [t for t in all_types if isinstance(t, MCPEntityType)]
    a2a_types: list[A2AEntityType] = [t for t in all_types if isinstance(t, A2AEntityType)]

    logger.info(
        f"🔍 Entity search from user '{user_context.get('username', 'unknown')}': "
        f"query='{query}', top_n={top_n}, search_type={search.search_type}, "
        f"mcp_types={mcp_types}, a2a_types={a2a_types}"
    )

    try:
        results: list = []

        if mcp_types:
            mcp_results = await _search_mcp_documents(search, query, mcp_types, mcp_server_repo)
            results.extend(mcp_results)

        if a2a_types and a2a_agent_repo is not None:
            a2a_results = await _search_a2a_documents(search, query, a2a_types, a2a_agent_repo)
            results.extend(a2a_results)

        # Re-sort merged results by relevance_score (desc) and cap at top_n
        if len(results) > top_n:
            results.sort(key=lambda r: r.get("relevance_score") or 0.0, reverse=True)
            results = results[:top_n]

        search_results = results
        logger.info(f"✅ Found {len(search_results)} results (mcp={len(mcp_types) > 0}, a2a={len(a2a_types) > 0})")

        success = True
        results_count = len(search_results)

        return {
            "query": query,
            "type_list": search.type_list,
            "total": len(search_results),
            "results": search_results,
        }
    finally:
        duration = time.perf_counter() - start_time
        try:
            _record_discovery_metrics(search_results, success, duration, search.search_type, results_count)
        except Exception as e:
            logger.warning(f"Failed to record tool discovery metric: {e}")


def _record_discovery_metrics(
    search_results: list,
    success: bool,
    duration: float,
    search_type: SearchType,
    results_count: int,
) -> None:
    discovered_names: set[str] = set()
    for result in search_results:
        name = result.get("server_name") if isinstance(result, dict) else None
        if name:
            discovered_names.add(name)

    if discovered_names:
        for name in discovered_names:
            record_tool_discovery(
                server_name=name,
                success=success,
                duration_seconds=duration,
                transport_type=str(search_type.value),
                tools_count=results_count,
            )
        return

    record_tool_discovery(
        server_name="registry",
        success=success,
        duration_seconds=duration,
        transport_type=str(search_type.value),
        tools_count=results_count,
    )


@router.post(
    "/search/agents",
    response_model=AgentSemanticSearchResponse,
    response_model_by_alias=True,
    summary="Semantic search for A2A agents and skills",
)
@track_registry_operation("search", resource_type="agent")
async def search_agents(
    request: Request,
    search_request: AgentSemanticSearchRequest,
    a2a_agent_repo: A2AAgentRepository = Depends(get_a2a_agent_repo),
) -> AgentSemanticSearchResponse:
    """
    Semantic search against the A2a_agents Weaviate collection.

    Searches A2A agent-level and skill entities.
    For MCP tool/resource/prompt discovery use POST /search/semantic instead.

    entityTypes filter:
      - "agent": agent-level results
      - "skill": individual skill results
      Default: both agent and skill types.
    """
    if not request.state.is_authenticated:
        raise HTTPException(detail="Not authenticated", status_code=401)
    user_context = request.state.user

    entity_types: list[A2AEntityType] = search_request.entityTypes or list(A2AEntityType)

    logger.info(
        "Semantic search (A2A) requested by %s (entities=%s, max=%s, include_disabled=%s)",
        user_context.get("username"),
        [et.value for et in entity_types],
        search_request.maxResults,
        search_request.includeDisabled,
    )

    filters: dict[str, object] = {"entity_type": entity_types}
    if not search_request.includeDisabled:
        filters["is_enabled"] = True

    query = search_request.query.strip()
    try:
        if not query:
            raw_docs = await a2a_agent_repo.afilter(filters=filters, limit=search_request.maxResults)
        else:
            raw_docs = await a2a_agent_repo.asearch_with_rerank(
                query=query,
                k=search_request.maxResults,
                candidate_k=min(max(search_request.maxResults * 10, 50), 100),
                search_type="hybrid",
                filters=filters,
            )
    except RuntimeError as exc:
        logger.error("A2A vector search unavailable: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent search is temporarily unavailable. Please try again later.",
        ) from exc

    filtered_agents: list[AgentSearchResult] = []
    filtered_skills: list[SkillSearchResult] = []

    for doc in raw_docs:
        entity_type = doc.get("entity_type")
        if entity_type == A2AEntityType.AGENT:
            filtered_agents.append(
                AgentSearchResult(
                    agentId=doc.get("agent_id"),
                    path=doc.get("path", ""),
                    agentName=doc.get("agent_name", ""),
                    description=doc.get("description"),
                    tags=doc.get("tags") or [],
                    isEnabled=doc.get("is_enabled", False),
                    relevanceScore=doc.get("relevance_score") or 0.0,
                    matchContext=doc.get("match_context"),
                )
            )
        elif entity_type == A2AEntityType.SKILL:
            filtered_skills.append(
                SkillSearchResult(
                    agentId=doc.get("agent_id"),
                    agentPath=doc.get("path", ""),
                    agentName=doc.get("agent_name", ""),
                    skillName=doc.get("skill_name", ""),
                    description=doc.get("description"),
                    relevanceScore=doc.get("relevance_score") or 0.0,
                    matchContext=doc.get("match_context"),
                )
            )

    return AgentSemanticSearchResponse(
        query=query,
        agents=filtered_agents,
        skills=filtered_skills,
        totalAgents=len(filtered_agents),
        totalSkills=len(filtered_skills),
    )


@router.post("/search/servers")
@track_registry_operation("search", resource_type="server")
async def search_servers(
    search: SearchRequest,
    user_context: CurrentUser,
    mcp_server_repo: MCPServerRepository = Depends(get_mcp_server_repo),
):
    """
    Search for MCP tools, resources, and prompts via vector search.

    All searches target the MCP_Servers Weaviate collection.
    For A2A agent/skill discovery use POST /search/agents instead.

    Results always contain the execution-ready identifier for the entity type:
    - tool   -> tool_name
    - resource -> resource_uri
    - prompt -> prompt_name

    Request body:
    {
        "query": "search",
        "top_n": 5,
        "search_type": "hybrid",
        "type_list": ["tool", "resource", "prompt"],
        "include_disabled": false
    }
    """
    return await search_entities_impl(
        search,
        user_context,
        mcp_server_repo=mcp_server_repo,
    )
