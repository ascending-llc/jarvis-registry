from datetime import UTC, datetime
from typing import Literal

from beanie import Document, Insert, PydanticObjectId, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from .enums import (
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncSkillErrorCode,
    SkillSyncTriggerType,
)


class SkillSyncSkillError(BaseModel):
    skillPath: str
    upstreamId: str
    errorCode: SkillSyncSkillErrorCode
    errorMessage: str
    phase: str


class SkillSyncDiscoverySummary(BaseModel):
    discoveredSkillCount: int = 0
    discoveredFileCount: int = 0
    skippedPaths: list[str] = Field(default_factory=list)


class SkillSyncApplySummary(BaseModel):
    skillsCreated: int = 0
    skillsUpdated: int = 0
    skillsDeleted: int = 0
    skillsFailed: int = 0
    filesCreated: int = 0
    filesUpdated: int = 0
    filesDeleted: int = 0


class SkillSyncFullRequestSnapshot(BaseModel):
    owner: str
    repo: str
    ref: str
    paths: list[str]
    configRevision: int = Field(default=1, ge=1)

    model_config = ConfigDict(frozen=True)


class SkillSyncDeleteRequestSnapshot(BaseModel):
    action: Literal["delete"] = "delete"
    configRevision: int = Field(default=1, ge=1)

    model_config = ConfigDict(frozen=True)


SkillSyncRequestSnapshot = SkillSyncFullRequestSnapshot | SkillSyncDeleteRequestSnapshot


class SkillSyncJob(Document):
    sourceId: PydanticObjectId
    jobType: SkillSyncJobType
    triggerType: SkillSyncTriggerType = SkillSyncTriggerType.MANUAL
    triggeredBy: str
    status: SkillSyncJobStatus = SkillSyncJobStatus.PENDING
    phase: SkillSyncJobPhase = SkillSyncJobPhase.QUEUED
    requestSnapshot: SkillSyncRequestSnapshot
    discoverySummary: SkillSyncDiscoverySummary = Field(default_factory=SkillSyncDiscoverySummary)
    applySummary: SkillSyncApplySummary = Field(default_factory=SkillSyncApplySummary)
    skillErrors: list[SkillSyncSkillError] = Field(default_factory=list)
    errorCode: str | None = None
    error: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    leaseOwner: str | None = None
    leaseExpiresAt: datetime | None = None
    heartbeatAt: datetime | None = None
    attemptCount: int = 0
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "skill_sync_jobs"
        keep_nulls = False
        use_state_management = True
        indexes = [
            IndexModel([("sourceId", 1), ("createdAt", -1)]),
            IndexModel([("sourceId", 1), ("status", 1)]),
            IndexModel([("status", 1), ("leaseExpiresAt", 1), ("createdAt", 1)]),
        ]

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
