import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
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
from registry_pkgs.models.federation_metadata import (
    FederationMetadata,
    detect_runtime_version_change,
    extract_enrichment_error,
    extract_runtime_arn,
)
from registry_pkgs.models.federation_sync_job import (
    FederationApplySummary,
    FederationDiscoverySummary,
    FederationSyncJob,
)

from ..core.config import settings
from ..utils.concurrency import run_bounded
from .federation.federation_handlers import (
    AwsAgentCoreSyncHandler,
    AzureAiFoundrySyncHandler,
    BaseFederationSyncHandler,
)
from .federation_crud_service import FederationCrudService
from .federation_job_service import FederationJobService

logger = logging.getLogger(__name__)

ACL_INHERITANCE_BATCH_SIZE = 500
_STALE_PROTECTED_SKIP_REASONS = frozenset({"detail_fetch_failed", "tag_fetch_failed"})


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _acl_key_part(value: Any) -> str:
    return str(_enum_value(value))


def _protected_runtime_arns(discovered: dict[str, list[Any]]) -> set[str]:
    """Return transiently failed runtime ARNs that must not be treated as stale."""
    return {
        str(runtime_arn)
        for skipped in discovered.get("skipped_runtimes", [])
        if skipped.get("reason") in _STALE_PROTECTED_SKIP_REASONS and (runtime_arn := skipped.get("runtimeArn"))
    }


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


class _ConflictOutcome(Enum):
    """Result of a unique-key conflict check in _build_sync_plan."""

    NO_CONFLICT = auto()
    SKIP_SILENT = auto()
    SKIP_WITH_ERROR = auto()


@dataclass
class VectorSyncOutcome:
    """Per-ARN vector sync results, split by whether the ARN was part of this sync's changes."""

    failed_changed_mcp_runtime_arns: set[str] = field(default_factory=set)
    failed_changed_a2a_runtime_arns: set[str] = field(default_factory=set)
    failed_repair_only_runtime_arns: set[str] = field(default_factory=set)
    error_messages: list[str] = field(default_factory=list)


@dataclass
class FederationSyncMutationResult:
    """Capture Mongo apply results that drive post-commit vector repair."""

    summary: FederationApplySummary
    changed_mcp_runtime_arns: set[str] = field(default_factory=set)
    changed_a2a_runtime_arns: set[str] = field(default_factory=set)
    deleted_mcp_runtime_arns: set[str] = field(default_factory=set)
    deleted_a2a_runtime_arns: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _DeleteItem:
    kind: str
    document: Any
    runtime_arn: str | None


@dataclass(frozen=True, slots=True)
class _CreateItem:
    kind: str
    document: Any
    remote_id: str


@dataclass(frozen=True, slots=True)
class _UpdateItem:
    kind: str
    existing: Any
    discovered: Any
    remote_id: str


@dataclass(frozen=True, slots=True)
class _VectorItem:
    kind: str
    runtime_arn: str


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

    @staticmethod
    def _complete_last_sync(last_sync: FederationLastSync, *, failed: bool) -> None:
        """Finalize the denormalized sync snapshot after vector work finishes."""
        last_sync.status = FederationSyncStatus.FAILED if failed else FederationSyncStatus.SUCCESS
        last_sync.finishedAt = datetime.now(UTC)

    async def run_sync(
        self,
        federation: Federation,
        job: FederationSyncJob,
        author_id: PydanticObjectId,
    ) -> FederationSyncJob:
        """
        Sync execution follows a fixed flow:
            1. discover remote resources
            2. bookkeeping (phase markers, discovery summary, sync plan) — atomic transaction
            3. apply per-resource writes — outside the transaction, failures isolated
            4. rebuild vector indexes — best-effort, per-resource
            5. finalize federation/job stats, lastSync, and status

        The Mongo transaction only covers bookkeeping (step 2) so that a single
        resource's write failure in step 3 does not roll back the entire batch.
        Each resource write is individually atomic (single-document).  Per-resource
        failures are captured in ``FederationApplySummary`` counters and the loop
        continues.  Vector sync (step 4) already isolates failures via
        ``VectorSyncOutcome``.  The job is reported as successful as long as at
        least one resource fully completes the pipeline (or nothing was discovered).
        """
        try:
            discovered = await self._discover_entities(federation, author_id=author_id)

            async with MongoDB.get_client().start_session() as mongo_session:
                async with await mongo_session.start_transaction():
                    sync_plan = await self._commit_bookkeeping_transaction(
                        federation=federation,
                        job=job,
                        discovered=discovered,
                        session=mongo_session,
                    )

            mutation_result = await self._apply_sync_plan(sync_plan)

            await self.federation_job_service.update_apply_summary(job, mutation_result.summary)
            await self.federation_job_service.mark_syncing(job, FederationJobPhase.SYNCING_VECTORS)

            try:
                vector_sync_outcome = await self._sync_vector_index_after_commit(
                    federation=federation,
                    job=job,
                    mutation_result=mutation_result,
                )
            except Exception as exc:
                logger.exception(
                    "Federation vector sync failed after Mongo commit: federation_id=%s job_id=%s",
                    federation.id,
                    job.id,
                )
                vector_sync_outcome = VectorSyncOutcome(
                    failed_changed_mcp_runtime_arns=set(mutation_result.changed_mcp_runtime_arns),
                    failed_changed_a2a_runtime_arns=set(mutation_result.changed_a2a_runtime_arns),
                    error_messages=[f"vector sync failed after Mongo commit:{federation.id}:{exc}"],
                )
            await self._finalize_sync_status(federation, job, mutation_result, vector_sync_outcome)
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
        vector_sync_outcome: VectorSyncOutcome,
    ) -> None:
        """
        Determine the final federation/job status after both the Mongo commit
        and vector sync have completed (or attempted).

        This is the only place that writes a terminal status for a committed
        apply, so the job remains active throughout the vector-sync tail.
        """
        apply_summary = mutation_result.summary
        apply_summary.vectorSyncFailedMcpServers = len(vector_sync_outcome.failed_changed_mcp_runtime_arns)
        apply_summary.vectorSyncFailedAgents = len(vector_sync_outcome.failed_changed_a2a_runtime_arns)
        apply_summary.errorMessages.extend(vector_sync_outcome.error_messages)
        apply_summary.errors += len(vector_sync_outcome.error_messages)

        stats = await self._build_federation_stats(
            federation.id,
            job.discoverySummary,
            apply_summary,
            session=None,
        )
        message = self._summarize_sync_errors(apply_summary.errorMessages) if apply_summary.errorMessages else None
        total_discovered = job.discoverySummary.discoveredMcpServers + job.discoverySummary.discoveredAgents
        sync_failed = total_discovered > 0 and stats.importedTotal == 0

        last_sync = self._build_last_sync(job, apply_summary)
        self._complete_last_sync(last_sync, failed=sync_failed)

        if sync_failed:
            await self.federation_crud_service.mark_sync_failed(
                federation,
                message or "Federation sync failed",
                last_sync=last_sync,
                stats=stats,
            )
            await self.federation_job_service.mark_failed(
                job,
                FederationJobPhase.FAILED,
                message or "Federation sync failed",
            )
            return

        await self.federation_crud_service.mark_sync_success(federation, last_sync, stats, message=message)
        await self.federation_job_service.mark_success(job, message=message)

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
            protected_runtime_arns=_protected_runtime_arns(discovered),
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

    async def _commit_bookkeeping_transaction(
        self,
        *,
        federation: Federation,
        job: FederationSyncJob,
        discovered: dict[str, list[Any]],
        session: AsyncClientSession,
    ) -> FederationSyncPlan:
        """Run bookkeeping updates and build the sync plan inside a Mongo transaction.

        The transaction ensures that the Federation and FederationSyncJob documents
        stay mutually consistent (phase markers, discovery summary).  The actual
        per-resource writes happen *outside* this transaction so that individual
        failures can be isolated without rolling back the entire batch.
        """
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
            protected_runtime_arns=_protected_runtime_arns(discovered),
            session=session,
        )
        return sync_plan

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

    async def _build_sync_plan(
        self,
        *,
        federation: Federation,
        discovered_mcp: list[Any],
        discovered_a2a: list[Any],
        protected_runtime_arns: set[str] | None = None,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncPlan:
        """Compare discovered resources against Mongo state without mutating it."""
        apply_summary = FederationApplySummary()
        sync_plan = FederationSyncPlan(
            summary=apply_summary,
            federation_id=federation.id,
            provider_type=federation.providerType,
            discovered_mcp_count=len(discovered_mcp),
            discovered_a2a_count=len(discovered_a2a),
        )

        existing_mcp, existing_mcp_by_remote = await self._load_existing_by_remote(
            ExtendedMCPServer, federation.id, session
        )
        existing_a2a, existing_a2a_by_remote = await self._load_existing_by_remote(A2AAgent, federation.id, session)

        existing_mcp_by_server_name = await self._prefetch_unique_key_owners(
            ExtendedMCPServer,
            "serverName",
            sorted({item.serverName for item in discovered_mcp if item.serverName}),
            session,
        )
        existing_a2a_by_path = await self._prefetch_unique_key_owners(
            A2AAgent,
            "path",
            sorted({item.path for item in discovered_a2a if getattr(item, "path", None)}),
            session,
        )

        discovered_mcp_ids = self._classify_mcp_items(
            federation, discovered_mcp, existing_mcp_by_remote, existing_mcp_by_server_name, apply_summary, sync_plan
        )
        discovered_mcp_ids.update(protected_runtime_arns or set())
        self._collect_stale_items(
            existing_mcp, discovered_mcp_ids, apply_summary, sync_plan.mcp_deletes, "deletedMcpServers"
        )

        discovered_a2a_ids = self._classify_a2a_items(
            federation, discovered_a2a, existing_a2a_by_remote, existing_a2a_by_path, apply_summary, sync_plan
        )
        discovered_a2a_ids.update(protected_runtime_arns or set())
        self._collect_stale_items(
            existing_a2a, discovered_a2a_ids, apply_summary, sync_plan.a2a_deletes, "deletedAgents"
        )

        return sync_plan

    async def _load_existing_by_remote(
        self,
        model_cls: type,
        federation_id: Any,
        session: AsyncClientSession | None,
    ) -> tuple[list[Any], dict[str, Any]]:
        """Load all documents owned by this federation, indexed by runtime ARN.

        Returns (full_list, by_remote_dict). The full list is needed later to detect
        stale items; the dict enables O(1) lookup when matching discovered items.
        """
        docs = await model_cls.find({"federationRefId": federation_id}, session=session).to_list()
        by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in docs
            if self._extract_runtime_arn(item.federationMetadata)
        }
        return docs, by_remote

    @staticmethod
    async def _prefetch_unique_key_owners(
        model_cls: type,
        key_field: str,
        values: list[str],
        session: AsyncClientSession | None,
    ) -> dict[str, Any]:
        """Batch-query persisted documents by a globally-unique key (serverName or path).

        The returned dict is a read-only snapshot of Mongo state — nothing writes into it
        during the classification loop. Same-batch collision detection uses separate
        planned_* dicts instead.
        """
        if not values:
            return {}
        return {
            getattr(doc, key_field): doc
            for doc in await model_cls.find({key_field: {"$in": values}}, session=session).to_list()
        }

    @staticmethod
    def _check_persisted_conflict(
        conflict_doc: Any | None,
        federation_id: Any,
        existing_self_id: Any | None = None,
    ) -> _ConflictOutcome:
        """Check a persisted document that owns the target unique key.

        Args:
            conflict_doc: The persisted document that holds the target key, or None.
            federation_id: The current federation's id.
            existing_self_id: When checking a rename, the id of the item being renamed —
                              a conflict with itself is not a real conflict.
        """
        if conflict_doc is None:
            return _ConflictOutcome.NO_CONFLICT
        conflict_id = getattr(conflict_doc, "id", None)
        if existing_self_id is not None and conflict_id is not None and conflict_id == existing_self_id:
            return _ConflictOutcome.NO_CONFLICT
        if conflict_doc.federationRefId is None:
            return _ConflictOutcome.SKIP_WITH_ERROR
        if conflict_doc.federationRefId != federation_id:
            return _ConflictOutcome.SKIP_SILENT
        return _ConflictOutcome.NO_CONFLICT

    @staticmethod
    def _check_batch_conflict(key_value: str, planned_keys: dict[str, Any]) -> bool:
        """Return True if another item in this same sync batch already claimed the key."""
        return key_value in planned_keys

    def _skip_on_provider_mismatch(
        self,
        summary: FederationApplySummary,
        expected_provider: FederationProviderType,
        metadata: FederationMetadata | None,
        resource_label: str,
    ) -> bool:
        """Record and skip metadata whose discriminator disagrees with its federation."""
        actual_provider = metadata.providerType if metadata is not None else None
        if actual_provider == expected_provider:
            return False

        self._record_apply_error(
            summary,
            f"{resource_label}: federationMetadata.providerType '{_enum_value(actual_provider) or '<missing>'}' "
            f"does not match federation provider '{_enum_value(expected_provider)}'",
        )
        return True

    def _classify_mcp_items(
        self,
        federation: Federation,
        discovered_mcp: list[Any],
        existing_by_remote: dict[str, Any],
        existing_by_server_name: dict[str, Any],
        summary: FederationApplySummary,
        plan: FederationSyncPlan,
    ) -> set[str]:
        """Classify each discovered MCP server as create, update, unchanged, or skip.

        For new items (no existing doc matched by runtime ARN), checks serverName against
        persisted owners and the current batch. For existing items whose serverName changed
        (rename), applies the same conflict checks before allowing the update.

        Returns the set of discovered runtime ARNs (used to detect stale items afterward).
        """
        discovered_ids: set[str] = set()
        planned_server_names: dict[str, Any] = {}

        for item in discovered_mcp:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                self._record_apply_error(
                    summary,
                    f"MCP server {getattr(item, 'serverName', '<unknown>')}: "
                    "missing a stable remote identifier and cannot be synced",
                )
                continue

            discovered_ids.add(remote_id)
            if self._skip_on_provider_mismatch(
                summary,
                federation.providerType,
                item.federationMetadata,
                f"MCP server {getattr(item, 'serverName', remote_id)}",
            ):
                summary.skippedMcpServers += 1
                continue

            existing = existing_by_remote.get(remote_id)

            if existing is None:
                if self._skip_on_conflict(
                    summary,
                    federation.id,
                    item.serverName,
                    "MCP server",
                    existing_by_server_name.get(item.serverName),
                    planned_server_names,
                    discovered_remote_id=remote_id,
                ):
                    summary.skippedMcpServers += 1
                    continue

            error_message = self._extract_resource_error(item)
            if error_message:
                self._record_apply_error(
                    summary, f"MCP server {getattr(item, 'serverName', remote_id)}: {error_message}"
                )
                continue

            if existing is None:
                summary.createdMcpServers += 1
                plan.mcp_creates.append((item, remote_id))
                planned_server_names[item.serverName] = item
            else:
                if existing.serverName != item.serverName:
                    if self._skip_on_conflict(
                        summary,
                        federation.id,
                        item.serverName,
                        "MCP server",
                        existing_by_server_name.get(item.serverName),
                        planned_server_names,
                        existing_self_id=getattr(existing, "id", None),
                        discovered_remote_id=remote_id,
                    ):
                        summary.skippedMcpServers += 1
                        continue
                    planned_server_names[item.serverName] = existing

                if self._is_resource_unchanged(existing, item):
                    summary.unchangedMcpServers += 1
                    plan.mcp_pre_existing_acl_targets.append(existing.id)
                else:
                    summary.updatedMcpServers += 1
                    plan.mcp_updates.append((existing, item, remote_id))

        return discovered_ids

    def _classify_a2a_items(
        self,
        federation: Federation,
        discovered_a2a: list[Any],
        existing_by_remote: dict[str, Any],
        existing_by_path: dict[str, A2AAgent],
        summary: FederationApplySummary,
        plan: FederationSyncPlan,
    ) -> set[str]:
        """Classify each discovered A2A agent as create, update, unchanged, or skip.

        For new items, checks path against persisted owners and the current batch.
        The A2A create branch has a separate sibling batch-collision check because
        both colliding items may be new discoveries with no persisted conflict.
        For existing items whose path changed (rename), delegates to _skip_on_conflict
        with existing_self_id to avoid self-conflict false positives.

        Returns the set of discovered runtime ARNs (used to detect stale items afterward).
        """
        discovered_ids: set[str] = set()
        planned_by_remote: dict[str, A2AAgent] = {}
        planned_paths: dict[str, Any] = {}

        for item in discovered_a2a:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                agent_name = getattr(getattr(item, "card", None), "name", None) or "<unknown>"
                self._record_apply_error(
                    summary, f"A2A agent {agent_name}: missing a stable remote identifier and cannot be synced"
                )
                continue

            discovered_ids.add(remote_id)
            agent_name = getattr(getattr(item, "card", None), "name", None) or remote_id
            if self._skip_on_provider_mismatch(
                summary,
                federation.providerType,
                item.federationMetadata,
                f"A2A agent {agent_name}",
            ):
                summary.skippedAgents += 1
                continue

            existing = existing_by_remote.get(remote_id) or planned_by_remote.get(remote_id)
            item_path = getattr(item, "path", None)
            path_conflict = existing_by_path.get(item.path) if item_path else None

            if existing is None and path_conflict is not None:
                if self._skip_on_conflict(
                    summary,
                    federation.id,
                    item.path,
                    f"A2A agent {agent_name}",
                    path_conflict,
                    planned_paths,
                    key_label="path",
                    discovered_remote_id=remote_id,
                ):
                    summary.skippedAgents += 1
                    continue

            if existing is None and self._check_batch_conflict(item.path, planned_paths):
                self._record_apply_error(
                    summary,
                    f"A2A agent {agent_name}: path '{item.path}' collides with another resource "
                    "discovered in this same sync",
                )
                summary.skippedAgents += 1
                continue

            error_message = self._extract_resource_error(item)
            if error_message:
                self._record_apply_error(summary, f"A2A agent {agent_name}: {error_message}")
                continue

            if existing is None:
                summary.createdAgents += 1
                plan.a2a_creates.append((item, remote_id))
                planned_by_remote[remote_id] = item
                if item_path:
                    planned_paths[item.path] = item
            else:
                if existing.path != item.path:
                    if self._skip_on_conflict(
                        summary,
                        federation.id,
                        item.path,
                        f"A2A agent {agent_name}",
                        path_conflict,
                        planned_paths,
                        existing_self_id=getattr(existing, "id", None),
                        key_label="path",
                        discovered_remote_id=remote_id,
                    ):
                        summary.skippedAgents += 1
                        continue

                if self._is_resource_unchanged(existing, item):
                    summary.unchangedAgents += 1
                    plan.a2a_pre_existing_acl_targets.append(existing.id)
                else:
                    summary.updatedAgents += 1
                    plan.a2a_updates.append((existing, item, remote_id))
                    if item_path:
                        planned_paths[item.path] = existing

        return discovered_ids

    def _skip_on_conflict(
        self,
        summary: FederationApplySummary,
        federation_id: Any,
        key_value: str,
        resource_label: str,
        persisted_conflict: Any | None,
        planned_keys: dict[str, Any],
        *,
        existing_self_id: Any | None = None,
        key_label: str = "serverName",
        discovered_remote_id: str | None = None,
    ) -> bool:
        """Run the full persisted + batch conflict check chain for a unique key.

        Checks in order:
        1. Persisted conflict — orphaned (federationRefId is None) records an error;
           cross-federation (different real owner) is a silent skip.
        2. Batch conflict — another item in this same sync already claimed the key.

        Returns True if the item should be skipped (caller must increment the skip
        counter and continue). Records the appropriate error or log before returning.
        """
        outcome = self._check_persisted_conflict(persisted_conflict, federation_id, existing_self_id)
        if outcome == _ConflictOutcome.SKIP_WITH_ERROR:
            self._record_apply_error(
                summary,
                f"{resource_label}: {key_label} '{key_value}' already exists and is not owned by any federation",
            )
            return True
        if outcome == _ConflictOutcome.SKIP_SILENT:
            logger.warning(
                "Skipping %s due to %s conflict: %s=%s owner_federation=%s conflicting_id=%s runtime_arn=%s",
                resource_label,
                key_label,
                key_label,
                key_value,
                getattr(persisted_conflict, "federationRefId", None),
                getattr(persisted_conflict, "id", None),
                discovered_remote_id,
            )
            return True
        if self._check_batch_conflict(key_value, planned_keys):
            self._record_apply_error(
                summary,
                f"{resource_label}: {key_label} '{key_value}' collides with another resource "
                "discovered in this same sync",
            )
            return True
        return False

    def _is_resource_unchanged(self, existing: Any, item: Any) -> bool:
        """Return True if neither federation metadata nor runtime access config changed."""
        runtime_access_changed = _runtime_access_changed(
            getattr(existing, "config", None),
            getattr(item, "config", None),
        )
        return (
            not self._runtime_metadata_changed(existing.federationMetadata, item.federationMetadata)
            and not runtime_access_changed
        )

    def _collect_stale_items(
        self,
        existing_docs: list[Any],
        discovered_ids: set[str],
        summary: FederationApplySummary,
        delete_list: list[tuple[Any, str | None]],
        counter_attr: str,
    ) -> None:
        """Find resources this federation owns that were not re-discovered, marking them for deletion."""
        for doc in existing_docs:
            arn = self._extract_runtime_arn(doc.federationMetadata)
            if arn and arn not in discovered_ids:
                setattr(summary, counter_attr, getattr(summary, counter_attr) + 1)
                delete_list.append((doc, arn))

    async def _apply_sync_plan(
        self,
        sync_plan: FederationSyncPlan,
    ) -> FederationSyncMutationResult:
        """Apply a previously computed sync plan with per-resource failure isolation.

        Each resource write runs outside any multi-document transaction so that a
        single failure does not roll back the entire batch.  Failures are recorded
        in the plan's ``FederationApplySummary`` and the loop continues.
        """
        summary = sync_plan.summary
        mutation_result = FederationSyncMutationResult(summary=summary)

        # Query Federation ACL entries once for batch inheritance.
        # Degrade gracefully if the query fails — resources are still written.
        federation_acl_entries, acl_query_success = await self._get_federation_acl_entries(
            sync_plan.federation_id,
        )

        if not acl_query_success:
            logger.error(
                "Failed to query Federation ACL entries for federation %s — "
                "resources will be written without ACL inheritance",
                sync_plan.federation_id,
            )
            self._record_apply_error(
                summary,
                f"ACL query failed for federation {sync_plan.federation_id}: resources written without ACL inheritance",
            )
            federation_acl_entries = []

        # Track all resources that need ACL inheritance
        resources_for_acl_inheritance: list[tuple[str, Any]] = []
        resources_for_acl_inheritance.extend(
            (ResourceType.MCPSERVER, resource_id) for resource_id in sync_plan.mcp_pre_existing_acl_targets
        )
        resources_for_acl_inheritance.extend(
            (ResourceType.REMOTE_AGENT, resource_id) for resource_id in sync_plan.a2a_pre_existing_acl_targets
        )

        # Fully await each phase before starting the next. Deletes must free
        # unique-indexed names/paths before replacement documents are inserted.
        await self._apply_delete_phase(sync_plan, mutation_result)
        await self._apply_create_phase(sync_plan, mutation_result, resources_for_acl_inheritance)
        await self._apply_update_phase(sync_plan, mutation_result, resources_for_acl_inheritance)

        # --- ACL inheritance ---
        # Degrade gracefully if ACL inheritance fails — resources are already
        # persisted and ACL can be re-applied on the next sync (INSERT-only idempotent).
        if federation_acl_entries and resources_for_acl_inheritance:
            try:
                await self._batch_inherit_federation_acl(
                    federation_acl_entries=federation_acl_entries,
                    resources=resources_for_acl_inheritance,
                )
            except Exception as exc:
                logger.exception(
                    "ACL inheritance failed, continuing without ACL: federation_id=%s",
                    sync_plan.federation_id,
                )
                self._record_apply_error(
                    summary,
                    f"ACL inheritance failed for {len(resources_for_acl_inheritance)} resources: {exc}",
                )
        elif not federation_acl_entries and resources_for_acl_inheritance:
            logger.info(
                "No ACL entries found on Federation %s, skipping ACL inheritance for %d resources",
                sync_plan.federation_id,
                len(resources_for_acl_inheritance),
            )

        return mutation_result

    async def _apply_delete_phase(
        self,
        sync_plan: FederationSyncPlan,
        mutation_result: FederationSyncMutationResult,
    ) -> None:
        items = [
            *(_DeleteItem("mcp", document, runtime_arn) for document, runtime_arn in sync_plan.mcp_deletes),
            *(_DeleteItem("a2a", document, runtime_arn) for document, runtime_arn in sync_plan.a2a_deletes),
        ]

        async def _delete_one(item: _DeleteItem) -> None:
            await item.document.delete()

        outcomes = await run_bounded(
            items,
            _delete_one,
            limit=settings.federation_mongo_apply_max_concurrency,
        )
        for outcome in outcomes:
            item = outcome.item
            if outcome.ok:
                if item.runtime_arn:
                    changed = (
                        mutation_result.changed_mcp_runtime_arns
                        if item.kind == "mcp"
                        else mutation_result.changed_a2a_runtime_arns
                    )
                    deleted = (
                        mutation_result.deleted_mcp_runtime_arns
                        if item.kind == "mcp"
                        else mutation_result.deleted_a2a_runtime_arns
                    )
                    changed.add(item.runtime_arn)
                    deleted.add(item.runtime_arn)
                continue

            label = "MCP server" if item.kind == "mcp" else "A2A agent"
            logger.error(
                "Failed to delete %s: federation_id=%s runtime_arn=%s error=%s",
                label,
                sync_plan.federation_id,
                item.runtime_arn,
                outcome.error,
                exc_info=outcome.exc_info,
            )
            self._record_apply_error(
                mutation_result.summary,
                f"{label} delete failed (runtime_arn={item.runtime_arn}): {outcome.error}",
            )

    async def _apply_create_phase(
        self,
        sync_plan: FederationSyncPlan,
        mutation_result: FederationSyncMutationResult,
        resources_for_acl_inheritance: list[tuple[str, Any]],
    ) -> None:
        items = [
            *(_CreateItem("mcp", document, remote_id) for document, remote_id in sync_plan.mcp_creates),
            *(_CreateItem("a2a", document, remote_id) for document, remote_id in sync_plan.a2a_creates),
        ]

        async def _create_one(item: _CreateItem) -> None:
            item.document.federationRefId = sync_plan.federation_id
            await item.document.insert()

        outcomes = await run_bounded(
            items,
            _create_one,
            limit=settings.federation_mongo_apply_max_concurrency,
        )
        for outcome in outcomes:
            item = outcome.item
            if outcome.ok:
                changed = (
                    mutation_result.changed_mcp_runtime_arns
                    if item.kind == "mcp"
                    else mutation_result.changed_a2a_runtime_arns
                )
                resource_type = ResourceType.MCPSERVER if item.kind == "mcp" else ResourceType.REMOTE_AGENT
                changed.add(item.remote_id)
                resources_for_acl_inheritance.append((resource_type, item.document.id))
                continue

            self._record_write_failure(
                summary=mutation_result.summary,
                kind=item.kind,
                operation="create",
                remote_id=item.remote_id,
                federation_id=sync_plan.federation_id,
                error=outcome.error,
            )

    async def _apply_update_phase(
        self,
        sync_plan: FederationSyncPlan,
        mutation_result: FederationSyncMutationResult,
        resources_for_acl_inheritance: list[tuple[str, Any]],
    ) -> None:
        items = [
            *(
                _UpdateItem("mcp", existing, discovered, remote_id)
                for existing, discovered, remote_id in sync_plan.mcp_updates
            ),
            *(
                _UpdateItem("a2a", existing, discovered, remote_id)
                for existing, discovered, remote_id in sync_plan.a2a_updates
            ),
        ]

        async def _update_one(item: _UpdateItem) -> None:
            if item.kind == "mcp":
                self._copy_mcp_update_fields(item.existing, item.discovered)
            else:
                self._copy_a2a_update_fields(item.existing, item.discovered)
            await item.existing.save()

        outcomes = await run_bounded(
            items,
            _update_one,
            limit=settings.federation_mongo_apply_max_concurrency,
        )
        for outcome in outcomes:
            item = outcome.item
            if outcome.ok:
                changed = (
                    mutation_result.changed_mcp_runtime_arns
                    if item.kind == "mcp"
                    else mutation_result.changed_a2a_runtime_arns
                )
                resource_type = ResourceType.MCPSERVER if item.kind == "mcp" else ResourceType.REMOTE_AGENT
                changed.add(item.remote_id)
                resources_for_acl_inheritance.append((resource_type, item.existing.id))
                continue

            self._record_write_failure(
                summary=mutation_result.summary,
                kind=item.kind,
                operation="update",
                remote_id=item.remote_id,
                federation_id=sync_plan.federation_id,
                error=outcome.error,
            )

    @staticmethod
    def _copy_mcp_update_fields(existing: Any, discovered: Any) -> None:
        existing.serverName = discovered.serverName
        existing.path = discovered.path
        existing.tags = list(discovered.tags or [])
        existing.config = dict(discovered.config or {})
        existing.numTools = discovered.numTools
        existing.federationMetadata = discovered.federationMetadata

    @staticmethod
    def _copy_a2a_update_fields(existing: Any, discovered: Any) -> None:
        existing.path = discovered.path
        existing.card = discovered.card
        existing.tags = list(discovered.tags or [])
        existing.wellKnown = discovered.wellKnown
        existing.federationMetadata = discovered.federationMetadata
        if discovered.config and existing.config:
            if hasattr(discovered.config, "type"):
                existing.config.type = discovered.config.type
            if hasattr(discovered.config, "runtimeAccess"):
                existing.config.runtimeAccess = discovered.config.runtimeAccess
            existing.config.enabled = discovered.config.enabled
        elif discovered.config:
            existing.config = discovered.config

    def _record_write_failure(
        self,
        *,
        summary: FederationApplySummary,
        kind: str,
        operation: str,
        remote_id: str,
        federation_id: Any,
        error: Exception | None,
    ) -> None:
        label = "MCP server" if kind == "mcp" else "A2A agent"
        logger.error(
            "Failed to %s %s: federation_id=%s remote_id=%s error=%s",
            operation,
            label,
            federation_id,
            remote_id,
            error,
            exc_info=(type(error), error, error.__traceback__) if error is not None else None,
        )
        self._record_apply_error(summary, f"{label} {operation} failed (remote_id={remote_id}): {error}")
        if kind == "mcp":
            summary.mongoApplyFailedMcpServers += 1
        else:
            summary.mongoApplyFailedAgents += 1

    async def _get_federation_acl_entries(
        self,
        federation_id: Any,
    ) -> tuple[list[RegistryAclEntry], bool]:
        """
        Get all ACL entries for a Federation (query once, use multiple times).

        Only called from ``_apply_sync_plan``, which runs with no active Mongo
        transaction — this never participates in one.

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

        Only called from ``_apply_sync_plan``, which runs with no active Mongo
        transaction — this never participates in one.

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
                    await RegistryAclEntry.insert_many(batch, ordered=False)
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
    ) -> VectorSyncOutcome:
        """Refresh only the changed runtime docs in Weaviate after Mongo commit.

        This runs outside the transaction on purpose: vector storage is a
        secondary index, not the source of truth. Replaying this step is safe
        because vector docs are deleted and rebuilt from persisted Mongo state
        after commit.
        """
        outcome = VectorSyncOutcome()
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

        def _record_failure(item: _VectorItem, error: Exception) -> None:
            logger.error(
                "%s runtime vector rebuild failed: federation_id=%s job_id=%s runtime_arn=%s error=%s",
                item.kind.upper(),
                federation.id,
                job.id,
                item.runtime_arn,
                error,
                exc_info=(type(error), error, error.__traceback__),
            )
            outcome.error_messages.append(
                f"{item.kind} runtime rebuild failed:{federation.id}:{item.runtime_arn}:{error}"
            )
            changed_arns = (
                mutation_result.changed_mcp_runtime_arns
                if item.kind == "mcp"
                else mutation_result.changed_a2a_runtime_arns
            )
            deleted_arns = (
                mutation_result.deleted_mcp_runtime_arns
                if item.kind == "mcp"
                else mutation_result.deleted_a2a_runtime_arns
            )
            if item.runtime_arn in changed_arns and item.runtime_arn not in deleted_arns:
                failed_changed = (
                    outcome.failed_changed_mcp_runtime_arns
                    if item.kind == "mcp"
                    else outcome.failed_changed_a2a_runtime_arns
                )
                failed_changed.add(item.runtime_arn)
                return
            outcome.failed_repair_only_runtime_arns.add(item.runtime_arn)

        vector_items: list[_VectorItem] = []
        collection_plans = (
            ("mcp", self.mcp_server_repo, sorted(mcp_runtime_arns_to_rebuild)),
            ("a2a", self.a2a_agent_repo, sorted(a2a_runtime_arns_to_rebuild)),
        )
        for kind, repository, runtime_arns in collection_plans:
            if not runtime_arns:
                continue
            try:
                await repository.ensure_collection()
            except Exception as exc:
                for runtime_arn in runtime_arns:
                    _record_failure(_VectorItem(kind, runtime_arn), exc)
                continue
            vector_items.extend(_VectorItem(kind, runtime_arn) for runtime_arn in runtime_arns)

        async def _sync_one(item: _VectorItem) -> None:
            if item.kind == "mcp":
                await self._sync_mcp_vectors_for_runtime(federation.id, item.runtime_arn)
                return
            await self._sync_a2a_vectors_for_runtime(federation.id, item.runtime_arn)

        fan_out_results = await run_bounded(
            vector_items,
            _sync_one,
            limit=settings.federation_vector_sync_max_concurrency,
        )
        for fan_out_result in fan_out_results:
            if fan_out_result.ok:
                continue
            item = fan_out_result.item
            error = fan_out_result.error
            if error is None:
                logger.error("Vector fan-out returned a failed result without an exception: item=%s", item)
                continue
            _record_failure(item, error)

        if not outcome.error_messages:
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
        else:
            logger.warning(
                "Federation vector sync completed with errors: federation_id=%s job_id=%s error_count=%d first_error=%s",
                federation.id,
                job.id,
                len(outcome.error_messages),
                outcome.error_messages[0],
            )

        return outcome

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
        discovery_summary: FederationDiscoverySummary,
        apply_summary: FederationApplySummary,
        session: AsyncClientSession | None = None,
    ) -> FederationStats:
        mcp_servers = await ExtendedMCPServer.find(
            {"federationRefId": federation_id},
            session=session,
        ).to_list()
        tool_count = sum(int(server.numTools or 0) for server in mcp_servers)

        mcp_server_count = discovery_summary.discoveredMcpServers
        agent_count = discovery_summary.discoveredAgents

        imported_total = (
            apply_summary.createdMcpServers
            + apply_summary.updatedMcpServers
            + apply_summary.unchangedMcpServers
            - apply_summary.vectorSyncFailedMcpServers
            - apply_summary.mongoApplyFailedMcpServers
            + apply_summary.createdAgents
            + apply_summary.updatedAgents
            + apply_summary.unchangedAgents
            - apply_summary.vectorSyncFailedAgents
            - apply_summary.mongoApplyFailedAgents
        )
        unimported_total = (mcp_server_count + agent_count) - imported_total

        return FederationStats(
            mcpServerCount=mcp_server_count,
            agentCount=agent_count,
            toolCount=tool_count,
            importedTotal=imported_total,
            unimportedTotal=unimported_total,
        )

    async def _sync_mcp_vectors_for_runtime(self, federation_id, runtime_arn: str) -> None:
        federation_id_str = str(federation_id)
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
        errors.extend(
            await self._delete_vectors_for_resource_kind(
                federation_id_str=federation_id_str,
                kind="mcp",
                repository=self.mcp_server_repo,
                runtime_arns=mcp_runtime_arns,
            )
        )
        errors.extend(
            await self._delete_vectors_for_resource_kind(
                federation_id_str=federation_id_str,
                kind="a2a",
                repository=self.a2a_agent_repo,
                runtime_arns=a2a_runtime_arns,
            )
        )
        return errors

    async def _delete_vectors_for_resource_kind(
        self,
        *,
        federation_id_str: str,
        kind: str,
        repository: Any,
        runtime_arns: list[str],
    ) -> list[str]:
        if not runtime_arns:
            return []

        await repository.ensure_collection()

        async def _delete_one(runtime_arn: str) -> None:
            await repository.delete_by_runtime_identity(federation_id_str, runtime_arn)

        outcomes = await run_bounded(
            runtime_arns,
            _delete_one,
            limit=settings.federation_vector_sync_max_concurrency,
        )
        errors: list[str] = []
        for outcome in outcomes:
            if outcome.ok:
                continue
            logger.error(
                "Failed to delete %s vector records for runtime %s: %s",
                kind.upper(),
                outcome.item,
                outcome.error,
                exc_info=outcome.exc_info,
            )
            errors.append(f"{kind} vector cleanup failed for {outcome.item}")
        return errors

    @staticmethod
    def _build_last_sync(job: FederationSyncJob, apply_summary: FederationApplySummary) -> FederationLastSync:
        return FederationLastSync(
            jobId=job.id,
            jobType=job.jobType,
            status=FederationSyncStatus.SUCCESS,
            startedAt=job.startedAt,
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
                vectorSyncFailedMcpServers=apply_summary.vectorSyncFailedMcpServers,
                vectorSyncFailedAgents=apply_summary.vectorSyncFailedAgents,
                mongoApplyFailedMcpServers=apply_summary.mongoApplyFailedMcpServers,
                mongoApplyFailedAgents=apply_summary.mongoApplyFailedAgents,
                errors=apply_summary.errors,
                errorMessages=list(apply_summary.errorMessages or []),
            ),
        )

    @staticmethod
    def _extract_resource_error(item: Any) -> str | None:
        metadata = getattr(item, "federationMetadata", None)
        error_message = extract_enrichment_error(metadata)
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
    def _extract_runtime_arn(metadata: FederationMetadata | None) -> str | None:
        return extract_runtime_arn(metadata)

    @classmethod
    def _runtime_metadata_changed(
        cls,
        existing_metadata: FederationMetadata | None,
        new_metadata: FederationMetadata | None,
    ) -> bool:
        # Federation sync currently treats runtime version drift as the canonical
        # signal that a discovered resource should overwrite the persisted one.
        return bool(detect_runtime_version_change(existing_metadata, new_metadata))
