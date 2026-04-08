from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from registry_pkgs.models.enums import (
    FederationJobStatus,
    FederationJobType,
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
)

from .acl_schema import ResourcePermissions
from .server_api_schemas import PaginationMetadata


class FederationCreateRequest(BaseModel):
    providerType: FederationProviderType
    displayName: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    providerConfig: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class FederationUpdateRequest(BaseModel):
    displayName: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    providerConfig: dict[str, Any] = Field(default_factory=dict)
    version: int
    syncAfterUpdate: bool = False

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class FederationSyncRequest(BaseModel):
    dryRun: bool = False
    reason: str | None = None
    providerConfig: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class FederationStatsResponse(BaseModel):
    mcpServerCount: int = 0
    agentCount: int = 0
    toolCount: int = 0
    importedTotal: int = 0

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationLastSyncSummaryResponse(BaseModel):
    discoveredMcpServers: int = 0
    discoveredAgents: int = 0

    createdMcpServers: int = 0
    updatedMcpServers: int = 0
    deletedMcpServers: int = 0
    unchangedMcpServers: int = 0

    createdAgents: int = 0
    updatedAgents: int = 0
    deletedAgents: int = 0
    unchangedAgents: int = 0

    errors: int = 0
    errorMessages: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationLastSyncResponse(BaseModel):
    jobId: str | None = None
    jobType: FederationJobType | None = None
    status: FederationSyncStatus | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    summary: FederationLastSyncSummaryResponse | None = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationListItemResponse(BaseModel):
    id: str
    providerType: FederationProviderType
    displayName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)

    status: FederationStatus
    syncStatus: FederationSyncStatus
    syncMessage: str | None = None

    stats: FederationStatsResponse
    lastSync: FederationLastSyncResponse | None = None
    permissions: ResourcePermissions | None = None

    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationSyncJobResponse(BaseModel):
    id: str
    federationId: str
    jobType: FederationJobType
    status: FederationJobStatus
    phase: str
    startedAt: datetime | None = None
    finishedAt: datetime | None = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationSyncDryRunSummaryResponse(BaseModel):
    discoveredMcpServers: int = 0
    discoveredAgents: int = 0

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

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationSyncDryRunResponse(BaseModel):
    dryRun: bool = True
    providerType: FederationProviderType
    providerConfig: dict[str, Any] = Field(default_factory=dict)
    summary: FederationSyncDryRunSummaryResponse
    message: str | None = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationDetailResponse(BaseModel):
    id: str
    providerType: FederationProviderType
    displayName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)

    status: FederationStatus
    syncStatus: FederationSyncStatus
    syncMessage: str | None = None

    providerConfig: dict[str, Any] = Field(default_factory=dict)
    stats: FederationStatsResponse
    lastSync: FederationLastSyncResponse | None = None
    recentJobs: list[FederationSyncJobResponse] = Field(default_factory=list)
    permissions: ResourcePermissions | None = None

    version: int
    createdBy: str | None = None
    updatedBy: str | None = None
    createdAt: datetime
    updatedAt: datetime

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationDeleteResponse(BaseModel):
    federationId: str
    jobId: str
    status: str = "deleting"

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)


class FederationPagedResponse(BaseModel):
    federations: list[FederationListItemResponse]
    pagination: PaginationMetadata

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, from_attributes=True)
