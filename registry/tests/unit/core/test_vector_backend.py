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


def test_build_backend_config_from_model_source_assembles_all_three_parts(monkeypatch):
    weaviate = WeaviateConfig(type=VectorStoreType.WEAVIATE, host="h", port=8080)
    bedrock = BedrockEmbeddingConfig(provider=EmbeddingProvider.AWS_BEDROCK, region="us-east-1", model="titan")
    monkeypatch.setattr(vector_backend, "build_vector_store_config", lambda _cfg: weaviate)
    captured = {}
    monkeypatch.setattr(
        vector_backend,
        "embedding_config_from_model_source",
        lambda source, *, encryption_key: captured.update(source=source, key=encryption_key) or bedrock,
    )
    monkeypatch.setattr(RerankConfig, "from_vector_config", classmethod(lambda cls, cfg: RerankConfig()))
    model_source = SimpleNamespace(deletedAt=None)

    result = vector_backend.build_backend_config_from_model_source(
        model_source, SimpleNamespace(vector_store_type="weaviate"), encryption_key=b"key"
    )

    assert isinstance(result, BackendConfig)
    assert result.vector_store_config is weaviate
    assert result.embedding_model_config is bedrock
    assert captured == {"source": model_source, "key": b"key"}


async def test_smoke_test_embedding_model_embeds_a_probe(monkeypatch):
    embedded = []
    embedding = SimpleNamespace(embed_query=lambda text: embedded.append(text) or [0.0])
    monkeypatch.setattr(
        vector_backend,
        "build_backend_config_from_model_source",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(vector_backend.VectorStoreFactory, "_create_embedding", classmethod(lambda cls, cfg: embedding))

    await vector_backend.smoke_test_embedding_model(SimpleNamespace(), SimpleNamespace(), encryption_key=b"key")

    assert embedded == ["healthcheck"]


async def test_smoke_test_embedding_model_propagates_failure(monkeypatch):
    def _boom(_text):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr(vector_backend, "build_backend_config_from_model_source", lambda *a, **k: object())
    monkeypatch.setattr(
        vector_backend.VectorStoreFactory,
        "_create_embedding",
        classmethod(lambda cls, cfg: SimpleNamespace(embed_query=_boom)),
    )

    with pytest.raises(RuntimeError, match="bad credentials"):
        await vector_backend.smoke_test_embedding_model(SimpleNamespace(), SimpleNamespace(), encryption_key=b"key")
