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
    collection.batch.dynamic.return_value.__enter__.return_value.failed_objects = []
    successful_ids = {doc.id for doc in docs[:successful_updates]}

    def update(*, uuid: str, properties: dict) -> None:
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
    repo.sync_to_vector_db.assert_not_awaited()
    repo.adapter.embedding.embed_documents.assert_not_called()
