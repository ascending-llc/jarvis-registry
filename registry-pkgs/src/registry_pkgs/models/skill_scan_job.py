"""One scan job per skill, leased. Registry-owned collection, modelled on SkillSyncJob."""

from datetime import UTC, datetime

from beanie import Document, Insert, PydanticObjectId, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from ..database.leased_job import LeasedDocumentMixin
from .enums import (
    ScanSeverity,
    SkillScanJobStatus,
    SkillScanResultStatus,
    SkillScanStatusReason,
    SkillScanTriggerType,
)


class SkillScanToolSummary(BaseModel):
    tool: str
    toolFingerprint: str
    status: SkillScanResultStatus
    statusReason: SkillScanStatusReason | None = None


class SkillScanSummary(BaseModel):
    contentDigest: str
    skillVersion: int
    status: SkillScanResultStatus  # worst tool status: failed > partial > succeeded; all skipped -> skipped
    maxSeverity: ScanSeverity | None = None  # None when there are no findings
    severityCounts: dict[ScanSeverity, int] = Field(default_factory=dict)
    tools: list[SkillScanToolSummary] = Field(default_factory=list)
    scannedAt: datetime


class SkillScanJob(LeasedDocumentMixin, Document):
    skillId: PydanticObjectId
    status: SkillScanJobStatus = SkillScanJobStatus.PENDING
    rerequested: bool = False  # set when a request arrives while SCANNING
    triggerType: SkillScanTriggerType  # of the latest request
    requestedBy: str | None = None  # user id of the latest request; None for GitHub sync
    requestedAt: datetime
    summary: SkillScanSummary | None = None  # last completed scan; read in one $in query by list views
    errorCode: str | None = None
    error: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    leaseOwner: str | None = None
    leaseToken: str | None = None
    leaseExpiresAt: datetime | None = None
    heartbeatAt: datetime | None = None
    attemptCount: int = 0
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "skill_scan_jobs"
        keep_nulls = False
        use_state_management = True
        indexes = [
            IndexModel([("skillId", 1)], unique=True),
            IndexModel([("status", 1), ("leaseExpiresAt", 1), ("requestedAt", 1)]),
        ]

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
