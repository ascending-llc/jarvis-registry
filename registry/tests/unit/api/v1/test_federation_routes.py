from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.api.v1.federation.federation_routes import (
    create_federation,
    delete_federation,
    list_federations,
    sync_federation,
    update_federation,
)
from registry.schemas.federation_api_schemas import (
    FederationCreateRequest,
    FederationSyncRequest,
    FederationUpdateRequest,
)
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobStatus,
    FederationJobType,
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
)


@pytest.fixture
def sample_user_context():
    return {
        "user_id": "test-user-id",
        "username": "testuser",
    }


@pytest.fixture
def sample_federation():
    now = datetime.now(UTC)
    federation_id = PydanticObjectId()
    return SimpleNamespace(
        id=federation_id,
        providerType=FederationProviderType.AWS_AGENTCORE,
        displayName="AWS AgentCore Prod",
        description="Production federation",
        tags=["prod"],
        status=FederationStatus.ACTIVE,
        syncStatus=FederationSyncStatus.IDLE,
        syncMessage=None,
        providerConfig={"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
        stats={"mcpServerCount": 0, "agentCount": 0, "toolCount": 0, "importedTotal": 0},
        lastSync=None,
        version=1,
        createdBy="test-user-id",
        updatedBy="test-user-id",
        createdAt=now,
        updatedAt=now,
    )


@pytest.fixture
def sample_job(sample_federation):
    return SimpleNamespace(
        id=PydanticObjectId(),
        federationId=sample_federation.id,
        jobType=FederationJobType.INITIAL_SYNC,
        status=FederationJobStatus.SUCCESS,
        phase=FederationJobPhase.COMPLETED,
        startedAt=datetime.now(UTC),
        finishedAt=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_federation_does_not_trigger_sync(sample_user_context, sample_federation, sample_job):
    federation_crud_service = MagicMock()
    federation_crud_service.create_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[])

    result = await create_federation(
        data=FederationCreateRequest(
            providerType=FederationProviderType.AWS_AGENTCORE,
            displayName="AWS AgentCore Prod",
            description="Production federation",
            tags=["prod"],
            providerConfig={"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
    )

    assert result.id == str(sample_federation.id)
    assert len(result.recentJobs) == 0


@pytest.mark.asyncio
async def test_create_federation_allows_empty_aws_provider_config(sample_user_context, sample_federation):
    created_federation = SimpleNamespace(**{**sample_federation.__dict__, "providerConfig": {"resourceTagsFilter": {}}})
    federation_crud_service = MagicMock()
    federation_crud_service.create_federation = AsyncMock(return_value=created_federation)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[])

    result = await create_federation(
        data=FederationCreateRequest(
            providerType=FederationProviderType.AWS_AGENTCORE,
            displayName="AWS AgentCore Prod",
            description="Production federation",
            tags=["prod"],
            providerConfig={},
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
    )

    assert result.providerConfig == {"resourceTagsFilter": {}}


@pytest.mark.asyncio
async def test_update_federation_runs_resync_for_provider_changes(sample_user_context, sample_federation, sample_job):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)

    updated_federation = SimpleNamespace(
        **{**sample_federation.__dict__, "providerConfig": {"region": "us-west-2"}, "version": 2}
    )
    federation_crud_service.update_federation = AsyncMock(return_value=updated_federation)
    federation_crud_service.mark_sync_pending = AsyncMock(return_value=updated_federation)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[sample_job])

    federation_job_service = MagicMock()
    federation_job_service.get_active_job = AsyncMock(return_value=None)
    federation_job_service.create_job = AsyncMock(return_value=sample_job)

    federation_sync_service = MagicMock()
    federation_sync_service.run_sync = AsyncMock(return_value=sample_job)

    result = await update_federation(
        federation_id=str(sample_federation.id),
        data=FederationUpdateRequest(
            displayName="AWS AgentCore Prod",
            description="Updated federation",
            tags=["prod"],
            providerConfig={"region": "us-west-2"},
            version=1,
            syncAfterUpdate=True,
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_job_service=federation_job_service,
        federation_sync_service=federation_sync_service,
    )

    federation_job_service.get_active_job.assert_awaited_once_with(sample_federation.id)
    federation_sync_service.run_sync.assert_awaited_once_with(
        federation=updated_federation,
        job=sample_job,
        user_id=sample_user_context["user_id"],
    )
    assert result.version == 2


@pytest.mark.asyncio
async def test_update_federation_requires_aws_region_and_assume_role(sample_user_context, sample_federation):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.update_federation = AsyncMock(
        side_effect=ValueError("AWS AgentCore federation requires providerConfig.region, providerConfig.assumeRoleArn")
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_federation(
            federation_id=str(sample_federation.id),
            data=FederationUpdateRequest(
                displayName="AWS AgentCore Prod",
                description="Updated federation",
                tags=["prod"],
                providerConfig={},
                version=1,
                syncAfterUpdate=False,
            ),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=MagicMock(),
            federation_sync_service=MagicMock(),
        )

    assert exc_info.value.status_code == 400
    assert "providerConfig.region" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_update_federation_returns_501_for_unimplemented_provider(
    sample_user_context, sample_federation, sample_job
):
    azure_federation = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerType": FederationProviderType.AZURE_AI_FOUNDRY,
            "providerConfig": {"subscriptionId": "sub-a"},
        }
    )

    updated_federation = SimpleNamespace(
        **{
            **azure_federation.__dict__,
            "providerConfig": {"subscriptionId": "sub-b"},
            "version": 2,
        }
    )

    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=azure_federation)
    federation_crud_service.update_federation = AsyncMock(return_value=updated_federation)
    federation_crud_service.mark_sync_pending = AsyncMock(return_value=updated_federation)

    federation_job_service = MagicMock()
    federation_job_service.get_active_job = AsyncMock(return_value=None)
    federation_job_service.create_job = AsyncMock(return_value=sample_job)

    federation_sync_service = MagicMock()
    federation_sync_service.run_sync = AsyncMock(
        side_effect=ValueError("Federation provider azure_ai_foundry is not implemented yet.")
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_federation(
            federation_id=str(azure_federation.id),
            data=FederationUpdateRequest(
                displayName="Azure Federation",
                description="Updated federation",
                tags=["prod"],
                providerConfig={"subscriptionId": "sub-b"},
                version=1,
                syncAfterUpdate=True,
            ),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=federation_job_service,
            federation_sync_service=federation_sync_service,
        )

    assert exc_info.value.status_code == 501
    assert "not implemented yet" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_delete_federation_returns_deleted_status(sample_user_context, sample_federation, sample_job):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.mark_deleting = AsyncMock(return_value=sample_federation)

    federation_job_service = MagicMock()
    federation_job_service.get_active_job = AsyncMock(return_value=None)
    federation_job_service.create_job = AsyncMock(return_value=sample_job)

    federation_sync_service = MagicMock()
    federation_sync_service.run_delete = AsyncMock(return_value=sample_job)

    result = await delete_federation(
        federation_id=str(sample_federation.id),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_job_service=federation_job_service,
        federation_sync_service=federation_sync_service,
    )

    federation_sync_service.run_delete.assert_awaited_once_with(federation=sample_federation, job=sample_job)
    assert result.federationId == str(sample_federation.id)
    assert result.status == "deleted"


@pytest.mark.asyncio
async def test_update_federation_rejects_deleting_status(sample_user_context, sample_federation):
    deleting_federation = SimpleNamespace(**{**sample_federation.__dict__, "status": FederationStatus.DELETING})
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=deleting_federation)

    with pytest.raises(HTTPException) as exc_info:
        await update_federation(
            federation_id=str(deleting_federation.id),
            data=FederationUpdateRequest(
                displayName="AWS AgentCore Prod",
                description="Updated federation",
                tags=["prod"],
                providerConfig={"region": "us-west-2"},
                version=1,
                syncAfterUpdate=True,
            ),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=MagicMock(),
            federation_sync_service=MagicMock(),
        )

    assert exc_info.value.status_code == 409
    assert "cannot be updated" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_rejects_running_sync_status(sample_user_context, sample_federation):
    syncing_federation = SimpleNamespace(**{**sample_federation.__dict__, "syncStatus": FederationSyncStatus.SYNCING})
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=syncing_federation)

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(syncing_federation.id),
            data=FederationSyncRequest(force=False, reason="manual"),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=MagicMock(),
            federation_sync_service=MagicMock(),
        )

    assert exc_info.value.status_code == 409
    assert "cannot start a new sync" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_requires_aws_region_and_assume_role(sample_user_context, sample_federation):
    federation_missing_config = SimpleNamespace(
        **{**sample_federation.__dict__, "providerConfig": {"resourceTagsFilter": {}}}
    )
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=federation_missing_config)
    federation_crud_service.validate_provider_config = MagicMock(
        side_effect=ValueError("AWS AgentCore federation requires providerConfig.region, providerConfig.assumeRoleArn")
    )

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(federation_missing_config.id),
            data=FederationSyncRequest(force=False, reason="manual"),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=MagicMock(),
            federation_sync_service=MagicMock(),
        )

    assert exc_info.value.status_code == 400
    assert "providerConfig.region" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_returns_501_for_unimplemented_provider(
    sample_user_context, sample_federation, sample_job
):
    azure_federation = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerType": FederationProviderType.AZURE_AI_FOUNDRY,
            "syncStatus": FederationSyncStatus.IDLE,
        }
    )

    pending_federation = SimpleNamespace(
        **{
            **azure_federation.__dict__,
            "syncStatus": FederationSyncStatus.PENDING,
        }
    )

    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=azure_federation)
    federation_crud_service.mark_sync_pending = AsyncMock(return_value=pending_federation)

    federation_job_service = MagicMock()
    federation_job_service.get_active_job = AsyncMock(return_value=None)
    federation_job_service.create_job = AsyncMock(return_value=sample_job)

    federation_sync_service = MagicMock()
    federation_sync_service.run_sync = AsyncMock(
        side_effect=ValueError("Federation provider azure_ai_foundry is not implemented yet.")
    )

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(azure_federation.id),
            data=FederationSyncRequest(force=False, reason="manual"),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=federation_job_service,
            federation_sync_service=federation_sync_service,
        )

    assert exc_info.value.status_code == 501
    assert "not implemented yet" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_returns_502_for_provider_discovery_failure(
    sample_user_context, sample_federation, sample_job
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.mark_sync_pending = AsyncMock(return_value=sample_federation)

    federation_job_service = MagicMock()
    federation_job_service.get_active_job = AsyncMock(return_value=None)
    federation_job_service.create_job = AsyncMock(return_value=sample_job)

    federation_sync_service = MagicMock()
    federation_sync_service.run_sync = AsyncMock(
        side_effect=RuntimeError("Failed to list AgentCore runtimes in us-east-1: Token has expired and refresh failed")
    )

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(sample_federation.id),
            data=FederationSyncRequest(force=False, reason="manual"),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=federation_job_service,
            federation_sync_service=federation_sync_service,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "external_service_error"


@pytest.mark.asyncio
async def test_delete_federation_rejects_disabled_status(sample_user_context, sample_federation):
    disabled_federation = SimpleNamespace(**{**sample_federation.__dict__, "status": FederationStatus.DISABLED})
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=disabled_federation)

    with pytest.raises(HTTPException) as exc_info:
        await delete_federation(
            federation_id=str(disabled_federation.id),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_job_service=MagicMock(),
            federation_sync_service=MagicMock(),
        )

    assert exc_info.value.status_code == 409
    assert "cannot be deleted" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_list_federations_uses_server_style_query_and_pagination(sample_federation):
    federation_crud_service = MagicMock()
    federation_crud_service.list_federations = AsyncMock(return_value=([sample_federation], 1))

    result = await list_federations(
        providerType="aws_agentcore",
        syncStatus="success",
        tag="prod",
        tags=["prod", "aws"],
        query="agentcore",
        keyword=None,
        page=2,
        per_page=10,
        pageSize=None,
        federation_crud_service=federation_crud_service,
    )

    federation_crud_service.list_federations.assert_awaited_once_with(
        provider_type="aws_agentcore",
        sync_status="success",
        tag="prod",
        tags=["prod", "aws"],
        keyword="agentcore",
        page=2,
        page_size=10,
    )
    assert len(result.federations) == 1
    assert result.pagination.page == 2
    assert result.pagination.perPage == 10
