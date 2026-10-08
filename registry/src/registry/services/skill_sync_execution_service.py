"""Execution orchestration for claimed skill sync jobs."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from registry_pkgs.database.leased_job import Lease, LeaseLostError
from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models.enums import (
    SkillSyncJobErrorCode,
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncSourceStatus,
    SkillSyncStateMachine,
    SkillSyncStatus,
)
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.skill_sync_job import SkillSyncFullRequestSnapshot, SkillSyncJob
from registry_pkgs.models.skill_sync_source import SkillSyncSource, SkillSyncSourceLastSync, SkillSyncSourceStats

from ..utils.crypto_utils import decrypt_value
from .access_control_service import ACLService
from .skill_sync_apply_service import SkillSyncApplyService
from .skill_sync_discovery_service import SkillSyncDiscoveryService
from .skill_sync_github_service import GitHubDownloadError, SkillSyncGitHubService
from .skill_sync_job_service import skill_sync_repository
from .skill_sync_source_crud_service import SkillSyncSourceCrudService
from .skill_sync_token_service import SkillSyncTokenService

logger = logging.getLogger(__name__)


class SkillSyncExecutionService:
    """Orchestrate one job that has already been claimed by the durable runner.

    This service validates the persisted snapshot and source revision, resolves credentials,
    dispatches full-sync or delete execution, advances phases, finalizes job/source state,
    and cleans temporary resources. It does not claim jobs or implement Skill persistence
    diffs; those belong to :class:`SkillSyncJobRunner` and :class:`SkillSyncApplyService`.
    """

    def __init__(
        self,
        *,
        source_crud_service: SkillSyncSourceCrudService,
        token_service: SkillSyncTokenService,
        github_service: SkillSyncGitHubService,
        discovery_service: SkillSyncDiscoveryService,
        apply_service: SkillSyncApplyService,
        acl_service: ACLService,
    ) -> None:
        self._source_crud_service = source_crud_service
        self._token_service = token_service
        self._github_service = github_service
        self._discovery_service = discovery_service
        self._apply_service = apply_service
        self._acl_service = acl_service

    async def run_claimed_job(self, job: SkillSyncJob, lease: Lease) -> None:
        """Validate persisted inputs and dispatch one job already owned by the runner.

        Full syncs reject a stale configuration revision before resolving credentials or
        contacting GitHub. Delete jobs intentionally use current source identity only.
        """
        source = await self._source_crud_service.get_source(str(job.sourceId))
        if source is None:
            await self._finalize_job(
                job,
                lease,
                SkillSyncJobStatus.FAILED,
                SkillSyncJobPhase.FAILED,
                error_code=SkillSyncJobErrorCode.INTERNAL_ERROR,
                error="Skill sync source no longer exists",
            )
            return
        if job.jobType == SkillSyncJobType.DELETE_SYNC:
            await self._run_delete(source, job, lease)
            return
        if not isinstance(job.requestSnapshot, SkillSyncFullRequestSnapshot):
            await self._fail_job(source, job, lease, "Full sync job has an invalid request snapshot")
            return
        if job.requestSnapshot.configRevision != source.configRevision:
            await self._fail_job(
                source,
                job,
                lease,
                f"Source configuration changed from revision {job.requestSnapshot.configRevision} "
                f"to {source.configRevision} before execution",
            )
            return

        client_secret = decrypt_value(source.githubAppClientSecretEncrypted)
        access_token = await self._token_service.resolve_access_token(
            user_id=job.triggeredBy,
            source_id=source.id,
            client_id=source.githubAppClientId,
            client_secret=client_secret,
        )
        if access_token is None:
            await self._fail_job(
                source,
                job,
                lease,
                "GitHub authorization is required before this sync can continue",
                error_code=SkillSyncJobErrorCode.GITHUB_AUTH_FAILED,
            )
            return
        await self._run_sync(source, job, lease, job.triggeredBy, access_token, job.requestSnapshot)

    async def recover_exhausted_job(self, job: SkillSyncJob) -> None:
        """Release source lifecycle state after the runner terminally fails an abandoned job."""
        source = await self._source_crud_service.get_source(str(job.sourceId))
        if source is None:
            return
        error = job.error or "Skill sync job exhausted its worker retries"
        if job.jobType == SkillSyncJobType.DELETE_SYNC:
            await self._source_crud_service.restore_after_delete_failure(source, error)
            return
        await self._source_crud_service.mark_sync_failed(source, error)

    async def _run_sync(
        self,
        source: SkillSyncSource,
        job: SkillSyncJob,
        lease: Lease,
        user_id: str,
        access_token: str,
        request_snapshot: SkillSyncFullRequestSnapshot,
    ) -> None:
        """Execute download, extraction, discovery, apply, and terminal bookkeeping.

        Repository coordinates come exclusively from the immutable request snapshot. The
        resolved commit SHA returned by the downloader identifies exactly what was applied.
        """
        extraction_dir = tempfile.mkdtemp(prefix=f"skillsync-{job.id}-")
        try:
            job.phase = SkillSyncJobPhase.DOWNLOADING
            job.startedAt = datetime.now(UTC)
            await skill_sync_repository.transition_or_raise(
                lease, {"phase": job.phase.value, "startedAt": job.startedAt}
            )
            source.syncStatus = SkillSyncStateMachine.transition_to_syncing(source.status, source.syncStatus)
            await source.save()

            tarball_path = Path(extraction_dir) / "tarball.tar.gz"
            commit_sha = await self._github_service.download_tarball(
                owner=request_snapshot.owner,
                repo=request_snapshot.repo,
                ref=request_snapshot.ref,
                access_token=access_token,
                dest_path=tarball_path,
            )
            job.phase = SkillSyncJobPhase.EXTRACTING
            await skill_sync_repository.transition_or_raise(lease, {"phase": job.phase.value})
            extraction = await asyncio.to_thread(
                self._github_service.extract_skill_folders,
                tarball_path,
                paths=request_snapshot.paths,
                extraction_dir=Path(extraction_dir),
            )
            job.phase = SkillSyncJobPhase.DISCOVERING
            await skill_sync_repository.transition_or_raise(lease, {"phase": job.phase.value})
            discovery = await asyncio.to_thread(self._discovery_service.discover_skills, extraction)
            job.discoverySummary = discovery.summary
            job.skillErrors.extend(discovery.errors)
            await skill_sync_repository.transition_or_raise(
                lease,
                {
                    "discoverySummary": job.discoverySummary.model_dump(mode="json"),
                    "skillErrors": [error.model_dump(mode="json") for error in job.skillErrors],
                },
            )
            if not discovery.skills:
                # Never apply an empty discovery: it could turn an upstream/parser outage into mass deletion.
                await self._fail_job(
                    source,
                    job,
                    lease,
                    f"No valid skills found in configured paths; {len(discovery.errors)} errors during discovery",
                    error_code=SkillSyncJobErrorCode.NO_SKILLS_FOUND,
                )
                return

            job.phase = SkillSyncJobPhase.APPLYING
            await skill_sync_repository.transition_or_raise(lease, {"phase": job.phase.value})
            apply_summary = await self._apply_service.apply_discovered_skills(
                source=source,
                job=job,
                discovery=discovery,
                user_id=user_id,
                commit_sha=commit_sha,
                request_snapshot=request_snapshot,
            )
            job.applySummary = apply_summary
            live_skills = await self._apply_service.list_live_skills(source.id)
            acl_error = await self._inherit_source_acl(source, live_skills)

            has_errors = bool(job.skillErrors) or apply_summary.skillsFailed > 0
            final_status = SkillSyncJobStatus.PARTIAL_SUCCESS if has_errors else SkillSyncJobStatus.SUCCESS
            # Compute the source's new state before finalizing, so a failure here still fails the job normally.
            sync_status_before = source.syncStatus
            next_sync_status = (
                SkillSyncStateMachine.transition_to_sync_partial_success(sync_status_before)
                if has_errors
                else SkillSyncStateMachine.transition_to_sync_success(sync_status_before)
            )
            stats = await self._apply_service.build_source_stats(live_skills)
            await self._finalize_job(job, lease, final_status, SkillSyncJobPhase.COMPLETED, error=acl_error)

            source.syncStatus = next_sync_status
            source.syncMessage = acl_error
            source.lastSync = SkillSyncSourceLastSync(
                jobId=str(job.id),
                status=final_status,
                startedAt=job.startedAt,
                finishedAt=job.finishedAt,
                commitSha=commit_sha,
            )
            source.stats = stats

            async def mark_source_failed(message: str) -> None:
                # Undo the in-memory success transition so the state machine accepts FAILED.
                source.syncStatus = sync_status_before
                await self._source_crud_service.mark_sync_failed(source, message)

            await self._save_source_after_finalize(source, on_failure=mark_source_failed)
        except GitHubDownloadError as exc:
            logger.exception("GitHub download failed for source %s", source.id)
            if exc.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED:
                await self._token_service.delete_user_access_token(user_id=user_id, source_id=source.id)
            await self._fail_job(source, job, lease, str(exc), error_code=exc.error_code)
        except LeaseLostError:
            # Another owner re-claimed this job; the fence already blocks our writes. Hand it back to
            # the runner instead of mislabelling the handoff as a sync failure (and don't touch source).
            raise
        except Exception as exc:
            logger.exception("Sync failed for source %s", source.id)
            await self._fail_job(source, job, lease, f"Internal error: {exc}")
        finally:
            shutil.rmtree(extraction_dir, ignore_errors=True)

    async def _inherit_source_acl(self, source: SkillSyncSource, live_skills: list[Skill]) -> str | None:
        """Copy the source's ACL grants to its skills; return an error message instead of raising.

        Skill content is already committed, so a failure must not change the job's status. Like federation
        sync, the message is recorded on the job and the source, and the next sync retries (insert-only).
        """
        try:
            await self._apply_service.inherit_source_acl_to_skills(source, [skill.id for skill in live_skills])
        except Exception as exc:
            logger.exception("ACL inheritance failed for source %s, continuing", source.id)
            return f"ACL inheritance failed for {len(live_skills)} skills: {exc}"
        return None

    async def _run_delete(self, source: SkillSyncSource, job: SkillSyncJob, lease: Lease) -> None:
        """Delete child resources first, then finalize the source only after cleanup succeeds."""
        try:
            job.phase = SkillSyncJobPhase.APPLYING
            job.startedAt = datetime.now(UTC)
            await skill_sync_repository.transition_or_raise(
                lease, {"phase": job.phase.value, "startedAt": job.startedAt}
            )
            job.applySummary = await self._apply_service.delete_source_skills(source)
            await self._acl_service.delete_acl_entries_for_resource(
                resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
                resource_id=source.id,
            )
            await self._token_service.delete_source_tokens(source.id)
            await self._finalize_job(job, lease, SkillSyncJobStatus.SUCCESS, SkillSyncJobPhase.COMPLETED)
            now = datetime.now(UTC)
            source.status = SkillSyncSourceStatus.DELETED
            source.syncStatus = SkillSyncStatus.SUCCESS
            source.deletedAt = now
            source.stats = SkillSyncSourceStats(skillCount=0, fileCount=0)
            source.lastSync = SkillSyncSourceLastSync(
                jobId=str(job.id),
                status=SkillSyncJobStatus.SUCCESS,
                startedAt=job.startedAt,
                finishedAt=job.finishedAt,
            )

            async def restore_source(message: str) -> None:
                # The source was not deleted after all; restore it as ACTIVE so the delete can be retried.
                source.deletedAt = None
                await self._source_crud_service.restore_after_delete_failure(source, message)

            await self._save_source_after_finalize(source, on_failure=restore_source)
        except LeaseLostError:
            # Another owner re-claimed this job; the fence already blocks our writes. Hand it back to
            # the runner instead of mislabelling the handoff as a delete failure (and don't touch source).
            raise
        except Exception as exc:
            logger.exception("Delete failed for source %s", source.id)
            await self._finalize_job(
                job,
                lease,
                SkillSyncJobStatus.FAILED,
                SkillSyncJobPhase.FAILED,
                error_code=SkillSyncJobErrorCode.INTERNAL_ERROR,
                error=str(exc),
            )
            await self._source_crud_service.restore_after_delete_failure(source, str(exc))

    @staticmethod
    async def _save_source_after_finalize(
        source: SkillSyncSource,
        *,
        on_failure: Callable[[str], Awaitable[None]],
    ) -> None:
        """Save the source once its job is terminal, recording a failure on the source only.

        ``_finalize_job`` released the lease, so this failure must not reach the job-failure handlers:
        re-finalizing the job would raise ``LeaseLostError`` and leave the source SYNCING/DELETING for
        good. ``on_failure`` moves the source to a retryable state instead; if that also fails, it is
        logged only.
        """
        try:
            await source.save()
        except Exception as exc:
            logger.exception("Saving source %s after finalizing its job failed", source.id)
            try:
                await on_failure(f"Internal error: {exc}")
            except Exception:
                logger.exception("Recording the failure on source %s failed", source.id)

    async def _fail_job(
        self,
        source: SkillSyncSource,
        job: SkillSyncJob,
        lease: Lease,
        error: str,
        *,
        error_code: SkillSyncJobErrorCode = SkillSyncJobErrorCode.INTERNAL_ERROR,
    ) -> None:
        """Persist the same terminal failure on both the job and its owning source."""
        await self._finalize_job(
            job,
            lease,
            SkillSyncJobStatus.FAILED,
            SkillSyncJobPhase.FAILED,
            error_code=error_code,
            error=error,
        )
        await self._source_crud_service.mark_sync_failed(source, error)

    async def _finalize_job(
        self,
        job: SkillSyncJob,
        lease: Lease,
        status: SkillSyncJobStatus,
        phase: SkillSyncJobPhase,
        *,
        error_code: SkillSyncJobErrorCode | None = None,
        error: str | None = None,
    ) -> None:
        """Finalize a job and release its lease, token-fenced so a stale execution cannot write.

        ``skillErrors`` and ``applySummary`` are taken from the in-memory job: the apply service and
        earlier phases accumulate them there, and this terminal write is what persists them.
        """
        job.status = status
        job.phase = phase
        job.errorCode = error_code.value if error_code else None
        job.error = error
        job.finishedAt = datetime.now(UTC)
        await skill_sync_repository.transition_or_raise(
            lease,
            {
                "status": status.value,
                "phase": phase.value,
                "errorCode": job.errorCode,
                "error": error,
                "finishedAt": job.finishedAt,
                "skillErrors": [skill_error.model_dump(mode="json") for skill_error in job.skillErrors],
                "applySummary": job.applySummary.model_dump(mode="json"),
            },
            release=True,
        )
