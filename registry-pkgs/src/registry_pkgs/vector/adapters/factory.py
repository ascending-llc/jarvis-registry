import importlib
import logging
from collections.abc import Callable

from ..config.config import BackendConfig
from ..enum.enums import EmbeddingProvider, VectorStoreType
from ..enum.exceptions import DependencyMissingError, UnsupportedBackendError
from .adapter import VectorStoreAdapter

logger = logging.getLogger(__name__)

# Registry for vector store creators
_VECTOR_STORE_CREATOR_REGISTRY: dict[str, Callable] = {}
_EMBEDDING_CREATOR_REGISTRY: dict[str, Callable] = {}


def register_vector_store_creator(name: str):
    """Decorator to register vector store creator function."""

    def decorator(creator_func: Callable):
        _VECTOR_STORE_CREATOR_REGISTRY[name] = creator_func
        return creator_func

    return decorator


def register_embedding_creator(name: str):
    """Decorator to register embedding creator function."""

    def decorator(creator_func: Callable):
        _EMBEDDING_CREATOR_REGISTRY[name] = creator_func
        return creator_func

    return decorator


def get_vector_store_creator(name: str) -> Callable:
    """Get vector store creator function."""
    if name not in _VECTOR_STORE_CREATOR_REGISTRY:
        available = list(_VECTOR_STORE_CREATOR_REGISTRY.keys())
        raise UnsupportedBackendError(name, available)
    return _VECTOR_STORE_CREATOR_REGISTRY[name]


def get_embedding_creator(name: str) -> Callable:
    """Get embedding creator function."""
    if name not in _EMBEDDING_CREATOR_REGISTRY:
        available = list(_EMBEDDING_CREATOR_REGISTRY.keys())
        raise UnsupportedBackendError(name, available)
    return _EMBEDDING_CREATOR_REGISTRY[name]


def get_registered_vector_stores() -> list:
    """Get list of registered vector store types."""
    return list(_VECTOR_STORE_CREATOR_REGISTRY.keys())


def get_registered_embeddings() -> list:
    """Get list of registered embedding providers."""
    return list(_EMBEDDING_CREATOR_REGISTRY.keys())


class VectorStoreFactory:
    """Factory class for creating vector store adapters using registry pattern."""

    @classmethod
    def create_adapter(cls, config: BackendConfig) -> VectorStoreAdapter:
        """Create vector store adapter.

        Args:
            config: BackendConfig instance

        Returns:
            VectorStoreAdapter instance

        Raises:
            UnsupportedBackendError: Unsupported database or embedding type
            DependencyMissingError: Required LangChain packages not installed
            ValueError: Invalid configuration
        """
        cls._validate_config(config)

        try:
            embedding = cls._create_embedding(config)
            creator = get_vector_store_creator(config.vector_store_type)
            return creator(config, embedding)
        except ImportError as e:
            cls._handle_import_error(config, e)

    @classmethod
    def _validate_config(cls, config: BackendConfig) -> None:
        """Validate configuration."""
        if config.vector_store_type not in get_registered_vector_stores():
            raise UnsupportedBackendError(config.vector_store_type, get_registered_vector_stores())

        if config.embedding_provider not in get_registered_embeddings():
            raise UnsupportedBackendError(config.embedding_provider, get_registered_embeddings())

        logger.info(f"Creating adapter: vector_store={config.vector_store_type}, embedding={config.embedding_provider}")

    @classmethod
    def _create_embedding(cls, config: BackendConfig):
        """Create embedding instance."""
        creator = get_embedding_creator(config.embedding_provider)
        return creator(config)

    @classmethod
    def _handle_import_error(cls, config: BackendConfig, error: ImportError) -> None:
        """Handle import errors with helpful installation guidance."""
        missing_packages = []

        # Check vector store packages
        try:
            if config.vector_store_type == VectorStoreType.WEAVIATE:
                importlib.import_module("langchain_weaviate")
        except ImportError:
            if config.vector_store_type == VectorStoreType.WEAVIATE:
                missing_packages.append("langchain_weaviate")

        # Check embedding packages
        try:
            if config.embedding_provider == EmbeddingProvider.OPENAI:
                importlib.import_module("langchain_openai")
            elif config.embedding_provider == EmbeddingProvider.AWS_BEDROCK:
                importlib.import_module("langchain_aws")
        except ImportError:
            if config.embedding_provider == EmbeddingProvider.OPENAI:
                missing_packages.append("langchain_openai")
            elif config.embedding_provider == EmbeddingProvider.AWS_BEDROCK:
                missing_packages.append("langchain_aws")

        if missing_packages:
            packages_str = ", ".join(missing_packages)
            install_cmd = "pip install " + " ".join(missing_packages)
            raise DependencyMissingError(
                packages_str, f"Required packages not installed: {packages_str}. Install with: {install_cmd}"
            )
        else:
            raise DependencyMissingError("unknown", str(error))

    @classmethod
    def get_supported_vector_stores(cls) -> list:
        """Get list of supported vector store types."""
        return get_registered_vector_stores()

    @classmethod
    def get_supported_embeddings(cls) -> list:
        """Get list of supported embedding providers."""
        return get_registered_embeddings()


# Convenience functions
def create_adapter(config: BackendConfig) -> VectorStoreAdapter:
    """Convenience function to create vector store adapter."""
    return VectorStoreFactory.create_adapter(config)


def get_supported_vector_stores() -> list:
    """Convenience function to get supported vector store types."""
    return VectorStoreFactory.get_supported_vector_stores()


def get_supported_embeddings() -> list:
    """Convenience function to get supported embedding providers."""
    return VectorStoreFactory.get_supported_embeddings()
