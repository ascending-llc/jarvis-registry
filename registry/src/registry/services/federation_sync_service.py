import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from registry_pkgs.database.decorators import get_current_session, use_transaction
from registry_pkgs.models import A2AAgent, ExtendedMCPServerDocument
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobType,
    FederationProviderType,
    FederationSyncStatus,
    FederationTriggerType,
)
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
    AzureAiFoundrySyncHandler,
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
    changed_mcp_runtime_arns: set[str] = field(default_factory=set)
    changed_a2a_runtime_arns: set[str] = field(default_factory=set)


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
            FederationProviderType.AZURE_AI_FOUNDRY: AzureAiFoundrySyncHandler(),
        }

    def get_sync_handler(self, provider_type: FederationProviderType) -> BaseFederationSyncHandler:
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
    def _get_current_session_or_none():
        try:
            return get_current_session()
        except RuntimeError:
            return None

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

        Vector sync is intentionally best-effort. Mongo is the source of truth,
        so vector failures are logged for repair instead of rolling back the
        successfully committed federation sync.
            Any exception moves both the federation and the job into failed state.
        """
        try:
            discovered = await self._discover_entities(federation)
            mutation_result = await self._commit_sync_transaction(
                federation=federation,
                job=job,
                discovered=discovered,
            )
            await self._sync_vector_index_after_commit(
                federation=federation,
                job=job,
                mutation_result=mutation_result,
            )
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

    async def update_federation_with_optional_resync(
        self,
        *,
        federation: Federation,
        display_name: str,
        description: str | None,
        tags: list[str],
        provider_config: dict[str, Any],
        version: int,
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
                version=version,
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
            version=version,
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
        mutation_result = await self._apply_sync_mutations(
            federation=federation,
            discovered_mcp=discovered_mcp,
            discovered_a2a=discovered_a2a,
        )
        await self.federation_job_service.update_apply_summary(job, mutation_result.summary)
        stats = await self._build_federation_stats(federation.id)
        last_sync = self._build_last_sync(job, mutation_result.summary)
        if mutation_result.summary.errorMessages:
            failure_message = self._summarize_sync_errors(mutation_result.summary.errorMessages)
            await self.federation_crud_service.mark_sync_failed(
                federation,
                failure_message,
                last_sync=last_sync,
                stats=stats,
            )
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, failure_message)
        else:
            await self.federation_crud_service.mark_sync_success(federation, last_sync, stats)
            await self.federation_job_service.mark_success(job)
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
        version: int,
        updated_by: str | None,
    ) -> tuple[Federation, FederationSyncJob]:
        """Persist the new federation definition and its pending resync job together."""
        federation = await self.federation_crud_service.update_federation(
            federation=federation,
            display_name=display_name,
            description=description,
            tags=tags,
            provider_config=normalized_provider_config,
            version=version,
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
        force: bool,
        reason: str | None,
        triggered_by: str | None,
    ) -> FederationSyncJob:
        """Start a user-triggered sync using the shared pending-job then run-sync flow."""
        active_job = await self.federation_job_service.get_active_job(federation.id)
        if active_job:
            raise ValueError("Federation already has an active sync job")

        job_type = FederationJobType.FORCE_SYNC if force else FederationJobType.FULL_SYNC
        job = await self.create_sync_job_and_mark_pending(
            federation=federation,
            job_type=job_type,
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

    async def _apply_sync_mutations(
        self,
        *,
        federation: Federation,
        discovered_mcp: list[Any],
        discovered_a2a: list[Any],
    ) -> FederationSyncMutationResult:
        # Keep the apply phase purely about Mongo state convergence. We collect
        # changed entities here so the caller can rebuild derived indexes after
        # the transaction commits successfully.
        apply_summary = FederationApplySummary()
        mutation_result = FederationSyncMutationResult(summary=apply_summary)
        session = self._get_current_session_or_none()

        # -------- MCP --------
        existing_mcp = await ExtendedMCPServerDocument.find(
            {"federationRefId": federation.id}, session=session
        ).to_list()
        existing_mcp_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
        }

        discovered_mcp_ids: set[str] = set()

        for item in discovered_mcp:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_mcp_ids.add(remote_id)
            existing = existing_mcp_by_remote.get(remote_id)

            if existing is None:
                server = item
                server.federationRefId = federation.id
                server.federationMetadata = server.federationMetadata or {}
                server.federationMetadata["providerType"] = _enum_value(federation.providerType)
                await server.insert(session=session)
                apply_summary.createdMcpServers += 1
                mutation_result.changed_mcp_runtime_arns.add(remote_id)
            else:
                if not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata):
                    apply_summary.unchangedMcpServers += 1
                else:
                    existing.serverName = item.serverName
                    existing.path = item.path
                    existing.tags = list(item.tags or [])
                    existing.config = dict(item.config or {})
                    existing.status = item.status or existing.status
                    existing.numTools = item.numTools
                    existing.federationMetadata = item.federationMetadata
                    await existing.save(session=session)
                    apply_summary.updatedMcpServers += 1
                    mutation_result.changed_mcp_runtime_arns.add(remote_id)

            error_message = self._extract_resource_error(item)
            if error_message:
                self._record_apply_error(
                    apply_summary,
                    mutation_result,
                    f"MCP server {getattr(item, 'serverName', remote_id)}: {error_message}",
                )

        stale_mcp = [
            item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
            and self._extract_runtime_arn(item.federationMetadata) not in discovered_mcp_ids
        ]
        for stale in stale_mcp:
            stale_runtime_arn = self._extract_runtime_arn(stale.federationMetadata)
            await stale.delete(session=session)
            apply_summary.deletedMcpServers += 1
            if stale_runtime_arn:
                mutation_result.changed_mcp_runtime_arns.add(stale_runtime_arn)

        # -------- A2A --------
        existing_a2a = await A2AAgent.find({"federationRefId": federation.id}, session=session).to_list()
        existing_a2a_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
        }
        discovered_a2a_paths = sorted({item.path for item in discovered_a2a if getattr(item, "path", None)})
        existing_a2a_by_path: dict[str, A2AAgent] = {}
        if discovered_a2a_paths:
            existing_a2a_by_path = {
                item.path: item
                for item in await A2AAgent.find({"path": {"$in": discovered_a2a_paths}}, session=session).to_list()
            }

        discovered_a2a_ids: set[str] = set()

        for item in discovered_a2a:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_a2a_ids.add(remote_id)
            existing = existing_a2a_by_remote.get(remote_id)
            path_conflict = existing_a2a_by_path.get(item.path) if getattr(item, "path", None) else None

            if existing is None and path_conflict is not None:
                if path_conflict.federationRefId == federation.id:
                    existing = path_conflict
                    existing_a2a_by_remote[remote_id] = existing
                else:
                    logger.warning(
                        "Skipping federated A2A sync because path is already owned by another agent: "
                        "federation_id=%s runtime_arn=%s path=%s existing_agent_id=%s existing_federation_ref_id=%s",
                        federation.id,
                        remote_id,
                        item.path,
                        getattr(path_conflict, "id", None),
                        getattr(path_conflict, "federationRefId", None),
                    )
                    apply_summary.skippedAgents += 1
                    continue

            if existing is None:
                agent = item
                agent.federationRefId = federation.id
                agent.federationMetadata = agent.federationMetadata or {}
                agent.federationMetadata["providerType"] = _enum_value(federation.providerType)
                await agent.insert(session=session)
                apply_summary.createdAgents += 1
                mutation_result.changed_a2a_runtime_arns.add(remote_id)
                existing_a2a_by_remote[remote_id] = agent
                if getattr(agent, "path", None):
                    existing_a2a_by_path[agent.path] = agent
            else:
                if existing.path != item.path and path_conflict is not None and path_conflict.id != existing.id:
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
                        getattr(path_conflict, "federationRefId", None),
                    )
                    apply_summary.skippedAgents += 1
                    continue
                if not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata):
                    apply_summary.unchangedAgents += 1
                else:
                    existing.path = item.path
                    existing.card = item.card
                    existing.tags = list(item.tags or [])
                    existing.status = item.status
                    existing.isEnabled = item.isEnabled
                    existing.wellKnown = item.wellKnown
                    existing.federationMetadata = item.federationMetadata
                    await existing.save(session=session)
                    apply_summary.updatedAgents += 1
                    mutation_result.changed_a2a_runtime_arns.add(remote_id)
                    if getattr(existing, "path", None):
                        existing_a2a_by_path[existing.path] = existing

            error_message = self._extract_resource_error(item)
            if error_message:
                agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                self._record_apply_error(
                    apply_summary,
                    mutation_result,
                    f"A2A agent {agent_name}: {error_message}",
                )

        stale_a2a = [
            item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
            and self._extract_runtime_arn(item.federationMetadata) not in discovered_a2a_ids
        ]
        for stale in stale_a2a:
            stale_runtime_arn = self._extract_runtime_arn(stale.federationMetadata)
            await stale.delete(session=session)
            apply_summary.deletedAgents += 1
            if stale_runtime_arn:
                mutation_result.changed_a2a_runtime_arns.add(stale_runtime_arn)

        return mutation_result

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

    async def run_delete(
        self,
        federation: Federation,
        job: FederationSyncJob,
    ) -> FederationSyncJob:
        await self.federation_job_service.mark_syncing(job, FederationJobPhase.APPLYING)

        try:
            await self._delete_transaction(federation)

            # If vector records still need explicit deletion, do it outside the transaction.
            stats = FederationStats(mcpServerCount=0, agentCount=0, toolCount=0, importedTotal=0)
            last_sync = FederationLastSync(
                jobId=job.id,
                jobType=job.jobType,
                status=FederationSyncStatus.SUCCESS,
                startedAt=job.startedAt,
                finishedAt=datetime.now(UTC),
            )

            await self.federation_crud_service.mark_sync_success(federation, last_sync, stats)
            await self.federation_crud_service.mark_deleted(federation)
            await self.federation_job_service.mark_success(job)
            return job
        except Exception as exc:
            await self.federation_crud_service.mark_delete_failed(federation, str(exc))
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, str(exc))
            raise

    async def _build_federation_stats(self, federation_id) -> FederationStats:
        session = self._get_current_session_or_none()
        mcp_count = await ExtendedMCPServerDocument.find(
            {"federationRefId": federation_id, "status": {"$ne": "deleted"}},
            session=session,
        ).count()
        agent_count = await A2AAgent.find(
            {"federationRefId": federation_id, "status": {"$ne": "deleted"}},
            session=session,
        ).count()
        mcp_servers = await ExtendedMCPServerDocument.find(
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
        current_server = await ExtendedMCPServerDocument.find_one(
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
        current_servers = await ExtendedMCPServerDocument.find({"federationRefId": federation_id}).to_list()
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
                createdAgents=apply_summary.createdAgents,
                updatedAgents=apply_summary.updatedAgents,
                deletedAgents=apply_summary.deletedAgents,
                unchangedAgents=apply_summary.unchangedAgents,
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
        mutation_result: FederationSyncMutationResult,
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
    async def _delete_transaction(self, federation: Federation) -> None:
        session = self._get_current_session_or_none()
        mcp_list = await ExtendedMCPServerDocument.find({"federationRefId": federation.id}, session=session).to_list()
        for item in mcp_list:
            await item.delete(session=session)

        a2a_list = await A2AAgent.find({"federationRefId": federation.id}, session=session).to_list()
        for item in a2a_list:
            await item.delete(session=session)

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
