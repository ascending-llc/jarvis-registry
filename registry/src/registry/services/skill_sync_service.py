from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import UTC, datetime

from beanie import PydanticObjectId

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile
from registry_pkgs.models import PrincipalType, SkillSource
from registry_pkgs.models.enums import (
    RoleBits,
    SkillSyncJobErrorCode,
    SkillSyncJobPhase,
    SkillSyncJobStateMachine,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncSkillErrorCode,
    SkillSyncSourceStatus,
    SkillSyncStateMachine,
    SkillSyncStatus,
    SkillSyncTriggerType,
)
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.skill_sync_job import (
    SkillSyncApplySummary,
    SkillSyncJob,
    SkillSyncSkillError,
)
from registry_pkgs.models.skill_sync_source import SkillSyncSource, SkillSyncSourceLastSync, SkillSyncSourceStats

from ..utils.crypto_utils import decrypt_value
from .access_control_service import ACLService
from .skill_sync_discovery_service import DiscoveredSkill, DiscoveryResult, SkillSyncDiscoveryService
from .skill_sync_github_service import GitHubDownloadError, SkillSyncGitHubService
from .skill_sync_job_service import SkillSyncJobService
from .skill_sync_source_crud_service import SkillSyncSourceCrudService
from .skill_sync_token_service import SkillSyncTokenService

logger = logging.getLogger(__name__)

_REGISTRY_FILE_SOURCE = "registry"

_background_tasks: set[asyncio.Task] = set()


def _on_background_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background sync task failed: %s", exc, exc_info=exc)


def _fire_background(coro) -> asyncio.Task:
    """Create a GC-safe background task with exception logging."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_on_background_done)
    return task


class SkillSyncService:
    def __init__(
        self,
        source_crud_service: SkillSyncSourceCrudService,
        job_service: SkillSyncJobService,
        token_service: SkillSyncTokenService,
        github_service: SkillSyncGitHubService,
        discovery_service: SkillSyncDiscoveryService,
        acl_service: ACLService,
    ) -> None:
        self._source_crud_service = source_crud_service
        self._job_service = job_service
        self._token_service = token_service
        self._github_service = github_service
        self._discovery_service = discovery_service
        self._acl_service = acl_service

    async def create_source_with_owner_acl(
        self,
        *,
        display_name: str,
        description: str | None,
        tags: list[str],
        owner: str,
        repo: str,
        ref: str,
        paths: list[str],
        skill_discovery_depth: int,
        github_app_client_id: str,
        github_app_client_secret: str,
        created_by: str,
        principal_id: PydanticObjectId,
        acl_service: ACLService,
    ) -> SkillSyncSource:
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                source = await self._source_crud_service.create_source(
                    display_name=display_name,
                    description=description,
                    tags=tags,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    paths=paths,
                    skill_discovery_depth=skill_discovery_depth,
                    github_app_client_id=github_app_client_id,
                    github_app_client_secret=github_app_client_secret,
                    created_by=created_by,
                    session=mongo_session,
                )
                await acl_service.grant_permission(
                    principal_type=PrincipalType.USER,
                    principal_id=principal_id,
                    resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
                    resource_id=source.id,
                    perm_bits=RoleBits.OWNER,
                    session=mongo_session,
                )
        return source

    async def trigger_sync(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
        trigger_type: SkillSyncTriggerType,
    ) -> tuple[SkillSyncJob | None, bool]:
        """Resolve OAuth token, create a job, and launch background sync. Returns (job, needs_auth)."""
        client_secret = decrypt_value(source.githubAppClientSecretEncrypted)
        access_token = await self._token_service.resolve_access_token(
            user_id=user_id,
            source_id=source.id,
            client_id=source.githubAppClientId,
            client_secret=client_secret,
        )
        if access_token is None:
            return None, True

        source = await self._source_crud_service.mark_sync_pending(source)
        job = await self._job_service.create_job(
            source_id=source.id,
            job_type=SkillSyncJobType.FULL_SYNC,
            trigger_type=trigger_type,
            triggered_by=user_id,
            request_snapshot={
                "owner": source.owner,
                "repo": source.repo,
                "ref": source.ref,
                "paths": source.paths,
                "skillDiscoveryDepth": source.skillDiscoveryDepth,
            },
        )
        _fire_background(self._run_sync(source, job, user_id, access_token))
        return job, False

    async def delete_source_with_skills(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
    ) -> SkillSyncJob:
        source = await self._source_crud_service.mark_deleting(source)
        job = await self._job_service.create_job(
            source_id=source.id,
            job_type=SkillSyncJobType.DELETE_SYNC,
            trigger_type=SkillSyncTriggerType.MANUAL,
            triggered_by=user_id,
            request_snapshot={"action": "delete"},
        )
        _fire_background(self._run_delete(source, job, user_id))
        return job

    async def _run_sync(
        self,
        source: SkillSyncSource,
        job: SkillSyncJob,
        user_id: str,
        access_token: str,
    ) -> None:
        """Full sync pipeline: download → extract → discover → apply → finalize."""
        try:
            job.status = SkillSyncJobStateMachine.transition_to_syncing(job.status)
            job.phase = SkillSyncJobPhase.DOWNLOADING
            job.startedAt = datetime.now(UTC)
            await job.save()

            source.syncStatus = SkillSyncStateMachine.transition_to_syncing(source.status, source.syncStatus)
            await source.save()

            tarball_bytes, commit_sha = await self._github_service.download_tarball(
                owner=source.owner,
                repo=source.repo,
                ref=source.ref,
                access_token=access_token,
            )

            job.phase = SkillSyncJobPhase.EXTRACTING
            await job.save()

            files = self._github_service.extract_files(
                tarball_bytes,
                paths=source.paths,
                max_depth=source.skillDiscoveryDepth,
            )

            job.phase = SkillSyncJobPhase.DISCOVERING
            await job.save()

            discovery = self._discovery_service.discover_skills(files)
            job.discoverySummary = discovery.summary
            job.skillErrors.extend(discovery.errors)
            await job.save()

            if not discovery.skills and not discovery.errors:
                await self._finalize_job(
                    job,
                    SkillSyncJobStatus.FAILED,
                    SkillSyncJobPhase.FAILED,
                    error_code=SkillSyncJobErrorCode.NO_SKILLS_FOUND,
                    error="No skills found in configured paths",
                )
                await self._source_crud_service.mark_sync_failed(source, "No skills found in configured paths")
                return

            job.phase = SkillSyncJobPhase.APPLYING
            await job.save()

            apply_summary = await self._apply_discovered_skills(
                source=source,
                job=job,
                discovery=discovery,
                user_id=user_id,
                commit_sha=commit_sha,
            )
            job.applySummary = apply_summary

            has_errors = bool(job.skillErrors) or apply_summary.skillsFailed > 0
            final_status = SkillSyncJobStatus.PARTIAL_SUCCESS if has_errors else SkillSyncJobStatus.SUCCESS
            await self._finalize_job(job, final_status, SkillSyncJobPhase.COMPLETED)

            if has_errors:
                source.syncStatus = SkillSyncStateMachine.transition_to_sync_partial_success(source.syncStatus)
            else:
                source.syncStatus = SkillSyncStateMachine.transition_to_sync_success(source.syncStatus)
            source.syncMessage = None
            source.lastSync = SkillSyncSourceLastSync(
                jobId=str(job.id),
                status=SkillSyncJobStatus(final_status),
                startedAt=job.startedAt,
                finishedAt=job.finishedAt,
                commitSha=commit_sha,
            )
            # Query actual live counts — summary arithmetic undercounts when individual skills fail
            live_skill_count = await Skill.find(
                {"source": SkillSource.GITHUB, "sourceMetadata.sourceId": str(source.id), "deletedAt": None}
            ).count()
            source.stats = SkillSyncSourceStats(
                skillCount=live_skill_count,
                fileCount=apply_summary.filesCreated + apply_summary.filesUpdated,
            )
            await source.save()

        except GitHubDownloadError as exc:
            logger.exception("GitHub download failed for source %s", source.id)
            await self._finalize_job(
                job,
                SkillSyncJobStatus.FAILED,
                SkillSyncJobPhase.FAILED,
                error_code=exc.error_code,
                error=str(exc),
            )
            await self._source_crud_service.mark_sync_failed(source, str(exc))
        except Exception as exc:
            logger.exception("Sync failed for source %s", source.id)
            await self._finalize_job(
                job,
                SkillSyncJobStatus.FAILED,
                SkillSyncJobPhase.FAILED,
                error_code=SkillSyncJobErrorCode.INTERNAL_ERROR,
                error=str(exc),
            )
            await self._source_crud_service.mark_sync_failed(source, f"Internal error: {exc}")

    async def _run_delete(
        self,
        source: SkillSyncSource,
        job: SkillSyncJob,
        user_id: str,
    ) -> None:
        """Soft-delete all skills + files + ACL for a source, then mark source as deleted."""
        try:
            job.status = SkillSyncJobStateMachine.transition_to_syncing(job.status)
            job.phase = SkillSyncJobPhase.APPLYING
            job.startedAt = datetime.now(UTC)
            await job.save()

            source_id_str = str(source.id)
            existing_skills = await Skill.find(
                {"source": SkillSource.GITHUB, "sourceMetadata.sourceId": source_id_str, "deletedAt": None}
            ).to_list()

            now = datetime.now(UTC)
            deleted_skills = 0
            deleted_files = 0
            for skill in existing_skills:
                async with MongoDB.get_client().start_session() as session:
                    async with await session.start_transaction():
                        file_result = await SkillFile.find({"skillId": skill.id}).delete(session=session)
                        deleted_files += file_result.deleted_count if file_result else 0
                        skill.deletedAt = now
                        await skill.save(session=session)
                        await self._acl_service.delete_acl_entries_for_resource(
                            resource_type=RegistryResourceType.SKILL.value,
                            resource_id=skill.id,
                            session=session,
                        )
                deleted_skills += 1

            await self._acl_service.delete_acl_entries_for_resource(
                resource_type=RegistryResourceType.SKILL_SYNC_SOURCE,
                resource_id=source.id,
            )
            await self._token_service.delete_source_tokens(source.id)

            job.applySummary = SkillSyncApplySummary(
                skillsDeleted=deleted_skills,
                filesDeleted=deleted_files,
            )
            await self._finalize_job(job, SkillSyncJobStatus.SUCCESS, SkillSyncJobPhase.COMPLETED)

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
            await source.save()

        except Exception as exc:
            logger.exception("Delete failed for source %s", source.id)
            await self._finalize_job(
                job,
                SkillSyncJobStatus.FAILED,
                SkillSyncJobPhase.FAILED,
                error_code=SkillSyncJobErrorCode.INTERNAL_ERROR,
                error=str(exc),
            )
            await self._source_crud_service.restore_after_delete_failure(source, str(exc))

    async def _apply_discovered_skills(
        self,
        *,
        source: SkillSyncSource,
        job: SkillSyncJob,
        discovery: DiscoveryResult,
        user_id: str,
        commit_sha: str,
    ) -> SkillSyncApplySummary:
        """Diff discovered skills against existing DB state: create / update / soft-delete."""
        source_id_str = str(source.id)
        user_object_id = PydanticObjectId(user_id)
        summary = SkillSyncApplySummary()

        existing_skills = await Skill.find(
            {"source": SkillSource.GITHUB, "sourceMetadata.sourceId": source_id_str, "deletedAt": None}
        ).to_list()
        existing_by_upstream: dict[str, Skill] = {}
        for s in existing_skills:
            if not s.sourceMetadata:
                continue
            upstream_id = s.sourceMetadata.get("upstreamId")
            if not upstream_id:
                logger.warning("Skill %s missing upstreamId in sourceMetadata, skipping from sync matching", s.id)
                continue
            existing_by_upstream[upstream_id] = s

        discovered_upstream_ids = {s.upstream_id for s in discovery.skills}
        now = datetime.now(UTC)

        for upstream_id, existing_skill in existing_by_upstream.items():
            if upstream_id not in discovered_upstream_ids:
                try:
                    async with MongoDB.get_client().start_session() as session:
                        async with await session.start_transaction():
                            await SkillFile.find({"skillId": existing_skill.id}).delete(session=session)
                            existing_skill.deletedAt = now
                            await existing_skill.save(session=session)
                            await self._acl_service.delete_acl_entries_for_resource(
                                resource_type=RegistryResourceType.SKILL.value,
                                resource_id=existing_skill.id,
                                session=session,
                            )
                    summary.skillsDeleted += 1
                except Exception as exc:
                    logger.exception("Failed to delete skill %s: %s", existing_skill.id, exc)
                    summary.skillsFailed += 1

        for discovered in discovery.skills:
            try:
                existing = existing_by_upstream.get(discovered.upstream_id)
                if existing:
                    await self._update_skill(existing, discovered, source, commit_sha, now)
                    summary.skillsUpdated += 1
                    file_counts = await self._sync_skill_files(existing.id, discovered, now)
                    summary.filesUpdated += file_counts[0]
                    summary.filesCreated += file_counts[1]
                    summary.filesDeleted += file_counts[2]
                else:
                    new_skill = await self._create_skill(
                        discovered,
                        source,
                        commit_sha,
                        user_object_id,
                        user_id,
                        now,
                    )
                    summary.skillsCreated += 1
                    file_counts = await self._sync_skill_files(new_skill.id, discovered, now)
                    summary.filesCreated += file_counts[0] + file_counts[1]
            except Exception as exc:
                logger.exception("Failed to apply skill %s: %s", discovered.upstream_id, exc)
                summary.skillsFailed += 1
                job.skillErrors.append(
                    SkillSyncSkillError(
                        skillPath=discovered.upstream_id,
                        upstreamId=discovered.upstream_id,
                        errorCode=SkillSyncSkillErrorCode.WRITE_FAILED,
                        errorMessage=str(exc),
                        phase="apply",
                    )
                )

        return summary

    async def _create_skill(
        self,
        discovered: DiscoveredSkill,
        source: SkillSyncSource,
        commit_sha: str,
        author_id: PydanticObjectId,
        user_id: str,
        now: datetime,
    ) -> Skill:
        """Insert a new Skill + owner ACL entry in one transaction."""
        skill = Skill(
            name=discovered.name,
            displayTitle=discovered.display_title,
            description=discovered.description,
            body=discovered.body,
            frontmatter=discovered.frontmatter,
            category=discovered.category,
            alwaysApply=discovered.always_apply,
            userInvocable=discovered.user_invocable,
            disableModelInvocation=discovered.disable_model_invocation,
            allowedTools=discovered.allowed_tools,
            author=author_id,
            authorName="GitHub Sync",
            source=SkillSource.GITHUB,
            sourceMetadata={
                "sourceId": str(source.id),
                "upstreamId": discovered.upstream_id,
                "owner": source.owner,
                "repo": source.repo,
                "ref": source.ref,
                "commitSha": commit_sha,
            },
            path=discovered.upstream_id,
            tags=discovered.tags,
            enabled=True,
            createdByRegistry=True,
            fileCount=len(discovered.files),
            version=1,
            createdAt=now,
            updatedAt=now,
        )
        async with MongoDB.get_client().start_session() as session:
            async with await session.start_transaction():
                await skill.insert(session=session)
                await self._acl_service.grant_permission(
                    principal_type=PrincipalType.USER,
                    principal_id=author_id,
                    resource_type=RegistryResourceType.SKILL.value,
                    resource_id=skill.id,
                    perm_bits=RoleBits.OWNER,
                    session=session,
                )
        return skill

    async def _update_skill(
        self,
        existing: Skill,
        discovered: DiscoveredSkill,
        source: SkillSyncSource,
        commit_sha: str,
        now: datetime,
    ) -> None:
        existing.displayTitle = discovered.display_title
        existing.description = discovered.description
        existing.body = discovered.body
        existing.frontmatter = discovered.frontmatter
        existing.category = discovered.category
        existing.alwaysApply = discovered.always_apply
        existing.userInvocable = discovered.user_invocable
        existing.disableModelInvocation = discovered.disable_model_invocation
        existing.allowedTools = discovered.allowed_tools
        existing.tags = discovered.tags
        existing.fileCount = len(discovered.files)
        existing.path = discovered.upstream_id
        existing.sourceMetadata = {
            **(existing.sourceMetadata or {}),
            "commitSha": commit_sha,
            "owner": source.owner,
            "repo": source.repo,
            "ref": source.ref,
        }
        existing.version = (existing.version or 0) + 1
        existing.updatedAt = now
        await existing.save()

    async def _sync_skill_files(
        self,
        skill_id: PydanticObjectId,
        discovered: DiscoveredSkill,
        now: datetime,
    ) -> tuple[int, int, int]:
        """Sync auxiliary files for one skill within a single transaction."""
        existing_files = await SkillFile.find({"skillId": skill_id}).to_list()
        existing_by_path: dict[str, SkillFile] = {f.relativePath: f for f in existing_files}
        discovered_paths = set()
        updated = 0
        created = 0
        deleted = 0

        async with MongoDB.get_client().start_session() as session:
            async with await session.start_transaction():
                for df in discovered.files:
                    rel_path = df.relative_path
                    discovered_paths.add(rel_path)
                    mime_type = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
                    is_binary = not _is_text_content(df.content)
                    text_content = df.content.decode("utf-8", errors="replace") if not is_binary else None

                    if rel_path in existing_by_path:
                        ef = existing_by_path[rel_path]
                        ef.content = text_content
                        ef.body = df.content if is_binary else None
                        ef.mimeType = mime_type
                        ef.bytes = df.size
                        ef.isBinary = is_binary
                        ef.updatedAt = now
                        await ef.save(session=session)
                        updated += 1
                    else:
                        sf = SkillFile(
                            skillId=skill_id,
                            relativePath=rel_path,
                            source=_REGISTRY_FILE_SOURCE,
                            mimeType=mime_type,
                            bytes=df.size,
                            content=text_content,
                            body=df.content if is_binary else None,
                            isBinary=is_binary,
                            createdAt=now,
                            updatedAt=now,
                        )
                        await sf.insert(session=session)
                        created += 1

                for path, ef in existing_by_path.items():
                    if path not in discovered_paths:
                        await ef.delete(session=session)
                        deleted += 1

        return updated, created, deleted

    @staticmethod
    async def _finalize_job(
        job: SkillSyncJob,
        status: SkillSyncJobStatus,
        phase: SkillSyncJobPhase,
        *,
        error_code: SkillSyncJobErrorCode | None = None,
        error: str | None = None,
    ) -> None:
        job.status = status
        job.phase = phase
        job.errorCode = error_code.value if error_code else None
        job.error = error
        job.finishedAt = datetime.now(UTC)
        await job.save()


def _is_text_content(content: bytes) -> bool:
    if b"\x00" in content[:8192]:
        return False
    try:
        content[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
