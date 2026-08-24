from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from registry.api.v1.skill_sync.skill_sync_source_routes import router
from registry.auth.dependencies import get_current_user
from registry.deps import (
    get_acl_service,
    get_skill_sync_job_service,
    get_skill_sync_oauth_service,
    get_skill_sync_service,
    get_skill_sync_source_crud_service,
    get_skill_sync_token_service,
)
from registry.schemas.acl_schema import ResourcePermissions
from registry.services.skill_sync_service import SyncTriggerResult
from registry_pkgs.models.enums import (
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncProviderType,
    SkillSyncSourceStatus,
    SkillSyncStatus,
    SkillSyncTriggerType,
)
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.skill_sync_job import SkillSyncApplySummary, SkillSyncDiscoverySummary
from registry_pkgs.models.skill_sync_source import SkillSyncSourceStats

USER_ID = "000000000000000000000111"
_VIEW_PERMS = ResourcePermissions(VIEW=True)


def _make_source(source_id=None, **overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": source_id or PydanticObjectId(),
        "providerType": SkillSyncProviderType.GITHUB,
        "displayName": "Skills",
        "description": None,
        "tags": [],
        "owner": "octocat",
        "repo": "skills",
        "ref": "main",
        "paths": ["skills"],
        "skillDiscoveryDepth": 2,
        "githubAppClientId": "client",
        "githubAppClientSecretEncrypted": "encrypted",
        "status": SkillSyncSourceStatus.ACTIVE,
        "syncStatus": SkillSyncStatus.IDLE,
        "syncMessage": None,
        "stats": SkillSyncSourceStats(),
        "lastSync": None,
        "createdBy": USER_ID,
        "updatedBy": USER_ID,
        "createdAt": now,
        "updatedAt": now,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_job(source_id, **overrides):
    now = datetime.now(UTC)
    defaults = {
        "id": PydanticObjectId(),
        "sourceId": source_id,
        "jobType": SkillSyncJobType.FULL_SYNC,
        "triggerType": SkillSyncTriggerType.MANUAL,
        "status": SkillSyncJobStatus.PENDING,
        "phase": SkillSyncJobPhase.QUEUED,
        "requestSnapshot": {},
        "discoverySummary": SkillSyncDiscoverySummary(),
        "applySummary": SkillSyncApplySummary(),
        "skillErrors": [],
        "errorCode": None,
        "error": None,
        "startedAt": None,
        "finishedAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _assert_permission_checked(ctx: SimpleNamespace, required_permission: str) -> None:
    ctx.acl_service.check_user_permission.assert_awaited_once_with(
        user_id=PydanticObjectId(USER_ID),
        resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
        resource_id=ctx.source.id,
        required_permission=required_permission,
    )


@pytest.fixture
def skill_sync_route_context():
    app = FastAPI()
    app.include_router(router)
    source = _make_source()
    job = _make_job(source.id)
    source_service = MagicMock()
    source_service.get_source = AsyncMock(return_value=source)
    source_service.get_recent_jobs = AsyncMock(return_value=[])
    source_service.list_sources = AsyncMock(return_value=([source], 1))
    source_service.update_source = AsyncMock(return_value=source)
    source_service.mark_sync_pending = AsyncMock(return_value=source)
    source_service.mark_sync_failed = AsyncMock(return_value=source)
    source_service.mark_deleting = AsyncMock(return_value=source)
    source_service.restore_after_delete_failure = AsyncMock(return_value=source)
    job_service = MagicMock()
    job_service.get_job = AsyncMock(return_value=job)
    job_service.get_active_job = AsyncMock(return_value=None)
    job_service.create_job = AsyncMock(return_value=job)
    job_service.mark_not_implemented = AsyncMock(return_value=job)
    token_service = MagicMock()
    token_service.resolve_access_token = AsyncMock(return_value=None)
    token_service.delete_source_tokens = AsyncMock()
    oauth_service = MagicMock()
    oauth_service.create_authorization_url.return_value = "https://github.com/login/oauth/authorize?state=test"
    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock(return_value=_VIEW_PERMS)
    acl_service.get_accessible_resource_ids = AsyncMock(return_value=[str(source.id)])
    acl_service.get_user_permissions_for_resource = AsyncMock(return_value=None)
    acl_service.get_user_permissions_for_resources = AsyncMock(return_value={source.id: ResourcePermissions(VIEW=True)})
    skill_sync_service = MagicMock()
    skill_sync_service.create_source_with_owner_acl = AsyncMock(return_value=source)

    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER_ID}
    app.dependency_overrides[get_skill_sync_source_crud_service] = lambda: source_service
    app.dependency_overrides[get_skill_sync_job_service] = lambda: job_service
    app.dependency_overrides[get_skill_sync_token_service] = lambda: token_service
    app.dependency_overrides[get_skill_sync_oauth_service] = lambda: oauth_service
    app.dependency_overrides[get_skill_sync_service] = lambda: skill_sync_service
    app.dependency_overrides[get_acl_service] = lambda: acl_service

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            source=source,
            job=job,
            source_service=source_service,
            job_service=job_service,
            token_service=token_service,
            oauth_service=oauth_service,
            skill_sync_service=skill_sync_service,
            acl_service=acl_service,
        )


def test_sync_triggers_sync_needs_auth(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.skill_sync_service.trigger_sync = AsyncMock(return_value=SyncTriggerResult())
    response = ctx.client.post(f"/skill-sync-sources/{ctx.source.id}/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["needsAuthorization"] is True
    _assert_permission_checked(ctx, "EDIT")
    ctx.skill_sync_service.trigger_sync.assert_awaited_once()


def test_sync_triggers_sync_returns_job(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.skill_sync_service.trigger_sync = AsyncMock(
        return_value=SyncTriggerResult(job=ctx.job, source=ctx.source, access_token="tok")
    )
    response = ctx.client.post(f"/skill-sync-sources/{ctx.source.id}/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["id"] == str(ctx.job.id)
    _assert_permission_checked(ctx, "EDIT")


def test_job_polling_is_scoped_to_source(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    response = ctx.client.get(f"/skill-sync-sources/{ctx.source.id}/jobs/{ctx.job.id}")
    assert response.status_code == 200
    assert response.json()["sourceId"] == str(ctx.source.id)
    assert response.json()["status"] == "pending"


def test_get_source_returns_detail(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    response = ctx.client.get(f"/skill-sync-sources/{ctx.source.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(ctx.source.id)
    assert body["displayName"] == "Skills"
    assert body["githubAppClientId"] == "client"
    assert body["hasClientSecret"] is True


def test_get_source_not_found(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.source_service.get_source = AsyncMock(return_value=None)
    response = ctx.client.get(f"/skill-sync-sources/{PydanticObjectId()}")
    assert response.status_code == 404


def test_list_sources_returns_paged(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    response = ctx.client.get("/skill-sync-sources")
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert len(body["sources"]) == 1
    assert body["sources"][0]["owner"] == "octocat"
    ctx.acl_service.get_user_permissions_for_resources.assert_awaited_once()
    ctx.acl_service.get_user_permissions_for_resource.assert_not_awaited()


def test_list_sources_empty(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.source_service.list_sources = AsyncMock(return_value=([], 0))
    response = ctx.client.get("/skill-sync-sources")
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0
    assert response.json()["sources"] == []


def test_get_job_not_found(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.job_service.get_job = AsyncMock(return_value=None)
    response = ctx.client.get(f"/skill-sync-sources/{ctx.source.id}/jobs/{PydanticObjectId()}")
    assert response.status_code == 404


def test_oauth_initiate_redirects_to_github(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    response = ctx.client.get(
        f"/skill-sync-sources/{ctx.source.id}/oauth/initiate",
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert "github.com/login/oauth/authorize" in response.headers["location"]
    _assert_permission_checked(ctx, "EDIT")


def test_create_source_delegates_transaction_to_service(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    payload = {
        "displayName": "Skills",
        "owner": "octocat",
        "repo": "skills",
        "paths": ["skills"],
        "githubAppClientId": "client",
        "githubAppClientSecret": "secret",
    }
    response = ctx.client.post("/skill-sync-sources", json=payload)
    assert response.status_code == 201
    ctx.skill_sync_service.create_source_with_owner_acl.assert_awaited_once()
    ctx.source_service.create_source.assert_not_called()


def test_delete_returns_202_with_job(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.skill_sync_service.delete_source_with_skills = AsyncMock(return_value=(ctx.job, ctx.source))
    response = ctx.client.delete(f"/skill-sync-sources/{ctx.source.id}")
    assert response.status_code == 202
    body = response.json()
    assert body["sourceId"] == str(ctx.source.id)
    assert body["jobId"] == str(ctx.job.id)
    assert body["status"] == "deleting"
    _assert_permission_checked(ctx, "DELETE")


def test_update_with_sync_triggers_sync(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.skill_sync_service.trigger_sync = AsyncMock(
        return_value=SyncTriggerResult(job=ctx.job, source=ctx.source, access_token="tok")
    )
    response = ctx.client.put(
        f"/skill-sync-sources/{ctx.source.id}",
        json={"displayName": "Renamed", "syncAfterUpdate": True},
    )
    assert response.status_code == 200
    assert response.json()["job"]["id"] == str(ctx.job.id)
    ctx.source_service.update_source.assert_awaited_once()
    ctx.skill_sync_service.trigger_sync.assert_awaited_once()


def test_update_with_sync_returns_needs_auth(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.skill_sync_service.trigger_sync = AsyncMock(return_value=SyncTriggerResult())
    response = ctx.client.put(
        f"/skill-sync-sources/{ctx.source.id}",
        json={"displayName": "Renamed", "syncAfterUpdate": True},
    )
    assert response.status_code == 200
    assert response.json()["needsAuthorization"] is True


def test_list_sources_maps_acl_runtime_error_to_500(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.acl_service.get_user_permissions_for_resources = AsyncMock(side_effect=RuntimeError("acl unavailable"))
    response = ctx.client.get("/skill-sync-sources")
    assert response.status_code == 500


def test_oauth_callback_exchanges_and_triggers_sync(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.oauth_service.exchange_callback = AsyncMock(return_value=USER_ID)
    ctx.skill_sync_service.trigger_sync = AsyncMock(
        return_value=SyncTriggerResult(job=ctx.job, source=ctx.source, access_token="tok")
    )
    response = ctx.client.get(
        f"/skill-sync-sources/{ctx.source.id}/oauth/callback?code=code&state=state",
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert "status=syncing" in response.headers["location"]
    ctx.oauth_service.exchange_callback.assert_awaited_once()
    ctx.skill_sync_service.trigger_sync.assert_awaited_once()


def test_acl_forbidden_returns_403(skill_sync_route_context) -> None:
    ctx = skill_sync_route_context
    ctx.acl_service.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))
    response = ctx.client.get(f"/skill-sync-sources/{ctx.source.id}")
    assert response.status_code == 403
