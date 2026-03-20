from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from tests.conftest import make_container

from registry.api.v1.search_routes import SearchRequest, SemanticSearchRequest, search_servers, semantic_search
from registry_pkgs.models.enums import ServerEntityType
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
        container=make_container(vector_service=vector_service),
    )

    vector_service.search_mixed.assert_awaited_once_with(
        query="test",
        entity_types=["mcp_server"],
        max_results=5,
    )
    assert response.totalServers == 1
    assert response.servers[0].serverName == "test-server"


@pytest.mark.asyncio
async def test_search_servers_uses_injected_server_service():
    server_service = MagicMock()
    server_service.get_server_by_id = AsyncMock(side_effect=[{"serverName": "server-1"}, {"serverName": "server-2"}])

    with (
        patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank",
            new=AsyncMock(return_value=[{"server_id": "id-1"}, {"server_id": "id-2"}]),
        ),
    ):
        response = await search_servers(
            search=SearchRequest(
                query="test",
                top_n=2,
                search_type=SearchType.HYBRID,
                type_list=[ServerEntityType.SERVER],
                include_disabled=False,
            ),
            user_context={"username": "tester"},
            container=make_container(server_service=server_service),
        )

    assert server_service.get_server_by_id.await_count == 2
    assert response["total"] == 2
    assert len(response["servers"]) == 2


class _FakeServerModel(BaseModel):
    serverName: str
    path: str


@pytest.mark.asyncio
async def test_search_servers_serializes_server_models_to_dicts():
    fake_server = _FakeServerModel(serverName="server-1", path="/server-1")

    with (
        patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank",
            new=AsyncMock(return_value=[{"server_id": "id-1"}]),
        ),
        patch.object(server_service := MagicMock(), "get_server_by_id", new=AsyncMock(return_value=fake_server)),
    ):
        response = await search_servers(
            search=SearchRequest(
                query="github",
                top_n=1,
                search_type=SearchType.HYBRID,
                type_list=[ServerEntityType.SERVER],
                include_disabled=False,
            ),
            user_context={"username": "tester"},
            container=make_container(server_service=server_service),
        )

    assert response["total"] == 1
    assert response["servers"] == [{"serverName": "server-1", "path": "/server-1"}]


@pytest.mark.asyncio
async def test_search_servers_lists_servers_when_query_is_empty():
    fake_server = _FakeServerModel(serverName="server-1", path="/server-1")
    list_servers = AsyncMock(return_value=([fake_server], 1))

    with (
        patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank",
            new=AsyncMock(side_effect=AssertionError("vector search should not run for empty server query")),
        ),
    ):
        response = await search_servers(
            search=SearchRequest(
                query="",
                top_n=5,
                search_type=SearchType.HYBRID,
                type_list=[ServerEntityType.SERVER],
                include_disabled=False,
            ),
            user_context={"username": "tester"},
            container=make_container(server_service=make_container(list_servers=list_servers)),
        )

    list_servers.assert_awaited_once_with(query=None, status="active", page=1, per_page=5)
    assert response["query"] == ""
    assert response["total"] == 1
    assert response["servers"] == [{"serverName": "server-1", "path": "/server-1"}]


@pytest.mark.asyncio
async def test_search_servers_filters_metadata_when_non_server_query_is_empty():
    filter_results = [{"server_id": "id-1", "server_name": "server-1", "entity_type": "tool", "tool_name": "search"}]

    with (
        patch(
            "registry.api.v1.search_routes.mcp_server_repo.afilter",
            new=AsyncMock(return_value=filter_results),
        ) as afilter,
        patch(
            "registry.api.v1.search_routes.mcp_server_repo.asearch_with_rerank",
            new=AsyncMock(side_effect=AssertionError("vector search should not run for empty non-server query")),
        ),
    ):
        response = await search_servers(
            search=SearchRequest(
                query="",
                top_n=5,
                search_type=SearchType.HYBRID,
                type_list=[ServerEntityType.TOOL],
                include_disabled=False,
            ),
            user_context={"username": "tester"},
            container=make_container(server_service=MagicMock()),
        )

    afilter.assert_awaited_once_with(filters={"enabled": True, "entity_type": ["tool"]}, limit=5)
    assert response["query"] == ""
    assert response["total"] == 1
    assert response["servers"] == filter_results
