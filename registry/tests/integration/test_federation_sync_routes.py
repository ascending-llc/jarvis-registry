from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.v1.federation.federation_routes import router
from registry.auth.dependencies import get_current_user
from registry.deps import (
    get_acl_service,
    get_federation_crud_service,
    get_federation_job_service,
    get_federation_sync_service,
)
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobStatus,
    FederationJobType,
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
)

USER_ID = "000000000000000000000111"


@pytest.fixture
def federation_route_context():
    app = FastAPI()
    app.include_router(router)
    federation = SimpleNamespace(
        id=PydanticObjectId(),
        providerType=FederationProviderType.AWS_AGENTCORE,
        providerConfig={
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole",
        },
        status=FederationStatus.ACTIVE,
        syncStatus=FederationSyncStatus.IDLE,
    )
    job = SimpleNamespace(
        id=PydanticObjectId(),
        federationId=federation.id,
        jobType=FederationJobType.FULL_SYNC,
        status=FederationJobStatus.PENDING,
        phase=FederationJobPhase.QUEUED,
        startedAt=None,
        finishedAt=None,
        error=None,
    )
    crud_service = MagicMock()
    crud_service.get_federation = AsyncMock(return_value=federation)
    crud_service.validate_provider_config = MagicMock(return_value=federation.providerConfig)
    sync_service = MagicMock()
    sync_service.create_manual_sync_job = AsyncMock(return_value=(job, PydanticObjectId(USER_ID)))
    sync_service.run_sync = AsyncMock()
    job_service = MagicMock()
    job_service.get_job = AsyncMock(return_value=job)
    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER_ID}
    app.dependency_overrides[get_federation_crud_service] = lambda: crud_service
    app.dependency_overrides[get_federation_sync_service] = lambda: sync_service
    app.dependency_overrides[get_federation_job_service] = lambda: job_service
    app.dependency_overrides[get_acl_service] = lambda: acl_service

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            federation=federation,
            job=job,
            sync_service=sync_service,
            job_service=job_service,
        )


def test_federation_sync_returns_202_and_pending_job(federation_route_context):
    context = federation_route_context

    response = context.client.post(
        f"/federations/{context.federation.id}/sync",
        json={"dryRun": False, "reason": "manual"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "id": str(context.job.id),
        "federationId": str(context.federation.id),
        "jobType": "full_sync",
        "status": "pending",
        "phase": "queued",
        "startedAt": None,
        "finishedAt": None,
        "error": None,
    }
    context.sync_service.run_sync.assert_awaited_once()


def test_federation_sync_dry_run_stays_200(federation_route_context):
    context = federation_route_context
    context.sync_service.preview_manual_sync = AsyncMock(
        return_value=SimpleNamespace(
            provider_type=context.federation.providerType,
            provider_config=context.federation.providerConfig,
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
    )

    response = context.client.post(
        f"/federations/{context.federation.id}/sync",
        json={"dryRun": True},
    )

    assert response.status_code == 200
    assert response.json()["dryRun"] is True
    context.sync_service.create_manual_sync_job.assert_not_awaited()


def test_federation_job_poll_returns_status_phase_and_error(federation_route_context):
    context = federation_route_context
    context.job.status = FederationJobStatus.FAILED
    context.job.phase = FederationJobPhase.FAILED
    context.job.error = "weaviate timeout"

    response = context.client.get(f"/federations/{context.federation.id}/jobs/{context.job.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["phase"] == "failed"
    assert response.json()["error"] == "weaviate timeout"


def test_federation_job_poll_returns_404_for_other_federation(federation_route_context):
    context = federation_route_context
    context.job_service.get_job.return_value = None

    response = context.client.get(f"/federations/{context.federation.id}/jobs/{PydanticObjectId()}")

    assert response.status_code == 404
    assert response.json()["detail"]["message"] == "Sync job not found"
