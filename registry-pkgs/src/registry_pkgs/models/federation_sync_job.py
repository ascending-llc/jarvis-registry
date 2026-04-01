from datetime import UTC, datetime
from typing import Any

from beanie import Document, Insert, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationJobStatus,
    FederationJobType,
    FederationTriggerType,
)


class FederationDiscoverySummary(BaseModel):
    discoveredMcpServers: int = 0
    discoveredAgents: int = 0

    model_config = ConfigDict(populate_by_name=True)


class FederationApplySummary(BaseModel):
    createdMcpServers: int = 0
    updatedMcpServers: int = 0
    deletedMcpServers: int = 0
    unchangedMcpServers: int = 0

    createdAgents: int = 0
    updatedAgents: int = 0
    deletedAgents: int = 0
    unchangedAgents: int = 0
    skippedAgents: int = 0
    errors: int = 0
    errorMessages: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class FederationSyncJob(Document):
    """
    Federation 同步任务表
    用于记录每一次：
    - initial_sync
    - full_sync
    - config_resync
    - force_sync
    - delete_sync
    """

    federationId: Any = Field(..., description="Reference to Federation._id")

    jobType: FederationJobType = Field(..., description="Job type")
    triggerType: FederationTriggerType = Field(
        default=FederationTriggerType.MANUAL,
        description="Trigger source",
    )
    triggeredBy: str | None = None

    status: FederationJobStatus = Field(
        default=FederationJobStatus.PENDING,
        description="Job status",
    )
    phase: FederationJobPhase = Field(
        default=FederationJobPhase.QUEUED,
        description="Execution phase",
    )

    requestSnapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider config snapshot at trigger time",
    )

    discoverySummary: FederationDiscoverySummary = Field(default_factory=FederationDiscoverySummary)
    applySummary: FederationApplySummary = Field(default_factory=FederationApplySummary)

    error: str | None = None

    startedAt: datetime | None = None
    finishedAt: datetime | None = None

    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "federation_sync_jobs"
        keep_nulls = False
        use_state_management = True
        indexes = [
            IndexModel([("federationId", 1), ("createdAt", -1)]),
            IndexModel([("federationId", 1), ("status", 1)]),
        ]

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self):
        self.updatedAt = datetime.now(UTC)
        if not self.createdAt:
            self.createdAt = datetime.now(UTC)

    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
    )
