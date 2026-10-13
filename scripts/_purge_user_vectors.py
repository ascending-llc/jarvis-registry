"""Delete MCP vectors without embedding credentials or implicit schema changes.

The regular adapter treats partial deletes and several read errors as empty
results. An operator-driven purge must instead stop before deleting Mongo data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from grpc import RpcError
from httpx import HTTPError
from pydantic import Field, SecretStr
from weaviate.exceptions import WeaviateBaseError

from registry_pkgs.core.config import JarvisEnvSettings
from registry_pkgs.vector.backends.weaviate_store import WeaviateStore
from registry_pkgs.vector.client import DatabaseClient, collection_name_for
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository


class VectorCleanupError(RuntimeError):
    """Safe operator message; raw SDK details stay in the exception cause."""


async def _run_vector_io[T](operation: Callable[..., T], *args: Any) -> T:
    try:
        return await asyncio.to_thread(operation, *args)
    except VectorCleanupError:
        raise
    except (WeaviateBaseError, RpcError, HTTPError, OSError, ValueError, RuntimeError) as exc:
        raise VectorCleanupError(
            f"Vector cleanup failed ({type(exc).__name__}); check the connection and retry"
        ) from exc


class VectorSettings(JarvisEnvSettings):
    """Only the connection settings required by a delete operation."""

    weaviate_host: str = Field(default="127.0.0.1", min_length=1)
    weaviate_port: int = Field(default=8080, ge=1, le=65535)
    weaviate_grpc_port: int = Field(default=50051, ge=1, le=65535)
    weaviate_api_key: SecretStr | None = None


class StrictWeaviateStore(WeaviateStore):
    """Require an existing, identifiable collection and verify every delete."""

    def __init__(self, settings: VectorSettings) -> None:
        super().__init__(
            embedding=None,
            config={
                "host": settings.weaviate_host,
                "port": settings.weaviate_port,
                "grpc_port": settings.weaviate_grpc_port,
                "api_key": settings.weaviate_api_key.get_secret_value() if settings.weaviate_api_key else None,
            },
        )
        self._validated_collections: dict[str, bool] = {}

    def collection_exists(self, collection_name: str | None = None) -> bool:
        return self._get_client().collections.exists(collection_name or self._default_collection)

    def validate_collection(self, collection_name: str) -> None:
        exists = self.collection_exists(collection_name)
        if exists:
            config = self._get_client().collections.get(collection_name).config.get()
            properties = {
                prop.get("name") if isinstance(prop, dict) else prop.name for prop in (config.properties or [])
            }
            if "server_id" not in properties:
                raise VectorCleanupError("Vector collection has no server_id property")
        # A confirmed missing collection contains no vectors. Connection failures
        # propagate, rather than being mistaken for a missing collection.
        self._validated_collections[collection_name] = exists

    def has_property(self, collection_name: str, property_name: str) -> bool:
        if property_name != "server_id" or collection_name not in self._validated_collections:
            raise VectorCleanupError("Vector collection must be validated before deletion")
        return self._validated_collections[collection_name]

    def delete_by_filter(self, filters: Any, collection_name: str | None = None) -> int:
        normalized = self.normalize_filters(filters)
        if normalized is None:
            raise ValueError("Refusing vector deletion without a valid filter")
        collection = self._get_client().collections.get(collection_name or self._default_collection)
        result = collection.data.delete_many(where=normalized)
        if result.failed:
            raise VectorCleanupError(
                f"Vector deletion partially failed: {result.successful} deleted, {result.failed} failed"
            )
        if collection.query.fetch_objects(filters=normalized, limit=1).objects:
            raise VectorCleanupError("Vector deletion left matching documents; rerun before deleting MongoDB records")
        return result.successful


class DeletionClient(DatabaseClient):
    """Inject the delete-only adapter without constructing an embedding client."""

    def __init__(self, adapter: StrictWeaviateStore, generation: str | None) -> None:
        super().__init__()
        self._deletion_adapter = adapter
        self._generation = generation

    @property
    def adapter(self) -> StrictWeaviateStore:
        return self._deletion_adapter

    @property
    def write_adapter(self) -> StrictWeaviateStore:
        return self._deletion_adapter

    def collection_name_for(self, base_name: str) -> str:
        return collection_name_for(base_name, self._generation)


class DeletionRepository(MCPServerRepository):
    """Keep the shared deletion operation, replacing its schema initialization."""

    async def ensure_collection(self) -> bool:
        await _run_vector_io(self.adapter.validate_collection, self.collection)
        return True

    async def adelete_by_filter(self, filters: Any) -> int:
        # The shared sync wrapper logs raw SDK errors, which may include credentials.
        return await _run_vector_io(self.adapter.delete_by_filter, filters, self.collection)


@asynccontextmanager
async def open_vector_repository(generation: str | None) -> AsyncIterator[MCPServerRepository]:
    """Open only during apply; the caller guards reindex state and generation."""
    try:
        adapter = StrictWeaviateStore(VectorSettings())
    except ValueError as exc:
        raise VectorCleanupError("Invalid Weaviate connection settings") from exc
    try:
        yield DeletionRepository(DeletionClient(adapter, generation))
    finally:
        await _run_vector_io(adapter.close)
