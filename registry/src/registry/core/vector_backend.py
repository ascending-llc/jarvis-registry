from __future__ import annotations

import logging

from registry.core.config import Settings
from registry_pkgs.database.model_gateway_selection_repository import get_model_gateway_selection
from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.vector.config.config import (
    BackendConfig,
    RerankConfig,
    build_vector_store_config,
)
from registry_pkgs.vector.config.model_source_adapter import embedding_config_from_model_source

logger = logging.getLogger(__name__)


async def resolve_vector_backend_config(settings: Settings) -> BackendConfig:
    """Return the BackendConfig used to build the process's DatabaseClient at startup."""
    vector_config = settings.vector_config
    selection = await get_model_gateway_selection(create_if_missing=False)
    embedding_model_source_id = selection.embeddingModelSourceId if selection else None
    if embedding_model_source_id is None:
        return BackendConfig.from_vector_config(vector_config)

    model_source = await ModelSource.get(embedding_model_source_id)
    if model_source is None or model_source.deletedAt is not None:
        logger.warning(
            "Configured embedding ModelSource %s not found/deleted; falling back to legacy vector_config",
            embedding_model_source_id,
        )
        return BackendConfig.from_vector_config(vector_config)

    return BackendConfig(
        vector_store_config=build_vector_store_config(vector_config),
        embedding_model_config=embedding_config_from_model_source(model_source, encryption_key=settings.encryption_key),
        rerank_config=RerankConfig.from_vector_config(vector_config),
    )
