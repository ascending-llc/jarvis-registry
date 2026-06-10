"""Unit tests for the POST /search route handler.

Search business logic lives in SearchService (see
tests/unit/services/search/test_service.py). These tests only cover the thin
handler: delegation to the service and mapping the service result onto the
SemanticSearchResponse model.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.conftest import make_container

from registry.api.v1.search_routes import SemanticSearchRequest, semantic_search


@pytest.mark.asyncio
async def test_semantic_search_maps_service_result_to_response():
    """Handler delegates to SearchService and maps MCP + A2A results."""
    request = make_container(state=make_container(is_authenticated=True, user={"username": "tester"}))
    search_service = MagicMock()
    search_service.semantic_search = AsyncMock(
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
            "agents": [
                {
                    "agent_id": "agent-1",
                    "path": "/deep-intel",
                    "agent_name": "deep-intel",
                    "description": "Deep intel agent",
                    "tags": ["research"],
                    "enabled": True,
                    "relevance_score": 0.85,
                }
            ],
            "skills": [
                {
                    "agent_id": "agent-1",
                    "path": "/deep-intel",
                    "agent_name": "deep-intel",
                    "skill_name": "web_search",
                    "relevance_score": 0.75,
                }
            ],
        }
    )

    response = await semantic_search(
        request=request,
        search_request=SemanticSearchRequest(
            query="test", entityTypes=["mcp_server", "a2a_agent", "skill"], maxResults=5
        ),
        search_service=search_service,
    )

    search_service.semantic_search.assert_awaited_once_with(
        query="test",
        entity_types=["mcp_server", "a2a_agent", "skill"],
        max_results=5,
        include_disabled=False,
    )
    assert response.totalServers == 1
    assert response.servers[0].serverName == "test-server"
    assert response.totalTools == 0
    assert response.totalAgents == 1
    assert response.agents[0].agentName == "deep-intel"
    assert response.agents[0].tags == ["research"]
    assert response.agents[0].isEnabled is True
    assert response.totalSkills == 1
    assert response.skills[0].skillName == "web_search"


@pytest.mark.asyncio
async def test_semantic_search_requires_authentication():
    """Unauthenticated requests are rejected before reaching the service."""
    from fastapi import HTTPException

    request = make_container(state=make_container(is_authenticated=False, user=None))
    search_service = MagicMock()
    search_service.semantic_search = AsyncMock(
        side_effect=AssertionError("service must not be called when unauthenticated")
    )

    with pytest.raises(HTTPException) as exc_info:
        await semantic_search(
            request=request,
            search_request=SemanticSearchRequest(query="test"),
            search_service=search_service,
        )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_semantic_search_wraps_unexpected_errors():
    from fastapi import HTTPException

    request = make_container(state=make_container(is_authenticated=True, user={"username": "tester"}))
    search_service = MagicMock()
    search_service.semantic_search = AsyncMock(side_effect=AttributeError("bad search mapping"))

    with pytest.raises(HTTPException) as exc_info:
        await semantic_search(
            request=request,
            search_request=SemanticSearchRequest(query="test"),
            search_service=search_service,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"
