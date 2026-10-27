"""``WeaviateStore.iter_property_values`` uses the cursor iterator, so it enumerates every object's
property regardless of collection size — unlike offset pagination, which caps at QUERY_MAXIMUM_RESULTS.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from registry_pkgs.vector.backends.weaviate_store import WeaviateStore


def _store_with_collection(objects: list[object]) -> WeaviateStore:
    store = WeaviateStore(embedding=MagicMock(), config={"host": "localhost", "port": 8080})
    collection = MagicMock(name="collection")
    collection.iterator.return_value = iter(objects)
    client = MagicMock(name="client")
    client.collections.exists.return_value = True
    client.collections.get.return_value = collection
    store._get_client = MagicMock(return_value=client)
    store._client_mock = client
    store._collection = collection  # keep the mock reachable for assertions
    return store


def _obj(server_id):
    return SimpleNamespace(properties={"server_id": server_id})


def test_iterator_yields_all_distinct_ids_over_large_collection():
    # 25,000 objects, only 300 distinct server_ids.
    objects = [_obj(f"srv-{i % 300}") for i in range(25_000)]
    store = _store_with_collection(objects)

    values = set(store.iter_property_values("server_id", "MCPServer"))

    assert len(values) == 300
    assert values == {f"srv-{i}" for i in range(300)}
    # Cursor iterator, not vector fetch.
    store._collection.iterator.assert_called_once_with(include_vector=False, return_properties=["server_id"])


def test_iterator_skips_none_values():
    objects = [_obj("a"), _obj(None), _obj("b"), _obj(None)]
    store = _store_with_collection(objects)

    assert set(store.iter_property_values("server_id", "MCPServer")) == {"a", "b"}


def test_iterator_yields_nothing_for_a_missing_collection():
    """Collections are created lazily, so an MCP-only install has no A2A collection: nothing to sweep."""
    store = _store_with_collection([_obj("a")])
    store._client_mock.collections.exists.return_value = False

    assert list(store.iter_property_values("agent_id", "A2AAgent")) == []
    store._client_mock.collections.get.assert_not_called()


def test_iterator_propagates_backend_errors_from_the_existence_check():
    """A Weaviate outage fails the pass instead of looking like an empty collection."""
    store = _store_with_collection([_obj("a")])
    store._client_mock.collections.exists.side_effect = ConnectionError("weaviate down")

    with pytest.raises(ConnectionError):
        list(store.iter_property_values("server_id", "MCPServer"))
