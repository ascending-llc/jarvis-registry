import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.leased_job import (
    CAMEL_CASE_LEASE_FIELDS,
    Lease,
    LeasedRepository,
    LeaseSpec,
)
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobStateMachine,
    FederationJobStatus,
    FederationJobType,
    FederationTriggerType,
)
from registry_pkgs.models.federation_sync_job import (
    FederationApplySummary,
    FederationDiscoverySummary,
    FederationSyncJob,
)

logger = logging.getLogger(__name__)

# Statuses that mean a federation sync is still in flight (could still write vectors).
_ACTIVE_STATUSES = [FederationJobStatus.PENDING.value, FederationJobStatus.SYNCING.value]

# A job created before job leasing (no lease fields) counts as live only while this fresh. Equals
# ``_FEDERATION_DRAIN_TIMEOUT`` in embedding_reindex_execution_service, the window decided for legacy jobs.
_LEGACY_JOB_LIVE_WINDOW = timedelta(minutes=10)

# Lease spec for FederationSyncJob: a sync has no overall timeout (AgentCore enrichment alone can run
# past 10 minutes), so the lease is sized small and heartbeated rather than sized to the whole job.
FEDERATION_SYNC_LEASE: LeaseSpec[FederationSyncJob] = LeaseSpec(
    document=FederationSyncJob,
    fields=CAMEL_CASE_LEASE_FIELDS,
    status_field="status",
    leased_statuses=frozenset(_ACTIVE_STATUSES),
    duration=timedelta(minutes=2),
    heartbeat_interval=timedelta(seconds=30),
)
federation_sync_lease_repository: LeasedRepository[FederationSyncJob] = LeasedRepository(FEDERATION_SYNC_LEASE)


def _live_active_filter(now: datetime) -> dict[str, Any]:
    """Jobs that are actually running: active status AND (live lease, OR legacy & recently touched).

    A job whose lease expired (its pod died) no longer counts as active, so it stops wedging the
    federation and the global embedding reindex. Jobs created before this release have no lease
    fields; they count as live only while updated within ``_LEGACY_JOB_LIVE_WINDOW``.
    """
    return {
        "status": {"$in": _ACTIVE_STATUSES},
        "$or": [
            {"leaseExpiresAt": {"$gt": now}},
            {"leaseToken": {"$exists": False}, "updatedAt": {"$gt": now - _LEGACY_JOB_LIVE_WINDOW}},
        ],
    }


class FederationJobService:
    @property
    def repository(self) -> LeasedRepository[FederationSyncJob]:
        return federation_sync_lease_repository

    async def get_job(
        self,
        job_id: str,
        *,
        federation_id: PydanticObjectId,
    ) -> FederationSyncJob | None:
        """Return a job only when it belongs to the requested federation."""
        try:
            object_id = PydanticObjectId(job_id)
        except (InvalidId, TypeError, ValueError):
            return None

        return await FederationSyncJob.find_one(
            {
                "_id": object_id,
                "federationId": federation_id,
            }
        )

    async def get_active_job(
        self,
        federation_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob | None:
        """Return the newest live (lease-backed) in-flight job for a federation, if any."""
        return await FederationSyncJob.find_one(
            {"federationId": federation_id, **_live_active_filter(datetime.now(UTC))},
            sort=[("createdAt", -1)],
            session=session,
        )

    async def has_active_jobs(self) -> bool:
        """True if ANY federation has a live (lease-backed) sync job still in flight.

        The embedding reindex catch-up uses this to drain federation writes before committing a
        new generation: new federation jobs are gated out during a reindex, so this only ever
        reports jobs that started before the reindex. A job whose lease expired (its pod died) is
        ignored, so a single orphan can no longer block reindex forever.
        """
        return await FederationSyncJob.find_one(_live_active_filter(datetime.now(UTC))) is not None

    async def list_active_jobs(self, *, limit: int) -> list[FederationSyncJob]:
        """Return up to ``limit`` live (lease-backed) in-flight sync jobs across ALL federations, oldest first.

        Only jobs whose lease is still live are listed; an orphaned job whose pod died is failed by
        the reaper within one lease duration and drops out on its own. Oldest first so the embedding
        reindex trigger names the longest-running jobs in its 409.
        """
        return (
            await FederationSyncJob.find(_live_active_filter(datetime.now(UTC)))
            .sort([("createdAt", 1)])
            .limit(limit)
            .to_list()
        )

    async def create_job(
        self,
        federation_id: PydanticObjectId,
        job_type: FederationJobType,
        trigger_type: FederationTriggerType,
        triggered_by: str | None,
        request_snapshot: dict[str, Any],
        *,
        lease_owner: str,
        session: AsyncClientSession | None = None,
    ) -> tuple[FederationSyncJob, Lease]:
        """Insert a job already leased by ``lease_owner`` so a PENDING job is never unowned.

        The id is pre-allocated so the minted lease fences on the document that is about to be
        inserted. Returns ``(job, lease)`` for the caller to execute under.
        """
        job_id = PydanticObjectId()
        lease = federation_sync_lease_repository.new_lease(lease_owner, doc_id=job_id)
        job = FederationSyncJob(
            id=job_id,
            federationId=federation_id,
            jobType=job_type,
            triggerType=trigger_type,
            triggeredBy=triggered_by,
            status=FederationJobStatus.PENDING,
            phase=FederationJobPhase.QUEUED,
            requestSnapshot=request_snapshot,
            discoverySummary=FederationDiscoverySummary(),
            applySummary=FederationApplySummary(),
            **federation_sync_lease_repository.insert_fields(lease),
        )
        await job.insert(session=session)
        return job, lease

    async def mark_syncing(
        self,
        job: FederationSyncJob,
        phase: FederationJobPhase,
        lease: Lease,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        new_status = FederationJobStateMachine.transition_to_syncing(job.status)
        job.status = new_status
        job.phase = phase
        job.startedAt = job.startedAt or datetime.now(UTC)
        await federation_sync_lease_repository.transition_or_raise(
            lease,
            {"status": new_status.value, "phase": phase.value, "startedAt": job.startedAt},
            session=session,
        )
        return job

    async def update_discovery_summary(
        self,
        job: FederationSyncJob,
        discovered_mcp_servers: int,
        discovered_agents: int,
        lease: Lease,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.discoverySummary = FederationDiscoverySummary(
            discoveredMcpServers=discovered_mcp_servers,
            discoveredAgents=discovered_agents,
        )
        await federation_sync_lease_repository.transition_or_raise(
            lease,
            {"discoverySummary": job.discoverySummary.model_dump()},
            session=session,
        )
        return job

    async def update_apply_summary(
        self,
        job: FederationSyncJob,
        apply_summary: FederationApplySummary,
        lease: Lease,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.applySummary = apply_summary
        await federation_sync_lease_repository.transition_or_raise(
            lease,
            {"applySummary": apply_summary.model_dump()},
            session=session,
        )
        return job

    async def mark_success(
        self,
        job: FederationSyncJob,
        lease: Lease,
        message: str | None = None,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        new_status = FederationJobStateMachine.transition_to_success(job.status)
        job.status = new_status
        job.phase = FederationJobPhase.COMPLETED
        job.error = message
        job.finishedAt = datetime.now(UTC)
        await federation_sync_lease_repository.transition_or_raise(
            lease,
            {
                "status": new_status.value,
                "phase": FederationJobPhase.COMPLETED.value,
                "error": message,
                "finishedAt": job.finishedAt,
                "applySummary": job.applySummary.model_dump(),
            },
            release=True,
            session=session,
        )
        return job

    async def mark_failed(
        self,
        job: FederationSyncJob,
        phase: FederationJobPhase,
        error: str,
        lease: Lease,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        new_status = FederationJobStateMachine.transition_to_failed(job.status)
        job.status = new_status
        job.phase = phase
        job.error = error
        job.finishedAt = datetime.now(UTC)
        await federation_sync_lease_repository.transition_or_raise(
            lease,
            {
                "status": new_status.value,
                "phase": phase.value,
                "error": error,
                "finishedAt": job.finishedAt,
                "applySummary": job.applySummary.model_dump(),
            },
            release=True,
            session=session,
        )
        return job
