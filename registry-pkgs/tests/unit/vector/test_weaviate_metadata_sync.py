from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from langchain_core.documents import Document

from registry_pkgs.vector.backends.weaviate_store import WeaviateStore
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository
from registry_pkgs.vector.sync_result import VectorSyncResult


@pytest.fixture(params=["mcp", "a2a", "tools"])
def sync_kind(request: pytest.FixtureRequest) -> str:
    return request.param


def _make_repository(
    kind: str, successful_updates: int
) -> tuple[MCPServerRepository | A2AAgentRepository, SimpleNamespace, MagicMock]:
    repo_class = A2AAgentRepository if kind == "a2a" else MCPServerRepository
    entity_id = "entity-1"
    docs = [
        Document(
            id=str(UUID(int=index + 1)),
            page_content=f"Tool chunk {index}",
            metadata={
                repo_class.ENTITY_ID_PROPERTY: entity_id,
                "tool_name": "read_file" if index == 0 else "write_file",
            },
        )
        for index in range(3)
    ]
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(
        objects=[
            SimpleNamespace(uuid=UUID(doc.id), properties={"content": doc.page_content, **doc.metadata}) for doc in docs
        ]
    )
    # batch_update_properties now fetches each object's existing vector by id and sends it back,
    # so Weaviate never re-vectorizes. Key by str(uuid); legacy single vectors use the "default" slot.
    vectors_by_id = {doc.id: [float(index)] * 4 for index, doc in enumerate(docs)}
    collection.vectors_by_id = vectors_by_id

    def fetch_by_ids(ids, *, limit, include_vector, return_properties):
        return SimpleNamespace(
            objects=[
                SimpleNamespace(uuid=UUID(doc_id), vector={"default": vectors_by_id[doc_id]})
                for doc_id in ids
                if doc_id in vectors_by_id
            ]
        )

    collection.query.fetch_objects_by_ids.side_effect = fetch_by_ids
    successful_ids = {doc.id for doc in docs[:successful_updates]}

    def update(*, uuid: str, properties: dict, vector: list) -> None:
        if uuid not in successful_ids:
            raise RuntimeError("Weaviate write unavailable")

    collection.data.update.side_effect = update
    store = WeaviateStore(embedding=MagicMock(), config={})
    store.get_collection = MagicMock(return_value=collection)
    store.collection_exists = MagicMock(return_value=True)
    store.ensure_filterable_properties = MagicMock()
    repo = repo_class(
        SimpleNamespace(adapter=store, write_adapter=store, collection_name_for=lambda collection: collection)
    )
    repo.sync_to_vector_db = AsyncMock()
    entity = SimpleNamespace(
        id=entity_id,
        vectorContentHash="same-content",
        to_documents=lambda: docs,
        is_tool_disabled=lambda name: name == "read_file",
    )
    return repo, entity, collection


def _assert_vectors_sent(collection: MagicMock) -> None:
    """Every data.update carries the object's fetched vector; each by-id fetch pins its chunk limit."""
    assert collection.query.fetch_objects_by_ids.call_count >= 1
    for fetch_call in collection.query.fetch_objects_by_ids.call_args_list:
        chunk_ids = fetch_call.args[0]
        assert fetch_call.kwargs["limit"] == len(chunk_ids)
    collection.batch.dynamic.assert_not_called()
    assert collection.data.update.call_args_list
    for call in collection.data.update.call_args_list:
        assert call.kwargs["vector"] == collection.vectors_by_id[call.kwargs["uuid"]]


def _store_with_collection() -> tuple[WeaviateStore, MagicMock]:
    collection = MagicMock()
    store = WeaviateStore(embedding=MagicMock(), config={})
    store.get_collection = MagicMock(return_value=collection)
    return store, collection


@pytest.mark.asyncio
@pytest.mark.parametrize("successful_updates", [0, 1, 3], ids=["all-failed", "partly-failed", "all-succeeded"])
async def test_new_metadata_sync_reports_real_backend_short_writes(sync_kind: str, successful_updates: int) -> None:
    """Weaviate catches write errors and returns a count; the new entry points must inspect it."""
    repo, entity, collection = _make_repository(sync_kind, successful_updates)

    if sync_kind == "tools":
        result = await repo.sync_tools_enabled(entity, ["read_file", "write_file"])
    else:
        result = await repo.sync_entity_metadata(entity, {"enabled": False})

    expected = VectorSyncResult(metadata_updated=successful_updates)
    expected_dict = expected.to_dict() if sync_kind == "a2a" else expected.to_dict_mcp()
    if successful_updates < 3:
        assert result["error"]
        expected_dict["error"] = result["error"]
    assert result == expected_dict
    assert collection.query.fetch_objects.call_count == 1
    assert collection.data.update.call_count == 3
    _assert_vectors_sent(collection)
    repo.sync_to_vector_db.assert_not_awaited()
    repo.adapter.embedding.embed_documents.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("successful_updates", [0, 1, 3], ids=["all-failed", "partly-failed", "all-succeeded"])
async def test_plain_metadata_patch_preserves_real_backend_count_contract(
    sync_kind: str, successful_updates: int
) -> None:
    """Script-facing patches retain their previous count-only result when the backend swallows errors."""
    repo, entity, collection = _make_repository(sync_kind, successful_updates)

    if sync_kind == "tools":
        result = await repo.update_tools_metadata(entity.id, ["read_file", "write_file"], {"tool_enabled": False})
    else:
        result = await repo.update_entity_metadata(repo.ENTITY_ID_PROPERTY, entity.id, {"enabled": False})

    assert result == VectorSyncResult(metadata_updated=successful_updates)
    assert collection.query.fetch_objects.call_count == 1
    assert collection.data.update.call_count == 3
    _assert_vectors_sent(collection)
    repo.sync_to_vector_db.assert_not_awaited()
    repo.adapter.embedding.embed_documents.assert_not_called()


@pytest.mark.asyncio
async def test_missing_vector_makes_metadata_sync_incomplete(sync_kind: str) -> None:
    """An object the vector fetch doesn't return surfaces as an incomplete patch, never a rebuild."""
    repo, entity, collection = _make_repository(sync_kind, successful_updates=3)
    missing_id = entity.to_documents()[1].id
    del collection.vectors_by_id[missing_id]

    if sync_kind == "tools":
        result = await repo.sync_tools_enabled(entity, ["read_file", "write_file"])
    else:
        result = await repo.sync_entity_metadata(entity, {"enabled": False})

    assert result["error"] == "Metadata-only update incomplete: 2/3 docs updated"
    assert collection.data.update.call_count == 2
    assert missing_id not in {call.kwargs["uuid"] for call in collection.data.update.call_args_list}
    _assert_vectors_sent(collection)
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.parametrize(
    "second_object",
    [
        None,
        SimpleNamespace(uuid=UUID(int=2), vector={}),
        SimpleNamespace(uuid=UUID(int=2), vector=None),
    ],
    ids=["not-returned", "empty-vector", "none-vector"],
)
def test_batch_update_skips_object_without_vector(second_object: SimpleNamespace | None) -> None:
    """An object that isn't returned, or has no default vector, is not written and counts as failed."""
    store, collection = _store_with_collection()
    ids = [str(UUID(int=1)), str(UUID(int=2))]
    objects = [SimpleNamespace(uuid=UUID(ids[0]), vector={"default": [1.0, 2.0]})]
    if second_object is not None:
        objects.append(second_object)
    collection.query.fetch_objects_by_ids.return_value = SimpleNamespace(objects=objects)

    updated = store.batch_update_properties(ids, {"enabled": False}, "MCP_Servers")

    assert updated == 1
    assert collection.data.update.call_count == 1
    assert collection.data.update.call_args.kwargs["uuid"] == ids[0]
    assert collection.data.update.call_args.kwargs["vector"] == [1.0, 2.0]
    collection.batch.dynamic.assert_not_called()


def test_batch_update_counts_failed_chunk_without_losing_others() -> None:
    """A chunk whose fetch raises counts as failed; the other chunk is still written."""
    store, collection = _store_with_collection()
    ids = [str(UUID(int=i)) for i in range(1, 102)]  # 101 ids -> chunks of 100 and 1

    def fetch(chunk_ids, *, limit, include_vector, return_properties):
        if len(chunk_ids) == 100:
            raise RuntimeError("Weaviate fetch unavailable")
        return SimpleNamespace(
            objects=[SimpleNamespace(uuid=UUID(doc_id), vector={"default": [0.1]}) for doc_id in chunk_ids]
        )

    collection.query.fetch_objects_by_ids.side_effect = fetch

    updated = store.batch_update_properties(ids, {"enabled": False}, "MCP_Servers")

    assert updated == 1
    assert collection.data.update.call_count == 1
    assert collection.data.update.call_args.kwargs["vector"] == [0.1]


def test_update_metadata_sends_fetched_vector() -> None:
    """The single-document path also sends the fetched vector and drops the non-property key."""
    store, collection = _store_with_collection()
    doc_id = str(UUID(int=7))
    collection.query.fetch_object_by_id.return_value = SimpleNamespace(
        uuid=UUID(doc_id), vector={"default": [9.0, 8.0]}
    )

    assert store.update_metadata(doc_id, {"enabled": False, "collection": "MCP_Servers"}, "MCP_Servers") is True
    assert collection.data.update.call_args.kwargs["vector"] == [9.0, 8.0]
    assert "collection" not in collection.data.update.call_args.kwargs["properties"]


def test_update_metadata_missing_object_returns_false() -> None:
    """A missing object is not written and returns False (never a vector-less update)."""
    store, collection = _store_with_collection()
    collection.query.fetch_object_by_id.return_value = None

    assert store.update_metadata(str(UUID(int=8)), {"enabled": False}, "MCP_Servers") is False
    collection.data.update.assert_not_called()


def test_update_metadata_fetch_error_returns_false() -> None:
    """A failed vector fetch is not followed by a write and returns False."""
    store, collection = _store_with_collection()
    collection.query.fetch_object_by_id.side_effect = RuntimeError("Weaviate fetch unavailable")

    assert store.update_metadata(str(UUID(int=10)), {"enabled": False}, "MCP_Servers") is False
    collection.data.update.assert_not_called()


def test_update_metadata_object_without_vector_returns_false() -> None:
    """An object present but carrying no default vector is not written either."""
    store, collection = _store_with_collection()
    collection.query.fetch_object_by_id.return_value = SimpleNamespace(uuid=UUID(int=9), vector={})

    assert store.update_metadata(str(UUID(int=9)), {"enabled": False}, "MCP_Servers") is False
    collection.data.update.assert_not_called()
