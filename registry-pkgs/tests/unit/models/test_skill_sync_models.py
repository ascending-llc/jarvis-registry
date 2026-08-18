from registry_pkgs.models.enums import (
    SkillSyncJobStatus,
    SkillSyncSourceStatus,
    SkillSyncStateMachine,
    SkillSyncStatus,
)
from registry_pkgs.models.skill_sync_job import SkillSyncApplySummary, SkillSyncDiscoverySummary, SkillSyncJob
from registry_pkgs.models.skill_sync_source import SkillSyncSource


def test_source_defaults_and_secret_field() -> None:
    source = SkillSyncSource.model_construct(
        displayName="Docs",
        owner="octocat",
        repo="skills",
        paths=["skills"],
        githubAppClientId="client-id",
        githubAppClientSecretEncrypted="encrypted-secret",
    )

    assert source.ref == "main"
    assert source.skillDiscoveryDepth == 2
    assert source.syncStatus == SkillSyncStatus.IDLE
    assert "githubAppClientSecret" not in source.model_dump()


def test_job_defaults_are_independent() -> None:
    first = SkillSyncJob.model_construct(sourceId="source", jobType="full_sync")
    second = SkillSyncJob.model_construct(sourceId="source", jobType="full_sync")

    first.discoverySummary.skippedPaths.append("missing")
    assert second.discoverySummary == SkillSyncDiscoverySummary()
    assert second.applySummary == SkillSyncApplySummary()
    assert second.status == SkillSyncJobStatus.PENDING


def test_state_machine_rejects_concurrent_sync() -> None:
    assert (
        SkillSyncStateMachine.transition_to_sync_pending(
            SkillSyncSourceStatus.ACTIVE,
            SkillSyncStatus.SUCCESS,
        )
        == SkillSyncStatus.PENDING
    )

    try:
        SkillSyncStateMachine.transition_to_sync_pending(
            SkillSyncSourceStatus.ACTIVE,
            SkillSyncStatus.SYNCING,
        )
    except ValueError as exc:
        assert "active sync job" in str(exc)
    else:
        raise AssertionError("Concurrent sync must be rejected")
