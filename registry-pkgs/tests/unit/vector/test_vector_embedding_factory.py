from types import SimpleNamespace

import pytest

from registry_pkgs.vector.adapters.factory import (
    VectorStoreFactory,
    get_embedding_creator,
    get_supported_embeddings,
)
from registry_pkgs.vector.config import (
    AzureOpenAIEmbeddingConfig,
    BackendConfig,
    BedrockEmbeddingConfig,
    OpenAIEmbeddingConfig,
    WeaviateConfig,
)
from registry_pkgs.vector.enum.enums import EmbeddingProvider, VectorStoreType


@pytest.mark.parametrize(
    ("provider", "expected_module"),
    [
        (EmbeddingProvider.OPENAI, "langchain_openai"),
        (EmbeddingProvider.AWS_BEDROCK, "langchain_aws"),
        (EmbeddingProvider.AZURE_OPENAI, "langchain_openai"),
    ],
)
def test_embedding_provider_is_registered_as_supported_embedding(provider, expected_module):
    """Configured embedding providers should be available in the creator registry."""
    assert provider in get_supported_embeddings()
    creator = get_embedding_creator(provider)
    assert callable(creator)
    assert expected_module in creator.__module__ or creator.__module__.endswith("embedding")


def test_create_openai_embedding_passes_expected_kwargs(monkeypatch):
    """OpenAI creator should instantiate langchain_openai with the expected kwargs."""
    captured_kwargs = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    def fake_import_module(module_name: str):
        if module_name == "langchain_openai":
            return SimpleNamespace(OpenAIEmbeddings=FakeOpenAIEmbeddings)
        raise AssertionError(f"Unexpected module import: {module_name}")

    monkeypatch.setattr("registry_pkgs.vector.adapters.create.embedding.importlib.import_module", fake_import_module)

    config = BackendConfig(
        vector_store_config=WeaviateConfig(type=VectorStoreType.WEAVIATE, host="localhost", port=8080),
        embedding_model_config=OpenAIEmbeddingConfig(
            provider=EmbeddingProvider.OPENAI,
            api_key="sk-test-key",
            model="text-embedding-3-small",
        ),
    )

    creator = get_embedding_creator(EmbeddingProvider.OPENAI)
    embedding = creator(config)

    assert isinstance(embedding, FakeOpenAIEmbeddings)
    assert captured_kwargs == {
        "api_key": "sk-test-key",
        "model": "text-embedding-3-small",
    }


def test_create_bedrock_embedding_passes_expected_kwargs(monkeypatch):
    """Bedrock creator should instantiate langchain_aws with the expected kwargs."""
    captured_kwargs = {}

    class FakeBedrockEmbeddings:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    def fake_import_module(module_name: str):
        if module_name == "langchain_aws":
            return SimpleNamespace(BedrockEmbeddings=FakeBedrockEmbeddings)
        raise AssertionError(f"Unexpected module import: {module_name}")

    monkeypatch.setattr("registry_pkgs.vector.adapters.create.embedding.importlib.import_module", fake_import_module)

    config = BackendConfig(
        vector_store_config=WeaviateConfig(type=VectorStoreType.WEAVIATE, host="localhost", port=8080),
        embedding_model_config=BedrockEmbeddingConfig(
            provider=EmbeddingProvider.AWS_BEDROCK,
            region="us-east-1",
            model="amazon.titan-embed-text-v2:0",
            access_key_id="test-access-key",
            secret_access_key="test-secret-key",
        ),
    )

    creator = get_embedding_creator(EmbeddingProvider.AWS_BEDROCK)
    embedding = creator(config)

    assert isinstance(embedding, FakeBedrockEmbeddings)
    assert captured_kwargs == {
        "region_name": "us-east-1",
        "model_id": "amazon.titan-embed-text-v2:0",
        "aws_access_key_id": "test-access-key",
        "aws_secret_access_key": "test-secret-key",
    }


def test_create_azure_openai_embedding_passes_expected_kwargs(monkeypatch):
    """Azure OpenAI creator should instantiate langchain_openai with Azure kwargs."""
    captured_kwargs = {}

    class FakeAzureOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    def fake_import_module(module_name: str):
        if module_name == "langchain_openai":
            return SimpleNamespace(AzureOpenAIEmbeddings=FakeAzureOpenAIEmbeddings)
        raise AssertionError(f"Unexpected module import: {module_name}")

    monkeypatch.setattr("registry_pkgs.vector.adapters.create.embedding.importlib.import_module", fake_import_module)

    config = BackendConfig(
        vector_store_config=WeaviateConfig(type=VectorStoreType.WEAVIATE, host="localhost", port=8080),
        embedding_model_config=AzureOpenAIEmbeddingConfig(
            provider=EmbeddingProvider.AZURE_OPENAI,
            api_key="test-key",
            endpoint="https://example.openai.azure.com",
            api_version="2024-06-01",
            resource_name="example",
            deployment_name="text-embedding-3-large",
        ),
    )

    creator = get_embedding_creator(EmbeddingProvider.AZURE_OPENAI)
    embedding = creator(config)

    assert isinstance(embedding, FakeAzureOpenAIEmbeddings)
    assert captured_kwargs == {
        "api_key": "test-key",
        "azure_endpoint": "https://example.openai.azure.com",
        "api_version": "2024-06-01",
        "azure_deployment": "text-embedding-3-large",
        "model": "text-embedding-3-large",
    }


@pytest.mark.parametrize(
    "config",
    [
        BackendConfig(
            vector_store_config=WeaviateConfig(type=VectorStoreType.WEAVIATE, host="localhost", port=8080),
            embedding_model_config=OpenAIEmbeddingConfig(
                provider=EmbeddingProvider.OPENAI,
                api_key="sk-test-key",
                model="text-embedding-3-small",
            ),
        ),
        BackendConfig(
            vector_store_config=WeaviateConfig(type=VectorStoreType.WEAVIATE, host="localhost", port=8080),
            embedding_model_config=BedrockEmbeddingConfig(
                provider=EmbeddingProvider.AWS_BEDROCK,
                region="us-east-1",
                model="amazon.titan-embed-text-v2:0",
            ),
        ),
        BackendConfig(
            vector_store_config=WeaviateConfig(type=VectorStoreType.WEAVIATE, host="localhost", port=8080),
            embedding_model_config=AzureOpenAIEmbeddingConfig(
                provider=EmbeddingProvider.AZURE_OPENAI,
                api_key="test-key",
                endpoint="https://example.openai.azure.com",
                api_version="2024-06-01",
                resource_name="example",
                deployment_name="text-embedding-3-large",
            ),
        ),
    ],
)
def test_validate_config_accepts_supported_embeddings(config):
    """Factory validation should accept all registered embedding providers."""
    VectorStoreFactory._validate_config(config)
