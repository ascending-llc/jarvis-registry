from datetime import UTC, datetime

from beanie import Document, Insert, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from .enums import SkillSyncJobStatus, SkillSyncProviderType, SkillSyncSourceStatus, SkillSyncStatus


class SkillSyncSourceStats(BaseModel):
    skillCount: int = 0
    fileCount: int = 0

    model_config = ConfigDict(populate_by_name=True)


class SkillSyncSourceLastSync(BaseModel):
    jobId: object
    status: SkillSyncJobStatus
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    commitSha: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class SkillSyncSource(Document):
    providerType: SkillSyncProviderType = SkillSyncProviderType.GITHUB
    displayName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    owner: str
    repo: str
    ref: str = "main"
    paths: list[str] = Field(default_factory=list)
    skillDiscoveryDepth: int = 2
    githubAppClientId: str
    githubAppClientSecretEncrypted: str
    status: SkillSyncSourceStatus = SkillSyncSourceStatus.ACTIVE
    syncStatus: SkillSyncStatus = SkillSyncStatus.IDLE
    syncMessage: str | None = None
    stats: SkillSyncSourceStats = Field(default_factory=SkillSyncSourceStats)
    lastSync: SkillSyncSourceLastSync | None = None
    createdBy: str | None = None
    updatedBy: str | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deletedAt: datetime | None = None

    class Settings:
        name = "skill_sync_sources"
        keep_nulls = False
        use_state_management = True
        indexes = [
            IndexModel([("providerType", 1), ("status", 1), ("updatedAt", -1)]),
            IndexModel([("syncStatus", 1), ("updatedAt", -1)]),
            IndexModel([("displayName", "text"), ("description", "text")]),
        ]

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
