from datetime import datetime
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

from registry_pkgs.models.enums import (
    SkillSyncJobPhase,
    SkillSyncJobStatus,
    SkillSyncJobType,
    SkillSyncProviderType,
    SkillSyncSourceStatus,
    SkillSyncStatus,
    SkillSyncTriggerType,
)
from registry_pkgs.models.skill_sync_job import SkillSyncRequestSnapshot

from .acl_schema import ResourcePermissions
from .server_api_schemas import PaginationMetadata


def _validate_relative_path(value: str) -> str:
    normalized = value.strip()
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or ".." in path.parts or "\\" in normalized:
        raise ValueError("paths must contain safe repository-relative POSIX paths")
    return normalized.rstrip("/") or "."


def _normalize_and_validate_paths(values: list[str]) -> list[str]:
    normalized = [_validate_relative_path(value) for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError("paths must not contain duplicates")
    if "." in normalized and len(normalized) > 1:
        raise ValueError("the repository root path '.' cannot be combined with other paths")

    sorted_paths = sorted(normalized)
    for index, parent in enumerate(sorted_paths):
        if any(child.startswith(parent + "/") for child in sorted_paths[index + 1 :]):
            raise ValueError("paths must not contain overlapping ancestor and descendant paths")
    return normalized


class SkillSyncSourceCreateRequest(BaseModel):
    displayName: str = Field(min_length=1, max_length=128)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    owner: str = Field(min_length=1, max_length=39, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
    repo: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    ref: str = Field(default="main", min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._/-]+$")
    paths: list[str] = Field(min_length=1)
    githubAppClientId: str = Field(min_length=1)
    githubAppClientSecret: str = Field(min_length=1)

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        return _normalize_and_validate_paths(values)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        if value.startswith("/") or any(part in value for part in ("..", "//", "@{", "\\")):
            raise ValueError("ref must be a safe Git ref")
        return value


class SkillSyncSourceUpdateRequest(BaseModel):
    displayName: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    tags: list[str] | None = None
    owner: str | None = Field(
        default=None, min_length=1, max_length=39, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
    )
    repo: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    ref: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._/-]+$")
    paths: list[str] | None = Field(default=None, min_length=1)
    githubAppClientId: str | None = Field(default=None, min_length=1)
    githubAppClientSecret: str | None = Field(default=None, min_length=1)
    syncAfterUpdate: bool = False

    @field_validator("paths")
    @classmethod
    def validate_paths(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return _normalize_and_validate_paths(values)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        if value is not None and (value.startswith("/") or any(part in value for part in ("..", "//", "@{", "\\"))):
            raise ValueError("ref must be a safe Git ref")
        return value


class SkillSyncSourceStatsResponse(BaseModel):
    skillCount: int = 0
    fileCount: int = 0
    model_config = ConfigDict(from_attributes=True)


class SkillSyncSourceLastSyncResponse(BaseModel):
    jobId: str
    status: SkillSyncJobStatus
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    commitSha: str | None = None
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class SkillSyncJobResponse(BaseModel):
    id: str
    sourceId: str
    jobType: SkillSyncJobType
    triggerType: SkillSyncTriggerType
    status: SkillSyncJobStatus
    phase: SkillSyncJobPhase
    requestSnapshot: SkillSyncRequestSnapshot
    discoverySummary: dict = Field(default_factory=dict)
    applySummary: dict = Field(default_factory=dict)
    skillErrors: list[dict] = Field(default_factory=list)
    errorCode: str | None = None
    error: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    model_config = ConfigDict(use_enum_values=True)


class SkillSyncSourceListItemResponse(BaseModel):
    id: str
    providerType: SkillSyncProviderType
    displayName: str
    description: str | None = None
    tags: list[str]
    owner: str
    repo: str
    ref: str
    paths: list[str]
    status: SkillSyncSourceStatus
    syncStatus: SkillSyncStatus
    syncMessage: str | None = None
    stats: SkillSyncSourceStatsResponse
    lastSync: SkillSyncSourceLastSyncResponse | None = None
    permissions: ResourcePermissions | None = None
    createdAt: datetime
    updatedAt: datetime
    model_config = ConfigDict(use_enum_values=True)


class SkillSyncSourceDetailResponse(SkillSyncSourceListItemResponse):
    githubAppClientId: str
    hasClientSecret: bool
    recentJobs: list[SkillSyncJobResponse] = Field(default_factory=list)
    createdBy: str | None = None
    updatedBy: str | None = None


class SkillSyncSourcePagedResponse(BaseModel):
    sources: list[SkillSyncSourceListItemResponse]
    pagination: PaginationMetadata


class SkillSyncTriggerResponse(BaseModel):
    job: SkillSyncJobResponse | None = None
    needsAuthorization: bool = False
    authorizeUrl: str | None = None


class SkillSyncDeleteResponse(BaseModel):
    sourceId: str
    jobId: str
    status: str
