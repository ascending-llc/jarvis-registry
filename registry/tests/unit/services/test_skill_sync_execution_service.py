from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_discovery_service import DiscoveryResult
from registry.services.skill_sync_execution_service import SkillSyncExecutionService
from registry.services.skill_sync_github_service import GitHubDownloadError
from registry_pkgs.database.leased_job import Lease
from registry_pkgs.models.enums import (
    SkillSyncJobErrorCode,
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncSkillErrorCode,
    SkillSyncSourceStatus,
    SkillSyncStatus,
)
from registry_pkgs.models.skill_sync_job import (
    SkillSyncApplySummary,
    SkillSyncDeleteRequestSnapshot,
    SkillSyncDiscoverySummary,
    SkillSyncFullRequestSnapshot,
    SkillSyncSkillError,
)
from registry_pkgs.models.skill_sync_source import SkillSyncSourceStats


def _snapshot(revision: int = 1) -> SkillSyncFullRequestSnapshot:
    return SkillSyncFullRequestSnapshot(
        owner="snapshot-owner",
        repo="snapshot-repo",
        ref="snapshot-ref",
        paths=["snapshot-path"],
        configRevision=revision,
    )


def _source(**overrides):
    values = {
        "id": PydanticObjectId(),
        "status": SkillSyncSourceStatus.ACTIVE,
        "syncStatus": SkillSyncStatus.PENDING,
        "syncMessage": None,
        "configRevision": 1,
        "githubAppClientSecretEncrypted": "encrypted-secret",
        "githubAppClientId": "client-id",
        "save": AsyncMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _job(source, **overrides):
    values = {
        "id": PydanticObjectId(),
        "sourceId": source.id,
        "jobType": SkillSyncJobType.FULL_SYNC,
        "triggeredBy": str(PydanticObjectId()),
        "requestSnapshot": _snapshot(),
        "status": SkillSyncJobStatus.SYNCING,
        "phase": SkillSyncJobPhase.QUEUED,
        "skillErrors": [],
        "discoverySummary": SkillSyncDiscoverySummary(),
        "applySummary": SkillSyncApplySummary(),
        "leaseOwner": "worker-1",
        "leaseExpiresAt": object(),
        "save": AsyncMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _service(source, *, access_token="access-token"):
    source_service = MagicMock(
        get_source=AsyncMock(return_value=source),
        mark_sync_failed=AsyncMock(),
        restore_after_delete_failure=AsyncMock(),
    )
    token_service = MagicMock(
        resolve_access_token=AsyncMock(return_value=access_token),
        delete_user_access_token=AsyncMock(),
        delete_source_tokens=AsyncMock(),
    )
    github_service = MagicMock(
        download_tarball=AsyncMock(return_value="a" * 40),
        extract_skill_folders=MagicMock(return_value=MagicMock()),
    )
    discovery_service = MagicMock()
    apply_service = MagicMock(
        apply_discovered_skills=AsyncMock(return_value=SkillSyncApplySummary()),
        list_live_skills=AsyncMock(return_value=[]),
        inherit_source_acl_to_skills=AsyncMock(),
        build_source_stats=AsyncMock(return_value=SkillSyncSourceStats()),
        delete_source_skills=AsyncMock(return_value=SkillSyncApplySummary(skillsDeleted=2, filesDeleted=3)),
    )
    acl_service = MagicMock(delete_acl_entries_for_resource=AsyncMock())
    service = SkillSyncExecutionService(
        source_crud_service=source_service,
        token_service=token_service,
        github_service=github_service,
        discovery_service=discovery_service,
        apply_service=apply_service,
        acl_service=acl_service,
    )
    return SimpleNamespace(
        service=service,
        source_service=source_service,
        token_service=token_service,
        github_service=github_service,
        discovery_service=discovery_service,
        apply_service=apply_service,
        acl_service=acl_service,
    )


def _lease(job) -> Lease:
    return Lease(doc_id=job.id, owner="worker-1", token="tok-1", expires_at=datetime.now(UTC) + timedelta(minutes=2))


class _RecordingRepo:
    """Stand-in for the leased repository: records each fenced write instead of hitting Mongo."""

    def __init__(self) -> None:
        self.writes: list[SimpleNamespace] = []

    async def transition_or_raise(self, lease, set_fields, *, unset=None, release=False, session=None) -> None:
        self.writes.append(SimpleNamespace(set_fields=set_fields, release=release))

    @property
    def last_write(self) -> SimpleNamespace:
        return self.writes[-1]


@pytest.fixture(autouse=True)
def fake_repo(monkeypatch):
    recorder = _RecordingRepo()
    monkeypatch.setattr("registry.services.skill_sync_execution_service.skill_sync_repository", recorder)
    return recorder


@pytest.fixture(autouse=True)
def decrypt_stub(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_execution_service.decrypt_value", lambda value: value)


@pytest.mark.asyncio
async def test_run_claimed_job_with_missing_source_fails_job_without_execution(fake_repo):
    ctx = _service(None)
    job = _job(SimpleNamespace(id=PydanticObjectId()))

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    assert job.error == "Skill sync source no longer exists"
    # The terminal write releases the lease (clears owner/token/expiry) through the fenced repository.
    assert fake_repo.last_write.release is True
    ctx.token_service.resolve_access_token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "source_revision", "message"),
    [
        (SkillSyncDeleteRequestSnapshot(), 1, "invalid request snapshot"),
        (_snapshot(1), 2, "changed from revision 1 to 2"),
    ],
)
async def test_run_claimed_job_rejects_invalid_or_stale_snapshot(snapshot, source_revision, message):
    source = _source(configRevision=source_revision)
    ctx = _service(source)
    job = _job(source, requestSnapshot=snapshot)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    assert message in job.error
    ctx.source_service.mark_sync_failed.assert_awaited_once_with(source, job.error)
    ctx.github_service.download_tarball.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_claimed_job_without_token_sets_auth_failure():
    source = _source()
    ctx = _service(source, access_token=None)
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    assert job.errorCode == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED.value
    ctx.github_service.download_tarball.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_sync_success_uses_snapshot_and_finalizes_source():
    source = _source()
    ctx = _service(source)
    discovered = SimpleNamespace(upstream_id="skills/demo")
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[discovered],
        errors=[],
        summary=SkillSyncDiscoverySummary(discoveredSkillCount=1),
    )
    live_skill = SimpleNamespace(id=PydanticObjectId())
    ctx.apply_service.list_live_skills.return_value = [live_skill]
    ctx.apply_service.build_source_stats.return_value = SkillSyncSourceStats(skillCount=1, fileCount=2)
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.SUCCESS
    assert job.phase == SkillSyncJobPhase.COMPLETED
    assert source.syncStatus == SkillSyncStatus.SUCCESS
    assert source.lastSync.commitSha == "a" * 40
    assert source.stats == SkillSyncSourceStats(skillCount=1, fileCount=2)
    ctx.github_service.download_tarball.assert_awaited_once()
    download_args = ctx.github_service.download_tarball.await_args.kwargs
    assert (download_args["owner"], download_args["repo"], download_args["ref"]) == (
        "snapshot-owner",
        "snapshot-repo",
        "snapshot-ref",
    )
    assert ctx.github_service.extract_skill_folders.call_args.kwargs["paths"] == ["snapshot-path"]
    ctx.apply_service.inherit_source_acl_to_skills.assert_awaited_once_with(source, [live_skill.id])


@pytest.mark.asyncio
async def test_full_sync_tolerates_acl_inheritance_failure():
    source = _source()
    ctx = _service(source)
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[SimpleNamespace(upstream_id="skills/demo")],
        errors=[],
        summary=SkillSyncDiscoverySummary(discoveredSkillCount=1),
    )
    ctx.apply_service.list_live_skills.return_value = [SimpleNamespace(id=PydanticObjectId())]
    ctx.apply_service.inherit_source_acl_to_skills.side_effect = RuntimeError("ACL unavailable")
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    # The status stays as the skill writes decided; the failure is recorded like federation sync does.
    assert job.status == SkillSyncJobStatus.SUCCESS
    assert source.syncStatus == SkillSyncStatus.SUCCESS
    assert job.error == "ACL inheritance failed for 1 skills: ACL unavailable"
    assert job.errorCode is None
    assert source.syncMessage == job.error
    assert source.lastSync is not None


@pytest.mark.asyncio
async def test_full_sync_clears_a_previous_sync_message_when_acl_inheritance_succeeds():
    source = _source()
    source.syncMessage = "ACL inheritance failed for 1 skills: ACL unavailable"
    ctx = _service(source)
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[SimpleNamespace(upstream_id="skills/demo")],
        errors=[],
        summary=SkillSyncDiscoverySummary(discoveredSkillCount=1),
    )
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.SUCCESS
    assert job.error is None
    assert source.syncMessage is None


@pytest.mark.asyncio
async def test_full_sync_with_skill_error_finishes_partial_success():
    source = _source()
    ctx = _service(source)
    skill_error = SkillSyncSkillError(
        skillPath="skills/broken",
        upstreamId="skills/broken",
        errorCode=SkillSyncSkillErrorCode.SKILL_PARSE_FAILED,
        errorMessage="bad yaml",
        phase="discovery",
    )
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[SimpleNamespace(upstream_id="skills/ok")],
        errors=[skill_error],
        summary=SkillSyncDiscoverySummary(discoveredSkillCount=1),
    )
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.PARTIAL_SUCCESS
    assert source.syncStatus == SkillSyncStatus.PARTIAL_SUCCESS
    assert job.skillErrors == [skill_error]


@pytest.mark.asyncio
async def test_full_sync_without_valid_skills_fails_before_apply():
    source = _source()
    ctx = _service(source)
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[],
        errors=[],
        summary=SkillSyncDiscoverySummary(),
    )
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    assert job.errorCode == SkillSyncJobErrorCode.NO_SKILLS_FOUND.value
    ctx.apply_service.apply_discovered_skills.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_auth_failure_deletes_invalid_user_token():
    source = _source()
    ctx = _service(source)
    ctx.github_service.download_tarball.side_effect = GitHubDownloadError(
        "expired token",
        SkillSyncJobErrorCode.GITHUB_AUTH_FAILED,
    )
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.errorCode == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED.value
    ctx.token_service.delete_user_access_token.assert_awaited_once_with(
        user_id=job.triggeredBy,
        source_id=source.id,
    )


@pytest.mark.asyncio
async def test_unexpected_sync_failure_sets_internal_error_and_cleans_up():
    source = _source()
    ctx = _service(source)
    ctx.github_service.download_tarball.side_effect = RuntimeError("disk unavailable")
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    assert job.errorCode == SkillSyncJobErrorCode.INTERNAL_ERROR.value
    assert job.error == "Internal error: disk unavailable"
    ctx.source_service.mark_sync_failed.assert_awaited_once_with(source, job.error)


@pytest.mark.asyncio
async def test_delete_job_cleans_children_acl_tokens_and_source():
    source = _source()
    ctx = _service(source)
    job = _job(source, jobType=SkillSyncJobType.DELETE_SYNC)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.SUCCESS
    assert job.applySummary == SkillSyncApplySummary(skillsDeleted=2, filesDeleted=3)
    assert source.status == SkillSyncSourceStatus.DELETED
    assert source.deletedAt is not None
    ctx.acl_service.delete_acl_entries_for_resource.assert_awaited_once()
    ctx.token_service.delete_source_tokens.assert_awaited_once_with(source.id)


@pytest.mark.asyncio
async def test_delete_failure_restores_source_and_fails_job():
    source = _source()
    ctx = _service(source)
    ctx.apply_service.delete_source_skills.side_effect = RuntimeError("delete failed")
    job = _job(source, jobType=SkillSyncJobType.DELETE_SYNC)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    ctx.source_service.restore_after_delete_failure.assert_awaited_once_with(source, "delete failed")


def _successful_discovery(ctx) -> None:
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[SimpleNamespace(upstream_id="skills/demo")],
        errors=[],
        summary=SkillSyncDiscoverySummary(discoveredSkillCount=1),
    )


@pytest.mark.asyncio
async def test_source_save_failure_after_finalize_marks_source_failed_without_refinalizing(fake_repo):
    # The terminal write released the lease, so the failure must not go back through the job-failure path
    # (which would raise LeaseLostError and strand the source in SYNCING).
    source = _source()
    source.save.side_effect = [None, RuntimeError("mongo down")]
    ctx = _service(source)
    _successful_discovery(ctx)
    status_when_marked_failed = []
    ctx.source_service.mark_sync_failed.side_effect = lambda src, message: status_when_marked_failed.append(
        src.syncStatus
    )
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.SUCCESS
    assert [write.release for write in fake_repo.writes].count(True) == 1
    ctx.source_service.mark_sync_failed.assert_awaited_once_with(source, "Internal error: mongo down")
    assert status_when_marked_failed == [SkillSyncStatus.SYNCING]


@pytest.mark.asyncio
async def test_source_stats_failure_fails_the_job_before_finalizing(fake_repo):
    source = _source()
    ctx = _service(source)
    _successful_discovery(ctx)
    ctx.apply_service.build_source_stats.side_effect = RuntimeError("stats failed")
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.FAILED
    assert [write.release for write in fake_repo.writes].count(True) == 1
    ctx.source_service.mark_sync_failed.assert_awaited_once_with(source, "Internal error: stats failed")


@pytest.mark.asyncio
async def test_delete_source_save_failure_after_finalize_restores_source_without_refinalizing(fake_repo):
    source = _source(deletedAt=None)
    source.save.side_effect = RuntimeError("mongo down")
    ctx = _service(source)
    job = _job(source, jobType=SkillSyncJobType.DELETE_SYNC)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.SUCCESS
    assert [write.release for write in fake_repo.writes].count(True) == 1
    ctx.source_service.restore_after_delete_failure.assert_awaited_once_with(source, "Internal error: mongo down")
    assert source.deletedAt is None


@pytest.mark.asyncio
async def test_source_recovery_failure_after_finalize_is_only_logged():
    source = _source()
    source.save.side_effect = [None, RuntimeError("mongo down")]
    ctx = _service(source)
    _successful_discovery(ctx)
    ctx.source_service.mark_sync_failed.side_effect = RuntimeError("still down")
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    assert job.status == SkillSyncJobStatus.SUCCESS
    ctx.source_service.mark_sync_failed.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_type", "recovery_method"),
    [
        (SkillSyncJobType.FULL_SYNC, "mark_sync_failed"),
        (SkillSyncJobType.DELETE_SYNC, "restore_after_delete_failure"),
    ],
)
async def test_recover_exhausted_job_releases_source_state(job_type, recovery_method):
    source = _source()
    ctx = _service(source)
    job = _job(source, jobType=job_type, error="abandoned")

    await ctx.service.recover_exhausted_job(job)

    getattr(ctx.source_service, recovery_method).assert_awaited_once_with(source, "abandoned")


@pytest.mark.asyncio
async def test_recover_exhausted_job_ignores_removed_source():
    ctx = _service(None)
    job = _job(SimpleNamespace(id=PydanticObjectId()), error="abandoned")

    await ctx.service.recover_exhausted_job(job)

    ctx.source_service.mark_sync_failed.assert_not_awaited()
    ctx.source_service.restore_after_delete_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase_writes_never_carry_lease_expiry(fake_repo):
    # The lease-reset regression: no phase write may set leaseExpiresAt, or it would clobber the
    # heartbeat's renewals. Only the terminal release clears the lease fields.
    source = _source()
    ctx = _service(source)
    ctx.discovery_service.discover_skills.return_value = DiscoveryResult(
        skills=[SimpleNamespace(upstream_id="skills/demo")],
        errors=[],
        summary=SkillSyncDiscoverySummary(discoveredSkillCount=1),
    )
    job = _job(source)

    await ctx.service.run_claimed_job(job, _lease(job))

    non_terminal = [write for write in fake_repo.writes if not write.release]
    assert non_terminal, "expected at least one phase write"
    for write in non_terminal:
        assert "leaseExpiresAt" not in write.set_fields
        assert "leaseToken" not in write.set_fields


@pytest.mark.asyncio
async def test_phase_write_after_token_replaced_aborts_before_source_write(monkeypatch):
    # Token-fenced: once another owner re-claims, the next phase write raises and no source write runs.
    from registry_pkgs.database.leased_job import LeaseLostError

    source = _source()
    ctx = _service(source)
    job = _job(source)

    class _LostRepo:
        async def transition_or_raise(self, *args, **kwargs):
            raise LeaseLostError("re-claimed by another owner")

    monkeypatch.setattr("registry.services.skill_sync_execution_service.skill_sync_repository", _LostRepo())

    with pytest.raises(LeaseLostError):
        await ctx.service.run_claimed_job(job, _lease(job))

    source.save.assert_not_awaited()
    ctx.source_service.mark_sync_failed.assert_not_awaited()
