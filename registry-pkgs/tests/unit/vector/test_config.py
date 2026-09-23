import pytest

from registry_pkgs.core.config import VectorConfig
from registry_pkgs.vector.config.config import WeaviateConfig, build_vector_store_config, extract_azure_resource_name


def test_extract_azure_resource_name_from_valid_endpoint():
    assert extract_azure_resource_name("https://acme.openai.azure.com") == "acme"
    assert extract_azure_resource_name("https://acme.openai.azure.com/") == "acme"
    assert extract_azure_resource_name("https://my-res-01.openai.azure.com/openai") == "my-res-01"


def test_extract_azure_resource_name_rejects_non_azure_endpoint():
    with pytest.raises(ValueError, match="openai.azure.com"):
        extract_azure_resource_name("https://example.com")


def test_extract_azure_resource_name_rejects_lookalike_host():
    with pytest.raises(ValueError, match="openai.azure.com"):
        extract_azure_resource_name("https://acme.openai.azure.com.attacker.example")


def test_build_vector_store_config_builds_registered_type():
    config = build_vector_store_config(
        VectorConfig(vector_store_type=" Weaviate ", weaviate_host="h", weaviate_port=1234)
    )

    assert isinstance(config, WeaviateConfig)
    assert config.host == "h"
    assert config.port == 1234


def test_build_vector_store_config_rejects_unset_type():
    with pytest.raises(ValueError, match="vector_store_type must be set"):
        build_vector_store_config(VectorConfig(vector_store_type=""))


def test_build_vector_store_config_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unsupported vector_store_type"):
        build_vector_store_config(VectorConfig(vector_store_type="pinecone"))
