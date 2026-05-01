from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document

from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


class _FakeAdapter:
    def __init__(self, docs: list[Document]):
        self.docs = list(docs)
        self.deleted_ids: list[str] = []
        self.metadata_updates: list[tuple[str, dict]] = []

    def collection_exists(self, _collection: str) -> bool:
        return True

    def get_vector_store(self, _collection: str):
        return object()

    def has_property(self, _collection: str, _property_name: str) -> bool:
        return True

    def filter_by_metadata(self, filters, limit: int, collection_name: str | None = None, **kwargs) -> list[Document]:
        matched = [doc for doc in self.docs if all(doc.metadata.get(k) == v for k, v in filters.items())]
        return matched[:limit]

    def delete(self, ids: list[str], collection_name: str | None = None) -> None:
        self.deleted_ids.extend(ids)
        self.docs = [doc for doc in self.docs if doc.id not in ids]

    def update_metadata(self, doc_id: str, metadata: dict, collection_name: str | None = None) -> bool:
        self.metadata_updates.append((doc_id, metadata))
        return True


def _make_a2a_repo(docs: list[Document]) -> A2AAgentRepository:
    return A2AAgentRepository(SimpleNamespace(adapter=_FakeAdapter(docs)))


def _make_mcp_repo(docs: list[Document]) -> MCPServerRepository:
    return MCPServerRepository(SimpleNamespace(adapter=_FakeAdapter(docs)))


def _make_agent(page_content: str = "x") -> SimpleNamespace:
    """Build a minimal agent stub whose hash is based on page_content."""
    agent = SimpleNamespace(
        id="agent-demo-id",
        card=SimpleNamespace(name="demo-agent", version="1.0.0", skills=[]),
        federationMetadata={"runtimeVersion": "7"},
        isEnabled=True,
        status="active",
        to_documents=lambda: [Document(page_content=page_content, metadata={})],
    )
    agent.vectorContentHash = None
    return agent


def _make_server(page_content: str = "x") -> SimpleNamespace:
    server = SimpleNamespace(
        id="server-demo-id",
        serverName="demo-server",
        federationMetadata={"runtimeVersion": "11"},
        status="active",
        config={"enabled": True},
        to_documents=lambda: [Document(page_content=page_content, metadata={})],
    )
    server.vectorContentHash = None
    return server


@pytest.mark.asyncio
async def test_a2a_sync_rebuilds_when_called():
    """sync_to_vector_db no longer has a hash gate — calling it always rebuilds."""
    page_content = "agent page content"

    existing_doc = Document(page_content=page_content, metadata={"agent_id": "agent-demo-id"})

    repo = _make_a2a_repo([existing_doc])
    repo.asave = AsyncMock(return_value=["doc-id-1"])

    agent = _make_agent(page_content)

    result = await repo.sync_to_vector_db(agent, is_delete=False)

    assert result["indexed"] == 1
    repo.asave.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_sync_rebuilds_when_called():
    """sync_to_vector_db no longer has a hash gate — calling it always rebuilds."""
    page_content = "server page content"

    existing_doc = Document(page_content=page_content, metadata={"server_id": "server-demo-id"})

    repo = _make_mcp_repo([existing_doc])
    repo.asave = AsyncMock(return_value=["doc-1"])

    server = _make_server(page_content)

    result = await repo.sync_to_vector_db(server, is_delete=False)

    assert result["indexed_tools"] == 1
    repo.asave.assert_awaited_once()


def test_mcp_has_runtime_identity_checks_federation_and_runtime():
    doc = Document(page_content="x", metadata={"federation_id": "fed-1", "runtimeArn": "arn:runtime:1"})
    repo = _make_mcp_repo([doc])
    assert repo.has_runtime_identity("fed-1", "arn:runtime:1") is True


def test_a2a_has_runtime_identity_returns_false_when_schema_missing():
    repo = _make_a2a_repo([])
    repo._collection_has_property = MagicMock(return_value=False)
    assert repo.has_runtime_identity("fed-1", "arn:runtime:1") is False


# ---------------------------------------------------------------------------
# update_entity_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a2a_update_entity_metadata_patches_all_matching_docs():
    """update_entity_metadata calls update_metadata on every doc matched by filter."""
    docs = [
        Document(page_content="a", metadata={"agent_id": "agent-1"}, id="doc-1"),
        Document(page_content="b", metadata={"agent_id": "agent-1"}, id="doc-2"),
        Document(page_content="c", metadata={"agent_id": "agent-2"}, id="doc-3"),
    ]
    repo = _make_a2a_repo(docs)

    result = await repo.update_entity_metadata("agent_id", "agent-1", {"is_enabled": False})

    assert result.metadata_updated == 2
    assert result.error is None
    updated_ids = [doc_id for doc_id, _ in repo.adapter.metadata_updates]
    assert "doc-1" in updated_ids
    assert "doc-2" in updated_ids
    assert "doc-3" not in updated_ids


@pytest.mark.asyncio
async def test_mcp_update_entity_metadata_returns_zero_when_no_docs():
    """update_entity_metadata skips gracefully when no matching docs exist."""
    repo = _make_mcp_repo([])

    result = await repo.update_entity_metadata("server_id", "missing-id", {"enabled": True})

    assert result.metadata_updated == 0
    assert result.error is None
    assert repo.adapter.metadata_updates == []


@pytest.mark.asyncio
async def test_update_entity_metadata_skips_when_adapter_lacks_update_metadata():
    """Adapter without update_metadata support returns early without error."""

    class _LegacyAdapter:
        def collection_exists(self, _):
            return True

        def get_vector_store(self, _):
            return object()

        def has_property(self, _, __):
            return True

        def filter_by_metadata(self, filters, limit, **kwargs):
            return [Document(page_content="x", metadata={"agent_id": "a"}, id="d1")]

    repo = A2AAgentRepository(SimpleNamespace(adapter=_LegacyAdapter()))

    result = await repo.update_entity_metadata("agent_id", "a", {"is_enabled": True})

    assert result.metadata_updated == 0
    assert result.error is None


@pytest.mark.asyncio
async def test_mcp_delete_by_server_id_returns_count():
    """delete_by_server_id returns the total number of docs removed across all entity types."""
    from registry_pkgs.models.enums import ServerEntityType

    # Create one doc per entity_type so each loop iteration removes one
    docs = [
        Document(page_content="t", metadata={"server_id": "srv-1", "entity_type": et.value}, id=f"id-{et.value}")
        for et in ServerEntityType
    ]
    repo = _make_mcp_repo(docs)

    count = await repo.delete_by_server_id("srv-1", "demo-server")

    assert count == len(list(ServerEntityType))
    assert repo.adapter.deleted_ids == [f"id-{et.value}" for et in ServerEntityType]


@pytest.mark.asyncio
async def test_mcp_delete_by_server_id_returns_zero_when_no_docs():
    """delete_by_server_id returns 0 when no matching docs exist."""
    repo = _make_mcp_repo([])

    count = await repo.delete_by_server_id("nonexistent", "ghost-server")

    assert count == 0


@pytest.mark.asyncio
async def test_delete_by_runtime_identity_removes_matching_docs():
    """delete_by_runtime_identity issues adelete_by_filter with federation + runtime ARN."""
    repo = _make_a2a_repo([])
    repo.adelete_by_filter = AsyncMock(return_value=5)

    deleted = await repo.delete_by_runtime_identity("fed-X", "arn:aws:lambda:us-east-1:123:function:fn")

    repo.adelete_by_filter.assert_awaited_once_with(
        {"federation_id": "fed-X", "runtimeArn": "arn:aws:lambda:us-east-1:123:function:fn"}
    )
    assert deleted == 5


@pytest.mark.asyncio
async def test_delete_by_runtime_identity_skips_when_schema_missing():
    """delete_by_runtime_identity returns 0 when collection lacks required properties."""
    repo = _make_a2a_repo([])
    repo._collection_has_property = MagicMock(return_value=False)

    deleted = await repo.delete_by_runtime_identity("fed-X", "arn:runtime:1")

    assert deleted == 0
