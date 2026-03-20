import asyncio
import logging
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from registry_pkgs.models.enums import ServerEntityType
from registry_pkgs.vector.client import get_db_client
from registry_pkgs.vector.enum.enums import SearchType
from registry_pkgs.vector.repositories.mcp_server_repository import get_mcp_server_repo

from ...auth.dependencies import CurrentUser
from ...core.telemetry_decorators import track_registry_operation
from ...schemas.case_conversion import APIBaseModel
from ...services.search.service import faiss_service
from ...services.server_service import server_service_v1
from ...utils.otel_metrics import record_tool_discovery

logger = logging.getLogger(__name__)

router = APIRouter()

EntityType = Literal["mcp_server", "tool", "a2a_agent"]


def _get_mcp_server_repo():
    return get_mcp_server_repo(get_db_client())


class _MCPServerRepoProxy:
    async def asearch_with_rerank(self, *args, **kwargs):
        return await _get_mcp_server_repo().asearch_with_rerank(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(_get_mcp_server_repo(), name)


mcp_server_repo = _MCPServerRepoProxy()


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
    path: str
    agentName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    trustLevel: str | None = None
    visibility: str | None = None
    isEnabled: bool = False
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
    totalAgents: int = 0


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
            raw_results = await faiss_service.search_mixed(
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
    type_list: list[ServerEntityType] | None = Field(
        default_factory=lambda: list(ServerEntityType), description="Type of document to return (default: all types)"
    )
    include_disabled: bool = Field(default=False, description="Include disabled results")


def _is_server_only_search(type_list: list[ServerEntityType] | None) -> bool:
    """Return True only for the dedicated full-server discovery path."""
    return bool(type_list) and len(type_list) == 1 and type_list[0] == ServerEntityType.SERVER


def _build_search_filters(search: SearchRequest) -> dict[str, object]:
    """Build shared vector-store filters from the request."""
    return {
        "enabled": not search.include_disabled,
        "entity_type": list(search.type_list),
    }


def _serialize_search_results(results: list) -> list:
    """Normalize model instances into JSON-compatible dicts for API/tool output."""
    return [result.model_dump(mode="json") if hasattr(result, "model_dump") else result for result in results]


async def _fetch_servers_by_ids(server_ids: list[str]) -> list:
    """Load full server documents from Mongo for the matched server ids."""
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(server_service_v1.get_server_by_id(server_id)) for server_id in server_ids]
    return [server for task in tasks if (server := task.result()) is not None]


def _extract_unique_server_ids(results: list[dict[str, object]]) -> list[str]:
    """Preserve result order while removing duplicate/empty server ids."""
    seen_server_ids: set[str] = set()
    server_ids: list[str] = []
    for result in results:
        server_id = str(result.get("server_id"))
        if not server_id or server_id in seen_server_ids:
            continue
        seen_server_ids.add(server_id)
        server_ids.append(server_id)
    return server_ids


async def _search_server_documents(search: SearchRequest, query: str) -> list:
    """Run the full-server discovery path using Mongo fallback for empty queries."""
    if not query:
        status = None if search.include_disabled else "active"
        raw_servers, _ = await server_service_v1.list_servers(
            query=None,
            status=status,
            page=1,
            per_page=search.top_n,
        )
        return raw_servers

    filters = _build_search_filters(search)
    results = await mcp_server_repo.asearch_with_rerank(
        query=query,
        search_type=search.search_type,
        filters=filters,
        k=search.top_n,
    )
    server_ids = _extract_unique_server_ids(results)
    return await _fetch_servers_by_ids(server_ids)


async def _search_non_server_documents(search: SearchRequest, query: str) -> list:
    """Run tool/resource/prompt discovery, using metadata filtering for empty queries."""
    filters = _build_search_filters(search)
    if not query:
        return await mcp_server_repo.afilter(filters=filters, limit=search.top_n)

    return await mcp_server_repo.asearch_with_rerank(
        query=query,
        k=search.top_n,
        candidate_k=min(search.top_n * 5, 100),
        search_type=search.search_type,
        filters=filters,
    )


def _record_discovery_metrics(
    search_results: list,
    success: bool,
    duration: float,
    search_type: SearchType,
    results_count: int,
) -> None:
    """Record discovery metrics once per discovered server name."""
    discovered_names: set[str] = set()
    for result in search_results:
        name = getattr(result, "serverName", None) or (result.get("server_name") if isinstance(result, dict) else None)
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


@router.post("/search/servers")
@track_registry_operation("search", resource_type="server")
async def search_servers(search: SearchRequest, user_context: CurrentUser):
    """
    Search for MCP servers with their tools, resources, and prompts.
    POC endpoint returning raw JSON with dual-format tool definitions.

    Request body:
    {
        "query": "search",
        "top_n": 5,
        "search_type": "hybrid",  # Optional: "near_text", near_vector,"bm25", or "hybrid" (default: "hybrid")
        "type_list": ["server"], # Optional: ["server", "tool", "resource", "prompt"] (default: ["server"])
        "include_disabled": false  # Optional: include disabled servers (default: false)
    }

    Returns raw JSON that can be converted to ExtendedMCPServer format.
    """
    query = search.query.strip()
    top_n = search.top_n
    start_time = time.perf_counter()
    success = False
    results_count = 0
    search_results: list = []

    logger.info(
        f"🔍 Server search from user '{user_context.get('username', 'unknown')}': "
        f"query='{query}', top_n={top_n}, search_type={search.search_type}"
    )

    try:
        if _is_server_only_search(search.type_list):
            raw_servers = await _search_server_documents(search, query)
            search_results = _serialize_search_results(raw_servers)
            logger.info(f"Found {len(search_results)} servers with full details")
        else:
            search_results = await _search_non_server_documents(search, query)
            logger.info(f"✅ Found {len(search_results)} servers")

        success = True
        results_count = len(search_results)

        return {"query": query, "type_list": search.type_list, "total": len(search_results), "servers": search_results}
    finally:
        # Record tool discovery metrics per discovered server
        duration = time.perf_counter() - start_time
        try:
            _record_discovery_metrics(search_results, success, duration, search.search_type, results_count)
        except Exception as e:
            logger.warning(f"Failed to record tool discovery metric: {e}")
