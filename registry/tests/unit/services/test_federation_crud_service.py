from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation_crud_service import FederationCrudService
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus
from registry_pkgs.models.federation import FederationLastSync, FederationStats


@pytest.fixture(autouse=True)
def federation_collection(monkeypatch):
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1, modified_count=1))
    monkeypatch.setattr(
        "registry.services.federation_crud_service.Federation.get_pymongo_collection", lambda: collection
    )
    return collection


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
        deletingJobId=None,
        stats=FederationStats(),
        deletedAt=None,
        save=AsyncMock(),
    )
    return federation


@pytest.mark.asyncio
async def test_update_federation_returns_persisted_updated_at(federation_collection):
    federation = _make_federation()
    federation.providerType = FederationProviderType.AWS_AGENTCORE
    federation.updatedAt = datetime(2020, 1, 1, tzinfo=UTC)

    result = await FederationCrudService().update_federation(
        federation=federation,
        display_name="Updated",
        description=None,
        tags=[],
        provider_config={"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/test"},
        updated_by="editor",
    )

    written_at = federation_collection.update_one.await_args.args[1]["$set"]["updatedAt"]
    assert result.updatedAt == written_at
    assert result.updatedAt > datetime(2020, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_mark_sync_pending_uses_state_machine():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SUCCESS)

    result = await service.mark_sync_pending(federation)

    assert result.syncStatus == FederationSyncStatus.PENDING
    federation.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_sync_pending_updates_last_sync_when_provided():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SUCCESS)
    last_sync = FederationLastSync(status=FederationSyncStatus.PENDING)

    result = await service.mark_sync_pending(federation, last_sync=last_sync)

    assert result.syncStatus == FederationSyncStatus.PENDING
    assert result.lastSync == last_sync
    federation.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_sync_pending_accepts_matching_noop_update(federation_collection):
    federation_collection.update_one.return_value = SimpleNamespace(matched_count=1, modified_count=0)
    federation = _make_federation(sync_status=FederationSyncStatus.SUCCESS)

    result = await FederationCrudService().mark_sync_pending(federation)

    assert result.syncStatus == FederationSyncStatus.PENDING


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

    result = await service.mark_syncing(federation, last_sync=last_sync, job_id=PydanticObjectId())

    assert result.syncStatus == FederationSyncStatus.SYNCING
    assert result.lastSync == last_sync
    federation.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_deleting_transitions_status_and_sync_status(federation_collection):
    service = FederationCrudService()
    federation = _make_federation(status=FederationStatus.ACTIVE, sync_status=FederationSyncStatus.IDLE)

    job_id = PydanticObjectId()
    result = await service.mark_deleting(federation, job_id=job_id)

    assert result.status == FederationStatus.DELETING
    assert result.syncStatus == FederationSyncStatus.PENDING
    assert result.deletingJobId == job_id
    query = federation_collection.update_one.await_args.args[0]
    assert query["syncStatus"] == {"$in": ["idle", "success", "failed"]}
    federation.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_delete_failed_restores_active_and_failed_sync_status(federation_collection):
    service = FederationCrudService()
    federation = _make_federation(status=FederationStatus.DELETING, sync_status=FederationSyncStatus.SYNCING)

    job_id = PydanticObjectId()
    result = await service.mark_delete_failed(federation, "delete failed", expected_job_id=job_id)

    assert result.status == FederationStatus.ACTIVE
    assert result.syncStatus == FederationSyncStatus.FAILED
    assert result.syncMessage == "delete failed"
    assert federation_collection.update_one.await_args.args[0]["deletingJobId"] == job_id
    federation.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_sync_success_updates_stats_and_last_sync():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SYNCING)
    now = FederationLastSync(status=FederationSyncStatus.SUCCESS, finishedAt=datetime.now(UTC))
    stats = FederationStats(mcpServerCount=1, agentCount=2, toolCount=3, importedTotal=3)

    result = await service.mark_sync_success(federation, last_sync=now, stats=stats, job_id=PydanticObjectId())

    assert result.syncStatus == FederationSyncStatus.SUCCESS
    assert result.lastSync == now
    assert result.stats == stats


@pytest.mark.asyncio
async def test_mark_sync_failed_updates_last_sync_and_stats_when_provided():
    service = FederationCrudService()
    federation = _make_federation(sync_status=FederationSyncStatus.SYNCING)
    last_sync = FederationLastSync(status=FederationSyncStatus.FAILED)
    stats = FederationStats(mcpServerCount=1, agentCount=2, toolCount=3, importedTotal=3)

    result = await service.mark_sync_failed(federation, "one resource failed", last_sync=last_sync, stats=stats)

    assert result.syncStatus == FederationSyncStatus.FAILED
    assert result.syncMessage == "one resource failed"
    assert result.lastSync == last_sync
    assert result.stats == stats


def test_normalize_provider_config_allows_empty_aws_config_for_create():
    service = FederationCrudService()

    result = service.normalize_provider_config(FederationProviderType.AWS_AGENTCORE, {})

    assert result == {"resourceTagsFilter": {}}


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
    }


def test_validate_provider_config_rejects_deprecated_federation_runtime_access():
    service = FederationCrudService()

    with pytest.raises(ValueError, match="providerConfig.runtimeAccess is no longer supported"):
        service.validate_provider_config(
            FederationProviderType.AWS_AGENTCORE,
            {
                "region": "us-east-1",
                "assumeRoleArn": "arn:aws:iam::123456789012:role/test-role",
                "runtimeAccess": {
                    "mode": "jwt",
                    "jwt": {
                        "discoveryUrl": "https://issuer.example/.well-known/openid-configuration",
                    },
                },
            },
        )


def test_normalize_provider_config_azure_encrypts_client_secret(monkeypatch):
    # Force a known encryption key so encrypt_value succeeds deterministically.
    from registry.core.config import settings as registry_settings

    monkeypatch.setattr(registry_settings, "encryption_key", b"0" * 32, raising=False)

    service = FederationCrudService()
    normalized = service.normalize_provider_config(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {
            "projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p",
            "tenantId": "tenant",
            "clientId": "client",
            "clientSecret": "plaintext-secret",
        },
    )

    assert normalized["clientSecret"] != "plaintext-secret"
    assert ":" in normalized["clientSecret"]  # hex(iv):hex(ciphertext)

    # Idempotent: a second pass must not re-encrypt an already encrypted value.
    second = service.normalize_provider_config(
        FederationProviderType.AZURE_AI_FOUNDRY,
        normalized,
    )
    assert second["clientSecret"] == normalized["clientSecret"]


def test_validate_provider_config_azure_requires_project_endpoint():
    service = FederationCrudService()

    with pytest.raises(ValueError, match="projectEndpoint"):
        service.validate_provider_config(
            FederationProviderType.AZURE_AI_FOUNDRY,
            {"tenantId": "t", "clientId": "c", "clientSecret": "s"},
        )


def test_validate_provider_config_azure_rejects_partial_sp_credentials():
    service = FederationCrudService()

    with pytest.raises(ValueError, match="all service principal fields"):
        service.validate_provider_config(
            FederationProviderType.AZURE_AI_FOUNDRY,
            {"projectEndpoint": "https://x", "tenantId": "t"},
        )


def test_validate_provider_config_azure_accepts_project_endpoint_only():
    service = FederationCrudService()
    normalized = service.validate_provider_config(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {"projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p"},
    )
    assert normalized["projectEndpoint"].startswith("https://")
    assert normalized.get("tenantId") is None


def test_validate_provider_config_azure_accepts_full_config(monkeypatch):
    from registry.core.config import settings as registry_settings

    monkeypatch.setattr(registry_settings, "encryption_key", b"0" * 32, raising=False)

    service = FederationCrudService()
    normalized = service.validate_provider_config(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {
            "projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p",
            "tenantId": "tenant",
            "clientId": "client",
            "clientSecret": "plaintext-secret",
        },
    )
    assert normalized["projectEndpoint"].startswith("https://")
    assert normalized["tenantId"] == "tenant"
