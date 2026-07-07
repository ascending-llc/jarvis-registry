"""Unit tests for SearchService.

These cover the search logic extracted out of api/v1/search_routes.py:
- search_entities(): flat, repo-based discovery used by mcpgw tools.
- semantic_search(): structured MCP (via vector_service.search_mixed) + A2A
  (via a2a_agent_repo) search used by the HTTP POST /search route.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry.services.search.service import SearchRequest, SearchService
from registry_pkgs.models.enums import A2AEntityType, MCPEntityType
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.vector.enum.enums import SearchType


def _make_permissive_acl_service() -> MagicMock:
    """Return an ACLService mock that allows all resources (for tests that don't care about ACL)."""
    acl = MagicMock()
    acl.get_accessible_resource_ids = AsyncMock(return_value=["id-1", "id-2", "agent-1", "agent-2"])
    return acl


def _make_service(*, vector_service=None, mcp_server_repo=None, a2a_agent_repo=None, acl_service=None) -> SearchService:
    return SearchService(
        vector_service=vector_service or MagicMock(),
        mcp_server_repo=mcp_server_repo or MagicMock(),
        a2a_agent_repo=a2a_agent_repo or MagicMock(),
        acl_service=acl_service or _make_permissive_acl_service(),
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

    call_kwargs = mcp_server_repo.afilter.call_args.kwargs
    assert call_kwargs["filters"]["enabled"] is True
    assert call_kwargs["filters"]["entity_type"] == ["tool"]
    assert "server_id" in call_kwargs["filters"]
    assert call_kwargs["limit"] == 5
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


@pytest.mark.asyncio
async def test_search_entities_skips_mcp_when_acl_lookup_fails():
    """An MCP ACL outage skips MCP results without breaking A2A discovery."""
    a2a_results = [{"agent_id": "agent-1", "agent_name": "analyst", "entity_type": "agent", "relevance_score": 0.8}]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock()
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=a2a_results)
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(side_effect=[RuntimeError("acl down"), ["agent-1"]])
    service = _make_service(
        mcp_server_repo=mcp_server_repo,
        a2a_agent_repo=a2a_agent_repo,
        acl_service=acl_service,
    )

    response = await service.search_entities(
        SearchRequest(
            query="analyst",
            top_n=5,
            search_type=SearchType.HYBRID,
            type_list=[MCPEntityType.TOOL, A2AEntityType.AGENT],
        ),
        {"username": "tester"},
    )

    mcp_server_repo.asearch_with_rerank.assert_not_awaited()
    a2a_agent_repo.asearch_with_rerank.assert_awaited_once()
    assert response["total"] == 1
    assert response["results"] == a2a_results


@pytest.mark.asyncio
async def test_search_entities_skips_a2a_when_acl_lookup_fails():
    """An A2A ACL outage skips A2A results without breaking MCP discovery."""
    mcp_results = [
        {"server_id": "id-1", "server_name": "github", "entity_type": "tool", "tool_name": "x", "relevance_score": 0.9}
    ]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(return_value=mcp_results)
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock()
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(side_effect=[["id-1"], RuntimeError("acl down")])
    service = _make_service(
        mcp_server_repo=mcp_server_repo,
        a2a_agent_repo=a2a_agent_repo,
        acl_service=acl_service,
    )

    response = await service.search_entities(
        SearchRequest(
            query="github",
            top_n=5,
            search_type=SearchType.HYBRID,
            type_list=[MCPEntityType.TOOL, A2AEntityType.AGENT],
        ),
        {"username": "tester"},
    )

    mcp_server_repo.asearch_with_rerank.assert_awaited_once()
    a2a_agent_repo.asearch_with_rerank.assert_not_awaited()
    assert response["total"] == 1
    assert response["results"] == mcp_results


# ---------------------------------------------------------------------------
# semantic_search (structured — used by HTTP POST /search)
# ---------------------------------------------------------------------------


_AUTH_CTX = {"user_id": "507f1f77bcf86cd799439011", "username": "tester"}


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

    result = await service.semantic_search(
        query="alpha", user_context=_AUTH_CTX, entity_types=["mcp_server", "tool"], max_results=5
    )

    vector_service.search_mixed.assert_awaited_once_with(
        query="alpha",
        entity_types=["mcp_server", "tool"],
        max_results=5,
        allowed_server_ids=["id-1", "id-2", "agent-1", "agent-2"],
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
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=[agent_doc, skill_doc])

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["agent-1"])

    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)

    result = await service.semantic_search(
        query="intel research", user_context=_AUTH_CTX, entity_types=["a2a_agent", "skill"], max_results=5
    )

    a2a_agent_repo.asearch_with_rerank.assert_awaited_once_with(
        query="intel research",
        k=5,
        candidate_k=50,
        search_type=SearchType.HYBRID,
        filters={
            "entity_type": [A2AEntityType.AGENT, A2AEntityType.SKILL],
            "enabled": True,
            "agent_id": {"$in": ["agent-1"]},
        },
    )
    assert result["agents"] == [agent_doc]
    assert result["skills"] == [skill_doc]


@pytest.mark.asyncio
async def test_semantic_search_passes_allowed_server_ids_to_search_mixed():
    """MCP results in semantic_search must be ACL-filtered, like the A2A half already is."""
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(return_value={"servers": [], "tools": []})

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["s1"])

    service = _make_service(vector_service=vector_service, acl_service=acl_service)

    await service.semantic_search(
        query="alpha", user_context=_AUTH_CTX, entity_types=["mcp_server", "tool"], max_results=5
    )

    vector_service.search_mixed.assert_awaited_once_with(
        query="alpha", entity_types=["mcp_server", "tool"], max_results=5, allowed_server_ids=["s1"]
    )


@pytest.mark.asyncio
async def test_semantic_search_skips_mcp_when_no_accessible_servers():
    """If the user has no accessible MCP servers, skip the Weaviate query entirely."""
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock()

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])

    service = _make_service(vector_service=vector_service, acl_service=acl_service)

    result = await service.semantic_search(
        query="alpha", user_context=_AUTH_CTX, entity_types=["mcp_server", "tool"], max_results=5
    )

    vector_service.search_mixed.assert_not_awaited()
    assert result["servers"] == []
    assert result["tools"] == []


@pytest.mark.asyncio
async def test_semantic_search_skips_a2a_when_no_accessible_agents():
    """Mirrors the MCP skip behavior: no accessible agents -> skip the A2A query entirely."""
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock()

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])

    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)

    result = await service.semantic_search(
        query="intel", user_context=_AUTH_CTX, entity_types=["a2a_agent", "skill"], max_results=5
    )

    a2a_agent_repo.asearch_with_rerank.assert_not_awaited()
    assert result["agents"] == []
    assert result["skills"] == []


@pytest.mark.asyncio
async def test_semantic_search_propagates_acl_failure_for_mcp():
    """An ACL/DB outage must surface as an error (RuntimeError -> HTTP 503), not as empty results."""
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(
        side_effect=RuntimeError("Failed to fetch accessible resources")
    )

    service = _make_service(acl_service=acl_service)

    with pytest.raises(RuntimeError):
        await service.semantic_search(
            query="alpha", user_context=_AUTH_CTX, entity_types=["mcp_server", "tool"], max_results=5
        )


@pytest.mark.asyncio
async def test_semantic_search_propagates_acl_failure_for_a2a():
    """Same ACL-failure-must-surface guarantee on the A2A side."""
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(
        side_effect=RuntimeError("Failed to fetch accessible resources")
    )

    service = _make_service(acl_service=acl_service)

    with pytest.raises(RuntimeError):
        await service.semantic_search(
            query="intel", user_context=_AUTH_CTX, entity_types=["a2a_agent", "skill"], max_results=5
        )


@pytest.mark.asyncio
async def test_semantic_search_awaits_a2a_side_even_when_mcp_acl_fails():
    """A failure on one side must not leave the other side's coroutine running unawaited.

    asyncio.gather's default behavior (no return_exceptions) raises on first failure
    without waiting for the sibling task, orphaning it. Regression guard: the A2A
    side must have actually completed by the time semantic_search raises.
    """
    a2a_completed = False

    async def slow_a2a_search(**kwargs):
        nonlocal a2a_completed
        await asyncio.sleep(0)
        a2a_completed = True
        return []

    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(side_effect=slow_a2a_search)

    acl_service = MagicMock()

    async def accessible_ids(*, user_id, resource_type):
        if resource_type == RegistryResourceType.MCP_SERVER.value:
            raise RuntimeError("Failed to fetch accessible resources")
        await asyncio.sleep(0)
        return ["a1"]

    acl_service.get_accessible_resource_ids = AsyncMock(side_effect=accessible_ids)

    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)

    with pytest.raises(RuntimeError):
        await service.semantic_search(
            query="intel", user_context=_AUTH_CTX, entity_types=["mcp_server", "a2a_agent"], max_results=5
        )

    assert a2a_completed is True


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

    result = await service.semantic_search(
        query="demo", user_context=_AUTH_CTX, entity_types=["mcp_server", "a2a_agent"], max_results=5
    )

    assert len(result["servers"]) == 1
    assert result["agents"] == []
    assert result["skills"] == []


@pytest.mark.asyncio
async def test_search_entities_adds_server_id_acl_filter():
    """ACL-allowed server IDs are injected into the Weaviate filter."""
    tool_results = [{"server_id": "s1", "server_name": "github", "entity_type": "tool", "tool_name": "t"}]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(return_value=tool_results)

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["s1", "s2"])

    service = _make_service(mcp_server_repo=mcp_server_repo, acl_service=acl_service)
    user_ctx = {"user_id": "507f1f77bcf86cd799439011", "username": "alice"}

    await service.search_entities(
        SearchRequest(query="github", top_n=2, type_list=[MCPEntityType.TOOL]),
        user_ctx,
    )

    call_kwargs = mcp_server_repo.asearch_with_rerank.call_args.kwargs
    assert call_kwargs["filters"]["server_id"] == {"$in": ["s1", "s2"]}


@pytest.mark.asyncio
async def test_search_entities_returns_empty_when_no_accessible_mcp_servers():
    """If user has no accessible MCP servers, return empty results without querying Weaviate."""
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock()

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])

    service = _make_service(mcp_server_repo=mcp_server_repo, acl_service=acl_service)
    user_ctx = {"user_id": "507f1f77bcf86cd799439011", "username": "alice"}

    result = await service.search_entities(
        SearchRequest(query="github", top_n=5, type_list=[MCPEntityType.TOOL]),
        user_ctx,
    )

    mcp_server_repo.asearch_with_rerank.assert_not_awaited()
    assert result["total"] == 0
    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_entities_adds_agent_id_acl_filter():
    """ACL-allowed agent IDs are injected into the Weaviate filter for A2A search."""
    agent_results = [{"agent_id": "a1", "agent_name": "analyst", "entity_type": "agent"}]
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=agent_results)

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["a1"])

    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)
    user_ctx = {"user_id": "507f1f77bcf86cd799439011", "username": "alice"}

    await service.search_entities(
        SearchRequest(query="analyst", top_n=3, type_list=[A2AEntityType.AGENT]),
        user_ctx,
    )

    call_kwargs = a2a_agent_repo.asearch_with_rerank.call_args.kwargs
    assert call_kwargs["filters"]["agent_id"] == {"$in": ["a1"]}


@pytest.mark.asyncio
async def test_search_entities_returns_empty_when_no_accessible_agents():
    """If user has no accessible A2A agents, return empty without querying Weaviate."""
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock()

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])

    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)
    user_ctx = {"user_id": "507f1f77bcf86cd799439011", "username": "alice"}

    result = await service.search_entities(
        SearchRequest(query="analyst", top_n=3, type_list=[A2AEntityType.AGENT]),
        user_ctx,
    )

    a2a_agent_repo.asearch_with_rerank.assert_not_awaited()
    assert result["total"] == 0
    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_entities_handles_none_user_id_as_public_only():
    """When user_id is None (unauthenticated), ACL lookup uses None and PUBLIC entries are returned."""
    tool_results = [{"server_id": "public-s1", "entity_type": "tool"}]
    mcp_server_repo = MagicMock()
    mcp_server_repo.asearch_with_rerank = AsyncMock(return_value=tool_results)

    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["public-s1"])

    service = _make_service(mcp_server_repo=mcp_server_repo, acl_service=acl_service)
    user_ctx = {"user_id": None, "username": None}

    await service.search_entities(
        SearchRequest(query="tools", top_n=5, type_list=[MCPEntityType.TOOL]),
        user_ctx,
    )

    acl_service.get_accessible_resource_ids.assert_awaited_once_with(
        user_id=None, resource_type=RegistryResourceType.MCP_SERVER.value
    )


# ---------------------------------------------------------------------------
# Helper methods backing semantic_search: _accessible_ids_for_semantic,
# _search_mcp_for_semantic, _search_a2a_for_semantic.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accessible_ids_for_semantic_returns_ids_when_present():
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["s1", "s2"])
    service = _make_service(acl_service=acl_service)

    result = await service._accessible_ids_for_semantic(_AUTH_CTX, RegistryResourceType.MCP_SERVER.value, "MCP servers")

    assert result == ["s1", "s2"]


@pytest.mark.asyncio
async def test_accessible_ids_for_semantic_returns_none_when_empty():
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])
    service = _make_service(acl_service=acl_service)

    result = await service._accessible_ids_for_semantic(_AUTH_CTX, RegistryResourceType.MCP_SERVER.value, "MCP servers")

    assert result is None


@pytest.mark.asyncio
async def test_accessible_ids_for_semantic_propagates_acl_failure():
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(side_effect=RuntimeError("db down"))
    service = _make_service(acl_service=acl_service)

    with pytest.raises(RuntimeError):
        await service._accessible_ids_for_semantic(_AUTH_CTX, RegistryResourceType.MCP_SERVER.value, "MCP servers")


@pytest.mark.asyncio
async def test_search_mcp_for_semantic_returns_empty_when_no_mcp_types():
    """No MCP entity types requested -> skip without even checking ACL."""
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(side_effect=AssertionError("ACL should not be checked"))
    service = _make_service(acl_service=acl_service)

    servers, tools = await service._search_mcp_for_semantic("alpha", [], 5, _AUTH_CTX)

    assert (servers, tools) == ([], [])


@pytest.mark.asyncio
async def test_search_mcp_for_semantic_returns_empty_when_no_accessible_servers():
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(side_effect=AssertionError("should not query Weaviate"))
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])
    service = _make_service(vector_service=vector_service, acl_service=acl_service)

    servers, tools = await service._search_mcp_for_semantic("alpha", [MCPEntityType.TOOL], 5, _AUTH_CTX)

    assert (servers, tools) == ([], [])


@pytest.mark.asyncio
async def test_search_mcp_for_semantic_passes_allowed_server_ids():
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(
        return_value={"servers": [{"path": "/demo"}], "tools": [{"tool_name": "t"}]}
    )
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["s1"])
    service = _make_service(vector_service=vector_service, acl_service=acl_service)

    servers, tools = await service._search_mcp_for_semantic("alpha", [MCPEntityType.TOOL], 5, _AUTH_CTX)

    vector_service.search_mixed.assert_awaited_once_with(
        query="alpha", entity_types=[MCPEntityType.TOOL], max_results=5, allowed_server_ids=["s1"]
    )
    assert servers == [{"path": "/demo"}]
    assert tools == [{"tool_name": "t"}]


@pytest.mark.asyncio
async def test_search_mcp_for_semantic_degrades_on_unexpected_exception():
    """A non-ACL failure (e.g. Weaviate query error) must degrade to empty, not raise."""
    vector_service = MagicMock()
    vector_service.search_mixed = AsyncMock(side_effect=ValueError("bad query"))
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["s1"])
    service = _make_service(vector_service=vector_service, acl_service=acl_service)

    servers, tools = await service._search_mcp_for_semantic("alpha", [MCPEntityType.TOOL], 5, _AUTH_CTX)

    assert (servers, tools) == ([], [])


@pytest.mark.asyncio
async def test_search_a2a_for_semantic_returns_empty_when_no_a2a_types():
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(side_effect=AssertionError("ACL should not be checked"))
    service = _make_service(acl_service=acl_service)

    agents, skills = await service._search_a2a_for_semantic("intel", [], 5, False, _AUTH_CTX)

    assert (agents, skills) == ([], [])


@pytest.mark.asyncio
async def test_search_a2a_for_semantic_returns_empty_when_no_accessible_agents():
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(side_effect=AssertionError("should not query Weaviate"))
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])
    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)

    agents, skills = await service._search_a2a_for_semantic("intel", [A2AEntityType.AGENT], 5, False, _AUTH_CTX)

    assert (agents, skills) == ([], [])


@pytest.mark.asyncio
async def test_search_a2a_for_semantic_passes_allowed_agent_ids():
    agent_doc = {"agent_id": "a1", "entity_type": A2AEntityType.AGENT}
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(return_value=[agent_doc])
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["a1"])
    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)

    agents, skills = await service._search_a2a_for_semantic("intel", [A2AEntityType.AGENT], 5, False, _AUTH_CTX)

    call_kwargs = a2a_agent_repo.asearch_with_rerank.call_args.kwargs
    assert call_kwargs["filters"]["agent_id"] == {"$in": ["a1"]}
    assert agents == [agent_doc]
    assert skills == []


@pytest.mark.asyncio
async def test_search_a2a_for_semantic_degrades_on_runtime_error():
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.asearch_with_rerank = AsyncMock(side_effect=RuntimeError("A2A offline"))
    acl_service = MagicMock()
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=["a1"])
    service = _make_service(a2a_agent_repo=a2a_agent_repo, acl_service=acl_service)

    agents, skills = await service._search_a2a_for_semantic("intel", [A2AEntityType.AGENT], 5, False, _AUTH_CTX)

    assert (agents, skills) == ([], [])
