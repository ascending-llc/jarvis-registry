from __future__ import annotations

import asyncio
import logging

from registry.core.config import Settings
from registry_pkgs.core.config import VectorConfig
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.vector.adapters.factory import VectorStoreFactory
from registry_pkgs.vector.config.config import (
    BackendConfig,
    RerankConfig,
    build_vector_store_config,
)
from registry_pkgs.vector.config.model_source_adapter import embedding_config_from_model_source

logger = logging.getLogger(__name__)


def build_backend_config_from_model_source(
    model_source: ModelSource,
    vector_config: VectorConfig,
    *,
    encryption_key: bytes,
    collection_generation: str | None = None,
) -> BackendConfig:
    """Assemble a BackendConfig embedding via ``model_source``, storing/reranking via ``vector_config``.

    The single ModelSource->BackendConfig mapping, shared by startup, the smoke test and the executor.
    ``collection_generation`` picks the generation to read/write (``None`` = legacy base collections).
    """
    return BackendConfig(
        vector_store_config=build_vector_store_config(vector_config),
        embedding_model_config=embedding_config_from_model_source(model_source, encryption_key=encryption_key),
        rerank_config=RerankConfig.from_vector_config(vector_config),
        collection_generation=collection_generation,
    )


async def smoke_test_embedding_model(
    model_source: ModelSource,
    vector_config: VectorConfig,
    *,
    encryption_key: bytes,
) -> None:
    """Prove the embedding client for ``model_source`` can actually embed, before anything persists.

    Builds only the embedding client (no vector-store connection is opened) and runs one blocking
    ``embed_query`` off the event loop. Any credential/endpoint/network failure propagates so the
    caller can reject the request before entering maintenance mode.
    """
    config = build_backend_config_from_model_source(model_source, vector_config, encryption_key=encryption_key)
    embedding = VectorStoreFactory._create_embedding(config)
    await asyncio.to_thread(embedding.embed_query, "healthcheck")


async def resolve_vector_backend_config(settings: Settings) -> BackendConfig:
    """Return the BackendConfig used to build the process's DatabaseClient at startup.

    Reads the active ``(embeddingModelSourceId, embeddingCollectionGeneration)`` pair so a pod that
    starts during or after a switch comes up on the matching model AND collection generation.
    """
    vector_config = settings.vector_config
    selection = await get_model_gateway_selection(create_if_missing=False)
    embedding_model_source_id = selection.embeddingModelSourceId if selection else None
    generation = selection.embeddingCollectionGeneration if selection else None
    if embedding_model_source_id is None:
        # Legacy env-var fallback is only ever used at generation 0 (no reindex has committed).
        return BackendConfig.from_vector_config(vector_config)

    model_source = await ModelSource.get(embedding_model_source_id)
    if model_source is None or model_source.deletedAt is not None:
        if generation is not None:
            # Fail-hard: a committed generation's source is gone, so we cannot pick the right model
            # for its vectors. is_in_use guards deletion, so this means the DB was edited out-of-band.
            raise RuntimeError(
                f"Active embedding generation '{generation}' names ModelSource "
                f"{embedding_model_source_id}, which is missing or soft-deleted"
            )
        logger.warning(
            "Configured embedding ModelSource %s not found/deleted; falling back to legacy vector_config",
            embedding_model_source_id,
        )
        return BackendConfig.from_vector_config(vector_config)

    return build_backend_config_from_model_source(
        model_source,
        vector_config,
        encryption_key=settings.encryption_key,
        collection_generation=generation,
    )
