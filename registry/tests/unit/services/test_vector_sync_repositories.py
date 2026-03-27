from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


class _FakeAdapter:
    def __init__(self, docs: list[Document]):
        self.docs = docs

    def collection_exists(self, _collection: str) -> bool:
        return True

    def get_vector_store(self, _collection: str):
        return object()

    def has_property(self, _collection: str, _property_name: str) -> bool:
        return True

    def filter_by_metadata(self, filters, limit: int, collection_name: str | None = None, **kwargs) -> list[Document]:
        offset = kwargs.get("offset", 0)
        return self.docs[offset : offset + limit]


@pytest.mark.asyncio
async def test_a2a_sync_skips_reindex_when_runtime_version_matches():
    repo = A2AAgentRepository(
        SimpleNamespace(adapter=_FakeAdapter([Document(page_content="x", metadata={"runtime_version": "7"})]))
    )
    repo.asave = AsyncMock()
    agent = SimpleNamespace(
        id="agent-demo-id",
        card=SimpleNamespace(name="demo-agent", version="1.0.0", skills=[]),
        federationMetadata={"runtimeVersion": "7"},
        to_documents=lambda: [Document(page_content="x", metadata={"runtime_version": "7"})],
    )

    result = await repo.sync_agent_to_vector_db(agent, is_delete=False)

    assert result["skipped"] == 1
    assert result["indexed"] == 0
    repo.asave.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_sync_skips_reindex_when_runtime_version_matches():
    repo = MCPServerRepository(
        SimpleNamespace(adapter=_FakeAdapter([Document(page_content="x", metadata={"runtime_version": "11"})]))
    )
    repo.asave = AsyncMock()
    server = SimpleNamespace(
        id="server-demo-id",
        serverName="demo-server",
        federationMetadata={"runtimeVersion": "11"},
        to_documents=lambda: [Document(page_content="x", metadata={"runtime_version": "11"})],
    )

    result = await repo.sync_server_to_vector_db(server, is_delete=False)

    assert result["skipped"] == 1
    assert result["indexed_tools"] == 0
    repo.asave.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_sync_rebuilds_when_doc_count_differs_even_if_version_matches():
    repo = MCPServerRepository(
        SimpleNamespace(adapter=_FakeAdapter([Document(page_content="x", metadata={"runtime_version": "11"})]))
    )
    repo.asave = AsyncMock(return_value=["doc-1", "doc-2"])
    server = SimpleNamespace(
        id="server-demo-id",
        serverName="demo-server",
        federationMetadata={"runtimeVersion": "11"},
        to_documents=lambda: [
            Document(page_content="x", metadata={"runtime_version": "11"}),
            Document(page_content="y", metadata={"runtime_version": "11"}),
        ],
    )

    result = await repo.sync_server_to_vector_db(server, is_delete=False)

    assert result["skipped"] == 0
    assert result["indexed_tools"] == 1
    repo.asave.assert_awaited_once()


def test_mcp_load_existing_docs_pages_beyond_first_batch():
    docs = [Document(page_content=f"doc-{i}", metadata={"runtime_version": "11"}) for i in range(505)]
    repo = MCPServerRepository(SimpleNamespace(adapter=_FakeAdapter(docs)))

    loaded = repo._load_existing_docs("server-demo-id")

    assert len(loaded) == 505
