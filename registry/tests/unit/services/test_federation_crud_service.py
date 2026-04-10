from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation_crud_service import FederationCrudService
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus
from registry_pkgs.models.federation import FederationLastSync, FederationStats


def _make_federation(
    *,
    status: FederationStatus = FederationStatus.ACTIVE,
    sync_status: FederationSyncStatus = FederationSyncStatus.IDLE,
):
    federation = SimpleNamespace(
        id=PydanticObjectId(),
        status=status,
        syncStatus=sync_status,
        syncMessage=None,
        lastSync=None,
        stats=FederationStats(),
        deletedAt=None,
        save=AsyncMock(),
    )
    return federation


@pytest.mark.asyncio
async def test_mark_sync_pending_uses_state_machine():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SUCCESS)

    result = await service.mark_sync_pending(federation)

    assert result.syncStatus == FederationSyncStatus.PENDING
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_sync_pending_updates_last_sync_when_provided():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SUCCESS)
    last_sync = FederationLastSync(status=FederationSyncStatus.PENDING)

    result = await service.mark_sync_pending(federation, last_sync=last_sync)

    assert result.syncStatus == FederationSyncStatus.PENDING
    assert result.lastSync == last_sync
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_sync_pending_rejects_invalid_status_transition():
    service = FederationCrudService()
    federation = _make_federation(status=FederationStatus.DELETING, sync_status=FederationSyncStatus.SUCCESS)

    with pytest.raises(ValueError, match="cannot transition to sync pending"):
        await service.mark_sync_pending(federation)


@pytest.mark.asyncio
async def test_mark_syncing_updates_last_sync_when_provided():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.PENDING)
    last_sync = FederationLastSync(status=FederationSyncStatus.SYNCING)

    result = await service.mark_syncing(federation, last_sync=last_sync)

    assert result.syncStatus == FederationSyncStatus.SYNCING
    assert result.lastSync == last_sync
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_deleting_transitions_status_and_sync_status():
    service = FederationCrudService()
    federation = _make_federation(status=FederationStatus.ACTIVE, sync_status=FederationSyncStatus.IDLE)

    result = await service.mark_deleting(federation)

    assert result.status == FederationStatus.DELETING
    assert result.syncStatus == FederationSyncStatus.PENDING
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_deleted_requires_valid_transition():
    service = FederationCrudService()
    federation = _make_federation(status=FederationStatus.DELETED, sync_status=FederationSyncStatus.SUCCESS)

    with pytest.raises(ValueError, match="cannot transition to deleted"):
        await service.mark_deleted(federation)


@pytest.mark.asyncio
async def test_mark_delete_failed_restores_active_and_failed_sync_status():
    service = FederationCrudService()
    federation = _make_federation(status=FederationStatus.DELETING, sync_status=FederationSyncStatus.SYNCING)

    result = await service.mark_delete_failed(federation, "delete failed")

    assert result.status == FederationStatus.ACTIVE
    assert result.syncStatus == FederationSyncStatus.FAILED
    assert result.syncMessage == "delete failed"
    federation.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_sync_success_updates_stats_and_last_sync():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SYNCING)
    now = datetime.now(UTC)
    stats = FederationStats(mcpServerCount=1, agentCount=2, toolCount=3, importedTotal=3)

    result = await service.mark_sync_success(federation, last_sync=now, stats=stats)

    assert result.syncStatus == FederationSyncStatus.SUCCESS
    assert result.lastSync == now
    assert result.stats == stats


@pytest.mark.asyncio
async def test_mark_sync_failed_updates_last_sync_and_stats_when_provided():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SYNCING)
    last_sync = object()
    stats = FederationStats(mcpServerCount=1, agentCount=2, toolCount=3, importedTotal=3)

    result = await service.mark_sync_failed(federation, "one resource failed", last_sync=last_sync, stats=stats)

    assert result.syncStatus == FederationSyncStatus.FAILED
    assert result.syncMessage == "one resource failed"
    assert result.lastSync == last_sync
    assert result.stats == stats


def test_normalize_provider_config_allows_empty_aws_config_for_create():
    service = FederationCrudService()

    result = service.normalize_provider_config(FederationProviderType.AWS_AGENTCORE, {})

    assert result == {"resourceTagsFilter": {}, "runtimeAccess": {"mode": "iam", "iam": {}}}


def test_validate_provider_config_requires_region_and_assume_role_for_aws():
    service = FederationCrudService()

    with pytest.raises(ValueError, match="providerConfig.region"):
        service.validate_provider_config(FederationProviderType.AWS_AGENTCORE, {})

    result = service.validate_provider_config(
        FederationProviderType.AWS_AGENTCORE,
        {
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/test-role",
        },
    )

    assert result == {
        "region": "us-east-1",
        "assumeRoleArn": "arn:aws:iam::123456789012:role/test-role",
        "resourceTagsFilter": {},
        "runtimeAccess": {"mode": "iam", "iam": {}},
    }


def test_validate_provider_config_allows_optional_jwt_runtime_settings():
    service = FederationCrudService()

    result = service.validate_provider_config(
        FederationProviderType.AWS_AGENTCORE,
        {
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/test-role",
            "runtimeAccess": {
                "mode": "jwt",
                "jwt": {
                    "discoveryUrl": "https://issuer.example/.well-known/openid-configuration",
                    "audiences": ["jarvis-services", "agentcore-runtime"],
                    "allowedClients": ["jarvis-registry"],
                    "allowedScopes": ["sync:read", "tools:read"],
                    "customClaims": {"tenant": "prod"},
                },
            },
        },
    )

    assert result["runtimeAccess"]["mode"] == "jwt"
    assert result["runtimeAccess"]["jwt"]["audiences"] == ["jarvis-services", "agentcore-runtime"]
    assert result["runtimeAccess"]["jwt"]["allowedClients"] == ["jarvis-registry"]
    assert result["runtimeAccess"]["jwt"]["allowedScopes"] == ["sync:read", "tools:read"]
