"""Repository.save hands each document's deterministic id to Weaviate as the object uuid.

A reindex retried after a lost lease re-inserts the same entities into the same generation; it stays
duplicate-free only because every write reuses ``uuid5`` ids, which Weaviate's batch import upserts.
These tests run the real ``to_documents()`` -> ``Repository.save`` -> ``WeaviateStore.add_documents``
-> ``langchain_core`` ``add_documents`` chain and patch only ``WeaviateVectorStore.add_texts``, the
point where ``langchain_weaviate`` writes ``uuid=ids[i]`` inside ``batch.dynamic()``.

Not covered: a switch to ``aadd_documents`` goes through ``aadd_texts`` -> ``insert_many`` instead, whose
duplicate-uuid behavior is unverified.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import AgentCard, AgentSkill
from beanie import PydanticObjectId
from langchain_weaviate import WeaviateVectorStore

from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.backends.weaviate_store import WeaviateStore
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


def _store() -> WeaviateStore:
    store = WeaviateStore(embedding=MagicMock(), config={"host": "localhost", "port": 8080})
    # No live Weaviate: the client is a mock and the collection is assumed to exist.
    store._client = MagicMock(name="weaviate_client")
    store._ensure_collection_with_vectorizer = MagicMock()
    return store


def _db_client(store: WeaviateStore) -> SimpleNamespace:
    return SimpleNamespace(adapter=store, write_adapter=store, collection_name_for=lambda base: base)


def _server() -> ExtendedMCPServer:
    tool = {"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}
    return ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName="id-server",
        config={
            "title": "Id Server",
            "description": "desc",
            "type": "streamable-http",
            "url": "https://example.com/mcp",
            "toolFunctions": {"tool_a": {**tool, "mcpToolName": "tool_a"}, "tool_b": {**tool, "mcpToolName": "tool_b"}},
            "resources": [{"name": "res1", "uri": "x://res1"}],
            "prompts": [{"name": "prompt1"}],
        },
        author=PydanticObjectId(),
        path="/mcp/id-server",
        registryDisabledTools=[],
    )


def _agent() -> A2AAgent:
    return A2AAgent.model_construct(
        id=PydanticObjectId(),
        path="id-agent",
        card=AgentCard(
            name="Id Agent",
            description="A test A2A agent",
            url="https://example.com/a2a",
            version="1.0.0",
            capabilities={"streaming": True},
            defaultInputModes=["text/plain"],
            defaultOutputModes=["application/json"],
            skills=[
                AgentSkill(id="s1", name="One", description="First skill", tags=["x"]),
                AgentSkill(id="s2", name="Two", description="Second skill", tags=["y"]),
            ],
        ),
        config=AgentConfig(title="Id Agent", description="A test A2A agent", type="jsonrpc", enabled=True),
        tags=[],
        author=PydanticObjectId(),
    )


@pytest.mark.parametrize(
    ("make_entity", "make_repo"),
    [(_server, MCPServerRepository), (_agent, A2AAgentRepository)],
    ids=["mcp_server", "a2a_agent"],
)
def test_save_passes_deterministic_doc_ids_to_weaviate(make_entity, make_repo) -> None:
    entity = make_entity()
    expected_ids = [doc.id for doc in entity.to_documents()]
    assert len(expected_ids) > 1 and None not in expected_ids  # guard: the entity really has stable ids
    repo = make_repo(_db_client(_store()))

    with patch.object(WeaviateVectorStore, "add_texts", autospec=True, return_value=expected_ids) as add_texts:
        repo.save(entity)

    add_texts.assert_called_once()
    assert add_texts.call_args.kwargs["ids"] == expected_ids


@pytest.mark.parametrize(
    ("make_entity", "make_repo"),
    [(_server, MCPServerRepository), (_agent, A2AAgentRepository)],
    ids=["mcp_server", "a2a_agent"],
)
def test_resaving_an_entity_reuses_the_same_ids(make_entity, make_repo) -> None:
    entity = make_entity()
    repo = make_repo(_db_client(_store()))

    with patch.object(WeaviateVectorStore, "add_texts", autospec=True, return_value=["x"]) as add_texts:
        repo.save(entity)
        repo.save(entity)

    first, second = (call.kwargs["ids"] for call in add_texts.call_args_list)
    # Same ids on the retry -> Weaviate upserts instead of duplicating the entity's documents.
    assert first == second
