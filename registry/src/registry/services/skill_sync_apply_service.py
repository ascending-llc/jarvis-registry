"""Persistence operations for applying discovered GitHub skills."""

from __future__ import annotations

import logging
import mimetypes
from datetime import UTC, datetime

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import ExtendedSkill as Skill
from registry_pkgs.models import ExtendedSkillFile as SkillFile
from registry_pkgs.models import PrincipalType, SkillSource
from registry_pkgs.models.enums import RoleBits, SkillSyncSkillErrorCode
from registry_pkgs.models.extended_access_role import RegistryAccessRole, RegistryResourceType
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry
from registry_pkgs.models.skill_sync_job import (
    SkillSyncApplySummary,
    SkillSyncFullRequestSnapshot,
    SkillSyncJob,
    SkillSyncSkillError,
)
from registry_pkgs.models.skill_sync_source import SkillSyncSource, SkillSyncSourceStats

from .access_control_service import ACLService
from .skill_sync_discovery_service import DiscoveredSkill, DiscoveryResult

logger = logging.getLogger(__name__)

_GITHUB_SYNC_FILE_SOURCE = "github-sync"
_ACL_INHERIT_BATCH_SIZE = 500


class SkillSyncApplyService:
    """Reconcile discovered repository content with live Skill persistence.

    This service computes create, update, and stale-delete operations by upstream identity,
    applies each Skill and its files in an isolated transaction, manages Skill ACL changes,
    and returns an apply summary while allowing sibling items to continue after failure.
    It does not own job leases, execution phases, credentials, or GitHub I/O.
    """

    def __init__(self, acl_service: ACLService) -> None:
        self._acl_service = acl_service

    async def apply_discovered_skills(
        self,
        *,
        source: SkillSyncSource,
        job: SkillSyncJob,
        discovery: DiscoveryResult,
        user_id: str,
        commit_sha: str,
        request_snapshot: SkillSyncFullRequestSnapshot,
    ) -> SkillSyncApplySummary:
        """Apply the repository snapshot as an item-isolated diff against live skills.

        A failed item is recorded and does not roll back successful siblings. Discovery
        errors with an upstream ID count as present, preventing parser failures from being
        misinterpreted as upstream deletion.
        """
        source_id_str = str(source.id)
        author_id = PydanticObjectId(user_id)
        summary = SkillSyncApplySummary()
        existing_skills = await self.list_live_skills(source.id)
        existing_by_upstream: dict[str, Skill] = {}
        for skill in existing_skills:
            upstream_id = (skill.sourceMetadata or {}).get("upstreamId")
            if not upstream_id:
                logger.warning("Skill %s missing upstreamId in sourceMetadata, skipping from sync matching", skill.id)
                continue
            existing_by_upstream[upstream_id] = skill

        present_upstream_ids = {f"{source_id_str}:{skill.upstream_id}" for skill in discovery.skills}
        # Preserve a previously synced skill when this run found it but could not parse it.
        present_upstream_ids.update(
            f"{source_id_str}:{error.upstreamId}" for error in discovery.errors if error.upstreamId
        )
        now = datetime.now(UTC)

        for upstream_id, existing_skill in existing_by_upstream.items():
            if upstream_id in present_upstream_ids:
                continue
            try:
                await self._delete_skill(existing_skill)
                summary.skillsDeleted += 1
            except Exception as exc:
                logger.exception("Failed to delete skill %s: %s", existing_skill.id, exc)
                summary.skillsFailed += 1
                metadata = existing_skill.sourceMetadata or {}
                job.skillErrors.append(
                    SkillSyncSkillError(
                        skillPath=metadata.get("skillPath", upstream_id),
                        upstreamId=metadata.get("upstreamId", upstream_id),
                        errorCode=SkillSyncSkillErrorCode.DELETE_FAILED,
                        errorMessage=str(exc),
                        phase="delete",
                    )
                )

        for discovered in discovery.skills:
            try:
                existing = existing_by_upstream.get(f"{source_id_str}:{discovered.upstream_id}")
                created, file_counts = await self._apply_discovered_skill(
                    existing=existing,
                    discovered=discovered,
                    source=source,
                    commit_sha=commit_sha,
                    request_snapshot=request_snapshot,
                    author_id=author_id,
                    now=now,
                )
                if created:
                    summary.skillsCreated += 1
                    summary.filesCreated += file_counts[0] + file_counts[1]
                else:
                    summary.skillsUpdated += 1
                    summary.filesUpdated += file_counts[0]
                    summary.filesCreated += file_counts[1]
                    summary.filesDeleted += file_counts[2]
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

    async def delete_source_skills(self, source: SkillSyncSource) -> SkillSyncApplySummary:
        """Delete every live child skill together with its files and ACL atomically."""
        summary = SkillSyncApplySummary()
        for skill in await self.list_live_skills(source.id):
            deleted_files = await self._delete_skill(skill)
            summary.skillsDeleted += 1
            summary.filesDeleted += deleted_files
        return summary

    @staticmethod
    async def list_live_skills(source_id: PydanticObjectId) -> list[Skill]:
        return await Skill.find({"source": SkillSource.GITHUB, "sourceMetadata.sourceId": str(source_id)}).to_list()

    @staticmethod
    async def build_source_stats(live_skills: list[Skill]) -> SkillSyncSourceStats:
        skill_ids = [skill.id for skill in live_skills]
        file_count = await SkillFile.find({"skillId": {"$in": skill_ids}}).count() if skill_ids else 0
        return SkillSyncSourceStats(skillCount=len(live_skills), fileCount=file_count)

    async def inherit_source_acl_to_skills(
        self,
        source: SkillSyncSource,
        skill_ids: list[PydanticObjectId],
    ) -> None:
        """Insert missing source ACL grants for child skills without overwriting grants."""
        if not skill_ids:
            return
        source_acl_entries = await RegistryAclEntry.find(
            {
                "resourceType": RegistryResourceType.SKILL_SYNC_SOURCE,
                "resourceId": source.id,
                "principalType": {"$ne": PrincipalType.PUBLIC},
                "principalId": {"$ne": None},
            }
        ).to_list()
        if not source_acl_entries:
            return
        existing_acl_entries = await RegistryAclEntry.find(
            {"resourceType": RegistryResourceType.SKILL, "resourceId": {"$in": skill_ids}}
        ).to_list()
        existing_index = {(str(entry.resourceId), str(entry.principalId)) for entry in existing_acl_entries}
        skill_roles = await RegistryAccessRole.find({"resourceType": RegistryResourceType.SKILL}).to_list()
        role_by_bits = {role.permBits: role.id for role in skill_roles}
        now = datetime.now(UTC)
        new_entries = []
        for skill_id in skill_ids:
            for source_entry in source_acl_entries:
                key = (str(skill_id), str(source_entry.principalId))
                if not source_entry.principalId or key in existing_index:
                    continue
                new_entries.append(
                    RegistryAclEntry(
                        principalType=source_entry.principalType,
                        principalId=source_entry.principalId,
                        resourceType=RegistryResourceType.SKILL,
                        resourceId=skill_id,
                        roleId=role_by_bits.get(source_entry.permBits),
                        permBits=source_entry.permBits,
                        grantedAt=now,
                        createdAt=now,
                        updatedAt=now,
                    )
                )
        for offset in range(0, len(new_entries), _ACL_INHERIT_BATCH_SIZE):
            await RegistryAclEntry.insert_many(new_entries[offset : offset + _ACL_INHERIT_BATCH_SIZE], ordered=False)

    async def _delete_skill(self, skill: Skill) -> int:
        """Remove one skill together with its files and ACL in one transaction.

        Hard delete matches how federation reconciles stale children and how a manual skill
        delete behaves, so no reader can resurrect a tombstone by forgetting a filter. Safe
        because an empty discovery never reaches apply and a skill this run failed to parse
        is never treated as stale.
        """
        async with MongoDB.get_client().start_session() as session:
            async with await session.start_transaction():
                result = await SkillFile.find({"skillId": skill.id}).delete(session=session)
                await skill.delete(session=session)
                await self._acl_service.delete_acl_entries_for_resource(
                    resource_type=RegistryResourceType.SKILL.value,
                    resource_id=skill.id,
                    session=session,
                )
        return result.deleted_count if result else 0

    async def _apply_discovered_skill(
        self,
        *,
        existing: Skill | None,
        discovered: DiscoveredSkill,
        source: SkillSyncSource,
        commit_sha: str,
        request_snapshot: SkillSyncFullRequestSnapshot,
        author_id: PydanticObjectId,
        now: datetime,
    ) -> tuple[bool, tuple[int, int, int]]:
        """Create or update one skill and synchronize all auxiliary files atomically."""
        async with MongoDB.get_client().start_session() as session:
            async with await session.start_transaction():
                if existing is None:
                    skill = await self._create_skill(
                        discovered, source, commit_sha, request_snapshot, author_id, now, session=session
                    )
                    created = True
                else:
                    await self._update_skill(existing, discovered, commit_sha, request_snapshot, now, session=session)
                    skill = existing
                    created = False
                file_counts = await self._sync_skill_files(skill.id, discovered, now, session=session)
        return created, file_counts

    async def _create_skill(
        self,
        discovered: DiscoveredSkill,
        source: SkillSyncSource,
        commit_sha: str,
        request_snapshot: SkillSyncFullRequestSnapshot,
        author_id: PydanticObjectId,
        now: datetime,
        *,
        session: AsyncClientSession,
    ) -> Skill:
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
                "provider": "github",
                "sourceId": str(source.id),
                "upstreamId": f"{source.id}:{discovered.upstream_id}",
                "skillPath": discovered.upstream_id,
                "owner": request_snapshot.owner,
                "repo": request_snapshot.repo,
                "ref": request_snapshot.ref,
                "commitSha": commit_sha,
                "syncedAt": now.isoformat(),
                "syncStatus": "synced",
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

    @staticmethod
    async def _update_skill(
        existing: Skill,
        discovered: DiscoveredSkill,
        commit_sha: str,
        request_snapshot: SkillSyncFullRequestSnapshot,
        now: datetime,
        *,
        session: AsyncClientSession,
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
            "owner": request_snapshot.owner,
            "repo": request_snapshot.repo,
            "ref": request_snapshot.ref,
            "syncedAt": now.isoformat(),
            "syncStatus": "synced",
        }
        existing.version = (existing.version or 0) + 1
        existing.updatedAt = now
        await existing.save(session=session)

    @staticmethod
    async def _sync_skill_files(
        skill_id: PydanticObjectId,
        discovered: DiscoveredSkill,
        now: datetime,
        *,
        session: AsyncClientSession,
    ) -> tuple[int, int, int]:
        existing_files = await SkillFile.find({"skillId": skill_id}, session=session).to_list()
        existing_by_path = {skill_file.relativePath: skill_file for skill_file in existing_files}
        discovered_paths: set[str] = set()
        updated = created = deleted = 0
        for auxiliary_file in discovered.files:
            relative_path = auxiliary_file.relative_path
            discovered_paths.add(relative_path)
            content = auxiliary_file.absolute_path.read_bytes()
            mime_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
            is_binary = not _is_text_content(content)
            text_content = content.decode("utf-8", errors="replace") if not is_binary else None
            if relative_path in existing_by_path:
                existing_file = existing_by_path[relative_path]
                existing_file.content = text_content
                existing_file.body = content if is_binary else None
                existing_file.mimeType = mime_type
                existing_file.bytes = auxiliary_file.size
                existing_file.isBinary = is_binary
                existing_file.updatedAt = now
                await existing_file.save(session=session)
                updated += 1
                continue
            await SkillFile(
                skillId=skill_id,
                relativePath=relative_path,
                source=_GITHUB_SYNC_FILE_SOURCE,
                mimeType=mime_type,
                bytes=auxiliary_file.size,
                content=text_content,
                body=content if is_binary else None,
                isBinary=is_binary,
                createdAt=now,
                updatedAt=now,
            ).insert(session=session)
            created += 1
        for path, existing_file in existing_by_path.items():
            if path not in discovered_paths:
                await existing_file.delete(session=session)
                deleted += 1
        return updated, created, deleted


def _is_text_content(content: bytes) -> bool:
    if b"\x00" in content[:8192]:
        return False
    try:
        content[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
