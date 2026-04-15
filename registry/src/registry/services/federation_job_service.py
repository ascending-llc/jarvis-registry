import logging
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.database.decorators import get_current_session
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


class FederationJobService:
    @staticmethod
    def _get_current_session_or_none():
        return get_current_session()

    async def get_active_job(self, federation_id: PydanticObjectId) -> FederationSyncJob | None:
        return await FederationSyncJob.find_one(
            {
                "federationId": federation_id,
                "status": {
                    "$in": [
                        FederationJobStatus.PENDING.value,
                        FederationJobStatus.SYNCING.value,
                    ]
                },
            },
            sort=[("createdAt", -1)],
        )

    async def create_job(
        self,
        federation_id: PydanticObjectId,
        job_type: FederationJobType,
        trigger_type: FederationTriggerType,
        triggered_by: str | None,
        request_snapshot: dict[str, Any],
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
        await job.insert(session=self._get_current_session_or_none())
        return job

    async def mark_syncing(self, job: FederationSyncJob, phase: FederationJobPhase) -> FederationSyncJob:
        job.status = FederationJobStateMachine.transition_to_syncing(job.status)
        job.phase = phase
        job.startedAt = job.startedAt or datetime.now(UTC)
        await job.save(session=self._get_current_session_or_none())
        return job

    async def update_discovery_summary(
        self,
        job: FederationSyncJob,
        discovered_mcp_servers: int,
        discovered_agents: int,
    ) -> FederationSyncJob:
        job.discoverySummary = FederationDiscoverySummary(
            discoveredMcpServers=discovered_mcp_servers,
            discoveredAgents=discovered_agents,
        )
        await job.save(session=self._get_current_session_or_none())
        return job

    async def update_apply_summary(
        self,
        job: FederationSyncJob,
        apply_summary: FederationApplySummary,
    ) -> FederationSyncJob:
        job.applySummary = apply_summary
        await job.save(session=self._get_current_session_or_none())
        return job

    async def mark_success(self, job: FederationSyncJob) -> FederationSyncJob:
        job.status = FederationJobStateMachine.transition_to_success(job.status)
        job.phase = FederationJobPhase.COMPLETED
        job.finishedAt = datetime.now(UTC)
        await job.save(session=self._get_current_session_or_none())
        return job

    async def mark_failed(self, job: FederationSyncJob, phase: FederationJobPhase, error: str) -> FederationSyncJob:
        job.status = FederationJobStateMachine.transition_to_failed(job.status)
        job.phase = phase
        job.error = error
        job.finishedAt = datetime.now(UTC)
        await job.save(session=self._get_current_session_or_none())
        return job
