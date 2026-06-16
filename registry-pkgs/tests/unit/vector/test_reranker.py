from unittest.mock import patch

import pytest
from pydantic import SecretStr

from registry_pkgs.vector.retrievers.reranker import _build_rerank_arn, create_reranker


def test_build_rerank_arn():
    """ARN is built as a foundation-model ARN from region + model ID."""
    arn = _build_rerank_arn("us-west-2", "cohere.rerank-v3-5:0")
    assert arn == "arn:aws:bedrock:us-west-2::foundation-model/cohere.rerank-v3-5:0"


def test_create_reranker_unsupported_type_raises():
    """Unknown providers must fail loudly."""
    with pytest.raises(ValueError, match="Unsupported reranker type"):
        create_reranker("flashrank")


def test_create_reranker_bedrock_requires_region():
    """Bedrock reranker cannot build an ARN without a region."""
    with pytest.raises(ValueError, match="requires 'region'"):
        create_reranker("bedrock_cohere", model_id="cohere.rerank-v3-5:0")


def test_create_reranker_bedrock_builds_compressor_with_credentials():
    """With creds present, they are passed through alongside the built ARN and top_n."""
    with patch("langchain_aws.BedrockRerank") as mock_rerank:
        create_reranker(
            "bedrock_cohere",
            region="us-east-1",
            model_id="cohere.rerank-v3-5:0",
            access_key_id="AKIA",
            secret_access_key="secret",
            top_n=7,
        )

    mock_rerank.assert_called_once_with(
        model_arn="arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0",
        region_name="us-east-1",
        top_n=7,
        aws_access_key_id=SecretStr("AKIA"),
        aws_secret_access_key=SecretStr("secret"),
    )


def test_create_reranker_bedrock_omits_credentials_when_absent():
    """Without explicit creds, only ARN/region/top_n are passed (default credential chain)."""
    with patch("langchain_aws.BedrockRerank") as mock_rerank:
        create_reranker("bedrock_cohere", region="us-east-1")

    mock_rerank.assert_called_once_with(
        model_arn="arn:aws:bedrock:us-east-1::foundation-model/cohere.rerank-v3-5:0",
        region_name="us-east-1",
        top_n=10,
    )
