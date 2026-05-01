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

**Hash unchanged** → `update_entity_metadata(...)` — metadata-only patch (no re-embedding).
Used for toggle enable/disable where only `enabled`/`status` change.

> **Invariant**: `enabled`/`status`/`isEnabled` must NOT appear in `page_content` inside
> `to_documents()`. If they do, toggle paths will incorrectly trigger full rebuilds.

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

Full rebuild: delete existing docs then reinsert. Hash gate removed — caller decides when to call.

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

#### `update_entity_metadata(entity_id_field, entity_id, metadata) -> VectorSyncResult`

Patch metadata fields on all matching docs without re-embedding. Used for enable/disable.

```python
result = await repo.update_entity_metadata(
    "server_id", str(server.id),
    {"enabled": False, "status": "inactive"}
)
# result.metadata_updated == number of docs patched
```

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

#### `update_entity_metadata`, `delete_by_runtime_identity`, `has_runtime_identity`

Same semantics as MCPServerRepository — inherited from `BaseVectorSyncRepository`.

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

- `.to_dict()` — A2A format (`indexed`, `failed`, ...)
- `.to_dict_mcp()` — MCP format (`indexed_tools`, `failed_tools`, ...)

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
