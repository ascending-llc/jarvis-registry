import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.client_session import AsyncClientSession

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


class FederationJobService:
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
        return await FederationSyncJob.find_one(
            {
                "federationId": federation_id,
                "status": {"$in": _ACTIVE_STATUSES},
            },
            sort=[("createdAt", -1)],
            session=session,
        )

    async def has_active_jobs(self) -> bool:
        """True if ANY federation has a sync job still in flight (PENDING/SYNCING).

        The embedding reindex catch-up uses this to drain federation writes before committing a
        new generation: new federation jobs are gated out during a reindex, so this only ever
        reports jobs that started before the reindex.
        """
        return await FederationSyncJob.find_one({"status": {"$in": _ACTIVE_STATUSES}}) is not None

    async def list_active_jobs(self, *, limit: int) -> list[FederationSyncJob]:
        """Return up to ``limit`` in-flight (PENDING/SYNCING) sync jobs across ALL federations, oldest first.

        Oldest first because a job orphaned by a pod restart never finishes, so it sorts ahead of any
        live sync; the embedding reindex trigger names these jobs in its 409 so an operator can tell
        which is which.
        """
        return (
            await FederationSyncJob.find({"status": {"$in": _ACTIVE_STATUSES}})
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
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job = FederationSyncJob(
            federationId=federation_id,
            jobType=job_type,
            triggerType=trigger_type,
            triggeredBy=triggered_by,
            status=FederationJobStatus.PENDING,
            phase=FederationJobPhase.QUEUED,
            requestSnapshot=request_snapshot,
            discoverySummary=FederationDiscoverySummary(),
            applySummary=FederationApplySummary(),
        )
        await job.insert(session=session)
        return job

    async def mark_syncing(
        self,
        job: FederationSyncJob,
        phase: FederationJobPhase,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.status = FederationJobStateMachine.transition_to_syncing(job.status)
        job.phase = phase
        job.startedAt = job.startedAt or datetime.now(UTC)
        await job.save(session=session)
        return job

    async def update_discovery_summary(
        self,
        job: FederationSyncJob,
        discovered_mcp_servers: int,
        discovered_agents: int,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.discoverySummary = FederationDiscoverySummary(
            discoveredMcpServers=discovered_mcp_servers,
            discoveredAgents=discovered_agents,
        )
        await job.save(session=session)
        return job

    async def update_apply_summary(
        self,
        job: FederationSyncJob,
        apply_summary: FederationApplySummary,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.applySummary = apply_summary
        await job.save(session=session)
        return job

    async def mark_success(
        self,
        job: FederationSyncJob,
        message: str | None = None,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.status = FederationJobStateMachine.transition_to_success(job.status)
        job.phase = FederationJobPhase.COMPLETED
        job.error = message
        job.finishedAt = datetime.now(UTC)
        await job.save(session=session)
        return job

    async def mark_failed(
        self,
        job: FederationSyncJob,
        phase: FederationJobPhase,
        error: str,
        session: AsyncClientSession | None = None,
    ) -> FederationSyncJob:
        job.status = FederationJobStateMachine.transition_to_failed(job.status)
        job.phase = phase
        job.error = error
        job.finishedAt = datetime.now(UTC)
        await job.save(session=session)
        return job
