from datetime import UTC, datetime
from typing import Any

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


class SkillSyncJob(Document):
    sourceId: PydanticObjectId
    jobType: SkillSyncJobType
    triggerType: SkillSyncTriggerType = SkillSyncTriggerType.MANUAL
    triggeredBy: str
    status: SkillSyncJobStatus = SkillSyncJobStatus.PENDING
    phase: SkillSyncJobPhase = SkillSyncJobPhase.QUEUED
    requestSnapshot: dict[str, Any] = Field(default_factory=dict)
    discoverySummary: SkillSyncDiscoverySummary = Field(default_factory=SkillSyncDiscoverySummary)
    applySummary: SkillSyncApplySummary = Field(default_factory=SkillSyncApplySummary)
    skillErrors: list[SkillSyncSkillError] = Field(default_factory=list)
    errorCode: str | None = None
    error: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "skill_sync_jobs"
        keep_nulls = False
        use_state_management = True
        indexes = [IndexModel([("sourceId", 1), ("createdAt", -1)]), IndexModel([("sourceId", 1), ("status", 1)])]

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
