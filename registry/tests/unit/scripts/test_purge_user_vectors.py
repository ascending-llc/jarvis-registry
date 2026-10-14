"""Purge vectors through the shared repository without contacting any service."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from weaviate.exceptions import WeaviateBaseError

from scripts import _purge_user_vectors as vectors


@pytest.fixture
def weaviate(monkeypatch):
    client = Mock()
    collection = client.collections.get.return_value
    client.collections.exists.return_value = True
    collection.config.get.return_value = SimpleNamespace(properties=[SimpleNamespace(name="server_id")])
    collection.data.delete_many.return_value = SimpleNamespace(successful=3, failed=0)
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[])
    connect = Mock(return_value=client)
    monkeypatch.setattr("weaviate.connect_to_local", connect)
    return SimpleNamespace(client=client, collection=collection, connect=connect)


@pytest.mark.parametrize("generation", [None, "aaaaaaaaaaaaaaaaaaaaaaaa"])
async def test_deletes_from_selected_generation_without_embeddings_or_schema_changes(weaviate, generation):
    expected = "MCP_Servers" if generation is None else f"MCP_Servers_{generation}"
    async with vectors.open_vector_repository(generation) as repo:
        weaviate.connect.assert_not_called()
        assert repo.adapter.embedding is None
        assert await repo.delete_by_server_id("server-id", "server-name") == 3
        assert repo.collection == expected

    assert {call.args[0] for call in weaviate.client.collections.get.call_args_list} == {expected}
    weaviate.collection.data.delete_many.assert_called_once()
    deleted_filter = weaviate.collection.data.delete_many.call_args.kwargs["where"]
    weaviate.collection.query.fetch_objects.assert_called_once_with(filters=deleted_filter, limit=1)
    weaviate.client.collections.create.assert_not_called()
    weaviate.collection.config.add_property.assert_not_called()
    weaviate.client.close.assert_called_once()


async def test_confirmed_absent_collection_is_noop(weaviate):
    weaviate.client.collections.exists.return_value = False
    async with vectors.open_vector_repository(None) as repo:
        assert await repo.delete_by_server_id("server-id") == 0
    weaviate.client.collections.get.assert_not_called()
    weaviate.client.collections.create.assert_not_called()
    weaviate.collection.data.delete_many.assert_not_called()


@pytest.mark.parametrize("phase", ["exists", "schema", "delete", "verify"])
async def test_remote_errors_propagate_and_close_connection(weaviate, phase):
    operations = {
        "exists": weaviate.client.collections.exists,
        "schema": weaviate.collection.config.get,
        "delete": weaviate.collection.data.delete_many,
        "verify": weaviate.collection.query.fetch_objects,
    }
    operations[phase].side_effect = OSError("remote operation failed")
    with pytest.raises(vectors.VectorCleanupError) as error:
        async with vectors.open_vector_repository(None) as repo:
            await repo.delete_by_server_id("server-id")
    assert isinstance(error.value.__cause__, OSError)
    if phase in {"exists", "schema"}:
        weaviate.collection.data.delete_many.assert_not_called()
    weaviate.client.close.assert_called_once()


async def test_missing_server_id_schema_blocks_deletion(weaviate):
    weaviate.collection.config.get.return_value = SimpleNamespace(properties=[SimpleNamespace(name="content")])
    with pytest.raises(vectors.VectorCleanupError, match="no server_id property"):
        async with vectors.open_vector_repository(None) as repo:
            await repo.delete_by_server_id("server-id")
    weaviate.collection.data.delete_many.assert_not_called()
    weaviate.collection.config.add_property.assert_not_called()


@pytest.mark.parametrize("failed,remaining", [(1, []), (0, [object()])])
async def test_partial_or_incomplete_delete_raises(weaviate, failed, remaining):
    weaviate.collection.data.delete_many.return_value = SimpleNamespace(successful=3, failed=failed)
    weaviate.collection.query.fetch_objects.return_value = SimpleNamespace(objects=remaining)
    with pytest.raises(vectors.VectorCleanupError):
        async with vectors.open_vector_repository(None) as repo:
            await repo.delete_by_server_id("server-id")
    weaviate.client.close.assert_called_once()


async def test_sync_network_calls_run_off_event_loop(weaviate):
    main_thread = threading.get_ident()
    calls = []

    def capture(result):
        def operation(*args, **kwargs):
            calls.append(threading.get_ident())
            return result

        return operation

    weaviate.connect.side_effect = capture(weaviate.client)
    weaviate.client.collections.exists.side_effect = capture(True)
    weaviate.collection.config.get.side_effect = capture(SimpleNamespace(properties=[{"name": "server_id"}]))
    weaviate.collection.data.delete_many.side_effect = capture(SimpleNamespace(successful=1, failed=0))
    weaviate.collection.query.fetch_objects.side_effect = capture(SimpleNamespace(objects=[]))
    weaviate.client.close.side_effect = capture(None)
    async with vectors.open_vector_repository(None) as repo:
        assert await repo.delete_by_server_id("server-id") == 1
    assert calls
    assert all(thread_id != main_thread for thread_id in calls)


def test_invalid_delete_filter_never_reaches_weaviate(weaviate):
    adapter = vectors.StrictWeaviateStore(vectors.VectorSettings())
    with pytest.raises(ValueError, match="without a valid filter"):
        adapter.delete_by_filter({})
    weaviate.connect.assert_not_called()


async def test_connection_settings_are_loaded_without_application_secrets(weaviate, monkeypatch):
    monkeypatch.setenv("WEAVIATE_HOST", "vector-host")
    monkeypatch.setenv("WEAVIATE_PORT", "8081")
    monkeypatch.setenv("WEAVIATE_GRPC_PORT", "50052")
    monkeypatch.setenv("WEAVIATE_API_KEY", "fixture-vector-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    async with vectors.open_vector_repository(None) as repo:
        await repo.delete_by_server_id("server-id")
    assert weaviate.connect.call_args.kwargs["host"] == "vector-host"
    assert weaviate.connect.call_args.kwargs["port"] == 8081
    assert weaviate.connect.call_args.kwargs["grpc_port"] == 50052
    assert weaviate.connect.call_args.kwargs["auth_credentials"] is not None


@pytest.mark.parametrize("phase", ["connect", "delete", "verify", "close"])
async def test_sdk_errors_keep_secret_in_cause_without_logging_it(weaviate, phase, caplog, capsys):
    secret = "credential-in-sdk-error-payload"
    operations = {
        "connect": weaviate.connect,
        "delete": weaviate.collection.data.delete_many,
        "verify": weaviate.collection.query.fetch_objects,
        "close": weaviate.client.close,
    }
    operations[phase].side_effect = WeaviateBaseError(secret)
    with pytest.raises(vectors.VectorCleanupError) as error:
        async with vectors.open_vector_repository(None) as repo:
            await repo.delete_by_server_id("server-id")
    assert secret not in str(error.value)
    assert secret in str(error.value.__cause__)
    assert secret not in caplog.text
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


async def test_invalid_settings_report_safe_error_before_connection(weaviate, monkeypatch):
    secret = "credential-in-malformed-port"
    monkeypatch.setenv("WEAVIATE_PORT", secret)
    with pytest.raises(vectors.VectorCleanupError, match="Invalid Weaviate connection settings") as error:
        async with vectors.open_vector_repository(None):
            pytest.fail("Invalid settings must prevent context entry")
    assert secret not in str(error.value)
    assert secret in str(error.value.__cause__)
    weaviate.connect.assert_not_called()
