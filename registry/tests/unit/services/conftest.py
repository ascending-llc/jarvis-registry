"""Fixtures shared by federation sync service test modules."""

import pytest

from tests.unit.services.federation_sync_test_helpers import (
    _FakeQuery,
    _make_federation_sync_service,
)


@pytest.fixture
def federation_sync_service():
    return _make_federation_sync_service()


@pytest.fixture
def default_empty_access_roles(monkeypatch):
    monkeypatch.setattr(
        "registry.services.federation_sync_service.RegistryAccessRole.find",
        lambda *args, **kwargs: _FakeQuery([]),
    )
