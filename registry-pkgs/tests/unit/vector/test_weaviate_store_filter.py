from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from langchain_core.documents import Document
from weaviate.classes.query import Filter

from registry_pkgs.vector.backends.weaviate_store import WeaviateStore


@pytest.fixture
def store_and_collection() -> tuple[WeaviateStore, MagicMock]:
    store = WeaviateStore(embedding=MagicMock(), config={})
    collection = MagicMock()
    store.get_collection = MagicMock(return_value=collection)
    return store, collection


@pytest.mark.parametrize("raise_on_error", [False, True])
def test_filter_returns_documents_and_forwards_only_query_kwargs(
    store_and_collection: tuple[WeaviateStore, MagicMock], raise_on_error: bool
) -> None:
    store, collection = store_and_collection
    doc_id = UUID("c23913e4-e9fe-4304-9d8e-bac291d0ea30")
    properties = {"content": "Searchable tool", "server_id": "server-1"}
    collection.query.fetch_objects.return_value = SimpleNamespace(
        objects=[SimpleNamespace(uuid=doc_id, properties=properties)]
    )
    filters = Filter.by_property("server_id").equal("server-1")

    result = store.filter_by_metadata(
        filters,
        limit=42,
        collection_name="MCPServer",
        raise_on_error=raise_on_error,
        include_vector=True,
    )

    assert result == [Document(id=str(doc_id), page_content="Searchable tool", metadata=properties)]
    store.get_collection.assert_called_once_with("MCPServer")
    collection.query.fetch_objects.assert_called_once_with(filters=filters, limit=42, include_vector=True)


def test_filter_returns_empty_list_on_query_error_by_default(
    store_and_collection: tuple[WeaviateStore, MagicMock], caplog: pytest.LogCaptureFixture
) -> None:
    store, collection = store_and_collection
    collection.query.fetch_objects.side_effect = RuntimeError("query unavailable")

    assert store.filter_by_metadata(None) == []
    assert "Filter by metadata failed: query unavailable" in caplog.text


def test_filter_reraises_query_error_in_strict_mode(
    store_and_collection: tuple[WeaviateStore, MagicMock], caplog: pytest.LogCaptureFixture
) -> None:
    store, collection = store_and_collection
    error = RuntimeError("query unavailable")
    collection.query.fetch_objects.side_effect = error

    with pytest.raises(RuntimeError, match="query unavailable") as raised:
        store.filter_by_metadata(None, raise_on_error=True)

    assert raised.value is error
    assert "Filter by metadata failed: query unavailable" in caplog.text


@pytest.mark.parametrize("method", ["get_collection", "normalize_filters"])
def test_filter_preserves_setup_error_propagation(
    store_and_collection: tuple[WeaviateStore, MagicMock], method: str
) -> None:
    store, collection = store_and_collection
    setattr(store, method, MagicMock(side_effect=ValueError("invalid configuration")))

    with pytest.raises(ValueError, match="invalid configuration"):
        store.filter_by_metadata(None)

    collection.query.fetch_objects.assert_not_called()
