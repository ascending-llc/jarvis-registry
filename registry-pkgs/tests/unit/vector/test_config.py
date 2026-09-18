import pytest

from registry_pkgs.vector.config.config import extract_azure_resource_name


def test_extract_azure_resource_name_from_valid_endpoint():
    assert extract_azure_resource_name("https://acme.openai.azure.com") == "acme"
    assert extract_azure_resource_name("https://acme.openai.azure.com/") == "acme"
    assert extract_azure_resource_name("https://my-res-01.openai.azure.com/openai") == "my-res-01"


def test_extract_azure_resource_name_rejects_non_azure_endpoint():
    with pytest.raises(ValueError, match="openai.azure.com"):
        extract_azure_resource_name("https://example.com")
