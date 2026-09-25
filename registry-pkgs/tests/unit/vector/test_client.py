import pytest

from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.vector.client import DatabaseClient, collection_name_for


def _initialized_client() -> DatabaseClient:
    client = DatabaseClient()
    client._adapter = object()  # stand-in adapter
    client._initialized = True
    return client


# --- reads (adapter) are never gated; only writes (write_adapter) are ---


def test_reads_never_blocked_during_reindex() -> None:
    client = _initialized_client()
    sentinel = object()
    client._adapter = sentinel
    client.set_reindex_active_check(lambda: True)

    # A reindex is active, but reads must still resolve — search is never blocked.
    assert client.adapter is sentinel


def test_write_adapter_raises_when_reindex_active() -> None:
    client = _initialized_client()
    client.set_reindex_active_check(lambda: True)

    with pytest.raises(EmbeddingReindexInProgressException):
        _ = client.write_adapter


def test_write_adapter_available_when_reindex_inactive() -> None:
    client = _initialized_client()
    sentinel = object()
    client._adapter = sentinel
    client.set_reindex_active_check(lambda: False)

    assert client.write_adapter is sentinel


def test_write_adapter_available_when_gate_unwired() -> None:
    client = _initialized_client()
    sentinel = object()
    client._adapter = sentinel

    assert client.write_adapter is sentinel


def test_uninitialized_raises_runtime_error_for_both() -> None:
    client = DatabaseClient()
    client.set_reindex_active_check(lambda: True)

    # The not-initialized guard runs first on both accessors.
    with pytest.raises(RuntimeError):
        _ = client.adapter
    with pytest.raises(RuntimeError):
        _ = client.write_adapter


# --- collection generations ---


def test_collection_name_for_function() -> None:
    assert collection_name_for("MCP_Servers", None) == "MCP_Servers"
    assert collection_name_for("MCP_Servers", "65f0") == "MCP_Servers_65f0"


def test_client_collection_name_for_reads_config_generation() -> None:
    from registry_pkgs.vector.config.config import BackendConfig

    client = _initialized_client()
    client._config = BackendConfig.model_construct(collection_generation=None)
    assert client.collection_name_for("A2a_agents") == "A2a_agents"
    # A swap onto a config with a generation changes the resolved name immediately.
    client._config = BackendConfig.model_construct(collection_generation="gen1")
    assert client.collection_name_for("A2a_agents") == "A2a_agents_gen1"


def test_swap_adapter_replaces_adapter_and_returns_old() -> None:
    client = _initialized_client()
    old_adapter = object()
    client._adapter = old_adapter
    new_adapter = object()
    new_config = object()

    returned = client.swap_adapter(new_adapter, new_config)

    assert returned is old_adapter
    # The gate is inactive here, so .adapter resolves to the new one for every repository.
    assert client.adapter is new_adapter
    assert client._config is new_config
