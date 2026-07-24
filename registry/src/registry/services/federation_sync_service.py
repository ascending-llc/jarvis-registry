import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, PrincipalType, RegistryAccessRole, ResourceType
from registry_pkgs.models.a2a_agent import AgentConfig
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobType,
    FederationProviderType,
    FederationSyncStatus,
    FederationTriggerType,
)
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry
from registry_pkgs.models.federation import (
    AgentCoreRuntimeAccessConfig,
    Federation,
    FederationLastSync,
    FederationLastSyncSummary,
    FederationStats,
)
from registry_pkgs.models.federation_sync_job import FederationApplySummary, FederationSyncJob

from .federation.agentcore_metadata import detect_runtime_version_change, extract_runtime_arn
from .federation.federation_handlers import (
    AwsAgentCoreSyncHandler,
    AzureAiFoundrySyncHandler,
    BaseFederationSyncHandler,
)
from .federation_crud_service import FederationCrudService
from .federation_job_service import FederationJobService

logger = logging.getLogger(__name__)

ACL_INHERITANCE_BATCH_SIZE = 500


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _acl_key_part(value: Any) -> str:
    return str(_enum_value(value))


def _normalize_runtime_access(
    config: AgentConfig | dict[str, Any] | None,
) -> AgentCoreRuntimeAccessConfig | None:
    """Extract and parse a resource's config.runtimeAccess into a canonical model.

    ExtendedMCPServer.config is an untyped dict (inherited from the codegen'd MCPServer base),
    so its runtimeAccess arrives as a plain dict; A2AAgent.config is the typed AgentConfig, so
    its runtimeAccess is already an AgentCoreRuntimeAccessConfig instance. Parsing both
    representations into the same model type here means equality comparison covers every JWT
    field (audiences, discoveryUrl, allowedClients, allowedScopes, customClaims) instead of just
    mode, and is immune to default-key drift between differently-aged stored dicts.
    """
    if not config:
        return None

    if isinstance(config, dict):
        raw_runtime_access = config.get("runtimeAccess")
    else:
        raw_runtime_access = getattr(config, "runtimeAccess", None)

    if raw_runtime_access is None:
        return None

    if isinstance(raw_runtime_access, AgentCoreRuntimeAccessConfig):
        return raw_runtime_access

    return AgentCoreRuntimeAccessConfig(**raw_runtime_access)


def _runtime_access_changed(
    existing_config: AgentConfig | dict[str, Any] | None,
    new_config: AgentConfig | dict[str, Any] | None,
) -> bool:
    try:
        existing_runtime_access = _normalize_runtime_access(existing_config)
        new_runtime_access = _normalize_runtime_access(new_config)
    except (TypeError, ValidationError):
        logger.warning(
            "Failed to parse stored runtimeAccess for change detection; treating as changed",
            exc_info=True,
        )
        return True
    return existing_runtime_access != new_runtime_access


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
    # The six operational fields below (mcp, a2a) * (creates, updates, pre_existing_acl_targets)
    # are all collected into resources_for_acl_inheritance during _apply_sync_plan.
    # Pre-existing resources have IDs available immediately; creates/updates get their IDs
    # after DB insert/save, which is why they're tracked separately.
    mcp_creates: list[tuple[Any, str]] = field(default_factory=list)
    mcp_updates: list[tuple[Any, Any, str]] = field(default_factory=list)
    mcp_deletes: list[tuple[Any, str | None]] = field(default_factory=list)
    mcp_pre_existing_acl_targets: list[Any] = field(default_factory=list)
    a2a_creates: list[tuple[Any, str]] = field(default_factory=list)
    a2a_updates: list[tuple[Any, Any, str]] = field(default_factory=list)
    a2a_deletes: list[tuple[Any, str | None]] = field(default_factory=list)
    a2a_pre_existing_acl_targets: list[Any] = field(default_factory=list)


@dataclass
class FederationSyncPreviewResult:
    provider_type: Any
    provider_config: dict[str, Any]
    summary: FederationApplySummary
    discovered_mcp_count: int
    discovered_a2a_count: int
    message: str | None = None


async def run_federation_sync_background(
    *,
    federation_sync_service: "FederationSyncService",
    federation: Federation,
    job: FederationSyncJob,
    author_id: PydanticObjectId,
) -> None:
    """Run a federation sync after the triggering response has been sent."""
    try:
        await federation_sync_service.run_sync(
            federation=federation,
            job=job,
            author_id=author_id,
        )
    except Exception:
        logger.exception(
            "Federation sync background task failed: federation_id=%s job_id=%s",
            federation.id,
            job.id,
        )


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

    async def _discover_entities(
        self,
        federation: Federation,
        *,
        author_id: PydanticObjectId,
    ) -> dict[str, list[Any]]:
        # Provider dispatch happens here. The federation already owns the
        # provider type and normalized provider config, so the sync service only
        # needs to select the correct handler and delegate discovery.
        handler = self.get_sync_handler(federation.providerType)
        logger.info("Dispatching federation %s sync to provider handler %s", federation.id, handler.__class__.__name__)
        return await handler.discover_entities(federation, author_id=author_id)

    async def _resolve_author_id(self, user_id: str | None) -> PydanticObjectId:
        # Defense in depth: route-layer ACL has already validated the caller,
        # but every code path that writes federated entities must also confirm
        # the user exists. This prevents a fabricated/stale user_id (or any
        # internal caller that bypasses the route) from landing a phantom
        # author ObjectId on persisted resources.
        if not user_id:
            raise ValueError("federation sync requires a user_id")
        user = await self.user_service.get_user_by_user_id(user_id)
        if user is None or user.id is None:
            raise ValueError(f"federation sync user not found: {user_id}")
        return user.id

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

    @classmethod
    def _build_final_sync_error(
        cls,
        apply_errors: list[str],
        vector_sync_error: str | None,
    ) -> str | None:
        if vector_sync_error and apply_errors:
            return (
                f"{len(apply_errors) + 1} resource sync failures. "
                f"First apply error: {apply_errors[0]}. "
                f"Vector sync error: {vector_sync_error}"
            )
        if vector_sync_error:
            return f"Vector sync error: {vector_sync_error}"
        if apply_errors:
            return cls._summarize_sync_errors(apply_errors)
        return None

    @staticmethod
    def _complete_last_sync(
        last_sync: FederationLastSync,
        *,
        failed: bool,
        vector_sync_error: str | None,
    ) -> None:
        """Finalize the denormalized sync snapshot after vector work finishes."""
        last_sync.status = FederationSyncStatus.FAILED if failed else FederationSyncStatus.SUCCESS
        last_sync.finishedAt = datetime.now(UTC)
        if not vector_sync_error:
            return

        summary = getattr(last_sync, "summary", None)
        if summary is None:
            summary = FederationLastSyncSummary()
            last_sync.summary = summary
        summary.errors += 1
        summary.errorMessages.append(f"Vector sync error: {vector_sync_error}")

    async def run_sync(
        self,
        federation: Federation,
        job: FederationSyncJob,
        author_id: PydanticObjectId,
    ) -> FederationSyncJob:
        """
        Sync execution follows a fixed flow:
            1. discover remote resources
            2. apply federation/job/resource mutations in one transaction
            3. persist stats and lastSync in the same transaction
            4. rebuild vector indexes outside the Mongo transaction

        Mongo remains the source of truth, so the vector rebuild still happens after
        the transaction commits. Vector sync runs in a best-effort sub-step: if it
        fails, the exception is caught, logged, and reflected in the final
        federation/job state by ``_finalize_sync_status``. The job is only reported
        as successful when both the Mongo commit and the vector rebuild succeed.
        """
        try:
            discovered = await self._discover_entities(federation, author_id=author_id)
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    mutation_result = await self._commit_sync_transaction(
                        federation=federation,
                        job=job,
                        discovered=discovered,
                        session=mongo_session,
                    )
            await self.federation_job_service.mark_syncing(job, FederationJobPhase.SYNCING_VECTORS)
            vector_sync_error: str | None = None
            try:
                await self._sync_vector_index_after_commit(
                    federation=federation,
                    job=job,
                    mutation_result=mutation_result,
                )
            except Exception as exc:
                logger.exception(
                    "Federation vector sync failed after commit: federation_id=%s job_id=%s",
                    federation.id,
                    job.id,
                )
                vector_sync_error = str(exc)

            await self._finalize_sync_status(federation, job, mutation_result, vector_sync_error)
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

    async def _finalize_sync_status(
        self,
        federation: Federation,
        job: FederationSyncJob,
        mutation_result: FederationSyncMutationResult,
        vector_sync_error: str | None,
    ) -> None:
        """
        Determine the final federation/job status after both the Mongo commit
        and vector sync have completed (or attempted).

        This is the only place that writes a terminal status for a committed
        apply, so the job remains active throughout the vector-sync tail.
        """
        if mutation_result.last_sync is None or mutation_result.stats is None:
            raise RuntimeError("Federation sync completed without final stats or lastSync payload")

        failure_message = self._build_final_sync_error(
            mutation_result.summary.errorMessages,
            vector_sync_error,
        )
        self._complete_last_sync(
            mutation_result.last_sync,
            failed=failure_message is not None,
            vector_sync_error=vector_sync_error,
        )
        if failure_message:
            await self.federation_crud_service.mark_sync_failed(
                federation,
                failure_message,
                last_sync=mutation_result.last_sync,
                stats=mutation_result.stats,
            )
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, failure_message)
            return

        await self.federation_crud_service.mark_sync_success(
            federation,
            mutation_result.last_sync,
            mutation_result.stats,
        )
        await self.federation_job_service.mark_success(job)

    async def preview_manual_sync(
        self,
        *,
        federation: Federation,
        reason: str | None,
        triggered_by: str | None,
    ) -> FederationSyncPreviewResult:
        """Run provider discovery and local diff without mutating persisted state."""
        del reason
        author_id = await self._resolve_author_id(triggered_by)
        discovered = await self._discover_entities(federation, author_id=author_id)
        sync_plan = await self._build_sync_plan(
            federation=federation,
            discovered_mcp=discovered.get("mcp_servers", []),
            discovered_a2a=discovered.get("a2a_agents", []),
            session=None,
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

        # Resolve the author up front so an unknown user fails before we create a
        # job; otherwise a phantom resync job would be left behind.
        author_id = await self._resolve_author_id(updated_by)
        active_job = await self.federation_job_service.get_active_job(federation.id)
        if active_job:
            raise ValueError("Federation already has an active sync job")

        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                federation, job = await self.update_federation_and_create_resync_job(
                    federation=federation,
                    display_name=display_name,
                    description=description,
                    tags=tags,
                    normalized_provider_config=normalized_provider_config,
                    updated_by=updated_by,
                    session=mongo_session,
                )
        await self.run_sync(
            federation=federation,
            job=job,
            author_id=author_id,
        )
        return federation, job

    async def _commit_sync_transaction(
        self,
        *,
        federation: Federation,
        job: FederationSyncJob,
        discovered: dict[str, list[Any]],
        session: AsyncClientSession,
    ) -> FederationSyncMutationResult:
        """Apply the discovered federation state in one Mongo transaction."""
        discovered_mcp = discovered.get("mcp_servers", [])
        discovered_a2a = discovered.get("a2a_agents", [])

        await self.federation_job_service.mark_syncing(job, FederationJobPhase.DISCOVERING, session=session)
        await self.federation_crud_service.mark_syncing(
            federation,
            last_sync=self._build_syncing_last_sync(job),
            session=session,
        )
        await self.federation_job_service.update_discovery_summary(
            job,
            discovered_mcp_servers=len(discovered_mcp),
            discovered_agents=len(discovered_a2a),
            session=session,
        )
        await self.federation_job_service.mark_syncing(job, FederationJobPhase.APPLYING, session=session)
        sync_plan = await self._build_sync_plan(
            federation=federation,
            discovered_mcp=discovered_mcp,
            discovered_a2a=discovered_a2a,
            session=session,
        )
        mutation_result = await self._apply_sync_plan(sync_plan, session=session)
        await self.federation_job_service.update_apply_summary(job, mutation_result.summary, session=session)
        stats = await self._build_federation_stats(federation.id, session=session)
        last_sync = self._build_last_sync(job, mutation_result.summary)
        mutation_result.stats = stats
        mutation_result.last_sync = last_sync
        return mutation_result

    async def update_federation_and_create_resync_job(
        self,
        *,
        federation: Federation,
        display_name: str,
        description: str | None,
        tags: list[str],
        normalized_provider_config: dict[str, Any],
        updated_by: str | None,
        session: AsyncClientSession,
    ) -> tuple[Federation, FederationSyncJob]:
        """Persist the new federation definition and its pending resync job together."""
        federation = await self.federation_crud_service.update_federation(
            federation=federation,
            display_name=display_name,
            description=description,
            tags=tags,
            provider_config=normalized_provider_config,
            updated_by=updated_by,
            session=session,
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
            session=session,
        )
        await self.federation_crud_service.mark_sync_pending(
            federation,
            last_sync=self._build_pending_last_sync(job),
            session=session,
        )
        return federation, job

    async def create_sync_job_and_mark_pending(
        self,
        *,
        federation: Federation,
        job_type: FederationJobType,
        trigger_type: FederationTriggerType,
        triggered_by: str | None,
        request_snapshot: dict[str, Any],
        session: AsyncClientSession,
    ) -> FederationSyncJob:
        """Create the sync job and move the federation into pending in one transaction."""
        job = await self.federation_job_service.create_job(
            federation_id=federation.id,
            job_type=job_type,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            request_snapshot=request_snapshot,
            session=session,
        )
        await self.federation_crud_service.mark_sync_pending(
            federation,
            last_sync=self._build_pending_last_sync(job),
            session=session,
        )
        return job

    async def create_manual_sync_job(
        self,
        *,
        federation: Federation,
        reason: str | None,
        triggered_by: str | None,
    ) -> tuple[FederationSyncJob, PydanticObjectId]:
        """Create a pending manual-sync job without running the sync inline."""
        author_id = await self._resolve_author_id(triggered_by)
        active_job = await self.federation_job_service.get_active_job(federation.id)
        if active_job:
            raise ValueError("Federation already has an active sync job")

        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
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
                    session=mongo_session,
                )
        return job, author_id

    async def start_manual_sync(
        self,
        *,
        federation: Federation,
        reason: str | None,
        triggered_by: str | None,
    ) -> FederationSyncJob:
        """Run a manual sync inline for compatibility with non-HTTP callers."""
        job, author_id = await self.create_manual_sync_job(
            federation=federation,
            reason=reason,
            triggered_by=triggered_by,
        )
        await self.run_sync(
            federation=federation,
            job=job,
            author_id=author_id,
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

        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                await self.federation_crud_service.mark_deleting(federation, session=mongo_session)
                job = await self.federation_job_service.create_job(
                    federation_id=federation.id,
                    job_type=FederationJobType.DELETE_SYNC,
                    trigger_type=FederationTriggerType.MANUAL,
                    triggered_by=triggered_by,
                    request_snapshot={
                        "providerType": _enum_value(federation.providerType),
                        "providerConfig": federation.providerConfig,
                    },
                    session=mongo_session,
                )
        await self.run_delete(federation=federation, job=job)
        return job

    @staticmethod
    def _collect_stale_resources(
        existing_resources: list[Any],
        discovered_ids: set[str],
        extract_runtime_arn: Callable[[dict[str, Any] | None], str | None],
    ) -> list[tuple[Any, str | None]]:
        """Return existing resources whose runtime ARN is not in the discovered set."""
        stale: list[tuple[Any, str | None]] = []
        for item in existing_resources:
            metadata = getattr(item, "federationMetadata", None)
            runtime_arn = extract_runtime_arn(metadata)
            if runtime_arn and runtime_arn not in discovered_ids:
                stale.append((item, runtime_arn))
        return stale

    async def _load_existing_resources_by_remote(
        self,
        federation: Federation,
        session: AsyncClientSession | None,
    ) -> tuple[
        list[ExtendedMCPServer], dict[str | None, ExtendedMCPServer], list[A2AAgent], dict[str | None, A2AAgent]
    ]:
        """Load existing MCP and A2A resources for this federation, indexed by runtime ARN."""
        existing_mcp, existing_a2a = await asyncio.gather(
            ExtendedMCPServer.find({"federationRefId": federation.id}, session=session).to_list(),
            A2AAgent.find({"federationRefId": federation.id}, session=session).to_list(),
        )
        existing_mcp_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
        }
        existing_a2a_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
        }

        return existing_mcp, existing_mcp_by_remote, existing_a2a, existing_a2a_by_remote

    @staticmethod
    async def _prefetch_global_conflicts(
        discovered_mcp: list[Any],
        discovered_a2a: list[Any],
        session: AsyncClientSession | None,
    ) -> tuple[dict[str, Any], dict[str, A2AAgent]]:
        """Pre-fetch globally unique serverName/path conflicts for discovered items.

        serverName (MCP) and path (A2A) are globally unique across all federations.
        Detecting conflicts here — before the classification loops — means both
        dry-run and real sync surface them without reaching _apply_sync_plan.
        """
        discovered_mcp_server_names = sorted({item.serverName for item in discovered_mcp if item.serverName})
        discovered_a2a_paths = sorted({item.path for item in discovered_a2a if getattr(item, "path", None)})

        async def _fetch_mcp_conflicts() -> list[Any]:
            if not discovered_mcp_server_names:
                return []
            return await ExtendedMCPServer.find(
                {"serverName": {"$in": discovered_mcp_server_names}},
                session=session,
            ).to_list()

        async def _fetch_a2a_conflicts() -> list[A2AAgent]:
            if not discovered_a2a_paths:
                return []
            return await A2AAgent.find(
                {"path": {"$in": discovered_a2a_paths}},
                session=session,
            ).to_list()

        mcp_conflict_results, a2a_conflict_results = await asyncio.gather(
            _fetch_mcp_conflicts(),
            _fetch_a2a_conflicts(),
        )

        existing_mcp_by_server_name: dict[str, Any] = {
            doc.serverName: doc for doc in mcp_conflict_results if doc.serverName
        }
        existing_a2a_by_path: dict[str, A2AAgent] = {
            item.path: item for item in a2a_conflict_results if getattr(item, "path", None)
        }

        return existing_mcp_by_server_name, existing_a2a_by_path

    def _resolve_mcp_name_conflict(
        self,
        item: Any,
        existing: Any | None,
        name_conflict: Any | None,
        federation: Federation,
        apply_summary: FederationApplySummary,
        planned_mcp_server_names: dict[str, Any],
    ) -> bool:
        """Check for persisted or same-batch serverName conflicts.

        Returns True if the item should be skipped. Mutates apply_summary on skip.
        """
        if name_conflict is not None:
            existing_id = getattr(existing, "id", None)
            conflict_id = getattr(name_conflict, "id", None)
            if existing_id is None or conflict_id is None or conflict_id != existing_id:
                if name_conflict.federationRefId is None:
                    # Orphaned serverName — no federation owns it.
                    self._record_apply_error(
                        apply_summary,
                        f"MCP server {item.serverName}: serverName already exists and is not owned by any federation",
                    )
                    apply_summary.skippedMcpServers += 1
                    logger.warning(
                        "Skipping MCP server because serverName exists but is not owned by any federation: "
                        "serverName=%s federation_id=%s",
                        item.serverName,
                        federation.id,
                    )
                    return True
                if name_conflict.federationRefId != federation.id:
                    # Cross-federation conflict: another federation already owns this serverName.
                    apply_summary.skippedMcpServers += 1
                    logger.warning(
                        "Skipping MCP server due to global serverName conflict: "
                        "serverName=%s already claimed by federation=%s",
                        item.serverName,
                        name_conflict.federationRefId,
                    )
                    return True
                # Same-federation name match with a different runtimeArn: the old doc will be
                # deleted first in _apply_sync_plan (deletes run before creates), so INSERT is safe.

        if item.serverName in planned_mcp_server_names:
            # Same-batch collision: another item discovered in this sync already claimed
            # this serverName (as a create or an earlier rename).
            self._record_apply_error(
                apply_summary,
                f"MCP server {item.serverName}: serverName collides with another resource discovered in this same sync",
            )
            apply_summary.skippedMcpServers += 1
            return True

        return False

    def _resolve_a2a_path_conflict(
        self,
        item: Any,
        existing: A2AAgent | None,
        path_conflict: A2AAgent | None,
        federation: Federation,
        apply_summary: FederationApplySummary,
    ) -> bool:
        """Check for persisted path conflicts.

        Returns True if the item should be skipped. Mutates apply_summary on skip.
        Same-batch collisions are handled by the caller because A2A create checks
        them before persisted conflicts, while A2A update checks them after.
        """
        if path_conflict is None:
            return False

        existing_id = getattr(existing, "id", None)
        conflict_id = getattr(path_conflict, "id", None)
        if not (existing_id is None or conflict_id is None or conflict_id != existing_id):
            # The conflict is the same document we are already updating; not a real conflict.
            return False

        agent_name = getattr(getattr(item, "card", None), "name", None) or self._extract_runtime_arn(
            item.federationMetadata
        )
        if path_conflict.federationRefId is None:
            # Orphaned path — no federation owns it.
            self._record_apply_error(
                apply_summary,
                f"A2A agent {agent_name}: path '{item.path}' already exists and is not owned by any federation",
            )
            apply_summary.skippedAgents += 1
            logger.warning(
                "Skipping federated A2A sync because path exists but is not owned by any federation: "
                "federation_id=%s runtime_arn=%s path=%s existing_agent_id=%s",
                federation.id,
                self._extract_runtime_arn(item.federationMetadata),
                item.path,
                getattr(path_conflict, "id", None),
            )
            return True

        if path_conflict.federationRefId != federation.id:
            # Cross-federation conflict: another federation already owns this path.
            apply_summary.skippedAgents += 1
            logger.warning(
                "Skipping federated A2A sync because path is already owned by another agent: "
                "federation_id=%s runtime_arn=%s path=%s existing_agent_id=%s existing_federation_ref_id=%s",
                federation.id,
                self._extract_runtime_arn(item.federationMetadata),
                item.path,
                getattr(path_conflict, "id", None),
                path_conflict.federationRefId,
            )
            return True

        # Same-federation path match with a different runtimeArn: the old doc will be
        # deleted first in _apply_sync_plan (deletes run before creates), so INSERT is safe.
        return False

    def _classify_discovered_mcp(
        self,
        discovered_mcp: list[Any],
        existing_mcp_by_remote: dict[str, Any],
        existing_mcp_by_server_name: dict[str, Any],
        federation: Federation,
        apply_summary: FederationApplySummary,
        sync_plan: FederationSyncPlan,
    ) -> set[str]:
        """Classify discovered MCP items into creates, updates, or unchanged.

        Mutates `sync_plan` and `apply_summary`. Returns the set of discovered runtime ARNs.
        """
        discovered_mcp_ids: set[str] = set()
        planned_mcp_server_names: dict[str, Any] = {}

        for item in discovered_mcp:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_mcp_ids.add(remote_id)
            existing = existing_mcp_by_remote.get(remote_id)

            if existing is None:
                if not item.serverName:
                    # serverName is required for federation sync; items without one cannot be classified.
                    apply_summary.skippedMcpServers += 1
                    continue

                name_conflict = existing_mcp_by_server_name.get(item.serverName)
                if self._resolve_mcp_name_conflict(
                    item,
                    None,
                    name_conflict,
                    federation,
                    apply_summary,
                    planned_mcp_server_names,
                ):
                    continue

                apply_summary.createdMcpServers += 1
                sync_plan.mcp_creates.append((item, remote_id))
                planned_mcp_server_names[item.serverName] = item
            else:
                if existing.serverName != item.serverName:
                    if not item.serverName:
                        apply_summary.skippedMcpServers += 1
                        continue

                    name_conflict = existing_mcp_by_server_name.get(item.serverName)
                    if self._resolve_mcp_name_conflict(
                        item,
                        existing,
                        name_conflict,
                        federation,
                        apply_summary,
                        planned_mcp_server_names,
                    ):
                        continue
                    planned_mcp_server_names[item.serverName] = existing

                runtime_access_changed = _runtime_access_changed(
                    getattr(existing, "config", None),
                    getattr(item, "config", None),
                )
                if (
                    not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata)
                    and not runtime_access_changed
                ):
                    apply_summary.unchangedMcpServers += 1
                    sync_plan.mcp_pre_existing_acl_targets.append(existing.id)
                else:
                    apply_summary.updatedMcpServers += 1
                    sync_plan.mcp_updates.append((existing, item, remote_id))

            error_message = self._extract_resource_error(item)
            if error_message:
                self._record_apply_error(
                    apply_summary,
                    f"MCP server {getattr(item, 'serverName', remote_id)}: {error_message}",
                )

        return discovered_mcp_ids

    def _classify_discovered_a2a(
        self,
        discovered_a2a: list[Any],
        existing_a2a_by_remote: dict[str, A2AAgent],
        existing_a2a_by_path: dict[str, A2AAgent],
        federation: Federation,
        apply_summary: FederationApplySummary,
        sync_plan: FederationSyncPlan,
    ) -> set[str]:
        """Classify discovered A2A items into creates, updates, or unchanged.

        Mutates `sync_plan` and `apply_summary`. Returns the set of discovered runtime ARNs.
        """
        discovered_a2a_ids: set[str] = set()
        planned_a2a_by_remote: dict[str, A2AAgent] = {}
        planned_a2a_paths: dict[str, A2AAgent] = {}

        for item in discovered_a2a:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_a2a_ids.add(remote_id)
            existing = existing_a2a_by_remote.get(remote_id) or planned_a2a_by_remote.get(remote_id)
            path_conflict = existing_a2a_by_path.get(item.path) if getattr(item, "path", None) else None

            if existing is None:
                if not getattr(item, "path", None):
                    # path is required for federation sync; items without one cannot be classified.
                    apply_summary.skippedAgents += 1
                    continue

                if item.path in planned_a2a_paths:
                    # Same-batch collision: another item discovered in this sync already claimed
                    # this path (as a create or an earlier rename).
                    agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                    self._record_apply_error(
                        apply_summary,
                        f"A2A agent {agent_name}: path '{item.path}' collides with another resource discovered in this same sync",
                    )
                    apply_summary.skippedAgents += 1
                    continue

                if self._resolve_a2a_path_conflict(item, None, path_conflict, federation, apply_summary):
                    continue

                apply_summary.createdAgents += 1
                sync_plan.a2a_creates.append((item, remote_id))
                planned_a2a_by_remote[remote_id] = item
                planned_a2a_paths[item.path] = item
            else:
                if existing.path != item.path:
                    if not getattr(item, "path", None):
                        apply_summary.skippedAgents += 1
                        continue

                    if self._resolve_a2a_path_conflict(item, existing, path_conflict, federation, apply_summary):
                        continue

                    if item.path in planned_a2a_paths:
                        # Same-batch collision: another item discovered in this sync already claimed
                        # this path (as a create or an earlier rename).
                        agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                        self._record_apply_error(
                            apply_summary,
                            f"A2A agent {agent_name}: path '{item.path}' collides with another resource discovered in this same sync",
                        )
                        apply_summary.skippedAgents += 1
                        continue
                    planned_a2a_paths[item.path] = existing

                runtime_access_changed = _runtime_access_changed(
                    getattr(existing, "config", None),
                    getattr(item, "config", None),
                )
                if (
                    not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata)
                    and not runtime_access_changed
                ):
                    apply_summary.unchangedAgents += 1
                    sync_plan.a2a_pre_existing_acl_targets.append(existing.id)
                else:
                    apply_summary.updatedAgents += 1
                    sync_plan.a2a_updates.append((existing, item, remote_id))
                    if getattr(item, "path", None):
                        planned_a2a_paths[item.path] = existing

            error_message = self._extract_resource_error(item)
            if error_message:
                agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
                self._record_apply_error(
                    apply_summary,
                    f"A2A agent {agent_name}: {error_message}",
                )

        return discovered_a2a_ids

    async def _build_sync_plan(
        self,
        *,
        federation: Federation,
        discovered_mcp: list[Any],
        discovered_a2a: list[Any],
        session: AsyncClientSession | None = None,
    ) -> FederationSyncPlan:
        """Build a diff plan comparing discovered resources against persisted Mongo state."""
        apply_summary = FederationApplySummary()
        sync_plan = FederationSyncPlan(
            summary=apply_summary,
            federation_id=federation.id,
            provider_type=federation.providerType,
            discovered_mcp_count=len(discovered_mcp),
            discovered_a2a_count=len(discovered_a2a),
        )

        (
            existing_mcp,
            existing_mcp_by_remote,
            existing_a2a,
            existing_a2a_by_remote,
        ) = await self._load_existing_resources_by_remote(federation, session)
        existing_mcp_by_server_name, existing_a2a_by_path = await self._prefetch_global_conflicts(
            discovered_mcp,
            discovered_a2a,
            session,
        )

        discovered_mcp_ids = self._classify_discovered_mcp(
            discovered_mcp,
            existing_mcp_by_remote,
            existing_mcp_by_server_name,
            federation,
            apply_summary,
            sync_plan,
        )
        for stale, stale_runtime_arn in self._collect_stale_resources(
            existing_mcp,
            discovered_mcp_ids,
            self._extract_runtime_arn,
        ):
            apply_summary.deletedMcpServers += 1
            sync_plan.mcp_deletes.append((stale, stale_runtime_arn))

        discovered_a2a_ids = self._classify_discovered_a2a(
            discovered_a2a,
            existing_a2a_by_remote,
            existing_a2a_by_path,
            federation,
            apply_summary,
            sync_plan,
        )
        for stale, stale_runtime_arn in self._collect_stale_resources(
            existing_a2a,
            discovered_a2a_ids,
            self._extract_runtime_arn,
        ):
            apply_summary.deletedAgents += 1
            sync_plan.a2a_deletes.append((stale, stale_runtime_arn))

        return sync_plan

    async def _apply_sync_plan(
        self,
        sync_plan: FederationSyncPlan,
        session: AsyncClientSession,
    ) -> FederationSyncMutationResult:
        """Apply a previously computed sync plan inside the current transaction."""
        mutation_result = FederationSyncMutationResult(summary=sync_plan.summary)

        # Query Federation ACL entries once for batch inheritance
        federation_acl_entries, acl_query_success = await self._get_federation_acl_entries(
            sync_plan.federation_id,
            session=session,
        )

        if not acl_query_success:
            logger.error(
                "Failed to query Federation ACL entries for federation %s",
                sync_plan.federation_id,
            )
            raise RuntimeError(f"ACL inheritance failed: could not query federation ACL for {sync_plan.federation_id}")

        # Track all resources that need ACL inheritance
        resources_for_acl_inheritance: list[tuple[str, Any]] = []
        resources_for_acl_inheritance.extend(
            (ResourceType.MCPSERVER, resource_id) for resource_id in sync_plan.mcp_pre_existing_acl_targets
        )
        resources_for_acl_inheritance.extend(
            (ResourceType.REMOTE_AGENT, resource_id) for resource_id in sync_plan.a2a_pre_existing_acl_targets
        )

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
            resources_for_acl_inheritance.append((ResourceType.MCPSERVER, server.id))

        for agent, remote_id in sync_plan.a2a_creates:
            agent.federationRefId = sync_plan.federation_id
            agent.federationMetadata = agent.federationMetadata or {}
            agent.federationMetadata["providerType"] = _enum_value(sync_plan.provider_type)
            await agent.insert(session=session)
            mutation_result.changed_a2a_runtime_arns.add(remote_id)
            resources_for_acl_inheritance.append((ResourceType.REMOTE_AGENT, agent.id))

        for existing, item, remote_id in sync_plan.mcp_updates:
            existing.serverName = item.serverName
            existing.path = item.path
            existing.tags = list(item.tags or [])
            existing.config = dict(item.config or {})
            existing.numTools = item.numTools
            existing.federationMetadata = item.federationMetadata
            await existing.save(session=session)
            mutation_result.changed_mcp_runtime_arns.add(remote_id)
            resources_for_acl_inheritance.append((ResourceType.MCPSERVER, existing.id))

        for existing, item, remote_id in sync_plan.a2a_updates:
            existing.path = item.path
            existing.card = item.card
            existing.tags = list(item.tags or [])
            existing.wellKnown = item.wellKnown
            existing.federationMetadata = item.federationMetadata
            if item.config and existing.config:
                # For an A2AAgent document created via Federation, the two fields `type` and `runtimeAccess` of `A2AAgent.config: AgentConfig`
                # should both be set according to data retrieved during the discovery process—`type` represents the A2A agent's actual
                # preferred protocol binding on its agent card; `runtimeAccess` tells us how to satisfy authentication requirements
                # when actually invoking it.
                if hasattr(item.config, "type"):
                    existing.config.type = item.config.type
                if hasattr(item.config, "runtimeAccess"):
                    existing.config.runtimeAccess = item.config.runtimeAccess
                existing.config.enabled = item.config.enabled
            elif item.config:
                existing.config = item.config
            await existing.save(session=session)
            mutation_result.changed_a2a_runtime_arns.add(remote_id)
            resources_for_acl_inheritance.append((ResourceType.REMOTE_AGENT, existing.id))

        # Batch inherit Federation ACL to all synced resources
        if federation_acl_entries and resources_for_acl_inheritance:
            await self._batch_inherit_federation_acl(
                federation_acl_entries=federation_acl_entries,
                resources=resources_for_acl_inheritance,
                session=session,
            )
        elif not federation_acl_entries and resources_for_acl_inheritance:
            logger.info(
                "No ACL entries found on Federation %s, skipping ACL inheritance for %d resources",
                sync_plan.federation_id,
                len(resources_for_acl_inheritance),
            )

        return mutation_result

    async def _get_federation_acl_entries(
        self,
        federation_id: Any,
        session: AsyncClientSession | None = None,
    ) -> tuple[list[RegistryAclEntry], bool]:
        """
        Get all ACL entries for a Federation (query once, use multiple times).

        Returns:
            Tuple of (entries, query_success):
                - entries: List of RegistryAclEntry for the Federation, excluding PUBLIC entries
                - query_success: True if query succeeded, False if query failed
        """
        try:
            entries = await RegistryAclEntry.find(
                {
                    "resourceType": RegistryResourceType.FEDERATION,
                    "resourceId": federation_id,
                    "principalType": {"$ne": PrincipalType.PUBLIC.value},
                    "principalId": {"$ne": None},
                },
                session=session,
            ).to_list()

            logger.debug("Found %d ACL entries for federation %s", len(entries), federation_id)
            return entries, True
        except Exception as e:
            logger.exception(
                "Failed to query Federation ACL entries: federation_id=%s error=%s",
                federation_id,
                str(e),
            )
            return [], False

    async def _batch_inherit_federation_acl(
        self,
        federation_acl_entries: list[RegistryAclEntry],
        resources: list[tuple[str, Any]],
        session: AsyncClientSession | None = None,
    ) -> None:
        """
        Batch inherit Federation ACL to multiple resources using INSERT-only logic.

        This method is optimized for performance:
        1. Query Federation ACL once (passed as parameter)
        2. Batch query existing ACL entries for all resources (with resourceType filter)
        3. Compute INSERT operations with principalId validation
        4. Batch insert new ACL entries in chunks (500 per batch)

        INSERT-only semantics:
        - For each user in Federation ACL, check if they have ACL on the resource
        - If NOT exists → INSERT new ACL entry with same permission
        - If EXISTS → DO NOTHING (keep existing permission, never UPDATE)
        - Users not in Federation ACL are not affected

        Args:
            federation_acl_entries: Pre-fetched Federation ACL entries (excluding PUBLIC)
            resources: List of (resource_type, resource_id) tuples
        """
        if not federation_acl_entries or not resources:
            return

        # Initialize statistics
        stats = {
            "federation_acl_count": len(federation_acl_entries),
            "resource_count": len(resources),
            "existing_acl_count": 0,
            "new_acl_count": 0,
            "skipped_count": 0,
            "invalid_principal_count": 0,
            "inserted_count": 0,
        }

        try:
            # Step 1: Batch query existing ACL entries for all resources using $in over resourceId
            # Build lookup set for post-query filtering on resourceType
            resource_lookup: set[tuple[str, str]] = {
                (_acl_key_part(resource_type), str(resource_id)) for resource_type, resource_id in resources
            }
            resource_types_in_scope = sorted({_acl_key_part(resource_type) for resource_type, _ in resources})
            all_acl_entries = await RegistryAclEntry.find(
                {
                    "resourceType": {"$in": resource_types_in_scope},
                    "resourceId": {"$in": [resource_id for _, resource_id in resources]},
                },
                session=session,
            ).to_list()
            # The MongoDB query above pre-filters by resourceType and resourceId using separate
            # $in clauses, but those are evaluated independently — it can return an entry whose
            # resourceType is in scope but whose resourceId belongs to a *different* resource type
            # (e.g., a remoteAgent entry whose resourceId happens to equal an mcpServer id).
            # The Python filter below checks the exact (resourceType, resourceId) pair against
            # resource_lookup to eliminate those false positives.  ObjectId collisions across
            # resource types are negligible in practice, so this is purely a correctness guard.
            existing_acl_entries = [
                entry
                for entry in all_acl_entries
                if (_acl_key_part(entry.resourceType), str(entry.resourceId)) in resource_lookup
            ]

            stats["existing_acl_count"] = len(existing_acl_entries)

            # Build index: (resource_type, resource_id, principal_type, principal_id) -> exists
            existing_acl_index: set[tuple[str, str, str, str]] = {
                (
                    _acl_key_part(entry.resourceType),
                    str(entry.resourceId),
                    _acl_key_part(entry.principalType),
                    str(entry.principalId),
                )
                for entry in existing_acl_entries
            }

            # Pre-fetch target-scoped roles so inherited ACL entries do not
            # keep federation roleIds on mcpServer/remoteAgent resources.
            target_resource_types = {_acl_key_part(resource_type) for resource_type, _ in resources}
            target_roles = await RegistryAccessRole.find(
                {"resourceType": {"$in": sorted(target_resource_types)}},
                session=session,
            ).to_list()
            role_id_lookup: dict[tuple[str, int], PydanticObjectId] = {
                (_acl_key_part(role.resourceType), role.permBits): role.id for role in target_roles
            }

            # Step 2: Compute new ACL entries to INSERT
            now = datetime.now(UTC)
            new_acl_entries: list[RegistryAclEntry] = []

            for resource_type, resource_id in resources:
                for fed_entry in federation_acl_entries:
                    # Validate principalId
                    if not fed_entry.principalId:
                        stats["invalid_principal_count"] += 1
                        logger.warning(
                            "Skipping ACL entry with null principalId: type=%s resource=%s/%s",
                            fed_entry.principalType,
                            resource_type,
                            resource_id,
                        )
                        continue

                    # Check if this principal already has ACL on this resource
                    acl_key = (
                        _acl_key_part(resource_type),
                        str(resource_id),
                        _acl_key_part(fed_entry.principalType),
                        str(fed_entry.principalId),
                    )

                    if acl_key in existing_acl_index:
                        # INSERT-only: skip if ACL already exists
                        stats["skipped_count"] += 1
                        continue

                    # Create new ACL entry to INSERT
                    new_entry = RegistryAclEntry(
                        principalType=fed_entry.principalType,
                        principalId=fed_entry.principalId,
                        resourceType=RegistryResourceType(resource_type),
                        resourceId=resource_id,
                        roleId=role_id_lookup.get((_acl_key_part(resource_type), fed_entry.permBits)),
                        permBits=fed_entry.permBits,
                        grantedAt=now,
                        createdAt=now,
                        updatedAt=now,
                    )
                    new_acl_entries.append(new_entry)

            stats["new_acl_count"] = len(new_acl_entries)

            # Step 3: Batch insert new ACL entries in chunks
            if new_acl_entries:
                for i in range(0, len(new_acl_entries), ACL_INHERITANCE_BATCH_SIZE):
                    batch = new_acl_entries[i : i + ACL_INHERITANCE_BATCH_SIZE]
                    await RegistryAclEntry.insert_many(batch, session=session, ordered=False)
                    stats["inserted_count"] += len(batch)

                logger.info(
                    "ACL inheritance completed: federation_acl=%d resources=%d existing_acl=%d "
                    "new_acl=%d skipped=%d invalid_principal=%d inserted=%d",
                    stats["federation_acl_count"],
                    stats["resource_count"],
                    stats["existing_acl_count"],
                    stats["new_acl_count"],
                    stats["skipped_count"],
                    stats["invalid_principal_count"],
                    stats["inserted_count"],
                )
            else:
                logger.debug(
                    "No new ACL entries to inherit: federation_acl=%d resources=%d existing_acl=%d skipped=%d",
                    stats["federation_acl_count"],
                    stats["resource_count"],
                    stats["existing_acl_count"],
                    stats["skipped_count"],
                )

        except Exception as e:
            logger.exception(
                "Failed to batch inherit Federation ACL: resources_count=%d error=%s stats=%s",
                len(resources),
                str(e),
                stats,
            )
            raise RuntimeError(f"ACL inheritance failed for {len(resources)} synced resources: {e}") from e

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
            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    mcp_arns, a2a_arns = await self._delete_transaction(
                        federation,
                        current_job_id=job.id,
                        session=mongo_session,
                    )

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

    async def _build_federation_stats(
        self,
        federation_id,
        session: AsyncClientSession | None = None,
    ) -> FederationStats:
        mcp_count = await ExtendedMCPServer.find(
            {"federationRefId": federation_id},
            session=session,
        ).count()
        agent_count = await A2AAgent.find(
            {"federationRefId": federation_id},
            session=session,
        ).count()
        mcp_servers = await ExtendedMCPServer.find(
            {"federationRefId": federation_id},
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

        result = await self.mcp_server_repo.sync_to_vector_db(current_server, is_delete=False)
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

        result = await self.a2a_agent_repo.sync_to_vector_db(current_agent, is_delete=False)
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

    async def _delete_transaction(
        self,
        federation: Federation,
        *,
        current_job_id,
        session: AsyncClientSession,
    ) -> tuple[list[str], list[str]]:
        """
        Atomically removes every MongoDB document owned by this federation.

        Returns (mcp_runtime_arns, a2a_runtime_arns) so the caller can clean up
        Weaviate vector records outside the transaction.
        """
        mcp_list = await ExtendedMCPServer.find({"federationRefId": federation.id}, session=session).to_list()
        mcp_runtime_arns = [arn for item in mcp_list if (arn := self._extract_runtime_arn(item.federationMetadata))]
        for item in mcp_list:
            await self.acl_service.delete_acl_entries_for_resource(
                resource_type=ResourceType.MCPSERVER,
                resource_id=item.id,
                session=session,
            )
            await item.delete(session=session)

        a2a_list = await A2AAgent.find({"federationRefId": federation.id}, session=session).to_list()
        a2a_runtime_arns = [arn for item in a2a_list if (arn := self._extract_runtime_arn(item.federationMetadata))]
        for item in a2a_list:
            await self.acl_service.delete_acl_entries_for_resource(
                resource_type=ResourceType.REMOTE_AGENT,
                resource_id=item.id,
                session=session,
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
            resource_type=RegistryResourceType.FEDERATION,
            resource_id=federation.id,
            session=session,
        )
        await federation.delete(session=session)
        return mcp_runtime_arns, a2a_runtime_arns

    @staticmethod
    def _extract_runtime_arn(metadata: dict[str, Any] | None) -> str | None:
        return extract_runtime_arn(metadata)

    @classmethod
    def _runtime_metadata_changed(
        cls,
        existing_metadata: dict[str, Any] | None,
        new_metadata: dict[str, Any] | None,
    ) -> bool:
        # Federation sync currently treats runtime version drift as the canonical
        # signal that a discovered resource should overwrite the persisted one.
        return bool(detect_runtime_version_change(existing_metadata, new_metadata))
