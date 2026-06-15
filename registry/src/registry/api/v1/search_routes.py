import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field

from ...core.telemetry_decorators import track_registry_operation
from ...deps import get_search_service
from ...schemas.case_conversion import APIBaseModel
from ...services.search.service import SearchService

logger = logging.getLogger(__name__)

router = APIRouter()

EntityType = Literal["mcp_server", "tool", "a2a_agent", "skill"]


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
    enabled: bool = False
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
    enabled: bool = False
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
    entityTypes: list[EntityType] | None = Field(
        default=None,
        description="Entity types to search: 'mcp_server', 'tool', 'a2a_agent', 'skill'. Default: all.",
    )
    maxResults: int = Field(default=10, ge=1, le=50, description="Maximum results per entity collection")
    includeDisabled: bool = Field(default=False, description="Include disabled agents/skills in results")


class SemanticSearchResponse(APIBaseModel):
    query: str
    servers: list[ServerSearchResult] = Field(default_factory=list)
    tools: list[ToolSearchResult] = Field(default_factory=list)
    agents: list[AgentSearchResult] = Field(default_factory=list)
    skills: list[SkillSearchResult] = Field(default_factory=list)
    totalServers: int = 0
    totalTools: int = 0
    totalAgents: int = 0
    totalSkills: int = 0


def _map_server(server: dict) -> ServerSearchResult:
    matching_tools = [
        MatchingToolResult(
            toolName=tool.get("tool_name", ""),
            description=tool.get("description"),
            relevanceScore=tool.get("relevance_score", 0.0),
            matchContext=tool.get("match_context"),
        )
        for tool in server.get("matching_tools", [])
    ]
    return ServerSearchResult(
        path=server.get("path", ""),
        serverName=server.get("server_name", ""),
        description=server.get("description"),
        tags=server.get("tags", []),
        numTools=server.get("num_tools", 0),
        enabled=server.get("is_enabled", False),
        relevanceScore=server.get("relevance_score", 0.0),
        matchContext=server.get("match_context"),
        matchingTools=matching_tools,
    )


def _map_tool(tool: dict) -> ToolSearchResult:
    return ToolSearchResult(
        serverPath=tool.get("server_path", ""),
        serverName=tool.get("server_name", ""),
        toolName=tool.get("tool_name", ""),
        description=tool.get("description"),
        relevanceScore=tool.get("relevance_score", 0.0),
        matchContext=tool.get("match_context"),
    )


def _map_agent(doc: dict) -> AgentSearchResult:
    return AgentSearchResult(
        agentId=doc.get("agent_id"),
        path=doc.get("path", ""),
        agentName=doc.get("card_name") or doc.get("agent_name", ""),
        description=doc.get("description"),
        tags=doc.get("tags") or [],
        enabled=doc.get("enabled", False),
        relevanceScore=doc.get("relevance_score") or 0.0,
        matchContext=doc.get("match_context"),
    )


def _map_skill(doc: dict) -> SkillSearchResult:
    return SkillSearchResult(
        agentId=doc.get("agent_id"),
        agentPath=doc.get("path", ""),
        agentName=doc.get("card_name") or doc.get("agent_name", ""),
        skillName=doc.get("skill_name", ""),
        description=doc.get("description"),
        relevanceScore=doc.get("relevance_score") or 0.0,
        matchContext=doc.get("match_context"),
    )


@router.post(
    "/search",
    response_model=SemanticSearchResponse,
    response_model_by_alias=True,
    summary="Unified semantic search for MCP servers/tools and A2A agents/skills",
)
@track_registry_operation("search", resource_type="semantic")
async def semantic_search(
    request: Request,
    search_request: SemanticSearchRequest,
    search_service: SearchService = Depends(get_search_service),
) -> SemanticSearchResponse:
    """Run a unified semantic search across MCP servers/tools and A2A agents/skills."""
    if not request.state.is_authenticated:
        raise HTTPException(detail="Not authenticated", status_code=401)

    try:
        raw = await search_service.semantic_search(
            query=search_request.query,
            entity_types=search_request.entityTypes,
            max_results=search_request.maxResults,
            include_disabled=search_request.includeDisabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("Search service unavailable: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic search is temporarily unavailable. Please try again later.",
        ) from exc
    except Exception as exc:
        logger.error("Semantic search failed unexpectedly: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc

    servers = [_map_server(s) for s in raw.get("servers", [])]
    tools = [_map_tool(t) for t in raw.get("tools", [])]
    agents = [_map_agent(a) for a in raw.get("agents", [])]
    skills = [_map_skill(s) for s in raw.get("skills", [])]

    return SemanticSearchResponse(
        query=search_request.query.strip(),
        servers=servers,
        tools=tools,
        agents=agents,
        skills=skills,
        totalServers=len(servers),
        totalTools=len(tools),
        totalAgents=len(agents),
        totalSkills=len(skills),
    )
