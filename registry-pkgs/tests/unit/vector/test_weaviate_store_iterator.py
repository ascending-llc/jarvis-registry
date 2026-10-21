"""``WeaviateStore.iter_property_values`` uses the cursor iterator, so it enumerates every object's
property regardless of collection size — unlike offset pagination, which caps at QUERY_MAXIMUM_RESULTS.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from registry_pkgs.vector.backends.weaviate_store import WeaviateStore


def _store_with_collection(objects: list[object]) -> WeaviateStore:
    store = WeaviateStore(embedding=MagicMock(), config={"host": "localhost", "port": 8080})
    collection = MagicMock(name="collection")
    collection.iterator.return_value = iter(objects)
    store.get_collection = MagicMock(return_value=collection)
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
