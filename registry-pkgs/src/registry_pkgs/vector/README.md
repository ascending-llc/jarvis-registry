# Vector Database Package

Unified interface for vector databases with multiple embedding providers (OpenAI, AWS Bedrock).

Built on [LangChain Vector Stores](https://docs.langchain.com/oss/python/integrations/vectorstores), providing a unified abstraction layer that supports any LangChain-compatible vector store. Currently implemented with Weaviate.

## Architecture Overview

```
Service Layer (Business Logic)
    ↓
Specialized Repository (MCPServerRepository / A2AAgentRepository)  ← Domain sync logic
    ↓
BaseVectorSyncRepository                                           ← Shared sync infrastructure
    ↓
Generic Repository[T]                                              ← CRUD, Search, Bulk ops
    ↓
VectorStoreAdapter (Unified Interface)                            ← Database abstraction
    ↓
LangChain VectorStore (Weaviate)                                  ← Native DB operations
```

## Sync Design

### Hash-gated sync (service layer)

`vectorContentHash` (SHA-256 of all `page_content` sorted and joined) is recomputed on every
MongoDB save via a `@before_event` hook on both `ExtendedMCPServer` and `A2AAgent`.

The service layer captures the hash before `.save()` and compares after to decide what to do:

```python
old_hash = server.vectorContentHash
await server.save()                          # @before_event recomputes hash
_schedule_vector_sync(server, old_hash)      # routes based on change
```

**Hash changed** → `sync_to_vector_db(is_delete=True)` — full Weaviate rebuild.

**Hash unchanged** → `sync_entity_metadata(...)` — compare the existing doc count with
`len(entity.to_documents())`. Matching counts get a metadata-only patch without re-embedding;
missing, partial or excess docs trigger a full rebuild of the entity. Enabling a server or agent
via its toggle endpoint already forces a full rebuild.

Per-tool enable/disable uses `sync_tools_enabled(...)`: one lookup and count check over all
flipped tools, then up to two metadata patches, or one full server rebuild on a mismatch.
Counts include document chunks, not just tools.

Both paths use strict metadata lookups: a failed read propagates instead of being mistaken for
zero docs and triggering embedding. The lookup is capped at 10,000 docs, so if the expected
count reaches that limit, the sync warns and patches the returned docs without comparing counts. Zero expected and found docs is a no-op.

Metadata syncs are not serialized. Overlapping rebuilds of the same entity converge, because
doc ids are deterministic per collection, entity and position, so a re-insert replaces docs
instead of duplicating them.

The new `sync_*` methods return an `error` if the adapter reports fewer successful metadata
updates than the number of matched docs, while `metadata_updated` retains the successful count.
A partial patch does not trigger a rebuild. The legacy `update_*` methods retain their existing
result semantics.

Services supply a MongoDB `reload` callback, called only before a mismatch-triggered rebuild.
If the entity no longer exists or its content hash changed, the task skips rebuilding. Otherwise
the rebuild uses the original task snapshot: before a transaction commits, reload may still see
older metadata. The guard does not detect newer metadata with the same content hash or prevent
a delete that commits after the check. Entities that are never saved again still require reindexing.

> **Invariant**: `config.enabled` must NOT appear in `page_content` inside
> `to_documents()`. If it does, toggle paths will incorrectly trigger full rebuilds.

### Federation path

Federation sync bypasses the service-layer routing. It manages its own delete + reinsert cycle
via `_sync_vector_index_after_commit` (post-transaction hook):

```python
await repo.delete_by_runtime_identity(federation_id, runtime_arn)
result = await repo.sync_to_vector_db(entity, is_delete=False)   # insert only
```

## Repository API

### MCPServerRepository

```python
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

repo = MCPServerRepository(db_client)
```

#### `sync_to_vector_db(server, *, is_delete=True) -> dict`

Full rebuild: delete existing docs then reinsert. Called after content changes or a doc-count
mismatch; the repository itself does not gate rebuilding on the content hash.

```python
result = await repo.sync_to_vector_db(server, is_delete=True)
# {"indexed_tools": 3, "failed_tools": 0, "deleted": 3, "version": "7", "error": None}
```

`is_delete=False` skips the delete step (federation path — caller already deleted by runtime ARN).

#### `delete_by_server_id(server_id, server_name?) -> int`

Remove all Weaviate docs for an MCP server (iterates over all `ServerEntityType` values).

```python
count = await repo.delete_by_server_id("507f1f77bcf86cd799439011", "github")
```

#### `sync_entity_metadata(entity, metadata, *, reload=None) -> dict`

Service-facing metadata sync with a doc-count check and full-rebuild fallback. Returns the
same MCP result keys as `sync_to_vector_db`, whether it patches, rebuilds or skips. An incomplete
metadata patch sets `error` and preserves the number of successful updates in `metadata_updated`.

```python
result = await repo.sync_entity_metadata(
    server,
    server.mutable_metadata(),
    reload=lambda: ExtendedMCPServer.get(server.id),
)
```

#### `sync_tools_enabled(server, tool_names, *, reload=None) -> dict`

Service-facing sync for flipped tools. Counts matching tool docs from `server.to_documents()`,
including chunks, and derives each tool's desired `tool_enabled` from `registryDisabledTools`.
A count mismatch rebuilds the whole server once. Partial patches report an error and retain the
total successful update count across both groups. An empty tool list makes no query.

#### `update_entity_metadata(entity_id_field, entity_id, metadata) -> VectorSyncResult`

Plain metadata patch for scripts such as `backfill_tool_enabled_property.py`. It updates only
existing docs without checking their expected count or rebuilding. Missing docs are a no-op.

```python
result = await repo.update_entity_metadata(
    "server_id", str(server.id),
    {"enabled": False}
)
# result.metadata_updated == number of docs patched
```

#### `update_tools_metadata(server_id, tool_names, metadata) -> VectorSyncResult`

Plain metadata patch for scripts, restricted to the named tools. Like `update_entity_metadata`,
it does not rebuild or check counts. An empty tool list makes no query.

#### `delete_by_runtime_identity(federation_id, runtime_arn) -> int`

Delete all docs matching a federation + runtime ARN pair. Used before federation reinsert.

```python
deleted = await repo.delete_by_runtime_identity("fed-1", "arn:aws:lambda:us-east-1:123:fn")
```

#### `has_runtime_identity(federation_id, runtime_arn) -> bool`

Return True if any Weaviate doc exists for the given federation identity.

#### `get_all_docs_by_server_id(server_id) -> dict`

Return all Weaviate docs grouped by entity type: `{"tools": [...], "resources": [...], "prompts": [...]}`.

---

### A2AAgentRepository

```python
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository

repo = A2AAgentRepository(db_client)
```

#### `sync_to_vector_db(agent, *, is_delete=True) -> dict`

Full rebuild for an A2A agent.

```python
result = await repo.sync_to_vector_db(agent, is_delete=True)
# {"indexed": 4, "failed": 0, "deleted": 4, "metadata_updated": 0, "version": "1", "error": None}
```

#### `delete_by_agent_id(agent_id, agent_name?) -> int`

Remove all Weaviate docs for an A2A agent.

```python
count = await repo.delete_by_agent_id("507f1f77bcf86cd799439011", "deep-intel")
```

#### `sync_entity_metadata`, `update_entity_metadata`, `delete_by_runtime_identity`, `has_runtime_identity`

Same semantics as MCPServerRepository — inherited from `BaseVectorSyncRepository`.
`sync_entity_metadata` returns the A2A `indexed`/`failed` keys, matching its full rebuild;
`update_entity_metadata` remains a plain patch returning `VectorSyncResult`.

---

## VectorSyncResult

```python
@dataclass
class VectorSyncResult:
    indexed: int = 0
    failed: int = 0
    deleted: int = 0
    metadata_updated: int = 0
    version: str | None = None
    error: str | None = None
```

- `.to_dict()` — Serialized result format (`indexed`, `failed`, `deleted`, `metadata_updated`, `version`, `error`)

---

## Generic Repository[T] base

`Repository[T]` in `repository.py` provides low-level operations shared by all repositories:

| Method | Description |
|---|---|
| `save(instance)` / `asave` | Vectorize and insert docs |
| `get(doc_id)` / `aget` | Fetch a single doc by Weaviate UUID |
| `delete(doc_id)` / `adelete` | Delete by Weaviate UUID or server_id |
| `delete_by_filter(filters)` / `adelete_by_filter` | Delete matching docs |
| `search(query, ...)` / `asearch` | Semantic / BM25 / hybrid search |
| `filter(filters, limit)` / `afilter` | Metadata-only filter |
| `search_with_rerank(...)` / `asearch_with_rerank` | Search + FlashRank reranking |
| `bulk_save(instances)` / `abulk_save` | Batch vectorize |
| `get_retriever(...)` | LangChain `AdapterRetriever` |
| `get_compression_retriever(...)` | LangChain `ContextualCompressionRetriever` |
