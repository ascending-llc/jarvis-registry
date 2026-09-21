"""Lifecycle entry points for skill sync sources and jobs."""

from dataclasses import dataclass

import httpx
from beanie import PydanticObjectId
from fastapi import HTTPException
from fastapi import status as http_status

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import PrincipalType
from registry_pkgs.models.enums import RoleBits, SkillSyncJobErrorCode, SkillSyncJobType, SkillSyncTriggerType
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.skill_sync_job import (
    SkillSyncDeleteRequestSnapshot,
    SkillSyncFullRequestSnapshot,
    SkillSyncJob,
)
from registry_pkgs.models.skill_sync_source import SkillSyncSource

from ..schemas.errors import ErrorCode, create_error_detail
from ..utils.crypto_utils import decrypt_value
from .access_control_service import ACLService
from .skill_sync_github_service import GitHubDownloadError, SkillSyncGitHubService
from .skill_sync_job_service import SkillSyncJobService
from .skill_sync_source_crud_service import SkillSyncSourceCrudService
from .skill_sync_token_service import SkillSyncTokenService

# Sized for the interactive dry-run test-connect request; the shared GitHub client's default
# (30s connect / 60s read) is tuned for tarball downloads and too slow for a synchronous check.
_TEST_CONNECTION_GITHUB_TIMEOUT = httpx.Timeout(10.0)


@dataclass
class SyncTriggerResult:
    job: SkillSyncJob | None = None


@dataclass
class ConnectionCheckResult:
    ok: bool = False
    needs_authorization: bool = False
    detail: str | None = None


class SkillSyncService:
    """Persist request-facing source lifecycle commands and immutable job requests.

    This facade performs authorization preflight, then atomically pairs source state
    transitions with job creation. It never claims or executes jobs and never mutates
    synchronized skills; those responsibilities belong to the runner and execution pipeline.
    It also performs a read-only GitHub connectivity check (`test_connection`) for dry-run
    test-connect requests, which neither mutates source state nor creates a job.
    """

    def __init__(
        self,
        *,
        source_crud_service: SkillSyncSourceCrudService,
        job_service: SkillSyncJobService,
        token_service: SkillSyncTokenService,
        github_service: SkillSyncGitHubService,
    ) -> None:
        self._source_crud_service = source_crud_service
        self._job_service = job_service
        self._token_service = token_service
        self._github_service = github_service

    async def _resolve_access_token_or_none(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
    ) -> str | None:
        """Decrypt the stored client secret and resolve a usable OAuth access token, if any."""
        client_secret = decrypt_value(source.githubAppClientSecretEncrypted)
        return await self._token_service.resolve_access_token(
            user_id=user_id,
            source_id=source.id,
            client_id=source.githubAppClientId,
            client_secret=client_secret,
        )

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
    ) -> SyncTriggerResult:
        """Authorize the user and atomically move the source to pending with an immutable job.

        Token resolution deliberately happens before the transaction so a user who must
        re-authorize does not leave behind a pending source with no executable job.
        """
        access_token = await self._resolve_access_token_or_none(source=source, user_id=user_id)
        if access_token is None:
            return SyncTriggerResult()

        # The pending source and its job are one invariant: neither may be visible alone.
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                source = await self._source_crud_service.mark_sync_pending(source, session=mongo_session)
                job = await self._job_service.create_job(
                    source_id=source.id,
                    job_type=SkillSyncJobType.FULL_SYNC,
                    trigger_type=trigger_type,
                    triggered_by=user_id,
                    # Workers consume this snapshot, never the source's mutable repository fields.
                    request_snapshot=SkillSyncFullRequestSnapshot(
                        owner=source.owner,
                        repo=source.repo,
                        ref=source.ref,
                        paths=source.paths,
                        configRevision=source.configRevision,
                    ),
                    session=mongo_session,
                )
        return SyncTriggerResult(job=job)

    async def test_connection(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
    ) -> ConnectionCheckResult:
        """Read-only pre-flight: verify the OAuth token plus repo/ref are reachable.

        This never marks the source pending, never creates a job, and never mutates
        synchronized skills; it is the non-destructive dry-run counterpart of trigger_sync.
        """
        access_token = await self._resolve_access_token_or_none(source=source, user_id=user_id)
        if access_token is None:
            return ConnectionCheckResult(needs_authorization=True)

        try:
            await self._github_service.resolve_commit_sha(
                owner=source.owner,
                repo=source.repo,
                ref=source.ref,
                access_token=access_token,
                timeout=_TEST_CONNECTION_GITHUB_TIMEOUT,
            )
        except GitHubDownloadError as exc:
            if exc.error_code == SkillSyncJobErrorCode.GITHUB_AUTH_FAILED:
                # A locally-unexpired but revoked/insufficient token surfaces as an auth
                # failure; route the user back through OAuth rather than reporting a
                # generic config error.
                return ConnectionCheckResult(needs_authorization=True, detail=str(exc))
            if exc.error_code == SkillSyncJobErrorCode.GITHUB_RATE_LIMITED:
                raise HTTPException(
                    http_status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=create_error_detail(ErrorCode.RATE_LIMITED, str(exc)),
                ) from exc
            if exc.error_code == SkillSyncJobErrorCode.DOWNLOAD_FAILED:
                raise HTTPException(
                    http_status.HTTP_502_BAD_GATEWAY,
                    detail=create_error_detail(ErrorCode.EXTERNAL_SERVICE_ERROR, str(exc)),
                ) from exc
            return ConnectionCheckResult(ok=False, detail=str(exc))
        return ConnectionCheckResult(ok=True)

    async def delete_source_with_skills(
        self,
        *,
        source: SkillSyncSource,
        user_id: str,
    ) -> tuple[SkillSyncJob, SkillSyncSource]:
        """Persist a delete job for execution by the durable runner."""
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                source = await self._source_crud_service.mark_deleting(source, session=mongo_session)
                job = await self._job_service.create_job(
                    source_id=source.id,
                    job_type=SkillSyncJobType.DELETE_SYNC,
                    trigger_type=SkillSyncTriggerType.MANUAL,
                    triggered_by=user_id,
                    request_snapshot=SkillSyncDeleteRequestSnapshot(configRevision=source.configRevision),
                    session=mongo_session,
                )
        return job, source
