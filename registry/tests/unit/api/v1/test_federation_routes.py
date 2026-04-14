from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.api.v1.federation.federation_routes import (
    create_federation,
    delete_federation,
    get_federation,
    list_federations,
    sync_federation,
    update_federation,
)
from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.federation_api_schemas import (
    FederationCreateRequest,
    FederationSyncDryRunResponse,
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


def _unwrap_route(func):
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


@pytest.fixture
def sample_user_context():
    return {
        "user_id": "000000000000000000000111",
        "username": "testuser",
    }


@pytest.fixture
def acl_service():
    service = MagicMock()
    service.grant_permission = AsyncMock()
    service.get_accessible_resource_ids = AsyncMock(return_value=[])
    service.get_user_permissions_for_resource = AsyncMock(return_value=ResourcePermissions(VIEW=True))
    service.check_user_permission = AsyncMock(
        return_value=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)
    )
    service.delete_acl_entries_for_resource = AsyncMock(return_value=1)
    return service


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
        providerConfig={
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole",
        },
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
        jobType=FederationJobType.FULL_SYNC,
        status=FederationJobStatus.SUCCESS,
        phase=FederationJobPhase.COMPLETED,
        startedAt=datetime.now(UTC),
        finishedAt=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_create_federation_does_not_trigger_sync(sample_user_context, sample_federation, acl_service):
    federation_crud_service = MagicMock()
    federation_crud_service.create_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[])

    result = await _unwrap_route(create_federation)(
        data=FederationCreateRequest(
            providerType=FederationProviderType.AWS_AGENTCORE,
            displayName="AWS AgentCore Prod",
            description="Production federation",
            tags=["prod"],
            providerConfig={"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        acl_service=acl_service,
    )

    assert result.id == str(sample_federation.id)
    assert len(result.recentJobs) == 0
    assert result.permissions == ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)
    acl_service.grant_permission.assert_awaited_once_with(
        principal_type="user",
        principal_id=PydanticObjectId(sample_user_context["user_id"]),
        resource_type="federation",
        resource_id=sample_federation.id,
        perm_bits=15,
    )


@pytest.mark.asyncio
async def test_create_federation_allows_empty_aws_provider_config(sample_user_context, sample_federation, acl_service):
    created_federation = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerConfig": {"resourceTagsFilter": {}},
        }
    )
    federation_crud_service = MagicMock()
    federation_crud_service.create_federation = AsyncMock(return_value=created_federation)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[])

    result = await _unwrap_route(create_federation)(
        data=FederationCreateRequest(
            providerType=FederationProviderType.AWS_AGENTCORE,
            displayName="AWS AgentCore Prod",
            description="Production federation",
            tags=["prod"],
            providerConfig={},
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        acl_service=acl_service,
    )

    assert result.providerConfig == {"resourceTagsFilter": {}}


@pytest.mark.asyncio
async def test_update_federation_runs_resync_for_provider_changes(
    sample_user_context, sample_federation, sample_job, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.validate_provider_config = MagicMock(
        return_value={"region": "us-west-2", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"}
    )
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[sample_job])

    updated_federation = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerConfig": {"region": "us-west-2", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
            "version": 2,
        }
    )
    federation_sync_service = MagicMock()
    federation_sync_service.update_federation_with_optional_resync = AsyncMock(
        return_value=(updated_federation, sample_job)
    )

    result = await update_federation(
        federation_id=str(sample_federation.id),
        data=FederationUpdateRequest(
            displayName="AWS AgentCore Prod",
            description="Updated federation",
            tags=["prod"],
            providerConfig={"region": "us-west-2"},
            syncAfterUpdate=True,
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_sync_service=federation_sync_service,
        acl_service=acl_service,
    )

    federation_sync_service.update_federation_with_optional_resync.assert_awaited_once_with(
        federation=sample_federation,
        display_name="AWS AgentCore Prod",
        description="Updated federation",
        tags=["prod"],
        provider_config={"region": "us-west-2"},
        updated_by=sample_user_context["user_id"],
        sync_after_update=True,
    )
    assert result.version == 2


@pytest.mark.asyncio
async def test_update_federation_requires_aws_region_and_assume_role(
    sample_user_context, sample_federation, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_sync_service = MagicMock()
    federation_sync_service.update_federation_with_optional_resync = AsyncMock(
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
                syncAfterUpdate=False,
            ),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=federation_sync_service,
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 400
    assert "providerConfig.region" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_update_federation_rejects_azure_provider(sample_user_context, sample_federation, acl_service):
    azure_federation = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerType": FederationProviderType.AZURE_AI_FOUNDRY,
            "displayName": "Azure Federation",
            "providerConfig": {"projectEndpoint": "https://old.projects.ai.azure.com"},
        }
    )

    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=azure_federation)
    federation_sync_service = MagicMock()
    federation_sync_service.update_federation_with_optional_resync = AsyncMock(
        side_effect=ValueError("Azure AI Foundry federation sync is not implemented yet")
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_federation(
            federation_id=str(azure_federation.id),
            data=FederationUpdateRequest(
                displayName="Azure Federation",
                description="Updated federation",
                tags=["prod"],
                providerConfig={"projectEndpoint": "https://new.projects.ai.azure.com"},
                syncAfterUpdate=True,
            ),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=federation_sync_service,
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 501


@pytest.mark.asyncio
async def test_update_federation_skips_resync_when_provider_config_is_unchanged(
    sample_user_context, sample_federation, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.validate_provider_config = MagicMock(return_value=sample_federation.providerConfig)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[])
    updated_federation = SimpleNamespace(
        **{**sample_federation.__dict__, "description": "Updated federation", "version": 2}
    )
    federation_crud_service.update_federation = AsyncMock(return_value=updated_federation)

    federation_sync_service = MagicMock()
    federation_sync_service.update_federation_with_optional_resync = AsyncMock(return_value=(updated_federation, None))

    result = await update_federation(
        federation_id=str(sample_federation.id),
        data=FederationUpdateRequest(
            displayName="AWS AgentCore Prod",
            description="Updated federation",
            tags=["prod"],
            providerConfig=sample_federation.providerConfig,
            syncAfterUpdate=True,
        ),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_sync_service=federation_sync_service,
        acl_service=acl_service,
    )

    federation_sync_service.update_federation_with_optional_resync.assert_awaited_once()
    assert result.version == 2


@pytest.mark.asyncio
async def test_delete_federation_returns_deleted_status(
    sample_user_context, sample_federation, sample_job, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)

    federation_sync_service = MagicMock()
    federation_sync_service.start_delete = AsyncMock(return_value=sample_job)

    result = await _unwrap_route(delete_federation)(
        federation_id=str(sample_federation.id),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_sync_service=federation_sync_service,
        acl_service=acl_service,
    )

    federation_sync_service.start_delete.assert_awaited_once_with(
        federation=sample_federation,
        triggered_by=sample_user_context["user_id"],
    )
    acl_service.delete_acl_entries_for_resource.assert_awaited_once_with(
        resource_type="federation",
        resource_id=sample_federation.id,
    )
    assert result.federationId == str(sample_federation.id)
    assert result.status == "deleted"


@pytest.mark.asyncio
async def test_delete_federation_maps_provider_failure(sample_user_context, sample_federation, acl_service):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)

    federation_sync_service = MagicMock()
    federation_sync_service.start_delete = AsyncMock(
        side_effect=RuntimeError("Failed to list AgentCore runtimes in us-east-1: Token has expired and refresh failed")
    )

    with pytest.raises(HTTPException) as exc_info:
        await _unwrap_route(delete_federation)(
            federation_id=str(sample_federation.id),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=federation_sync_service,
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "external_service_error"


@pytest.mark.asyncio
async def test_update_federation_rejects_deleting_status(sample_user_context, sample_federation, acl_service):
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
                syncAfterUpdate=True,
            ),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=MagicMock(),
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 409
    assert "cannot be updated" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_rejects_running_sync_status(sample_user_context, sample_federation, acl_service):
    syncing_federation = SimpleNamespace(**{**sample_federation.__dict__, "syncStatus": FederationSyncStatus.SYNCING})
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=syncing_federation)

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(syncing_federation.id),
            data=FederationSyncRequest(),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=MagicMock(),
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 409
    assert "cannot start a new sync" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_requires_aws_region_and_assume_role(sample_user_context, sample_federation, acl_service):
    federation_missing_config = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerConfig": {"resourceTagsFilter": {}},
        }
    )
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=federation_missing_config)
    federation_crud_service.validate_provider_config = MagicMock(
        side_effect=ValueError("AWS AgentCore federation requires providerConfig.region, providerConfig.assumeRoleArn")
    )

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(federation_missing_config.id),
            data=FederationSyncRequest(),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=MagicMock(),
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 400
    assert "providerConfig.region" in exc_info.value.detail["message"]


@pytest.mark.asyncio
async def test_sync_federation_rejects_azure_provider(sample_user_context, sample_federation, acl_service):
    azure_federation = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "providerType": FederationProviderType.AZURE_AI_FOUNDRY,
            "providerConfig": {"projectEndpoint": "https://example.projects.ai.azure.com"},
            "syncStatus": FederationSyncStatus.IDLE,
        }
    )

    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=azure_federation)
    federation_crud_service.validate_provider_config = MagicMock(
        side_effect=ValueError("Azure AI Foundry federation sync is not implemented yet")
    )

    federation_sync_service = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(azure_federation.id),
            data=FederationSyncRequest(),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=federation_sync_service,
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 501


@pytest.mark.asyncio
async def test_sync_federation_dry_run_returns_preview_response(sample_user_context, sample_federation, acl_service):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.validate_provider_config = MagicMock(return_value=sample_federation.providerConfig)

    preview_result = SimpleNamespace(
        provider_type=sample_federation.providerType,
        provider_config=sample_federation.providerConfig,
        discovered_mcp_count=2,
        discovered_a2a_count=1,
        summary=SimpleNamespace(
            createdMcpServers=1,
            updatedMcpServers=0,
            deletedMcpServers=0,
            unchangedMcpServers=1,
            createdAgents=1,
            updatedAgents=0,
            deletedAgents=0,
            unchangedAgents=0,
            skippedAgents=0,
            errors=0,
            errorMessages=[],
        ),
        message=None,
    )
    federation_sync_service = MagicMock()
    federation_sync_service.preview_manual_sync = AsyncMock(return_value=preview_result)
    federation_sync_service.start_manual_sync = AsyncMock()

    result = await sync_federation(
        federation_id=str(sample_federation.id),
        data=FederationSyncRequest(dryRun=True, reason="test connection"),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_sync_service=federation_sync_service,
        acl_service=acl_service,
    )

    assert isinstance(result, FederationSyncDryRunResponse)
    assert result.dryRun is True
    assert result.summary.discoveredMcpServers == 2
    assert result.summary.createdAgents == 1
    federation_sync_service.preview_manual_sync.assert_awaited_once_with(
        federation=sample_federation,
        reason="test connection",
        triggered_by=sample_user_context["user_id"],
    )
    federation_sync_service.start_manual_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_federation_dry_run_bypasses_running_sync_status(
    sample_user_context, sample_federation, acl_service
):
    syncing_federation = SimpleNamespace(**{**sample_federation.__dict__, "syncStatus": FederationSyncStatus.SYNCING})
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=syncing_federation)
    federation_crud_service.validate_provider_config = MagicMock(return_value=syncing_federation.providerConfig)

    preview_result = SimpleNamespace(
        provider_type=syncing_federation.providerType,
        provider_config=syncing_federation.providerConfig,
        discovered_mcp_count=0,
        discovered_a2a_count=0,
        summary=SimpleNamespace(
            createdMcpServers=0,
            updatedMcpServers=0,
            deletedMcpServers=0,
            unchangedMcpServers=0,
            createdAgents=0,
            updatedAgents=0,
            deletedAgents=0,
            unchangedAgents=0,
            skippedAgents=0,
            errors=0,
            errorMessages=[],
        ),
        message=None,
    )
    federation_sync_service = MagicMock()
    federation_sync_service.preview_manual_sync = AsyncMock(return_value=preview_result)

    result = await sync_federation(
        federation_id=str(syncing_federation.id),
        data=FederationSyncRequest(dryRun=True),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_sync_service=federation_sync_service,
        acl_service=acl_service,
    )

    assert result.dryRun is True
    federation_sync_service.preview_manual_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_federation_returns_502_for_provider_discovery_failure(
    sample_user_context, sample_federation, sample_job, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)

    federation_sync_service = MagicMock()
    federation_sync_service.start_manual_sync = AsyncMock(
        side_effect=RuntimeError("Failed to list AgentCore runtimes in us-east-1: Token has expired and refresh failed")
    )

    with pytest.raises(HTTPException) as exc_info:
        await sync_federation(
            federation_id=str(sample_federation.id),
            data=FederationSyncRequest(),
            user_context=sample_user_context,
            federation_crud_service=federation_crud_service,
            federation_sync_service=federation_sync_service,
            acl_service=acl_service,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "external_service_error"


@pytest.mark.asyncio
async def test_sync_federation_creates_pending_job_in_first_transaction(
    sample_user_context, sample_federation, sample_job, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.validate_provider_config = MagicMock(return_value=sample_federation.providerConfig)

    federation_sync_service = MagicMock()
    federation_sync_service.start_manual_sync = AsyncMock(return_value=sample_job)

    result = await sync_federation(
        federation_id=str(sample_federation.id),
        data=FederationSyncRequest(reason="manual"),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        federation_sync_service=federation_sync_service,
        acl_service=acl_service,
    )

    federation_sync_service.start_manual_sync.assert_awaited_once_with(
        federation=sample_federation,
        reason="manual",
        triggered_by=sample_user_context["user_id"],
    )
    assert result.id == str(sample_job.id)


@pytest.mark.asyncio
async def test_list_federations_uses_server_style_query_and_pagination(
    sample_federation, sample_user_context, acl_service
):
    federation_crud_service = MagicMock()
    federation_crud_service.list_federations = AsyncMock(return_value=([sample_federation], 1))
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[str(sample_federation.id)])
    acl_service.get_user_permissions_for_resource = AsyncMock(return_value=ResourcePermissions(VIEW=True))

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
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        acl_service=acl_service,
    )

    federation_crud_service.list_federations.assert_awaited_once_with(
        provider_type="aws_agentcore",
        sync_status="success",
        tag="prod",
        tags=["prod", "aws"],
        keyword="agentcore",
        page=2,
        page_size=10,
        accessible_federation_ids=[str(sample_federation.id)],
    )
    assert len(result.federations) == 1
    assert result.pagination.page == 2
    assert result.pagination.perPage == 10
    assert result.federations[0].permissions == ResourcePermissions(VIEW=True)


@pytest.mark.asyncio
async def test_list_federations_includes_last_sync(sample_federation, sample_user_context, acl_service):
    now = datetime.now(UTC)
    federation_with_last_sync = SimpleNamespace(
        **{
            **sample_federation.__dict__,
            "lastSync": SimpleNamespace(
                jobId=PydanticObjectId(),
                jobType=FederationJobType.FULL_SYNC,
                status=FederationSyncStatus.SUCCESS,
                startedAt=now,
                finishedAt=now,
                summary=SimpleNamespace(
                    discoveredMcpServers=2,
                    discoveredAgents=1,
                    createdMcpServers=1,
                    updatedMcpServers=1,
                    deletedMcpServers=0,
                    unchangedMcpServers=0,
                    createdAgents=1,
                    updatedAgents=0,
                    deletedAgents=0,
                    unchangedAgents=0,
                    errors=0,
                    errorMessages=[],
                ),
            ),
        }
    )
    federation_crud_service = MagicMock()
    federation_crud_service.list_federations = AsyncMock(return_value=([federation_with_last_sync], 1))
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[str(sample_federation.id)])
    acl_service.get_user_permissions_for_resource = AsyncMock(return_value=ResourcePermissions(VIEW=True))

    result = await list_federations(
        providerType=None,
        syncStatus=None,
        tag=None,
        tags=None,
        query=None,
        keyword=None,
        page=1,
        per_page=20,
        pageSize=None,
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        acl_service=acl_service,
    )

    assert result.federations[0].lastSync is not None
    assert result.federations[0].lastSync.jobType == FederationJobType.FULL_SYNC
    assert result.federations[0].lastSync.status == FederationSyncStatus.SUCCESS
    assert result.federations[0].lastSync.summary is not None
    assert result.federations[0].lastSync.summary.discoveredMcpServers == 2


@pytest.mark.asyncio
async def test_list_federations_returns_empty_when_user_has_no_access(sample_user_context, acl_service):
    federation_crud_service = MagicMock()
    federation_crud_service.list_federations = AsyncMock(return_value=([], 0))
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[])

    result = await list_federations(
        providerType=None,
        syncStatus=None,
        tag=None,
        tags=None,
        query=None,
        keyword=None,
        page=1,
        per_page=20,
        pageSize=None,
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        acl_service=acl_service,
    )

    federation_crud_service.list_federations.assert_awaited_once_with(
        provider_type=None,
        sync_status=None,
        tag=None,
        tags=None,
        keyword=None,
        page=1,
        page_size=20,
        accessible_federation_ids=[],
    )
    assert result.federations == []
    assert result.pagination.total == 0


@pytest.mark.asyncio
async def test_get_federation_checks_view_permission(sample_user_context, sample_federation, acl_service):
    federation_crud_service = MagicMock()
    federation_crud_service.get_federation = AsyncMock(return_value=sample_federation)
    federation_crud_service.get_recent_jobs = AsyncMock(return_value=[])

    result = await get_federation(
        federation_id=str(sample_federation.id),
        user_context=sample_user_context,
        federation_crud_service=federation_crud_service,
        acl_service=acl_service,
    )

    acl_service.check_user_permission.assert_awaited_once()
    assert result.permissions == ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)
