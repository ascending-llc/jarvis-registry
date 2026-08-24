from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_source_crud_service import SkillSyncSourceCrudService
from registry_pkgs.models.enums import SkillSyncProviderType, SkillSyncSourceStatus, SkillSyncStatus
from registry_pkgs.models.skill_sync_source import SkillSyncSourceStats

MODULE = "registry.services.skill_sync_source_crud_service"


def _make_source(
    *,
    status: SkillSyncSourceStatus = SkillSyncSourceStatus.ACTIVE,
    sync_status: SkillSyncStatus = SkillSyncStatus.IDLE,
    deleted_at=None,
):
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerType=SkillSyncProviderType.GITHUB,
        displayName="Demo",
        description=None,
        tags=[],
        owner="acme",
        repo="skills",
        ref="main",
        paths=[],
        configRevision=1,
        githubAppClientId="client-id",
        githubAppClientSecretEncrypted="encrypted-secret",
        status=status,
        syncStatus=sync_status,
        syncMessage=None,
        stats=SkillSyncSourceStats(),
        updatedBy=None,
        deletedAt=deleted_at,
        save=AsyncMock(),
    )


class _FakeFinder:
    def __init__(self, items):
        self._items = items

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self):
        return self._items

    async def count(self):
        return len(self._items)


@pytest.fixture(autouse=True)
def crypto_stub(monkeypatch):
    monkeypatch.setattr(f"{MODULE}.is_encrypted", lambda value: value.startswith("encrypted:"))
    monkeypatch.setattr(f"{MODULE}.encrypt_value", lambda value: f"encrypted:{value}")


class _StubSource:
    """Stand-in for the Beanie SkillSyncSource document (avoids needing DB init in unit tests)."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.insert = AsyncMock()


@pytest.mark.asyncio
async def test_create_source_encrypts_secret_and_inserts(monkeypatch):
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource", _StubSource)

    service = SkillSyncSourceCrudService()
    source = await service.create_source(
        display_name="Demo",
        description="desc",
        tags=["a"],
        owner="acme",
        repo="skills",
        ref="main",
        paths=["skills/"],
        github_app_client_id="client-id",
        github_app_client_secret="plaintext-secret",
        created_by="user-1",
    )

    assert source.githubAppClientSecretEncrypted == "encrypted:plaintext-secret"
    assert source.status == SkillSyncSourceStatus.ACTIVE
    assert source.syncStatus == SkillSyncStatus.IDLE
    assert source.createdBy == "user-1"
    assert source.updatedBy == "user-1"
    source.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_source_does_not_reencrypt_already_encrypted_secret(monkeypatch):
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource", _StubSource)

    service = SkillSyncSourceCrudService()
    source = await service.create_source(
        display_name="Demo",
        description=None,
        tags=[],
        owner="acme",
        repo="skills",
        ref="main",
        paths=[],
        github_app_client_id="client-id",
        github_app_client_secret="encrypted:already",
        created_by=None,
    )

    assert source.githubAppClientSecretEncrypted == "encrypted:already"


@pytest.mark.asyncio
async def test_get_source_returns_none_for_invalid_id():
    service = SkillSyncSourceCrudService()

    result = await service.get_source("not-an-object-id")

    assert result is None


@pytest.mark.asyncio
async def test_get_source_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.get", AsyncMock(return_value=None))

    service = SkillSyncSourceCrudService()
    result = await service.get_source(str(PydanticObjectId()))

    assert result is None


@pytest.mark.asyncio
async def test_get_source_returns_none_when_status_deleted(monkeypatch):
    source = _make_source(status=SkillSyncSourceStatus.DELETED)
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.get", AsyncMock(return_value=source))

    service = SkillSyncSourceCrudService()
    result = await service.get_source(str(source.id))

    assert result is None


@pytest.mark.asyncio
async def test_get_source_returns_none_when_deleted_at_set(monkeypatch):
    from datetime import UTC, datetime

    source = _make_source(deleted_at=datetime.now(UTC))
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.get", AsyncMock(return_value=source))

    service = SkillSyncSourceCrudService()
    result = await service.get_source(str(source.id))

    assert result is None


@pytest.mark.asyncio
async def test_get_source_returns_source_when_active(monkeypatch):
    source = _make_source()
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.get", AsyncMock(return_value=source))

    service = SkillSyncSourceCrudService()
    result = await service.get_source(str(source.id))

    assert result is source


@pytest.mark.asyncio
async def test_list_sources_returns_items_and_count(monkeypatch):
    items = [_make_source(), _make_source()]
    finder = _FakeFinder(items)
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.find", lambda *_a, **_kw: finder)

    service = SkillSyncSourceCrudService()
    result_items, total = await service.list_sources()

    assert result_items == items
    assert total == 2


@pytest.mark.asyncio
async def test_list_sources_filters_by_sync_status_tag_and_keyword(monkeypatch):
    captured = {}

    def _find(query):
        captured["query"] = query
        return _FakeFinder([])

    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.find", _find)

    service = SkillSyncSourceCrudService()
    await service.list_sources(sync_status="failed", tag="prod", keyword="deploy")

    filters = captured["query"]["$and"]
    assert {"syncStatus": "failed"} in filters
    assert {"tags": "prod"} in filters
    assert {"$text": {"$search": "deploy"}} in filters


@pytest.mark.asyncio
async def test_list_sources_returns_empty_for_empty_accessible_ids(monkeypatch):
    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.find", lambda *_a, **_kw: _FakeFinder([]))

    service = SkillSyncSourceCrudService()
    items, total = await service.list_sources(accessible_source_ids=[])

    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_sources_filters_by_accessible_source_ids(monkeypatch):
    captured = {}
    source_id = str(PydanticObjectId())

    def _find(query):
        captured["query"] = query
        return _FakeFinder([])

    monkeypatch.setattr(f"{MODULE}.SkillSyncSource.find", _find)

    service = SkillSyncSourceCrudService()
    await service.list_sources(accessible_source_ids=[source_id])

    filters = captured["query"]["$and"]
    assert any("_id" in flt for flt in filters)


@pytest.mark.asyncio
async def test_update_source_maps_fields_and_saves():
    source = _make_source()
    service = SkillSyncSourceCrudService()

    result = await service.update_source(
        source,
        {"displayName": "New Name", "tags": ["x", "y"]},
        updated_by="user-2",
    )

    assert result.displayName == "New Name"
    assert result.tags == ["x", "y"]
    assert result.updatedBy == "user-2"
    assert result.configRevision == 1
    source.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_source_encrypts_changed_secret():
    source = _make_source()
    service = SkillSyncSourceCrudService()

    result = await service.update_source(
        source,
        {"githubAppClientSecret": "new-plaintext"},
        updated_by=None,
    )

    assert result.githubAppClientSecretEncrypted == "encrypted:new-plaintext"
    assert result.configRevision == 2


@pytest.mark.asyncio
async def test_update_source_increments_config_revision_for_repository_change():
    source = _make_source()
    service = SkillSyncSourceCrudService()

    result = await service.update_source(source, {"ref": "release"}, updated_by=None)

    assert result.configRevision == 2


@pytest.mark.asyncio
async def test_update_source_rejects_non_active_status():
    source = _make_source(status=SkillSyncSourceStatus.DELETING)
    service = SkillSyncSourceCrudService()

    with pytest.raises(ValueError, match="cannot be updated"):
        await service.update_source(source, {"displayName": "x"}, updated_by=None)


@pytest.mark.asyncio
async def test_mark_sync_pending_transitions_and_clears_message():
    source = _make_source(sync_status=SkillSyncStatus.FAILED)
    source.syncMessage = "old error"
    service = SkillSyncSourceCrudService()

    result = await service.mark_sync_pending(source)

    assert result.syncStatus == SkillSyncStatus.PENDING
    assert result.syncMessage is None
    source.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_sync_pending_rejects_when_source_not_active():
    source = _make_source(status=SkillSyncSourceStatus.DELETING)
    service = SkillSyncSourceCrudService()

    with pytest.raises(ValueError, match="cannot start a sync"):
        await service.mark_sync_pending(source)


@pytest.mark.asyncio
async def test_mark_sync_failed_sets_status_and_message():
    source = _make_source(sync_status=SkillSyncStatus.SYNCING)
    service = SkillSyncSourceCrudService()

    result = await service.mark_sync_failed(source, "boom")

    assert result.syncStatus == SkillSyncStatus.FAILED
    assert result.syncMessage == "boom"
    source.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_deleting_transitions_status_and_sync_status():
    source = _make_source(status=SkillSyncSourceStatus.ACTIVE, sync_status=SkillSyncStatus.IDLE)
    service = SkillSyncSourceCrudService()

    result = await service.mark_deleting(source)

    assert result.status == SkillSyncSourceStatus.DELETING
    assert result.syncStatus == SkillSyncStatus.PENDING
    assert result.syncMessage is None
    source.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_deleting_rejects_when_already_deleting():
    source = _make_source(status=SkillSyncSourceStatus.DELETING)
    service = SkillSyncSourceCrudService()

    with pytest.raises(ValueError, match="cannot start a sync"):
        await service.mark_deleting(source)


@pytest.mark.asyncio
async def test_restore_after_delete_failure_sets_active_and_failed():
    source = _make_source(status=SkillSyncSourceStatus.DELETING, sync_status=SkillSyncStatus.SYNCING)
    service = SkillSyncSourceCrudService()

    result = await service.restore_after_delete_failure(source, "delete failed")

    assert result.status == SkillSyncSourceStatus.ACTIVE
    assert result.syncStatus == SkillSyncStatus.FAILED
    assert result.syncMessage == "delete failed"
    source.save.assert_awaited_once()
