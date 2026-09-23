import pytest

from registry_pkgs.core.exceptions import EmbeddingReindexInProgressException
from registry_pkgs.vector.client import DatabaseClient


def _initialized_client() -> DatabaseClient:
    client = DatabaseClient()
    client._adapter = object()  # stand-in adapter
    client._initialized = True
    return client


def test_adapter_raises_when_reindex_active() -> None:
    client = _initialized_client()
    client.set_reindex_active_check(lambda: True)

    with pytest.raises(EmbeddingReindexInProgressException):
        _ = client.adapter


def test_adapter_available_when_reindex_inactive() -> None:
    client = _initialized_client()
    sentinel = object()
    client._adapter = sentinel
    client.set_reindex_active_check(lambda: False)

    assert client.adapter is sentinel


def test_adapter_available_when_gate_unwired() -> None:
    client = _initialized_client()
    sentinel = object()
    client._adapter = sentinel

    # No reindex check wired at all -> behaves exactly as before this change.
    assert client.adapter is sentinel


def test_uninitialized_still_raises_runtime_error() -> None:
    client = DatabaseClient()
    client.set_reindex_active_check(lambda: True)

    # The not-initialized guard runs first, so callers still see the original error.
    with pytest.raises(RuntimeError):
        _ = client.adapter
