import logging
from typing import Any

from langchain_classic.retrievers.document_compressors.base import BaseDocumentCompressor

logger = logging.getLogger(__name__)

_DEFAULT_RERANK_MODEL_ID = "cohere.rerank-v3-5:0"
_BEDROCK_COHERE = "bedrock_cohere"
_SUPPORTED_RERANKERS = (_BEDROCK_COHERE,)
_BEDROCK_IMPORT_ERROR = "langchain-aws is required for Bedrock reranking. Install with: uv sync"


def _build_rerank_arn(region: str, model_id: str) -> str:
    """Build a Bedrock foundation-model ARN from region and model ID."""
    return f"arn:aws:bedrock:{region}::foundation-model/{model_id}"


def create_reranker(reranker_type: str, **kwargs) -> BaseDocumentCompressor:
    """
    Create reranker instance based on provider type.

    Args:
        reranker_type: Reranker provider (e.g., "bedrock_cohere")
        **kwargs: Additional reranker parameters

    Returns:
        BaseDocumentCompressor instance

    Raises:
        ValueError: If reranker_type is not supported
    """
    reranker_type = reranker_type.lower()

    if reranker_type == _BEDROCK_COHERE:
        return _create_bedrock_cohere_reranker(**kwargs)

    raise ValueError(f"Unsupported reranker type: {reranker_type}. Supported types: {', '.join(_SUPPORTED_RERANKERS)}")


def _create_bedrock_cohere_reranker(**kwargs) -> BaseDocumentCompressor:
    """
    Create an AWS Bedrock Cohere reranker.

    Reranking runs as a remote Bedrock API call, so there is no local model
    to load and no per-inference activation memory in the pod.

    Args:
        **kwargs: Reranker parameters
            - region: AWS region (required, used to build the model ARN)
            - model_id: Bedrock Cohere model ID (default: "cohere.rerank-v3-5:0")
            - access_key_id / secret_access_key / session_token: optional AWS creds
            - top_n: Number of results to return

    Returns:
        BedrockRerank instance
    """
    try:
        from langchain_aws import BedrockRerank
    except ImportError as e:
        logger.error(_BEDROCK_IMPORT_ERROR)
        raise ImportError(_BEDROCK_IMPORT_ERROR) from e

    region = kwargs.get("region")
    if not region:
        raise ValueError("Bedrock reranker requires 'region' to build the model ARN")

    model_id = kwargs.get("model_id") or _DEFAULT_RERANK_MODEL_ID

    rerank_kwargs: dict[str, Any] = {
        "model_arn": _build_rerank_arn(region, model_id),
        "region_name": region,
        "top_n": kwargs.get("top_n", 10),
    }

    access_key_id = kwargs.get("access_key_id")
    secret_access_key = kwargs.get("secret_access_key")
    if access_key_id and secret_access_key:
        rerank_kwargs["aws_access_key_id"] = access_key_id
        rerank_kwargs["aws_secret_access_key"] = secret_access_key

    session_token = kwargs.get("session_token")
    if session_token:
        rerank_kwargs["aws_session_token"] = session_token

    logger.info("Creating Bedrock Cohere reranker: model_id=%s, region=%s", model_id, region)
    return BedrockRerank(**rerank_kwargs)
