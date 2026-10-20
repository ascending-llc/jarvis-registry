import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from langchain_core.documents import Document

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.testing.federation_metadata import (
    make_agentcore_a2a_metadata,
    make_agentcore_mcp_metadata,
)
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository
from registry_pkgs.vector.sync_result import VectorSyncResult


class _FakeAdapter:
    def __init__(self, docs: list[Document]):
        self.docs = list(docs)
        self.added_ids: list[str] = []
        self.deleted_ids: list[str] = []
        self.metadata_updates: list[tuple[str, dict]] = []
        self.filter_calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.lookup_error: Exception | None = None
        self.count_error: Exception | None = None
        self.patch_error: Exception | None = None
        self.failed_update_ids: set[str] = set()

    def collection_exists(self, _collection: str) -> bool:
        return True

    def get_vector_store(self, _collection: str):
        return object()

    def has_property(self, _collection: str, _property_name: str) -> bool:
        return True

    def filter_by_metadata(
        self, filters, limit: int, collection_name: str | None = None, *, raise_on_error: bool = False, **kwargs
    ) -> list[Document]:
        self.filter_calls.append(
            {"filters": filters, "limit": limit, "collection_name": collection_name, "raise_on_error": raise_on_error}
        )
        if self.lookup_error is not None:
            raise self.lookup_error
        return self._matching_docs(filters)[:limit]

    def _matching_docs(self, filters: dict) -> list[Document]:
        def matches(doc) -> bool:
            for k, v in filters.items():
                field = doc.metadata.get(k)
                # A list value means membership (mirrors the real adapter's list -> contains_any).
                if isinstance(v, list):
                    if field not in v:
                        return False
                elif field != v:
                    return False
            return True

        return [doc for doc in self.docs if matches(doc)]

    def count_by_metadata(self, filters: dict, collection_name: str | None = None) -> int:
        self.count_calls.append({"filters": filters, "collection_name": collection_name})
        if self.count_error is not None:
            raise self.count_error
        return len(self._matching_docs(filters))

    def add_documents(self, documents: list[Document], collection_name: str | None = None, **kwargs) -> list[str]:
        ids = []
        for doc in documents:
            assert doc.id is not None
            ids.append(doc.id)
        self.added_ids.extend(ids)
        self.docs = [doc for doc in self.docs if doc.id not in ids]
        self.docs.extend(doc.model_copy(deep=True) for doc in documents)
        return ids

    def delete(self, ids: list[str], collection_name: str | None = None) -> None:
        self.deleted_ids.extend(ids)
        self.docs = [doc for doc in self.docs if doc.id not in ids]

    def delete_by_filter(self, filters: dict, collection_name: str | None = None) -> int:
        matched = [doc for doc in self.docs if all(doc.metadata.get(k) == v for k, v in filters.items())]
        ids = [doc.id for doc in matched]
        self.deleted_ids.extend(ids)
        self.docs = [doc for doc in self.docs if doc.id not in ids]
        return len(ids)

    def update_metadata(self, doc_id: str, metadata: dict, collection_name: str | None = None) -> bool:
        if self.patch_error is not None:
            raise self.patch_error
        if doc_id in self.failed_update_ids:
            return False
        self.metadata_updates.append((doc_id, metadata))
        return True

    def ensure_filterable_properties(self, collection_name: str, property_names) -> None:
        pass


class _FakeBatchAdapter(_FakeAdapter):
    def __init__(self, docs: list[Document]):
        super().__init__(docs)
        self.batch_updates: list[tuple[list[str], dict]] = []

    def batch_update_properties(self, doc_ids: list[str], update_data: dict, collection_name: str | None = None) -> int:
        self.batch_updates.append((doc_ids, update_data))
        return sum(self.update_metadata(doc_id, update_data, collection_name) for doc_id in doc_ids)


def _fake_db_client(docs: list[Document], *, batch: bool = False) -> SimpleNamespace:
    # Reads use .adapter, writes use .write_adapter (same underlying fake here, since
    # no reindex is active), and .collection is resolved via .collection_name_for (generation 0).
    adapter = (_FakeBatchAdapter if batch else _FakeAdapter)(docs)
    return SimpleNamespace(
        adapter=adapter,
        write_adapter=adapter,
        collection_name_for=lambda base: base,
    )


def _make_a2a_repo(docs: list[Document]) -> A2AAgentRepository:
    return A2AAgentRepository(_fake_db_client(docs))


def _make_mcp_repo(docs: list[Document]) -> MCPServerRepository:
    return MCPServerRepository(_fake_db_client(docs))


def _make_agent(page_content: str = "x") -> SimpleNamespace:
    """Build a minimal agent stub whose hash is based on page_content."""
    agent = SimpleNamespace(
        id="agent-demo-id",
        card=SimpleNamespace(name="demo-agent", version="1.0.0", skills=[]),
        config=SimpleNamespace(enabled=True),
        federationMetadata=make_agentcore_a2a_metadata(runtime_version="7"),
        to_documents=lambda: [Document(page_content=page_content, metadata={})],
    )
    agent.vectorContentHash = None
    return agent


def _make_server(page_content: str = "x") -> SimpleNamespace:
    server = SimpleNamespace(
        id="server-demo-id",
        serverName="demo-server",
        federationMetadata=make_agentcore_mcp_metadata(runtime_version="11"),
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

    result = await repo.update_entity_metadata("agent_id", "agent-1", {"enabled": False})

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

        def ensure_filterable_properties(self, collection_name: str, property_names) -> None:
            pass

    legacy = _LegacyAdapter()
    repo = A2AAgentRepository(
        SimpleNamespace(adapter=legacy, write_adapter=legacy, collection_name_for=lambda base: base)
    )

    result = await repo.update_entity_metadata("agent_id", "a", {"enabled": True})

    assert result.metadata_updated == 0
    assert result.error is None


@pytest.mark.asyncio
async def test_mcp_delete_by_server_id_returns_count():
    """delete_by_server_id returns the total number of docs removed across all entity types."""
    from registry_pkgs.models.enums import MCPEntityType

    # Create one doc per entity_type so each loop iteration removes one
    docs = [
        Document(page_content="t", metadata={"server_id": "srv-1", "entity_type": et.value}, id=f"id-{et.value}")
        for et in MCPEntityType
    ]
    repo = _make_mcp_repo(docs)

    count = await repo.delete_by_server_id("srv-1", "demo-server")

    assert count == len(list(MCPEntityType))
    assert repo.adapter.deleted_ids == [f"id-{et.value}" for et in MCPEntityType]


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


# ---------------------------------------------------------------------------
# update_tools_metadata / _update_metadata_by_filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_update_tools_metadata_patches_only_named_tools():
    """update_tools_metadata patches only the docs whose tool_name is in the given set, on that server."""
    docs = [
        Document(page_content="a", metadata={"server_id": "srv-1", "tool_name": "read_file"}, id="d1"),
        Document(page_content="b", metadata={"server_id": "srv-1", "tool_name": "write_file"}, id="d2"),
        Document(page_content="c", metadata={"server_id": "srv-1", "tool_name": "delete_file"}, id="d3"),
        Document(page_content="d", metadata={"server_id": "srv-2", "tool_name": "read_file"}, id="d4"),
    ]
    repo = _make_mcp_repo(docs)

    result = await repo.update_tools_metadata("srv-1", ["read_file", "write_file"], {"tool_enabled": False})

    assert result.metadata_updated == 2
    updated_ids = [doc_id for doc_id, _ in repo.adapter.metadata_updates]
    assert set(updated_ids) == {"d1", "d2"}  # not delete_file (d3), not srv-2 (d4)


@pytest.mark.asyncio
async def test_mcp_update_tools_metadata_empty_is_noop():
    """An empty tool_names list makes no adapter calls."""
    docs = [Document(page_content="a", metadata={"server_id": "srv-1", "tool_name": "read_file"}, id="d1")]
    repo = _make_mcp_repo(docs)

    result = await repo.update_tools_metadata("srv-1", [], {"tool_enabled": True})

    assert result.metadata_updated == 0
    assert repo.adapter.metadata_updates == []


@pytest.mark.asyncio
async def test_update_entity_metadata_still_delegates_to_shared_helper():
    """The refactored update_entity_metadata keeps its whole-entity behavior (multi-key not needed)."""
    docs = [
        Document(page_content="a", metadata={"server_id": "srv-1", "tool_name": "read_file"}, id="d1"),
        Document(page_content="b", metadata={"server_id": "srv-1", "tool_name": "write_file"}, id="d2"),
        Document(page_content="c", metadata={"server_id": "srv-2", "tool_name": "read_file"}, id="d3"),
    ]
    repo = _make_mcp_repo(docs)

    result = await repo.update_entity_metadata("server_id", "srv-1", {"tool_enabled": True})

    assert result.metadata_updated == 2  # both srv-1 docs, regardless of tool_name
    updated_ids = {doc_id for doc_id, _ in repo.adapter.metadata_updates}
    assert updated_ids == {"d1", "d2"}


# Shared repair rules are exercised through all three public entry points.
@pytest.fixture(params=["mcp", "a2a", "tools"])
def sync_kind(request: pytest.FixtureRequest) -> str:
    return request.param


def _sync_result(kind: str, **values) -> dict:
    result = VectorSyncResult(**values)
    return result.to_dict() if kind == "a2a" else result.to_dict_mcp()


def _make_sync_case(
    kind: str, *, expected: int = 3, found: int = 3, batch: bool = False
) -> tuple[A2AAgentRepository | MCPServerRepository, SimpleNamespace]:
    entity = _make_agent() if kind == "a2a" else _make_server()
    entity.vectorContentHash = "current-content"
    entity.registryDisabledTools = ["read_file"]
    entity.is_tool_disabled = lambda name: name in entity.registryDisabledTools
    id_property = "agent_id" if kind == "a2a" else "server_id"

    def documents(count: int) -> list[Document]:
        return [
            Document(
                id=f"doc-{i}",
                page_content=f"chunk {i}",
                metadata={id_property: str(entity.id), "tool_name": "read_file", "chunk_index": i},
            )
            for i in range(count)
        ]

    entity.to_documents = lambda: documents(expected)
    repo_class = A2AAgentRepository if kind == "a2a" else MCPServerRepository
    repo = repo_class(_fake_db_client(documents(found), batch=batch))
    repo.sync_to_vector_db = AsyncMock(return_value=_sync_result(kind, indexed=expected, deleted=found))
    return repo, entity


async def _run_metadata_sync(
    kind: str,
    repo: A2AAgentRepository | MCPServerRepository,
    entity: SimpleNamespace,
    *,
    reload: Callable[[], Awaitable[SimpleNamespace | None]] | None = None,
) -> dict:
    if kind == "tools":
        return await repo.sync_tools_enabled(entity, ["read_file"], reload=reload)
    return await repo.sync_entity_metadata(entity, {"enabled": False}, reload=reload)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True], ids=["individual-patch", "batch-patch"])
async def test_metadata_sync_matching_count_patches_all_docs(sync_kind: str, batch: bool) -> None:
    repo, entity = _make_sync_case(sync_kind, batch=batch)
    reload = AsyncMock()

    result = await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    metadata = {"tool_enabled": False} if sync_kind == "tools" else {"enabled": False}
    assert result == _sync_result(sync_kind, metadata_updated=3)
    assert repo.adapter.metadata_updates == [(f"doc-{i}", metadata) for i in range(3)]
    assert len(repo.adapter.filter_calls) == 1
    assert repo.adapter.filter_calls[0]["raise_on_error"] is True
    assert repo.adapter.count_calls == []
    repo.sync_to_vector_db.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expected,found", [(3, 0), (3, 1), (3, 4), (0, 2)], ids=["missing", "partial", "extra", "stale"]
)
async def test_metadata_sync_count_mismatch_rebuilds_once(
    sync_kind: str, expected: int, found: int, caplog: pytest.LogCaptureFixture
) -> None:
    repo, entity = _make_sync_case(sync_kind, expected=expected, found=found)

    with caplog.at_level(logging.WARNING):
        result = await _run_metadata_sync(sync_kind, repo, entity)

    repo.sync_to_vector_db.assert_awaited_once_with(entity, is_delete=True)
    assert result is repo.sync_to_vector_db.return_value
    assert result == _sync_result(sync_kind, indexed=expected, deleted=found)
    assert repo.adapter.metadata_updates == []
    assert len(repo.adapter.filter_calls) == 1
    assert any(
        record.levelno == logging.WARNING
        and str(entity.id) in record.getMessage()
        and str(expected) in record.getMessage()
        and str(found) in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["entity", "tools"])
@pytest.mark.parametrize("has_stale_docs", [False, True], ids=["missing", "stale"])
async def test_metadata_sync_rebuild_restores_documents_and_tool_states(entry: str, has_stale_docs: bool) -> None:
    """Exercise the real lookup, delete, save and verification chain against the in-memory adapter."""
    server = ExtendedMCPServer.model_construct(
        id=PydanticObjectId("65f000000000000000000001"),
        serverName="repair-server",
        path="/repair-server",
        registryDisabledTools=["read_file"],
        config={
            "enabled": True,
            "toolFunctions": {
                "read_file": {"mcpToolName": "read_file", "function": {"description": "Read a file."}},
                "write_file": {"mcpToolName": "write_file", "function": {"description": "Write a file."}},
            },
        },
    )
    server.refresh_content_hash()
    expected_docs = server.to_documents()
    initial_docs = []
    if has_stale_docs:
        initial_docs = [
            doc.model_copy(
                update={"page_content": "outdated content", "metadata": {**doc.metadata, "tool_enabled": True}},
                deep=True,
            )
            for doc in expected_docs
        ]
        initial_docs.append(
            Document(
                id="stale-doc",
                page_content="obsolete read_file chunk",
                metadata={"server_id": str(server.id), "tool_name": "read_file", "tool_enabled": True},
            )
        )
    unrelated = Document(
        id="unrelated",
        page_content="another server's tool",
        metadata={"server_id": "other-server", "tool_name": "read_file", "tool_enabled": True},
    )
    repo = MCPServerRepository(_fake_db_client([*initial_docs, unrelated], batch=True))
    reload = AsyncMock(return_value=server)

    async def sync() -> dict:
        if entry == "tools":
            return await repo.sync_tools_enabled(server, ["read_file"], reload=reload)
        return await repo.sync_entity_metadata(server, server.mutable_metadata(), reload=reload)

    result = await sync()

    assert result == VectorSyncResult(indexed=2, deleted=len(initial_docs)).to_dict_mcp()
    restored = [doc for doc in repo.adapter.docs if doc.metadata["server_id"] == str(server.id)]
    assert restored == expected_docs
    assert {doc.metadata["tool_name"]: doc.metadata["tool_enabled"] for doc in restored} == {
        "read_file": False,
        "write_file": True,
    }
    assert [doc for doc in repo.adapter.docs if doc.metadata["server_id"] == "other-server"] == [unrelated]
    expected_ids = [doc.id for doc in expected_docs]
    deleted_ids = [doc.id for doc in initial_docs]
    assert repo.adapter.added_ids == expected_ids
    assert repo.adapter.deleted_ids == deleted_ids
    assert repo.adapter.metadata_updates == []
    reload.assert_awaited_once_with()

    # Once repaired, the next metadata sync must patch without another delete or embedding write.
    result = await sync()

    assert result == VectorSyncResult(metadata_updated=1 if entry == "tools" else 2).to_dict_mcp()
    assert repo.adapter.added_ids == expected_ids
    assert repo.adapter.deleted_ids == deleted_ids
    reload.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_metadata_sync_empty_entity_is_noop(sync_kind: str) -> None:
    repo, entity = _make_sync_case(sync_kind, expected=0, found=0)
    reload = AsyncMock()

    result = await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    assert result == _sync_result(sync_kind)
    assert repo.adapter.metadata_updates == []
    repo.sync_to_vector_db.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [0, 3], ids=["empty-query", "capped-query"])
async def test_metadata_sync_above_query_cap_skips_rebuild(
    sync_kind: str, found: int, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("registry_pkgs.vector.base_sync_repository._MAX_METADATA_DOCS", 2)
    repo, entity = _make_sync_case(sync_kind, expected=3, found=found)
    reload = AsyncMock()

    with caplog.at_level(logging.WARNING):
        result = await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    assert result == _sync_result(sync_kind, metadata_updated=min(found, 2))
    assert len(repo.adapter.metadata_updates) == min(found, 2)
    assert repo.adapter.filter_calls[0]["limit"] == 2
    assert repo.adapter.count_calls == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    repo.sync_to_vector_db.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [1, 2, 3], ids=["partial", "exact", "extra"])
async def test_metadata_sync_at_query_cap_checks_complete_count(
    sync_kind: str, found: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("registry_pkgs.vector.base_sync_repository._MAX_METADATA_DOCS", 2)
    repo, entity = _make_sync_case(sync_kind, expected=2, found=found)

    result = await _run_metadata_sync(sync_kind, repo, entity)

    assert len(repo.adapter.filter_calls) == 1
    assert repo.adapter.filter_calls[0]["limit"] == 2
    assert len(repo.adapter.count_calls) == (1 if found >= 2 else 0)
    if repo.adapter.count_calls:
        assert repo.adapter.count_calls[0] == {
            "filters": repo.adapter.filter_calls[0]["filters"],
            "collection_name": repo.collection,
        }
    if found == 2:
        assert result == _sync_result(sync_kind, metadata_updated=2)
        repo.sync_to_vector_db.assert_not_awaited()
    else:
        repo.sync_to_vector_db.assert_awaited_once_with(entity, is_delete=True)
        assert result is repo.sync_to_vector_db.return_value
        assert repo.adapter.metadata_updates == []


@pytest.mark.asyncio
async def test_metadata_sync_at_query_cap_count_failure_does_not_rebuild(
    sync_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("registry_pkgs.vector.base_sync_repository._MAX_METADATA_DOCS", 2)
    repo, entity = _make_sync_case(sync_kind, expected=2, found=3)
    repo.adapter.count_error = RuntimeError("count unavailable")
    reload = AsyncMock()

    with pytest.raises(RuntimeError, match="count unavailable"):
        await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    assert len(repo.adapter.count_calls) == 1
    assert repo.adapter.metadata_updates == []
    repo.sync_to_vector_db.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_sync_lookup_failure_propagates_without_rebuild(sync_kind: str) -> None:
    repo, entity = _make_sync_case(sync_kind)
    repo.adapter.lookup_error = RuntimeError("lookup unavailable")
    reload = AsyncMock()

    with pytest.raises(RuntimeError, match="lookup unavailable"):
        await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    assert repo.adapter.filter_calls[0]["raise_on_error"] is True
    assert repo.adapter.metadata_updates == []
    repo.sync_to_vector_db.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_sync_matching_reload_hash_rebuilds_original_snapshot(sync_kind: str) -> None:
    repo, entity = _make_sync_case(sync_kind, found=0)
    committed = SimpleNamespace(**vars(entity))
    committed.config = SimpleNamespace(enabled=False) if sync_kind == "a2a" else {"enabled": False}
    committed.registryDisabledTools = []
    committed.tags = ["previously-committed-tag"]
    reload = AsyncMock(return_value=committed)

    result = await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    reload.assert_awaited_once_with()
    repo.sync_to_vector_db.assert_awaited_once_with(entity, is_delete=True)
    assert repo.sync_to_vector_db.await_args.args[0] is entity
    assert result is repo.sync_to_vector_db.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True], ids=["newer-content", "deleted-entity"])
async def test_metadata_sync_reload_guard_skips_obsolete_snapshot(
    sync_kind: str, deleted: bool, caplog: pytest.LogCaptureFixture
) -> None:
    repo, entity = _make_sync_case(sync_kind, found=0)
    latest = SimpleNamespace(**vars(entity))
    latest.vectorContentHash = "newer-content"
    reload = AsyncMock(return_value=None if deleted else latest)

    with caplog.at_level(logging.INFO):
        result = await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    reload.assert_awaited_once_with()
    assert result == _sync_result(sync_kind)
    assert repo.adapter.metadata_updates == []
    repo.sync_to_vector_db.assert_not_awaited()
    assert any(record.levelno == logging.INFO and str(entity.id) in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_metadata_sync_reload_failure_does_not_rebuild(sync_kind: str) -> None:
    repo, entity = _make_sync_case(sync_kind, found=0)
    reload = AsyncMock(side_effect=RuntimeError("Mongo unavailable"))

    with pytest.raises(RuntimeError, match="Mongo unavailable"):
        await _run_metadata_sync(sync_kind, repo, entity, reload=reload)

    assert repo.adapter.metadata_updates == []
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True], ids=["individual-patch", "batch-patch"])
async def test_metadata_sync_patch_failure_returns_error_without_rebuild(sync_kind: str, batch: bool) -> None:
    repo, entity = _make_sync_case(sync_kind, batch=batch)
    repo.adapter.patch_error = RuntimeError("patch unavailable")

    result = await _run_metadata_sync(sync_kind, repo, entity)

    assert result == _sync_result(sync_kind, error="patch unavailable")
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True], ids=["individual-patch", "batch-patch"])
@pytest.mark.parametrize("updated", [0, 2], ids=["all-writes-fail", "partial-success"])
async def test_metadata_sync_incomplete_patch_returns_error_without_rebuild(
    sync_kind: str, batch: bool, updated: int
) -> None:
    repo, entity = _make_sync_case(sync_kind, batch=batch)
    repo.adapter.failed_update_ids = {f"doc-{i}" for i in range(updated, 3)}

    result = await _run_metadata_sync(sync_kind, repo, entity)

    assert result["error"]
    assert result == _sync_result(sync_kind, metadata_updated=updated, error=result["error"])
    assert len(repo.adapter.metadata_updates) == updated
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [False, True], ids=["entity", "tools"])
@pytest.mark.parametrize("batch", [False, True], ids=["individual-patch", "batch-patch"])
@pytest.mark.parametrize("updated", [0, 2], ids=["all-writes-fail", "partial-success"])
async def test_plain_metadata_patch_preserves_incomplete_write_result(tools: bool, batch: bool, updated: int) -> None:
    repo, server = _make_sync_case("tools", batch=batch)
    repo.adapter.failed_update_ids = {f"doc-{i}" for i in range(updated, 3)}

    if tools:
        result = await repo.update_tools_metadata(server.id, ["read_file"], {"tool_enabled": False})
    else:
        result = await repo.update_entity_metadata("server_id", server.id, {"enabled": False})

    assert result == VectorSyncResult(metadata_updated=updated)
    assert repo.adapter.count_calls == []
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_sync_collection_failure_propagates(sync_kind: str) -> None:
    repo, entity = _make_sync_case(sync_kind)
    repo.ensure_collection = AsyncMock(return_value=False)

    with pytest.raises(RuntimeError, match="not initialized"):
        await _run_metadata_sync(sync_kind, repo, entity)

    assert repo.adapter.filter_calls == []
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_sync_unsupported_adapter_skips_lookup(sync_kind: str) -> None:
    repo, entity = _make_sync_case(sync_kind)
    legacy = SimpleNamespace(filter_by_metadata=MagicMock())
    repo.db_client.adapter = legacy
    repo.db_client.write_adapter = legacy
    repo.ensure_collection = AsyncMock(return_value=True)

    result = await _run_metadata_sync(sync_kind, repo, entity)

    assert result == _sync_result(sync_kind)
    legacy.filter_by_metadata.assert_not_called()
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [False, True], ids=["entity", "tools"])
@pytest.mark.parametrize("lookup_fails", [False, True], ids=["successful-query", "lookup-exception"])
async def test_plain_metadata_patches_keep_non_strict_lookup_and_error_contract(
    tools: bool, lookup_fails: bool
) -> None:
    repo, entity = _make_sync_case("tools")
    if lookup_fails:
        repo.adapter.lookup_error = RuntimeError("adapter lookup failed")

    if tools:
        result = await repo.update_tools_metadata(entity.id, ["read_file"], {"tool_enabled": False})
    else:
        result = await repo.update_entity_metadata("server_id", entity.id, {"enabled": False})

    assert isinstance(result, VectorSyncResult)
    assert result.error == ("adapter lookup failed" if lookup_fails else None)
    assert result.metadata_updated == (0 if lookup_fails else 3)
    assert repo.adapter.filter_calls[0]["raise_on_error"] is False
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [False, True], ids=["entity", "tools"])
async def test_plain_metadata_patch_collection_failure_still_raises(tools: bool) -> None:
    repo, entity = _make_sync_case("tools")
    repo.ensure_collection = AsyncMock(return_value=False)

    with pytest.raises(RuntimeError, match="not initialized"):
        if tools:
            await repo.update_tools_metadata(entity.id, ["read_file"], {"tool_enabled": False})
        else:
            await repo.update_entity_metadata("server_id", entity.id, {"enabled": False})

    assert repo.adapter.filter_calls == []


@pytest.mark.asyncio
async def test_tools_enabled_counts_chunks_and_ignores_other_tools_and_servers() -> None:
    server = ExtendedMCPServer.model_construct(
        id=PydanticObjectId("65f000000000000000000001"),
        serverName="chunked-server",
        path="/chunked-server",
        registryDisabledTools=["read_file"],
        config={
            "enabled": True,
            "toolFunctions": {
                "read_file_mcp_chunked": {
                    "mcpToolName": "read_file",
                    "function": {"description": "Read file contents and return the requested text. " * 100},
                },
                "write_file_mcp_chunked": {
                    "mcpToolName": "write_file",
                    "function": {"description": "Write a file."},
                },
            },
            "resources": [{"name": "readme", "uri": "file:///README.md"}],
        },
    )
    docs = server.to_documents()
    selected_docs = [doc for doc in docs if doc.metadata.get("tool_name") == "read_file"]
    assert len(selected_docs) > 1
    assert [doc.metadata["chunk_index"] for doc in selected_docs] == list(range(len(selected_docs)))
    assert all(doc.metadata["total_chunks"] == len(selected_docs) for doc in selected_docs)
    assert all(doc.metadata["tool_enabled"] is False for doc in selected_docs)
    assert any(doc.metadata.get("tool_name") == "write_file" for doc in docs)
    assert any("tool_name" not in doc.metadata for doc in docs)
    unrelated = Document(
        id="unrelated", page_content="other server", metadata={"server_id": "other-server", "tool_name": "read_file"}
    )
    repo = MCPServerRepository(_fake_db_client([*docs, unrelated], batch=True))
    repo.sync_to_vector_db = AsyncMock()

    result = await repo.sync_tools_enabled(server, ["read_file"])

    assert result == _sync_result("tools", metadata_updated=len(selected_docs))
    assert repo.adapter.batch_updates == [([doc.id for doc in selected_docs], {"tool_enabled": False})]
    assert repo.adapter.filter_calls[0]["filters"] == {"server_id": str(server.id), "tool_name": ["read_file"]}
    repo.sync_to_vector_db.assert_not_awaited()


def _make_mixed_tools_case(*, missing: bool = False) -> tuple[MCPServerRepository, SimpleNamespace]:
    repo, server = _make_sync_case("tools", expected=2, found=2, batch=True)
    docs = server.to_documents()
    docs[1].metadata["tool_name"] = "write_file"
    server.to_documents = lambda: docs
    repo.adapter.docs = docs[:1] if missing else docs
    return repo, server


@pytest.mark.asyncio
async def test_tools_enabled_patches_both_states_from_server_snapshot() -> None:
    repo, server = _make_mixed_tools_case()

    result = await repo.sync_tools_enabled(server, ["read_file", "write_file"])

    assert result == _sync_result("tools", metadata_updated=2)
    assert repo.adapter.batch_updates == [
        (["doc-0"], {"tool_enabled": False}),
        (["doc-1"], {"tool_enabled": True}),
    ]
    assert len(repo.adapter.filter_calls) == 1
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_tools_enabled_missing_one_toggled_tool_rebuilds_once_without_patch() -> None:
    repo, server = _make_mixed_tools_case(missing=True)

    result = await repo.sync_tools_enabled(server, ["read_file", "write_file"])

    repo.sync_to_vector_db.assert_awaited_once_with(server, is_delete=True)
    assert result is repo.sync_to_vector_db.return_value
    assert repo.adapter.batch_updates == []
    assert len(repo.adapter.filter_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("both_fail", [False, True], ids=["second-group-succeeds", "both-groups-fail"])
async def test_tools_enabled_attempts_both_groups_and_preserves_first_error(both_fail: bool) -> None:
    repo, server = _make_mixed_tools_case()
    repo.adapter.batch_update_properties = MagicMock(
        side_effect=[RuntimeError("first patch failed"), RuntimeError("second patch failed") if both_fail else 1]
    )

    result = await repo.sync_tools_enabled(server, ["read_file", "write_file"])

    assert result == _sync_result("tools", metadata_updated=0 if both_fail else 1, error="first patch failed")
    assert repo.adapter.batch_update_properties.call_count == 2
    repo.sync_to_vector_db.assert_not_awaited()


@pytest.mark.asyncio
async def test_tools_enabled_empty_names_skips_all_work() -> None:
    repo, server = _make_sync_case("tools")
    repo.ensure_collection = AsyncMock()
    server.to_documents = MagicMock()
    reload = AsyncMock()

    result = await repo.sync_tools_enabled(server, [], reload=reload)

    assert result == _sync_result("tools")
    assert repo.adapter.filter_calls == []
    assert repo.adapter.metadata_updates == []
    repo.ensure_collection.assert_not_awaited()
    server.to_documents.assert_not_called()
    repo.sync_to_vector_db.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.fixture
def metadata_threads(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # One worker can be held at the adapter while the other processes queued calls in order.
    with ThreadPoolExecutor(max_workers=2) as executor:

        async def to_thread(func: Callable, *args, **kwargs):
            return await asyncio.get_running_loop().run_in_executor(executor, partial(func, *args, **kwargs))

        monkeypatch.setattr(asyncio, "to_thread", to_thread)
        yield


class _BlockingAdapter(_FakeBatchAdapter):
    def __init__(self, docs: list[Document], entity_id: str, phase: str):
        super().__init__(docs)
        self.entity_id = entity_id
        self.phase = phase
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.trace: list[tuple[str, str]] = []
        self.blocked_error: Exception | None = None
        self._loop = asyncio.get_running_loop()
        self._blocked = False

    def _perform[R](self, entity_id: str, phase: str, operation: Callable[[], R]) -> R:
        self.trace.append((entity_id, f"{phase}:start"))
        try:
            if entity_id == self.entity_id and phase == self.phase and not self._blocked:
                self._blocked = True
                self._loop.call_soon_threadsafe(self.started.set)
                if not self.release.wait(timeout=5):
                    raise TimeoutError("test did not release the blocked adapter call")
                if self.blocked_error is not None:
                    raise self.blocked_error
            return operation()
        finally:
            self.trace.append((entity_id, f"{phase}:end"))

    def filter_by_metadata(
        self, filters: dict, limit: int, collection_name: str | None = None, *, raise_on_error: bool = False, **kwargs
    ) -> list[Document]:
        return self._perform(
            filters["server_id"],
            "lookup",
            partial(
                super().filter_by_metadata,
                filters=filters,
                limit=limit,
                collection_name=collection_name,
                raise_on_error=raise_on_error,
                **kwargs,
            ),
        )

    def batch_update_properties(self, doc_ids: list[str], update_data: dict, collection_name: str | None = None) -> int:
        entity_id = next(doc.metadata["server_id"] for doc in self.docs if doc.id == doc_ids[0])
        return self._perform(
            entity_id,
            "patch",
            partial(
                super().batch_update_properties,
                doc_ids=doc_ids,
                update_data=update_data,
                collection_name=collection_name,
            ),
        )


def _make_blocking_case(phase: str) -> tuple[MCPServerRepository, SimpleNamespace, SimpleNamespace, _BlockingAdapter]:
    repo, server = _make_sync_case("tools", expected=1, found=1, batch=True)
    other_doc = Document(
        id="other-doc", page_content="other server", metadata={"server_id": "other-server", "tool_name": "read_file"}
    )
    other = SimpleNamespace(**vars(server))
    other.id = "other-server"
    other.to_documents = lambda: [other_doc]
    adapter = _BlockingAdapter([*repo.adapter.docs, other_doc], server.id, phase)
    repo.db_client.adapter = adapter
    repo.db_client.write_adapter = adapter
    return repo, server, other, adapter


async def _run_public_metadata_entry(entry: str, repo: MCPServerRepository, server: SimpleNamespace):
    if entry == "entity":
        return await repo.sync_entity_metadata(server, {"enabled": False})
    if entry == "tools":
        return await repo.sync_tools_enabled(server, ["read_file"])
    if entry == "legacy-entity":
        return await repo.update_entity_metadata("server_id", server.id, {"enabled": False})
    return await repo.update_tools_metadata(server.id, ["read_file"], {"tool_enabled": False})


async def _run_other_entity(repo: MCPServerRepository, other: SimpleNamespace) -> None:
    # Let contenders enter their public methods and start any scheduled operation tasks.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    result = await asyncio.wait_for(repo.sync_entity_metadata(other, {"enabled": False}), timeout=5)
    assert result == _sync_result("mcp", metadata_updated=1)


@pytest.mark.asyncio
@pytest.mark.usefixtures("metadata_threads")
@pytest.mark.parametrize("phase", ["lookup", "patch"])
@pytest.mark.parametrize(
    "first_entry,second_entry",
    [
        ("entity", "tools"),
        ("tools", "entity"),
        ("legacy-entity", "tools"),
        ("tools", "legacy-entity"),
        ("legacy-tools", "entity"),
        ("entity", "legacy-tools"),
    ],
)
async def test_metadata_entries_serialize_one_entity_but_allow_other_entities(
    phase: str, first_entry: str, second_entry: str
) -> None:
    repo, server, other, adapter = _make_blocking_case(phase)
    first = asyncio.create_task(_run_public_metadata_entry(first_entry, repo, server))
    tasks = [first]
    try:
        await asyncio.wait_for(adapter.started.wait(), timeout=5)
        second = asyncio.create_task(_run_public_metadata_entry(second_entry, repo, server))
        tasks.append(second)
        await _run_other_entity(repo, other)
        assert adapter.trace.count((server.id, "lookup:start")) == 1
        assert not second.done()
    finally:
        adapter.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    for task in tasks:
        assert task.result() is not None
    entity_trace = [phase for entity_id, phase in adapter.trace if entity_id == server.id]
    assert entity_trace == ["lookup:start", "lookup:end", "patch:start", "patch:end"] * 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("metadata_threads")
@pytest.mark.parametrize(
    "owner_entry,phase,fail_after_cancel",
    [("entity", "patch", False), ("legacy-tools", "patch", False), ("entity", "lookup", True)],
)
async def test_cancelled_metadata_owner_finishes_thread_before_releasing_entity(
    owner_entry: str, phase: str, fail_after_cancel: bool
) -> None:
    repo, server, other, adapter = _make_blocking_case(phase)
    if fail_after_cancel:
        adapter.blocked_error = RuntimeError("lookup failed after cancellation")
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    owner = asyncio.create_task(_run_public_metadata_entry(owner_entry, repo, server))
    tasks = [owner]
    try:
        await asyncio.wait_for(adapter.started.wait(), timeout=5)
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        follower = asyncio.create_task(_run_public_metadata_entry("tools", repo, server))
        tasks.append(follower)
        await _run_other_entity(repo, other)
        assert not owner.done()
        assert not follower.done()
        assert adapter.trace.count((server.id, "lookup:start")) == 1
    finally:
        adapter.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
        loop.set_exception_handler(previous_handler)

    assert owner.cancelled()
    assert follower.result() == _sync_result("tools", metadata_updated=1)
    assert adapter.trace.count((server.id, "lookup:start")) == 2
    assert unhandled == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("metadata_threads")
async def test_cancelled_metadata_waiter_never_queries_or_patches() -> None:
    repo, server, other, adapter = _make_blocking_case("patch")
    owner = asyncio.create_task(_run_public_metadata_entry("entity", repo, server))
    tasks = [owner]
    try:
        await asyncio.wait_for(adapter.started.wait(), timeout=5)
        waiter = asyncio.create_task(_run_public_metadata_entry("legacy-tools", repo, server))
        tasks.append(waiter)
        await _run_other_entity(repo, other)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, timeout=5)
        assert adapter.trace.count((server.id, "lookup:start")) == 1
    finally:
        adapter.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    assert owner.result() == _sync_result("mcp", metadata_updated=1)
    assert adapter.trace.count((server.id, "lookup:start")) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("metadata_threads")
async def test_metadata_follower_waits_for_rebuild_before_querying() -> None:
    repo, server, other, adapter = _make_blocking_case("never")
    adapter.docs = other.to_documents()
    rebuild_started = asyncio.Event()
    release_rebuild = asyncio.Event()

    async def rebuild(entity: SimpleNamespace, *, is_delete: bool) -> dict:
        assert entity is server and is_delete
        rebuild_started.set()
        await release_rebuild.wait()
        adapter.docs.extend(server.to_documents())
        return _sync_result("mcp", indexed=1)

    repo.sync_to_vector_db = AsyncMock(side_effect=rebuild)
    first = asyncio.create_task(_run_public_metadata_entry("entity", repo, server))
    tasks = [first]
    try:
        await asyncio.wait_for(rebuild_started.wait(), timeout=5)
        follower = asyncio.create_task(_run_public_metadata_entry("legacy-tools", repo, server))
        tasks.append(follower)
        await _run_other_entity(repo, other)
        assert adapter.trace.count((server.id, "lookup:start")) == 1
        assert not follower.done()
    finally:
        release_rebuild.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    assert first.result() == _sync_result("mcp", indexed=1)
    assert follower.result() == VectorSyncResult(metadata_updated=1)
    repo.sync_to_vector_db.assert_awaited_once_with(server, is_delete=True)
