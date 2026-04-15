import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from registry_pkgs.database.decorators import get_current_session, use_transaction
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, PrincipalType, ResourceType
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobType,
    FederationProviderType,
    FederationSyncStatus,
    FederationTriggerType,
    RoleBits,
)
from registry_pkgs.models.extended_acl_entry import ExtendedResourceType
from registry_pkgs.models.federation import (
    Federation,
    FederationLastSync,
    FederationLastSyncSummary,
    FederationStats,
)
from registry_pkgs.models.federation_sync_job import FederationApplySummary, FederationSyncJob

from .agentcore_import_service import AgentCoreImportService
from .federation.federation_handlers import (
    AwsAgentCoreSyncHandler,
    BaseFederationSyncHandler,
)
from .federation_crud_service import FederationCrudService
from .federation_job_service import FederationJobService

logger = logging.getLogger(__name__)


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@dataclass
class FederationSyncMutationResult:
    """Capture Mongo apply results that drive post-commit vector repair."""

    summary: FederationApplySummary
    stats: FederationStats | None = None
    last_sync: FederationLastSync | None = None
    changed_mcp_runtime_arns: set[str] = field(default_factory=set)
    changed_a2a_runtime_arns: set[str] = field(default_factory=set)


@dataclass
class FederationSyncPlan:
    """Read-only diff plan shared by dry-run preview and real apply."""

    summary: FederationApplySummary
    federation_id: Any
    provider_type: Any
    discovered_mcp_count: int
    discovered_a2a_count: int
    mcp_creates: list[tuple[Any, str]] = field(default_factory=list)
    mcp_updates: list[tuple[Any, Any, str]] = field(default_factory=list)
    mcp_deletes: list[tuple[Any, str | None]] = field(default_factory=list)
    a2a_creates: list[tuple[Any, str]] = field(default_factory=list)
    a2a_updates: list[tuple[Any, Any, str]] = field(default_factory=list)
    a2a_deletes: list[tuple[Any, str | None]] = field(default_factory=list)


@dataclass
class FederationSyncPreviewResult:
    provider_type: Any
    provider_config: dict[str, Any]
    summary: FederationApplySummary
    discovered_mcp_count: int
    discovered_a2a_count: int
    message: str | None = None


class FederationSyncService:
    def __init__(
        self,
        federation_crud_service: FederationCrudService,
        federation_job_service: FederationJobService,
        mcp_server_repo,
        a2a_agent_repo,
        acl_service,
        user_service,
    ):
        self.federation_crud_service = federation_crud_service
        self.federation_job_service = federation_job_service
        self.mcp_server_repo = mcp_server_repo
        self.a2a_agent_repo = a2a_agent_repo
        self.acl_service = acl_service
        self.user_service = user_service

        self.sync_handlers: dict[FederationProviderType, BaseFederationSyncHandler] = {
            FederationProviderType.AWS_AGENTCORE: AwsAgentCoreSyncHandler(),
        }

    def get_sync_handler(self, provider_type: FederationProviderType) -> BaseFederationSyncHandler:
        if provider_type == FederationProviderType.AZURE_AI_FOUNDRY:
            raise ValueError("Azure AI Foundry federation sync is not implemented yet")
        handler = self.sync_handlers.get(provider_type)
        if handler is None:
            raise ValueError(f"Unsupported federation provider type: {provider_type}")
        return handler

    async def _discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        # Provider dispatch happens here. The federation already owns the
        # provider type and normalized provider config, so the sync service only
        # needs to select the correct handler and delegate discovery.
        handler = self.get_sync_handler(federation.providerType)
        logger.info("Dispatching federation %s sync to provider handler %s", federation.id, handler.__class__.__name__)
        return await handler.discover_entities(federation)

    @staticmethod
    def _resolve_job_started_at(job: FederationSyncJob) -> datetime:
        started_at = getattr(job, "startedAt", None)
        if started_at is not None:
            return started_at
        created_at = getattr(job, "createdAt", None)
        if created_at is not None:
            return created_at
        return datetime.now(UTC)

    @classmethod
    def _build_pending_last_sync(cls, job: FederationSyncJob) -> FederationLastSync:
        return FederationLastSync(
            jobId=job.id,
            jobType=job.jobType,
            status=FederationSyncStatus.PENDING,
            startedAt=cls._resolve_job_started_at(job),
            finishedAt=None,
        )

    @classmethod
    def _build_syncing_last_sync(cls, job: FederationSyncJob) -> FederationLastSync:
        return FederationLastSync(
            jobId=job.id,
            jobType=job.jobType,
            status=FederationSyncStatus.SYNCING,
            startedAt=cls._resolve_job_started_at(job),
            finishedAt=None,
        )

    @classmethod
    def _build_failed_last_sync(cls, job: FederationSyncJob, error_message: str) -> FederationLastSync:
        return FederationLastSync(
            jobId=job.id,
            jobType=job.jobType,
            status=FederationSyncStatus.FAILED,
            startedAt=cls._resolve_job_started_at(job),
            finishedAt=datetime.now(UTC),
            summary=FederationLastSyncSummary(
                errors=1,
                errorMessages=[error_message],
            ),
        )

    async def run_sync(
        self,
        federation: Federation,
        job: FederationSyncJob,
        user_id: str | None,
    ) -> FederationSyncJob:
        """
        Sync execution follows a fixed flow:
            1. discover remote resources
            2. apply federation/job/resource mutations in one transaction
            3. persist stats and lastSync in the same transaction
            4. rebuild vector indexes outside the Mongo transaction

        Mongo remains the source of truth, so vector rebuild still happens after
        the transaction commits. However, vector sync is part of the externally
        observed federation sync contract: if it fails, we surface the failure
        to callers and move both the federation and the job into failed state
        even though the Mongo transaction has already committed.
        """
        try:
            discovered = await self._discover_entities(federation)
            mutation_result = await self._commit_sync_transaction(
                federation=federation,
                job=job,
                discovered=discovered,
                user_id=user_id,
            )
            if mutation_result.summary.errorMessages:
                return job
            await self._sync_vector_index_after_commit(
                federation=federation,
                job=job,
                mutation_result=mutation_result,
            )
            if mutation_result.last_sync is None or mutation_result.stats is None:
                raise RuntimeError("Federation sync completed without final stats or lastSync payload")
            await self.federation_crud_service.mark_sync_success(
                federation,
                mutation_result.last_sync,
                mutation_result.stats,
            )
            await self.federation_job_service.mark_success(job)
            return job

        except Exception as exc:
            logger.exception("Failed to run federation sync")
            await self.federation_crud_service.mark_sync_failed(
                federation,
                str(exc),
                last_sync=self._build_failed_last_sync(job, str(exc)),
            )
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, str(exc))
            raise

    async def preview_manual_sync(
        self,
        *,
        federation: Federation,
        reason: str | None,
        triggered_by: str | None,
    ) -> FederationSyncPreviewResult:
        """Run provider discovery and local diff without mutating persisted state."""
        del reason, triggered_by
        discovered = await self._discover_entities(federation)
        sync_plan = await self._build_sync_plan(
            federation=federation,
            discovered_mcp=discovered.get("mcp_servers", []),
            discovered_a2a=discovered.get("a2a_agents", []),
        )
        message = None
        if sync_plan.summary.errorMessages:
            message = self._summarize_sync_errors(sync_plan.summary.errorMessages)
        return FederationSyncPreviewResult(
            provider_type=federation.providerType,
            provider_config=dict(federation.providerConfig or {}),
            summary=sync_plan.summary,
            discovered_mcp_count=sync_plan.discovered_mcp_count,
            discovered_a2a_count=sync_plan.discovered_a2a_count,
            message=message,
        )

    async def update_federation_with_optional_resync(
        self,
        *,
        federation: Federation,
        display_name: str,
        description: str | None,
        tags: list[str],
        provider_config: dict[str, Any],
        updated_by: str | None,
        sync_after_update: bool,
    ) -> tuple[Federation, FederationSyncJob | None]:
        """Update federation metadata and optionally run a config-driven resync.

        A plain update remains a single federation write. When provider config
        changes and the caller requests a resync, we first commit the updated
        federation definition plus a pending resync job, then execute the sync
        as a separate phase.
        """

        normalized_provider_config = self.federation_crud_service.validate_provider_config(
            federation.providerType,
            provider_config,
        )
        need_resync = bool(sync_after_update and dict(federation.providerConfig or {}) != normalized_provider_config)

        if not need_resync:
            updated = await self.federation_crud_service.update_federation(
                federation=federation,
                display_name=display_name,
                description=description,
                tags=tags,
                provider_config=provider_config,
                updated_by=updated_by,
            )
            return updated, None

        active_job = await self.federation_job_service.get_active_job(federation.id)
        if active_job:
            raise ValueError("Federation already has an active sync job")

        federation, job = await self.update_federation_and_create_resync_job(
            federation=federation,
            display_name=display_name,
            description=description,
            tags=tags,
            normalized_provider_config=normalized_provider_config,
            updated_by=updated_by,
        )
        await self.run_sync(
            federation=federation,
            job=job,
            user_id=updated_by,
        )
        return federation, job

    @use_transaction
    async def _commit_sync_transaction(
        self,
        *,
        federation: Federation,
        job: FederationSyncJob,
        discovered: dict[str, list[Any]],
        user_id: str | None,
    ) -> FederationSyncMutationResult:
        """Apply the discovered federation state in one Mongo transaction."""
        discovered_mcp = discovered.get("mcp_servers", [])
        discovered_a2a = discovered.get("a2a_agents", [])

        await self.federation_job_service.mark_syncing(job, FederationJobPhase.DISCOVERING)
        await self.federation_crud_service.mark_syncing(
            federation,
            last_sync=self._build_syncing_last_sync(job),
        )
        await self.federation_job_service.update_discovery_summary(
            job,
            discovered_mcp_servers=len(discovered_mcp),
            discovered_agents=len(discovered_a2a),
        )
        await self.federation_job_service.mark_syncing(job, FederationJobPhase.APPLYING)
        sync_plan = await self._build_sync_plan(
            federation=federation,
            discovered_mcp=discovered_mcp,
            discovered_a2a=discovered_a2a,
        )
        mutation_result = await self._apply_sync_plan(sync_plan, user_id=user_id)
        await self.federation_job_service.update_apply_summary(job, mutation_result.summary)
        stats = await self._build_federation_stats(federation.id)
        last_sync = self._build_last_sync(job, mutation_result.summary)
        mutation_result.stats = stats
        mutation_result.last_sync = last_sync
        if mutation_result.summary.errorMessages:
            failure_message = self._summarize_sync_errors(mutation_result.summary.errorMessages)
            await self.federation_crud_service.mark_sync_failed(
                federation,
                failure_message,
                last_sync=last_sync,
                stats=stats,
            )
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, failure_message)
        return mutation_result

    @use_transaction
    async def update_federation_and_create_resync_job(
        self,
        *,
        federation: Federation,
        display_name: str,
        description: str | None,
        tags: list[str],
        normalized_provider_config: dict[str, Any],
        updated_by: str | None,
    ) -> tuple[Federation, FederationSyncJob]:
        """Persist the new federation definition and its pending resync job together."""
        federation = await self.federation_crud_service.update_federation(
            federation=federation,
            display_name=display_name,
            description=description,
            tags=tags,
            provider_config=normalized_provider_config,
            updated_by=updated_by,
        )
        job = await self.federation_job_service.create_job(
            federation_id=federation.id,
            job_type=FederationJobType.CONFIG_RESYNC,
            trigger_type=FederationTriggerType.API,
            triggered_by=updated_by,
            request_snapshot={
                "providerType": _enum_value(federation.providerType),
                "providerConfig": federation.providerConfig,
            },
        )
        await self.federation_crud_service.mark_sync_pending(
            federation,
            last_sync=self._build_pending_last_sync(job),
        )
        return federation, job

    @use_transaction
    async def create_sync_job_and_mark_pending(
        self,
        *,
        federation: Federation,
        job_type: FederationJobType,
        trigger_type: FederationTriggerType,
        triggered_by: str | None,
        request_snapshot: dict[str, Any],
    ) -> FederationSyncJob:
        """Create the sync job and move the federation into pending in one transaction."""
        job = await self.federation_job_service.create_job(
            federation_id=federation.id,
            job_type=job_type,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            request_snapshot=request_snapshot,
        )
        await self.federation_crud_service.mark_sync_pending(
            federation,
            last_sync=self._build_pending_last_sync(job),
        )
        return job

    async def start_manual_sync(
        self,
        *,
        federation: Federation,
        reason: str | None,
        triggered_by: str | None,
    ) -> FederationSyncJob:
        """Start a user-triggered sync using the shared pending-job then run-sync flow."""
        active_job = await self.federation_job_service.get_active_job(federation.id)
        if active_job:
            raise ValueError("Federation already has an active sync job")

        job = await self.create_sync_job_and_mark_pending(
            federation=federation,
            job_type=FederationJobType.FULL_SYNC,
            trigger_type=FederationTriggerType.MANUAL,
            triggered_by=triggered_by,
            request_snapshot={
                "providerType": _enum_value(federation.providerType),
                "providerConfig": federation.providerConfig,
                "reason": reason,
            },
        )
        await self.run_sync(
            federation=federation,
            job=job,
            user_id=triggered_by,
        )
        return job

    async def start_delete(
        self,
        *,
        federation: Federation,
        triggered_by: str | None,
    ) -> FederationSyncJob:
        """Register the delete job and then execute the delete apply phase."""
        active_job = await self.federation_job_service.get_active_job(federation.id)
        if active_job:
            raise ValueError("Federation already has an active job")

        await self.federation_crud_service.mark_deleting(federation)
        job = await self.federation_job_service.create_job(
            federation_id=federation.id,
            job_type=FederationJobType.DELETE_SYNC,
            trigger_type=FederationTriggerType.MANUAL,
            triggered_by=triggered_by,
            request_snapshot={
                "providerType": _enum_value(federation.providerType),
                "providerConfig": federation.providerConfig,
            },
        )
        await self.run_delete(federation=federation, job=job)
        return job

    async def _build_sync_plan(
        self,
        *,
        federation: Federation,
        discovered_mcp: list[Any],
        discovered_a2a: list[Any],
    ) -> FederationSyncPlan:
        """Compare discovered resources against Mongo state without mutating it."""
        # Step 1: initialize the plan and summary.
        apply_summary = FederationApplySummary()
        sync_plan = FederationSyncPlan(
            summary=apply_summary,
            federation_id=federation.id,
            provider_type=federation.providerType,
            discovered_mcp_count=len(discovered_mcp),
            discovered_a2a_count=len(discovered_a2a),
        )
        session = get_current_session()

        # Step 2: load current MCP and A2A state for this federation.
        existing_mcp = await ExtendedMCPServer.find({"federationRefId": federation.id}, session=session).to_list()
        existing_mcp_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
        }
        existing_a2a = await A2AAgent.find({"federationRefId": federation.id}, session=session).to_list()
        existing_a2a_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
        }

        # Step 3: pre-compute global uniqueness conflicts for both resource types.
        # serverName (MCP) and path (A2A) are globally unique across all federations.
        # Detecting conflicts here — before the classification loops — means both
        # dry-run and real sync surface them without reaching _apply_sync_plan.
        create_candidate_names = [
            item.serverName
            for item in discovered_mcp
            if item.serverName and self._extract_runtime_arn(item.federationMetadata) not in existing_mcp_by_remote
        ]
        existing_mcp_by_server_name: dict[str, Any] = {}
        if create_candidate_names:
            existing_mcp_by_server_name = {
                doc.serverName: doc
                for doc in await ExtendedMCPServer.find(
                    {"serverName": {"$in": create_candidate_names}},
                    session=session,
                ).to_list()
            }

        discovered_a2a_paths = sorted({item.path for item in discovered_a2a if getattr(item, "path", None)})
        existing_a2a_by_path: dict[str, A2AAgent] = {}
        if discovered_a2a_paths:
            existing_a2a_by_path = {
                item.path: item
                for item in await A2AAgent.find({"path": {"$in": discovered_a2a_paths}}, session=session).to_list()
            }

        # Step 4: classify discovered MCP items.
        discovered_mcp_ids: set[str] = set()

        for item in discovered_mcp:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_mcp_ids.add(remote_id)
            existing = existing_mcp_by_remote.get(remote_id)

            if existing is None:
                name_conflict = existing_mcp_by_server_name.get(item.serverName)
                if name_conflict is not None and name_conflict.federationRefId != federation.id:
                    # Cross-federation conflict: another federation already owns this serverName — skip.
                    logger.warning(
                        "Skipping MCP server due to global serverName conflict: "
                        "serverName=%s already claimed by federation=%s",
                        item.serverName,
                        name_conflict.federationRefId,
                    )
                    apply_summary.skippedMcpServers += 1
                    #  The `name_conflict` field is not considered an error record and is skipped; it is only logged.
                    logger.warning(
                        f"MCP server '{item.serverName}' skipped: serverName already exists "
                        f"(owned by federation {name_conflict.federationRefId or 'unknown'})"
                    )
                    continue
                # Same-federation name match with a different runtimeArn: the old doc will be
                # deleted first in _apply_sync_plan (deletes run before creates), so INSERT is safe.

            if existing is None:
                apply_summary.createdMcpServers += 1
                sync_plan.mcp_creates.append((item, remote_id))
            else:
                if not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata):
                    apply_summary.unchangedMcpServers += 1
                else:
                    apply_summary.updatedMcpServers += 1
                    sync_plan.mcp_updates.append((existing, item, remote_id))

            error_message = self._extract_resource_error(item)
            if error_message:
                self._record_apply_error(
                    apply_summary,
                    f"MCP server {getattr(item, 'serverName', remote_id)}: {error_message}",
                )

        # Step 5: mark stale MCP items.
        stale_mcp = [
            item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
            and self._extract_runtime_arn(item.federationMetadata) not in discovered_mcp_ids
        ]
        for stale in stale_mcp:
            apply_summary.deletedMcpServers += 1
            sync_plan.mcp_deletes.append((stale, self._extract_runtime_arn(stale.federationMetadata)))

        # Step 6: classify discovered A2A items and check path conflicts.
        discovered_a2a_ids: set[str] = set()
        planned_a2a_by_remote: dict[str, A2AAgent] = {}

        for item in discovered_a2a:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_a2a_ids.add(remote_id)
            existing = existing_a2a_by_remote.get(remote_id) or planned_a2a_by_remote.get(remote_id)
            path_conflict = existing_a2a_by_path.get(item.path) if getattr(item, "path", None) else None

            if existing is None and path_conflict is not None:
                if path_conflict.federationRefId != federation.id:
                    # Cross-federation conflict: another federation already owns this path — skip.
                    agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                    logger.warning(
                        "Skipping federated A2A sync because path is already owned by another agent: "
                        "federation_id=%s runtime_arn=%s path=%s existing_agent_id=%s existing_federation_ref_id=%s",
                        federation.id,
                        remote_id,
                        item.path,
                        getattr(path_conflict, "id", None),
                        path_conflict.federationRefId,
                    )
                    apply_summary.skippedAgents += 1
                    logger.warning(
                        f"A2A agent '{agent_name}' skipped: path '{item.path}' already exists "
                        f"(owned by federation {path_conflict.federationRefId or 'unknown'})"
                    )
                    continue
                # Same-federation path match with a different runtimeArn: the old doc will be
                # deleted first in _apply_sync_plan (deletes run before creates), so INSERT is safe.

            if existing is None:
                apply_summary.createdAgents += 1
                sync_plan.a2a_creates.append((item, remote_id))
                planned_a2a_by_remote[remote_id] = item
            else:
                existing_id = getattr(existing, "id", None)
                path_conflict_id = getattr(path_conflict, "id", None)
                if (
                    existing.path != item.path
                    and path_conflict is not None
                    and (existing_id is None or path_conflict_id is None or path_conflict_id != existing_id)
                ):
                    agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                    logger.warning(
                        "Skipping federated A2A update because target path is already owned by another agent: "
                        "federation_id=%s runtime_arn=%s existing_agent_id=%s existing_path=%s target_path=%s "
                        "conflict_agent_id=%s conflict_federation_ref_id=%s",
                        federation.id,
                        remote_id,
                        getattr(existing, "id", None),
                        existing.path,
                        item.path,
                        getattr(path_conflict, "id", None),
                        path_conflict.federationRefId,
                    )
                    apply_summary.skippedAgents += 1
                    logger.warning(
                        f"A2A agent '{agent_name}' skipped: path '{item.path}' already exists "
                        f"(owned by federation {path_conflict.federationRefId or 'unknown'})"
                    )
                    continue
                if not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata):
                    apply_summary.unchangedAgents += 1
                else:
                    apply_summary.updatedAgents += 1
                    sync_plan.a2a_updates.append((existing, item, remote_id))
                    if getattr(item, "path", None):
                        existing_a2a_by_path[item.path] = existing

            error_message = self._extract_resource_error(item)
            if error_message:
                agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                self._record_apply_error(
                    apply_summary,
                    f"A2A agent {agent_name}: {error_message}",
                )

        # Step 7: mark stale A2A items.
        stale_a2a = [
            item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
            and self._extract_runtime_arn(item.federationMetadata) not in discovered_a2a_ids
        ]
        for stale in stale_a2a:
            apply_summary.deletedAgents += 1
            sync_plan.a2a_deletes.append((stale, self._extract_runtime_arn(stale.federationMetadata)))

        return sync_plan

    async def _apply_sync_plan(
        self,
        sync_plan: FederationSyncPlan,
        *,
        user_id: str | None,
    ) -> FederationSyncMutationResult:
        """Apply a previously computed sync plan inside the current transaction."""
        session = get_current_session()
        mutation_result = FederationSyncMutationResult(summary=sync_plan.summary)

        # Deletes run first so that unique-indexed fields (serverName, path) are freed
        # before new docs with the same name/path are inserted.
        for stale, stale_runtime_arn in sync_plan.mcp_deletes:
            await stale.delete(session=session)
            if stale_runtime_arn:
                mutation_result.changed_mcp_runtime_arns.add(stale_runtime_arn)

        for stale, stale_runtime_arn in sync_plan.a2a_deletes:
            await stale.delete(session=session)
            if stale_runtime_arn:
                mutation_result.changed_a2a_runtime_arns.add(stale_runtime_arn)

        for server, remote_id in sync_plan.mcp_creates:
            server.federationRefId = sync_plan.federation_id
            server.federationMetadata = server.federationMetadata or {}
            server.federationMetadata["providerType"] = _enum_value(sync_plan.provider_type)
            await server.insert(session=session)
            mutation_result.changed_mcp_runtime_arns.add(remote_id)
            await self._grant_owner(user_id, ResourceType.MCPSERVER, server.id)

        for agent, remote_id in sync_plan.a2a_creates:
            agent.federationRefId = sync_plan.federation_id
            agent.federationMetadata = agent.federationMetadata or {}
            agent.federationMetadata["providerType"] = _enum_value(sync_plan.provider_type)
            await agent.insert(session=session)
            mutation_result.changed_a2a_runtime_arns.add(remote_id)
            await self._grant_owner(user_id, ResourceType.REMOTE_AGENT, agent.id)

        for existing, item, remote_id in sync_plan.mcp_updates:
            existing.serverName = item.serverName
            existing.path = item.path
            existing.tags = list(item.tags or [])
            existing.config = dict(item.config or {})
            existing.status = item.status or existing.status
            existing.numTools = item.numTools
            existing.federationMetadata = item.federationMetadata
            await existing.save(session=session)
            mutation_result.changed_mcp_runtime_arns.add(remote_id)
            await self._grant_owner(user_id, ResourceType.MCPSERVER, existing.id)

        for existing, item, remote_id in sync_plan.a2a_updates:
            existing.path = item.path
            existing.card = item.card
            existing.tags = list(item.tags or [])
            existing.status = item.status
            existing.isEnabled = item.isEnabled
            existing.wellKnown = item.wellKnown
            existing.federationMetadata = item.federationMetadata
            await existing.save(session=session)
            mutation_result.changed_a2a_runtime_arns.add(remote_id)
            await self._grant_owner(user_id, ResourceType.REMOTE_AGENT, existing.id)

        return mutation_result

    async def _grant_owner(self, user_id: str | None, resource_type: ResourceType, resource_id: Any) -> None:
        """Grant OWNER ACL to the syncing user for a federation-imported resource.

        No-op when user_id is absent (e.g. system/scheduled syncs).
        Idempotent: acl_service.grant_permission uses upsert internally,
        so calling this on an UPDATE safely adds the new syncer as a co-owner
        without affecting existing owners.
        """
        if not user_id:
            return
        try:
            await self.acl_service.grant_permission(
                principal_type=PrincipalType.USER,
                principal_id=user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                perm_bits=RoleBits.OWNER,
            )
        except Exception as e:
            logger.exception(
                "Failed to grant OWNER ACL for user=%s resource_type=%s resource_id=%s e=%s",
                user_id,
                resource_type,
                resource_id,
                str(e),
            )

    async def _sync_vector_index_after_commit(
        self,
        *,
        federation: Federation,
        job: FederationSyncJob,
        mutation_result: FederationSyncMutationResult,
    ) -> None:
        """Refresh only the changed runtime docs in Weaviate after Mongo commit.

        This runs outside the transaction on purpose: vector storage is a
        secondary index, not the source of truth. Replaying this step is safe
        because vector docs are deleted and rebuilt from persisted Mongo state
        after commit.
        """
        errors: list[str] = []
        current_mcp_runtime_arns = {
            runtime_arn for runtime_arn in await self._current_mcp_runtime_arns(federation.id) if runtime_arn
        }
        current_a2a_runtime_arns = {
            runtime_arn for runtime_arn in await self._current_a2a_runtime_arns(federation.id) if runtime_arn
        }
        missing_mcp_runtime_arns = {
            runtime_arn
            for runtime_arn in current_mcp_runtime_arns
            if not self.mcp_server_repo.has_runtime_identity(str(federation.id), runtime_arn)
        }
        missing_a2a_runtime_arns = {
            runtime_arn
            for runtime_arn in current_a2a_runtime_arns
            if not self.a2a_agent_repo.has_runtime_identity(str(federation.id), runtime_arn)
        }
        mcp_runtime_arns_to_rebuild = mutation_result.changed_mcp_runtime_arns | missing_mcp_runtime_arns
        a2a_runtime_arns_to_rebuild = mutation_result.changed_a2a_runtime_arns | missing_a2a_runtime_arns

        logger.info(
            "Federation vector sync plan: federation_id=%s job_id=%s "
            "mcp_checked=%d mcp_changed=%d mcp_missing=%d mcp_rebuild=%d collection=%s "
            "a2a_checked=%d a2a_changed=%d a2a_missing=%d a2a_rebuild=%d collection=%s",
            federation.id,
            job.id,
            len(current_mcp_runtime_arns),
            len(mutation_result.changed_mcp_runtime_arns),
            len(missing_mcp_runtime_arns),
            len(mcp_runtime_arns_to_rebuild),
            getattr(self.mcp_server_repo, "collection", "MCP_Servers"),
            len(current_a2a_runtime_arns),
            len(mutation_result.changed_a2a_runtime_arns),
            len(missing_a2a_runtime_arns),
            len(a2a_runtime_arns_to_rebuild),
            getattr(self.a2a_agent_repo, "collection", "A2a_agents"),
        )

        for runtime_arn in sorted(mcp_runtime_arns_to_rebuild):
            try:
                await self._sync_mcp_vectors_for_runtime(federation.id, runtime_arn)
            except Exception as exc:
                errors.append(f"mcp runtime rebuild failed:{federation.id}:{runtime_arn}:{exc}")

        for runtime_arn in sorted(a2a_runtime_arns_to_rebuild):
            try:
                await self._sync_a2a_vectors_for_runtime(federation.id, runtime_arn)
            except Exception as exc:
                errors.append(f"a2a runtime rebuild failed:{federation.id}:{runtime_arn}:{exc}")

        if not errors:
            logger.info(
                "Federation vector sync completed: federation_id=%s job_id=%s "
                "mcp_rebuilt=%d collection=%s a2a_rebuilt=%d collection=%s",
                federation.id,
                job.id,
                len(mcp_runtime_arns_to_rebuild),
                getattr(self.mcp_server_repo, "collection", "MCP_Servers"),
                len(a2a_runtime_arns_to_rebuild),
                getattr(self.a2a_agent_repo, "collection", "A2a_agents"),
            )

        if errors:
            logger.warning(
                "Federation vector sync completed with errors: federation_id=%s job_id=%s error_count=%d first_error=%s",
                federation.id,
                job.id,
                len(errors),
                errors[0],
            )
            raise RuntimeError("; ".join(errors))

    async def run_delete(
        self,
        federation: Federation,
        job: FederationSyncJob,
    ) -> FederationSyncJob:
        await self.federation_job_service.mark_syncing(job, FederationJobPhase.APPLYING)

        try:
            federation_id_str = str(federation.id)
            mcp_arns, a2a_arns = await self._delete_transaction(federation, current_job_id=job.id)

            vector_errors = await self._delete_vectors_for_federation(federation_id_str, mcp_arns, a2a_arns)
            if vector_errors:
                job.applySummary.errorMessages.extend(vector_errors)

            await self.federation_job_service.mark_success(job)
            return job
        except Exception as exc:
            # Federation doc may already be gone if the transaction committed but vector
            # cleanup failed; attempt to record the failure and swallow any secondary error.
            try:
                await self.federation_crud_service.mark_delete_failed(federation, str(exc))
            except Exception as e:
                logger.exception("Could not record delete failure on federation %s e: %s", federation.id, str(e))
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, str(exc))
            raise

    async def _build_federation_stats(self, federation_id) -> FederationStats:
        session = get_current_session()
        mcp_count = await ExtendedMCPServer.find(
            {"federationRefId": federation_id, "status": {"$ne": "deleted"}},
            session=session,
        ).count()
        agent_count = await A2AAgent.find(
            {"federationRefId": federation_id, "status": {"$ne": "deleted"}},
            session=session,
        ).count()
        mcp_servers = await ExtendedMCPServer.find(
            {"federationRefId": federation_id, "status": {"$ne": "deleted"}},
            session=session,
        ).to_list()
        tool_count = sum(int(server.numTools or 0) for server in mcp_servers)
        return FederationStats(
            mcpServerCount=mcp_count,
            agentCount=agent_count,
            toolCount=tool_count,
            importedTotal=mcp_count + agent_count,
        )

    async def _sync_mcp_vectors_for_runtime(self, federation_id, runtime_arn: str) -> None:
        federation_id_str = str(federation_id)
        await self.mcp_server_repo.ensure_collection()
        # Delete and rebuild one MCP runtime at a time. runtimeArn identifies the
        # concrete remote resource; federation_id prevents cross-federation deletes.
        await self.mcp_server_repo.delete_by_runtime_identity(federation_id_str, runtime_arn)
        current_server = await ExtendedMCPServer.find_one(
            {
                "federationRefId": federation_id,
                "federationMetadata.runtimeArn": runtime_arn,
            }
        )
        if current_server is None:
            return

        result = await self.mcp_server_repo.sync_server_to_vector_db(current_server, is_delete=False)
        if not result or result.get("failed_tools"):
            detail = result.get("error") if result else None
            suffix = f":{detail}" if detail else ""
            raise RuntimeError(f"mcp sync failed for {current_server.serverName}{suffix}")

    async def _sync_a2a_vectors_for_runtime(self, federation_id, runtime_arn: str) -> None:
        federation_id_str = str(federation_id)
        await self.a2a_agent_repo.ensure_collection()
        await self.a2a_agent_repo.delete_by_runtime_identity(federation_id_str, runtime_arn)
        current_agent = await A2AAgent.find_one(
            {
                "federationRefId": federation_id,
                "federationMetadata.runtimeArn": runtime_arn,
            }
        )
        if current_agent is None:
            return

        result = await self.a2a_agent_repo.sync_agent_to_vector_db(current_agent, is_delete=False)
        if not result or result.get("failed"):
            detail = result.get("error") if result else None
            suffix = f":{detail}" if detail else ""
            raise RuntimeError(f"a2a sync failed for {current_agent.card.name}{suffix}")

    async def _current_mcp_runtime_arns(self, federation_id) -> list[str]:
        current_servers = await ExtendedMCPServer.find({"federationRefId": federation_id}).to_list()
        return [
            runtime_arn
            for runtime_arn in (self._extract_runtime_arn(server.federationMetadata) for server in current_servers)
            if runtime_arn
        ]

    async def _current_a2a_runtime_arns(self, federation_id) -> list[str]:
        current_agents = await A2AAgent.find({"federationRefId": federation_id}).to_list()
        return [
            runtime_arn
            for runtime_arn in (self._extract_runtime_arn(agent.federationMetadata) for agent in current_agents)
            if runtime_arn
        ]

    async def _delete_vectors_for_federation(
        self,
        federation_id_str: str,
        mcp_runtime_arns: list[str],
        a2a_runtime_arns: list[str],
    ) -> list[str]:
        """Remove Weaviate vector records for all MCP and A2A runtimes belonging to a deleted federation.

        Returns a list of error messages for any ARNs that could not be cleaned up.
        Failures are non-fatal — MongoDB is the source of truth and the resources are
        already gone; orphaned vector records are a cosmetic issue that can be repaired
        by a future rebuild.
        """
        errors: list[str] = []

        if mcp_runtime_arns:
            await self.mcp_server_repo.ensure_collection()
            for arn in mcp_runtime_arns:
                try:
                    await self.mcp_server_repo.delete_by_runtime_identity(federation_id_str, arn)
                except Exception as e:
                    logger.exception("Failed to delete MCP vector records for runtime %s,e: %s", arn, str(e))
                    errors.append(f"mcp vector cleanup failed for {arn}")

        if a2a_runtime_arns:
            await self.a2a_agent_repo.ensure_collection()
            for arn in a2a_runtime_arns:
                try:
                    await self.a2a_agent_repo.delete_by_runtime_identity(federation_id_str, arn)
                except Exception as e:
                    logger.error("Failed to delete A2A vector records for runtime %s ,e: %s", arn, str(e))
                    errors.append(f"a2a vector cleanup failed for {arn}")
        return errors

    @staticmethod
    def _build_last_sync(job: FederationSyncJob, apply_summary: FederationApplySummary) -> FederationLastSync:
        sync_status = FederationSyncStatus.FAILED if apply_summary.errors else FederationSyncStatus.SUCCESS
        return FederationLastSync(
            jobId=job.id,
            jobType=job.jobType,
            status=sync_status,
            startedAt=job.startedAt,
            finishedAt=datetime.now(UTC),
            summary=FederationLastSyncSummary(
                discoveredMcpServers=job.discoverySummary.discoveredMcpServers,
                discoveredAgents=job.discoverySummary.discoveredAgents,
                createdMcpServers=apply_summary.createdMcpServers,
                updatedMcpServers=apply_summary.updatedMcpServers,
                deletedMcpServers=apply_summary.deletedMcpServers,
                unchangedMcpServers=apply_summary.unchangedMcpServers,
                skippedMcpServers=apply_summary.skippedMcpServers,
                createdAgents=apply_summary.createdAgents,
                updatedAgents=apply_summary.updatedAgents,
                deletedAgents=apply_summary.deletedAgents,
                unchangedAgents=apply_summary.unchangedAgents,
                skippedAgents=apply_summary.skippedAgents,
                errors=apply_summary.errors,
                errorMessages=list(apply_summary.errorMessages or []),
            ),
        )

    @staticmethod
    def _extract_resource_error(item: Any) -> str | None:
        metadata = getattr(item, "federationMetadata", None) or {}
        error_message = metadata.get("enrichmentError")
        if error_message:
            return str(error_message)

        well_known = getattr(item, "wellKnown", None)
        if well_known is not None and getattr(well_known, "lastSyncStatus", None) == "failed":
            sync_error = getattr(well_known, "syncError", None)
            if sync_error:
                return str(sync_error)
            return "resource sync failed"

        return None

    @staticmethod
    def _record_apply_error(
        apply_summary: FederationApplySummary,
        error_message: str,
    ) -> None:
        apply_summary.errors += 1
        apply_summary.errorMessages.append(error_message)

    @staticmethod
    def _summarize_sync_errors(error_messages: list[str]) -> str:
        if not error_messages:
            return "Federation sync failed"
        if len(error_messages) == 1:
            return error_messages[0]
        return f"{len(error_messages)} resource sync failures. First error: {error_messages[0]}"

    @use_transaction
    async def _delete_transaction(
        self,
        federation: Federation,
        *,
        current_job_id,
    ) -> tuple[list[str], list[str]]:
        """
        Atomically removes every MongoDB document owned by this federation.

        Returns (mcp_runtime_arns, a2a_runtime_arns) so the caller can clean up
        Weaviate vector records outside the transaction.
        """
        session = get_current_session()
        mcp_list = await ExtendedMCPServer.find({"federationRefId": federation.id}, session=session).to_list()
        mcp_runtime_arns = [arn for item in mcp_list if (arn := self._extract_runtime_arn(item.federationMetadata))]
        for item in mcp_list:
            await self.acl_service.delete_acl_entries_for_resource(
                resource_type=ResourceType.MCPSERVER,
                resource_id=item.id,
            )
            await item.delete(session=session)

        a2a_list = await A2AAgent.find({"federationRefId": federation.id}, session=session).to_list()
        a2a_runtime_arns = [arn for item in a2a_list if (arn := self._extract_runtime_arn(item.federationMetadata))]
        for item in a2a_list:
            await self.acl_service.delete_acl_entries_for_resource(
                resource_type=ResourceType.REMOTE_AGENT,
                resource_id=item.id,
            )
            await item.delete(session=session)

        # Delete all sync job history except the in-progress DELETE job.
        old_jobs = await FederationSyncJob.find(
            {"federationId": federation.id, "_id": {"$ne": current_job_id}},
            session=session,
        ).to_list()
        for old_job in old_jobs:
            await old_job.delete(session=session)

        # Delete the federation's own ACL entries.
        await self.acl_service.delete_acl_entries_for_resource(
            resource_type=ExtendedResourceType.FEDERATION,
            resource_id=federation.id,
        )
        await federation.delete(session=session)
        return mcp_runtime_arns, a2a_runtime_arns

    @staticmethod
    def _extract_runtime_arn(metadata: dict[str, Any] | None) -> str | None:
        return AgentCoreImportService.extract_runtime_arn(metadata)

    @classmethod
    def _runtime_metadata_changed(
        cls,
        existing_metadata: dict[str, Any] | None,
        new_metadata: dict[str, Any] | None,
    ) -> bool:
        # Federation sync currently treats runtime version drift as the canonical
        # signal that a discovered resource should overwrite the persisted one.
        return bool(AgentCoreImportService.detect_runtime_version_change(existing_metadata, new_metadata))
