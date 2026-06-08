import logging
from functools import lru_cache
from typing import Any

from langchain_classic.retrievers.document_compressors.base import BaseDocumentCompressor

logger = logging.getLogger(__name__)

_DEFAULT_FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"


def create_reranker(reranker_type: str, **kwargs) -> BaseDocumentCompressor:
    """
    Create reranker instance based on provider type.

    Args:
        reranker_type: Reranker provider (e.g., "flashrank")
        **kwargs: Additional reranker parameters

    Returns:
        BaseDocumentCompressor instance

    Raises:
        ValueError: If reranker_type is not supported
    """
    reranker_type = reranker_type.lower()

    if reranker_type == "flashrank":
        return _create_flashrank_reranker(**kwargs)
    else:
        raise ValueError(f"Unsupported reranker type: {reranker_type}. Supported types: flashrank")


@lru_cache(maxsize=4)
def _get_flashrank_ranker(model: str) -> Any:
    """Load and cache the underlying FlashRank Ranker once per model."""
    try:
        from flashrank import Ranker
    except ImportError as e:
        logger.error("FlashRank not installed. Install with: pip install flashrank")
        raise ImportError("FlashRank is required for reranking. Install with: pip install flashrank") from e

    logger.info(f"Loading FlashRank model: {model}")
    return Ranker(model_name=model)


def _create_flashrank_reranker(**kwargs) -> BaseDocumentCompressor:
    """
    Create FlashRank reranker.

    Args:
        **kwargs: FlashRank parameters
            - model: Model name (default: "ms-marco-MiniLM-L-12-v2")
            - top_n: Number of results to return (handled by caller)

    Returns:
        FlashRankRerank instance
    """
    try:
        from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank
    except ImportError as e:
        logger.error("FlashRank not installed. Install with: pip install flashrank")
        raise ImportError("FlashRank is required for reranking. Install with: pip install flashrank") from e

    # Extract model name (default to MiniLM model)
    model = kwargs.get("model", _DEFAULT_FLASHRANK_MODEL)

    # Create reranker
    ranker = _get_flashrank_ranker(model)
    return FlashrankRerank(client=ranker, top_n=kwargs.get("top_n", 10))
