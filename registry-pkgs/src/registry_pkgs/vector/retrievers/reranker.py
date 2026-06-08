import logging
from functools import lru_cache
from threading import Lock
from typing import Any

from langchain_classic.retrievers.document_compressors.base import BaseDocumentCompressor

logger = logging.getLogger(__name__)

_DEFAULT_FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"
_FLASHRANK_LOAD_LOCK = Lock()
_FLASHRANK_IMPORT_ERROR = "FlashRank is required for reranking. Install with: uv sync"


def _flashrank_import_error() -> ImportError:
    """Log and build a consistent ImportError for any missing FlashRank import."""
    logger.error(_FLASHRANK_IMPORT_ERROR)
    return ImportError(_FLASHRANK_IMPORT_ERROR)


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


def _get_flashrank_ranker(model: str) -> Any:
    """Load and cache the underlying FlashRank Ranker."""
    with _FLASHRANK_LOAD_LOCK:
        return _load_flashrank_ranker(model)


@lru_cache(maxsize=4)
def _load_flashrank_ranker(model: str) -> Any:
    """Create the underlying FlashRank Ranker."""
    try:
        from flashrank import Ranker
    except ImportError as e:
        raise _flashrank_import_error() from e

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
        raise _flashrank_import_error() from e

    # Extract model name (default to MiniLM model)
    model = kwargs.get("model", _DEFAULT_FLASHRANK_MODEL)

    # Create reranker
    ranker = _get_flashrank_ranker(model)
    return FlashrankRerank(client=ranker, top_n=kwargs.get("top_n", 10))
