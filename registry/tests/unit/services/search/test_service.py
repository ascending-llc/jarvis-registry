"""Unit tests for SearchService.

These cover the search logic extracted out of api/v1/search_routes.py:
- search_entities(): flat, repo-based discovery used by mcpgw tools.
- semantic_search(): structured MCP (via vector_service.search_mixed) + A2A
  (via a2a_agent_repo) search used by the HTTP POST /search route.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from registry.services.search.service import SearchRequest, SearchService
from registry_pkgs.models.enums import A2AEntityType, MCPEntityType
from registry_pkgs.vector.enum.enums import SearchType


def _make_service(*, vector_service=None, mcp_server_repo=None, a2a_agent_repo=None) -> SearchService:
    return SearchService(
        vector_service=vector_service or MagicMock(),
        mcp_server_repo=mcp_server_repo or MagicMock(),
        a2a_agent_repo=a2a_agent_repo or MagicMock(),
    )


# ---------------------------------------------------------------------------
# search_entities (flat, repo-based — used by mcpgw discover_* tools)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_request_accepts_a2a_entity_types():
    request = SearchRequest(type_list=[A2AEntityType.AGENT, A2AEntityType.SKILL])

    assert request.type_list == [A2AEntityType.AGENT, A2AEntityType.SKILL]


@pytest.mark.asyncio
async def test_search_entities_uses_vector_directly_for_mcp():
    """MCP tool search goes through the vector repo and returns tool docs directly."""
    tool_results = [
        {"server_id": "id-1", "server_name": "github", "entity_type": "tool", "tool_name": "search_code"},
        {"server_id": "id-1", "server_name": "github", "entity_type": "tool", "tool_name": "create_pr"},
    ]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(return_value=tool_results)
    service = _make_service(mcp_server_repo=mcp_server_repo)

    response = await service.search_entities(
        SearchRequest(query="github", top_n=2, search_type=SearchType.HYBRID, type_list=[MCPEntityType.TOOL]),
        {"username": "tester"},
    )

    mcp_server_repo.asearch_with_rerank.assert_awaited_once()
    assert response["total"] == 2
    assert response["results"] == tool_results


@pytest.mark.asyncio
async def test_search_entities_filters_metadata_when_query_is_empty():
    filter_results = [{"server_id": "id-1", "server_name": "server-1", "entity_type": "tool", "tool_name": "search"}]
    mcp_server_repo = MagicMock()
    mcp_server_repo.afilter = AsyncMock(return_value=filter_results)
    mcp_server_repo.asearch_with_rerank = AsyncMock(
        side_effect=AssertionError("vector search should not run for empty query")
    )
    service = _make_service(mcp_server_repo=mcp_server_repo)

    response = await service.search_entities(
        SearchRequest(query="", top_n=5, search_type=SearchType.HYBRID, type_list=[MCPEntityType.TOOL]),
        {"username": "tester"},
    )

    mcp_server_repo.afilter.assert_awaited_once_with(filters={"enabled": True, "entity_type": ["tool"]}, limit=5)
    assert response["query"] == ""
    assert response["total"] == 1
    assert response["results"] == filter_results


@pytest.mark.asyncio
async def test_search_entities_routes_a2a_types_to_a2a_repo():
    a2a_results = [
        {
            "agent_id": "agent-1",
            "agent_name": "deep-intel",
            "path": "/deep-intel",
            "entity_type": A2AEntityType.AGENT,
            "relevance_score": 0.91,
        }
    ]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(
        side_effect=AssertionError("MCP repo should not run for pure A2A searches")
    )
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=a2a_results)
    service = _make_service(mcp_server_repo=mcp_server_repo, a2a_agent_repo=a2a_agent_repo)

    response = await service.search_entities(
        SearchRequest(query="deep intel", top_n=3, search_type=SearchType.HYBRID, type_list=[A2AEntityType.AGENT]),
        {"username": "tester"},
    )

    a2a_agent_repo.asearch_with_rerank.assert_awaited_once()
    assert response["total"] == 1
    assert response["results"] == a2a_results


@pytest.mark.asyncio
async def test_search_entities_skips_a2a_on_runtime_error():
    """An A2A vector outage must not break a mixed MCP + A2A discovery call."""
    mcp_results = [
        {"server_id": "id-1", "server_name": "github", "entity_type": "tool", "tool_name": "x", "relevance_score": 0.9}
    ]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(return_value=mcp_results)
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(side_effect=RuntimeError("A2A offline"))
    service = _make_service(mcp_server_repo=mcp_server_repo, a2a_agent_repo=a2a_agent_repo)

    response = await service.search_entities(
        SearchRequest(
            query="github",
            top_n=5,
            search_type=SearchType.HYBRID,
            type_list=[MCPEntityType.TOOL, A2AEntityType.AGENT],
        ),
        {"username": "tester"},
    )

    assert response["total"] == 1
    assert response["results"] == mcp_results


# ---------------------------------------------------------------------------
# semantic_search (structured — used by HTTP POST /search)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_search_mcp_uses_search_mixed():
    """MCP servers/tools come from vector_service.search_mixed unchanged."""
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(
        return_value={
            "servers": [{"path": "/demo", "server_name": "Demo", "relevance_score": 0.9, "matching_tools": []}],
            "tools": [{"server_path": "/demo", "server_name": "Demo", "tool_name": "alpha", "relevance_score": 0.8}],
        }
    )
    service = _make_service(vector_service=vector_service)

    result = await service.semantic_search(query="alpha", entity_types=["mcp_server", "tool"], max_results=5)

    vector_service.search_mixed.assert_awaited_once_with(
        query="alpha", entity_types=["mcp_server", "tool"], max_results=5
    )
    assert len(result["servers"]) == 1
    assert len(result["tools"]) == 1
    assert result["agents"] == []
    assert result["skills"] == []


@pytest.mark.asyncio
async def test_semantic_search_splits_a2a_into_agents_and_skills():
    """A2A docs from a2a_agent_repo are split by entity_type into agents and skills."""
    agent_doc = {
        "agent_id": "agent-1",
        "agent_name": "deep-intel",
        "path": "/deep-intel",
        "entity_type": A2AEntityType.AGENT,
        "relevance_score": 0.85,
    }
    skill_doc = {
        "agent_id": "agent-1",
        "agent_name": "deep-intel",
        "path": "/deep-intel",
        "entity_type": A2AEntityType.SKILL,
        "skill_name": "web_search",
        "relevance_score": 0.75,
    }
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(return_value={"servers": [], "tools": []})
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=[agent_doc, skill_doc])
    service = _make_service(vector_service=vector_service, a2a_agent_repo=a2a_agent_repo)

    result = await service.semantic_search(query="intel research", entity_types=["a2a_agent", "skill"], max_results=5)

    a2a_agent_repo.asearch_with_rerank.assert_awaited_once()
    assert result["agents"] == [agent_doc]
    assert result["skills"] == [skill_doc]


@pytest.mark.asyncio
async def test_semantic_search_degrades_gracefully_when_a2a_unavailable():
    """An A2A vector outage must not break the MCP half of the response."""
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(
        return_value={"servers": [{"path": "/demo", "server_name": "Demo", "relevance_score": 0.9}], "tools": []}
    )
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(side_effect=RuntimeError("A2A offline"))
    service = _make_service(vector_service=vector_service, a2a_agent_repo=a2a_agent_repo)

    result = await service.semantic_search(query="demo", entity_types=["mcp_server", "a2a_agent"], max_results=5)

    assert len(result["servers"]) == 1
    assert result["agents"] == []
    assert result["skills"] == []
