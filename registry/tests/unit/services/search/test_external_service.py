from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry.services.search.external_service import ExternalVectorSearchService
from registry_pkgs.models.enums import MCPEntityType


def _service() -> ExternalVectorSearchService:
    return ExternalVectorSearchService(mcp_server_repo=MagicMock())


def _tool_doc(*, server_id, server_name, path, tool_name, score, enabled=True, tags=None, desc="a tool"):
    """Shape returned by ExtendedMCPServer.from_document for a reranked tool document."""
    return {
        "server_id": server_id,
        "server_name": server_name,
        "entity_type": MCPEntityType.TOOL,
        "path": path,
        "is_enabled": enabled,
        "tags": tags or [],
        "relevance_score": score,
        "description": desc,
        "tool_name": tool_name,
    }


def _resource_doc(*, server_id, server_name, path, resource_name, score, desc="a resource"):
    return {
        "server_id": server_id,
        "server_name": server_name,
        "entity_type": MCPEntityType.RESOURCE,
        "path": path,
        "is_enabled": True,
        "tags": [],
        "relevance_score": score,
        "description": desc,
        "match_context": desc[:200],
        "resource_name": resource_name,
    }


def _prompt_doc(*, server_id, server_name, path, prompt_name, score, desc="a prompt"):
    return {
        "server_id": server_id,
        "server_name": server_name,
        "entity_type": MCPEntityType.PROMPT,
        "path": path,
        "is_enabled": True,
        "tags": [],
        "relevance_score": score,
        "description": desc,
        "match_context": desc[:200],
        "prompt_name": prompt_name,
    }


def _service_returning(docs: list[dict]) -> ExternalVectorSearchService:
    repo = MagicMock()
    repo.asearch_with_rerank = AsyncMock(return_value=docs)
    return ExternalVectorSearchService(mcp_server_repo=repo)


def _fake_server(*, enabled: bool):
    return SimpleNamespace(
        serverName="test-server",
        path="/test",
        tags=["t"],
        numTools=2,
        numStars=0,
        config={"enabled": enabled, "title": "Test", "description": "desc"},
    )


def test_servers_to_results_is_enabled_from_config_enabled():
    service = _service()

    results = service._servers_to_results([_fake_server(enabled=True)])

    assert len(results) == 1
    assert results[0]["is_enabled"] is True
    # status is hard-coded; never reflects the deprecated doc field
    assert results[0]["status"] == "active"


def test_servers_to_results_disabled_when_config_enabled_false():
    service = _service()

    results = service._servers_to_results([_fake_server(enabled=False)])

    assert results[0]["is_enabled"] is False
    assert results[0]["status"] == "active"


def test_servers_to_results_disabled_when_enabled_missing():
    service = _service()
    server = SimpleNamespace(serverName="s", path="/s", tags=[], numTools=0, numStars=0, config={"title": "t"})

    results = service._servers_to_results([server])

    assert results[0]["is_enabled"] is False


async def test_search_mixed_returns_servers_and_tools_keys():
    # Regression: previously returned {"servers","agents"} and crashed on dict.config
    service = _service_returning(
        [_tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="send", score=0.9)]
    )

    result = await service.search_mixed(query="send email", entity_types=["mcp_server", "tool"], max_results=10)

    assert set(result.keys()) == {"servers", "tools"}
    assert len(result["tools"]) == 1
    assert len(result["servers"]) == 1


async def test_search_mixed_groups_tools_under_server():
    docs = [
        _tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="send", score=0.9, tags=["mail"]),
        _tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="draft", score=0.7),
        _tool_doc(server_id="s2", server_name="Weather", path="/weather", tool_name="forecast", score=0.5),
    ]
    service = _service_returning(docs)

    result = await service.search_mixed(query="send email", entity_types=["mcp_server", "tool"], max_results=10)

    assert len(result["tools"]) == 3
    servers = {s["server_name"]: s for s in result["servers"]}
    assert set(servers) == {"Email", "Weather"}
    # server score is the max of its matched tools; both matched tools are grouped under it
    assert servers["Email"]["relevance_score"] == 0.9
    assert len(servers["Email"]["matching_tools"]) == 2
    assert servers["Email"]["tags"] == ["mail"]
    assert servers["Email"]["is_enabled"] is True


async def test_search_mixed_respects_entity_filter_tool_only():
    service = _service_returning(
        [_tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="send", score=0.9)]
    )

    result = await service.search_mixed(query="x", entity_types=["tool"], max_results=10)

    assert len(result["tools"]) == 1
    assert result["servers"] == []


async def test_search_mixed_respects_entity_filter_server_only():
    service = _service_returning(
        [_tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="send", score=0.9)]
    )

    result = await service.search_mixed(query="x", entity_types=["mcp_server"], max_results=10)

    assert len(result["servers"]) == 1
    assert result["tools"] == []


async def test_search_mixed_server_search_includes_resource_and_prompt_docs():
    docs = [
        _resource_doc(
            server_id="s1",
            server_name="Docs",
            path="/docs",
            resource_name="manual",
            score=0.8,
            desc="server manual resource",
        ),
        _prompt_doc(
            server_id="s2",
            server_name="Writer",
            path="/writer",
            prompt_name="compose",
            score=0.7,
            desc="server writing prompt",
        ),
    ]
    service = _service_returning(docs)

    result = await service.search_mixed(query="manual", entity_types=["mcp_server"], max_results=10)

    servers = {s["server_name"]: s for s in result["servers"]}
    assert set(servers) == {"Docs", "Writer"}
    assert result["tools"] == []
    assert servers["Docs"]["match_context"] == "server manual resource"
    assert servers["Docs"]["matching_tools"] == []


async def test_search_mixed_tool_only_filters_out_resource_and_prompt_docs():
    repo = MagicMock()
    repo.asearch_with_rerank = AsyncMock(return_value=[])
    service = ExternalVectorSearchService(mcp_server_repo=repo)

    result = await service.search_mixed(query="manual", entity_types=["tool"], max_results=10)

    repo.asearch_with_rerank.assert_awaited_once()
    assert repo.asearch_with_rerank.await_args.kwargs["filters"] == {"entity_type": [MCPEntityType.TOOL]}
    assert result == {"servers": [], "tools": []}


async def test_search_mixed_combined_search_returns_resource_backed_server_but_only_tool_results():
    docs = [
        _resource_doc(server_id="s1", server_name="Docs", path="/docs", resource_name="manual", score=0.95),
        _tool_doc(server_id="s1", server_name="Docs", path="/docs", tool_name="search", score=0.5),
    ]
    service = _service_returning(docs)

    result = await service.search_mixed(query="docs", entity_types=["mcp_server", "tool"], max_results=10)

    assert len(result["servers"]) == 1
    assert result["servers"][0]["server_name"] == "Docs"
    assert result["servers"][0]["relevance_score"] == 0.95
    assert len(result["servers"][0]["matching_tools"]) == 1
    assert len(result["tools"]) == 1
    assert result["tools"][0]["tool_name"] == "search"


async def test_search_mixed_skips_docs_without_stable_server_key():
    service = _service_returning(
        [
            {
                "server_name": "Duplicate",
                "entity_type": MCPEntityType.TOOL,
                "relevance_score": 0.9,
                "description": "missing stable key",
                "tool_name": "send",
            }
        ]
    )

    result = await service.search_mixed(query="duplicate", entity_types=["mcp_server", "tool"], max_results=10)

    assert result["servers"] == []
    assert len(result["tools"]) == 1


async def test_search_mixed_limits_matching_tools_per_server():
    docs = [
        _tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="low", score=0.1),
        _tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="high", score=0.9),
        _tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="mid", score=0.5),
    ]
    service = _service_returning(docs)

    result = await service.search_mixed(query="email", entity_types=["mcp_server", "tool"], max_results=2)

    matching_tools = result["servers"][0]["matching_tools"]
    assert [tool["tool_name"] for tool in matching_tools] == ["high", "mid"]


async def test_search_mixed_uses_regular_search_when_rerank_disabled():
    repo = MagicMock()
    repo.asearch = AsyncMock(
        return_value=[_tool_doc(server_id="s1", server_name="Email", path="/email", tool_name="send", score=0.9)]
    )
    repo.asearch_with_rerank = AsyncMock(side_effect=AssertionError("rerank must not run when disabled"))
    service = ExternalVectorSearchService(mcp_server_repo=repo, enable_rerank=False)

    result = await service.search_mixed(query="x", entity_types=["mcp_server", "tool"], max_results=10)

    repo.asearch.assert_awaited_once()
    assert len(result["servers"]) == 1
    assert len(result["tools"]) == 1


async def test_search_mixed_swallows_runtime_repo_errors():
    repo = MagicMock()
    repo.asearch_with_rerank = AsyncMock(side_effect=RuntimeError("weaviate down"))
    service = ExternalVectorSearchService(mcp_server_repo=repo)

    result = await service.search_mixed(query="x", entity_types=["mcp_server", "tool"], max_results=10)

    assert result == {"servers": [], "tools": []}


async def test_search_mixed_does_not_swallow_mapping_errors():
    service = _service_returning([SimpleNamespace(entity_type=MCPEntityType.TOOL)])

    with pytest.raises(AttributeError):
        await service.search_mixed(query="x", entity_types=["mcp_server", "tool"], max_results=10)
