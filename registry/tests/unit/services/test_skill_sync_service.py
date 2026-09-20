from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_service import SkillSyncService


class _FakeTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    async def start_transaction(self):
        return _FakeTransaction()


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, *args):
        return None


def _source(**overrides):
    values = {
        "id": PydanticObjectId(),
        "githubAppClientSecretEncrypted": "encrypted",
        "githubAppClientId": "client",
        "owner": "octocat",
        "repo": "skills",
        "ref": "main",
        "paths": ["skills"],
        "configRevision": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _service(source_service=None, job_service=None, token_service=None, github_service=None) -> SkillSyncService:
    return SkillSyncService(
        source_crud_service=source_service or MagicMock(),
        job_service=job_service or MagicMock(),
        token_service=token_service or MagicMock(),
        github_service=github_service or MagicMock(),
    )


@pytest.fixture
def transaction_client(monkeypatch):
    client = MagicMock()
    client.start_session.return_value = _FakeSessionContext()
    monkeypatch.setattr("registry.services.skill_sync_service.MongoDB.get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_create_source_with_owner_acl_persists_both_in_one_transaction(transaction_client):
    source = _source()
    source_service = MagicMock(create_source=AsyncMock(return_value=source))
    acl_service = MagicMock(grant_permission=AsyncMock())

    result = await _service(source_service=source_service).create_source_with_owner_acl(
        display_name="Skills",
        description=None,
        tags=["official"],
        owner="octocat",
        repo="skills",
        ref="main",
        paths=["skills"],
        github_app_client_id="client",
        github_app_client_secret="secret",
        created_by="user-1",
        principal_id=PydanticObjectId(),
        acl_service=acl_service,
    )

    assert result is source
    assert (
        source_service.create_source.await_args.kwargs["session"]
        is acl_service.grant_permission.await_args.kwargs["session"]
    )


@pytest.mark.asyncio
async def test_trigger_sync_persists_typed_snapshot_from_marked_source(transaction_client, monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    source = _source()
    job = SimpleNamespace(id=PydanticObjectId())
    source_service = MagicMock(mark_sync_pending=AsyncMock(return_value=source))
    job_service = MagicMock(create_job=AsyncMock(return_value=job))
    token_service = MagicMock(resolve_access_token=AsyncMock(return_value="access-token"))

    result = await _service(source_service, job_service, token_service).trigger_sync(
        source=source,
        user_id=str(PydanticObjectId()),
        trigger_type=MagicMock(),
    )

    snapshot = job_service.create_job.await_args.kwargs["request_snapshot"]
    assert result.job is job
    assert snapshot.model_dump() == {
        "owner": "octocat",
        "repo": "skills",
        "ref": "main",
        "paths": ["skills"],
        "configRevision": 3,
    }
    assert (
        source_service.mark_sync_pending.await_args.kwargs["session"]
        is job_service.create_job.await_args.kwargs["session"]
    )


@pytest.mark.asyncio
async def test_trigger_sync_without_access_token_does_not_change_source(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    source_service = MagicMock(mark_sync_pending=AsyncMock())
    job_service = MagicMock(create_job=AsyncMock())
    token_service = MagicMock(resolve_access_token=AsyncMock(return_value=None))

    result = await _service(source_service, job_service, token_service).trigger_sync(
        source=_source(),
        user_id="user-1",
        trigger_type=MagicMock(),
    )

    assert result.job is None
    source_service.mark_sync_pending.assert_not_awaited()
    job_service.create_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_connection_needs_authorization_when_no_token(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    source_service = MagicMock(mark_sync_pending=AsyncMock())
    job_service = MagicMock(create_job=AsyncMock())
    token_service = MagicMock(resolve_access_token=AsyncMock(return_value=None))
    github_service = MagicMock(resolve_commit_sha=AsyncMock())

    result = await _service(source_service, job_service, token_service, github_service).test_connection(
        source=_source(),
        user_id="user-1",
    )

    assert result.needs_authorization is True
    assert result.ok is False
    github_service.resolve_commit_sha.assert_not_awaited()
    source_service.mark_sync_pending.assert_not_awaited()
    job_service.create_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_connection_ok_when_repo_ref_resolvable(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    source_service = MagicMock(mark_sync_pending=AsyncMock())
    job_service = MagicMock(create_job=AsyncMock())
    token_service = MagicMock(resolve_access_token=AsyncMock(return_value="access-token"))
    github_service = MagicMock(resolve_commit_sha=AsyncMock(return_value="sha123"))

    result = await _service(source_service, job_service, token_service, github_service).test_connection(
        source=_source(),
        user_id="user-1",
    )

    assert result.ok is True
    assert result.needs_authorization is False
    github_service.resolve_commit_sha.assert_awaited_once()
    # Read-only: never mutates or enqueues.
    source_service.mark_sync_pending.assert_not_awaited()
    job_service.create_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_connection_reports_detail_when_github_fails(monkeypatch):
    from registry.services.skill_sync_github_service import GitHubDownloadError
    from registry_pkgs.models.enums import SkillSyncJobErrorCode

    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    token_service = MagicMock(resolve_access_token=AsyncMock(return_value="access-token"))
    github_service = MagicMock(
        resolve_commit_sha=AsyncMock(
            side_effect=GitHubDownloadError(
                "Repository octocat/skills ref main not found",
                SkillSyncJobErrorCode.GITHUB_NOT_FOUND,
            )
        )
    )

    result = await _service(token_service=token_service, github_service=github_service).test_connection(
        source=_source(),
        user_id="user-1",
    )

    assert result.ok is False
    assert result.needs_authorization is False
    assert "not found" in (result.detail or "")


@pytest.mark.asyncio
async def test_test_connection_flags_reauth_when_token_revoked(monkeypatch):
    from registry.services.skill_sync_github_service import GitHubDownloadError
    from registry_pkgs.models.enums import SkillSyncJobErrorCode

    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    token_service = MagicMock(resolve_access_token=AsyncMock(return_value="access-token"))
    github_service = MagicMock(
        resolve_commit_sha=AsyncMock(
            side_effect=GitHubDownloadError(
                "GitHub authentication failed (HTTP 401)",
                SkillSyncJobErrorCode.GITHUB_AUTH_FAILED,
            )
        )
    )

    result = await _service(token_service=token_service, github_service=github_service).test_connection(
        source=_source(),
        user_id="user-1",
    )

    assert result.ok is False
    assert result.needs_authorization is True
    assert "authentication failed" in (result.detail or "")


@pytest.mark.asyncio
async def test_delete_source_persists_typed_delete_snapshot_in_transaction(transaction_client):
    source = _source()
    job = SimpleNamespace(id=PydanticObjectId())
    source_service = MagicMock(mark_deleting=AsyncMock(return_value=source))
    job_service = MagicMock(create_job=AsyncMock(return_value=job))

    result_job, result_source = await _service(source_service, job_service).delete_source_with_skills(
        source=source,
        user_id="user-1",
    )

    snapshot = job_service.create_job.await_args.kwargs["request_snapshot"]
    assert result_job is job
    assert result_source is source
    assert snapshot.model_dump() == {"action": "delete", "configRevision": 3}
    assert (
        source_service.mark_deleting.await_args.kwargs["session"] is job_service.create_job.await_args.kwargs["session"]
    )
