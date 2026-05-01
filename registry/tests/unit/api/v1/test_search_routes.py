from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.conftest import make_container

from registry.api.v1.search_routes import (
    AgentSemanticSearchRequest,
    SearchRequest,
    SemanticSearchRequest,
    search_agents,
    search_servers,
    semantic_search,
)
from registry_pkgs.models.enums import A2AEntityType, MCPEntityType
from registry_pkgs.vector.enum.enums import SearchType


@pytest.mark.asyncio
async def test_semantic_search_uses_injected_vector_service():
    request = make_container(
        state=make_container(
            is_authenticated=True,
            user={"username": "tester"},
        )
    )
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(
        return_value={
            "servers": [
                {
                    "path": "/test-server",
                    "server_name": "test-server",
                    "description": "Test server",
                    "tags": ["test"],
                    "num_tools": 1,
                    "is_enabled": True,
                    "relevance_score": 0.9,
                    "matching_tools": [],
                }
            ],
            "tools": [],
        }
    )

    response = await semantic_search(
        request=request,
        search_request=SemanticSearchRequest(query="test", entityTypes=["mcp_server"], maxResults=5),
        vector_service=vector_service,
    )

    vector_service.search_mixed.assert_awaited_once_with(
        query="test",
        entity_types=["mcp_server"],
        max_results=5,
    )
    assert response.totalServers == 1
    assert response.servers[0].serverName == "test-server"


@pytest.mark.asyncio
async def test_search_servers_uses_vector_directly():
    """Tool search must go through vector search and return tool_name directly."""
    tool_results = [
        {"server_id": "id-1", "server_name": "github", "entity_type": "tool", "tool_name": "search_code"},
        {"server_id": "id-1", "server_name": "github", "entity_type": "tool", "tool_name": "create_pr"},
    ]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(return_value=tool_results)

    response = await search_servers(
        search=SearchRequest(query="github", top_n=2, search_type=SearchType.HYBRID, type_list=[MCPEntityType.TOOL]),
        user_context={"username": "tester"},
        mcp_server_repo=mcp_server_repo,
    )

    mcp_server_repo.asearch_with_rerank.assert_awaited_once()
    assert response["total"] == 2
    assert response["results"] == tool_results


@pytest.mark.asyncio
async def test_search_servers_filters_metadata_when_tool_query_is_empty():
    filter_results = [{"server_id": "id-1", "server_name": "server-1", "entity_type": "tool", "tool_name": "search"}]
    mcp_server_repo = MagicMock()
    mcp_server_repo.afilter = AsyncMock(return_value=filter_results)
    mcp_server_repo.asearch_with_rerank = AsyncMock(
        side_effect=AssertionError("vector search should not run for empty non-server query")
    )

    response = await search_servers(
        search=SearchRequest(query="", top_n=5, search_type=SearchType.HYBRID, type_list=[MCPEntityType.TOOL]),
        user_context={"username": "tester"},
        mcp_server_repo=mcp_server_repo,
    )

    mcp_server_repo.afilter.assert_awaited_once_with(filters={"enabled": True, "entity_type": ["tool"]}, limit=5)
    assert response["query"] == ""
    assert response["total"] == 1
    assert response["results"] == filter_results


@pytest.mark.asyncio
async def test_search_agents_returns_agents_and_skills():
    """search_agents splits raw docs by entity_type into agents and skills."""
    agent_doc = {
        "agent_id": "agent-1",
        "agent_name": "deep-intel",
        "path": "/deep-intel",
        "entity_type": A2AEntityType.AGENT,
        "relevance_score": 0.85,
        "description": "Deep intel agent",
        "tags": ["research"],
        "is_enabled": True,
    }
    skill_doc = {
        "agent_id": "agent-1",
        "agent_name": "deep-intel",
        "path": "/deep-intel",
        "entity_type": A2AEntityType.SKILL,
        "skill_name": "web_search",
        "relevance_score": 0.75,
        "description": "Search the web",
        "is_enabled": True,
    }
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=[agent_doc, skill_doc])

    request = make_container(state=make_container(is_authenticated=True, user={"username": "tester"}))

    response = await search_agents(
        request=request,
        search_request=AgentSemanticSearchRequest(query="intel research", maxResults=5),
        a2a_agent_repo=a2a_agent_repo,
    )

    assert response.totalAgents == 1
    assert response.totalSkills == 1
    assert response.agents[0].agentName == "deep-intel"
    assert response.agents[0].tags == ["research"]
    assert response.skills[0].skillName == "web_search"


@pytest.mark.asyncio
async def test_search_agents_uses_afilter_for_empty_query():
    """search_agents falls back to afilter when query is empty."""
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.afilter = AsyncMock(return_value=[])
    a2a_agent_repo.asearch_with_rerank = AsyncMock(
        side_effect=AssertionError("vector search should not run for empty query")
    )

    request = make_container(state=make_container(is_authenticated=True, user={"username": "tester"}))

    response = await search_agents(
        request=request,
        search_request=AgentSemanticSearchRequest(query="", maxResults=10),
        a2a_agent_repo=a2a_agent_repo,
    )

    a2a_agent_repo.afilter.assert_awaited_once_with(
        filters={"is_enabled": True, "entity_type": list(A2AEntityType)}, limit=10
    )
    assert response.totalAgents == 0
    assert response.totalSkills == 0
