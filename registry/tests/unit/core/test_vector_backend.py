from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from registry.core import vector_backend
from registry_pkgs.vector.config.config import (
    BackendConfig,
    BedrockEmbeddingConfig,
    RerankConfig,
    WeaviateConfig,
)
from registry_pkgs.vector.enum.enums import EmbeddingProvider, VectorStoreType

pytestmark = pytest.mark.asyncio


def _settings():
    return SimpleNamespace(
        vector_config=SimpleNamespace(vector_store_type="weaviate"),
        encryption_key=b"key",
    )


async def test_no_selection_falls_back_to_vector_config(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(vector_backend, "get_model_gateway_selection", AsyncMock(return_value=None))
    monkeypatch.setattr(vector_backend.BackendConfig, "from_vector_config", classmethod(lambda cls, cfg: sentinel))

    result = await vector_backend.resolve_vector_backend_config(_settings())

    assert result is sentinel


async def test_selection_with_missing_source_falls_back(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        vector_backend,
        "get_model_gateway_selection",
        AsyncMock(return_value=SimpleNamespace(embeddingModelSourceId="abc")),
    )
    monkeypatch.setattr(vector_backend.ModelSource, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(vector_backend.BackendConfig, "from_vector_config", classmethod(lambda cls, cfg: sentinel))

    result = await vector_backend.resolve_vector_backend_config(_settings())

    assert result is sentinel


async def test_selection_with_bedrock_source_builds_backend_from_source(monkeypatch):
    model_source = SimpleNamespace(deletedAt=None)
    monkeypatch.setattr(
        vector_backend,
        "get_model_gateway_selection",
        AsyncMock(return_value=SimpleNamespace(embeddingModelSourceId="abc")),
    )
    monkeypatch.setattr(vector_backend.ModelSource, "get", AsyncMock(return_value=model_source))

    weaviate = WeaviateConfig(type=VectorStoreType.WEAVIATE, host="h", port=8080)
    bedrock = BedrockEmbeddingConfig(provider=EmbeddingProvider.AWS_BEDROCK, region="us-east-1", model="titan")
    monkeypatch.setattr(vector_backend, "build_vector_store_config", lambda _cfg: weaviate)
    monkeypatch.setattr(vector_backend, "embedding_config_from_model_source", lambda _s, *, encryption_key: bedrock)
    monkeypatch.setattr(RerankConfig, "from_vector_config", classmethod(lambda cls, cfg: RerankConfig()))

    result = await vector_backend.resolve_vector_backend_config(_settings())

    assert isinstance(result, BackendConfig)
    assert result.embedding_model_config is bedrock
    assert result.vector_store_config is weaviate
