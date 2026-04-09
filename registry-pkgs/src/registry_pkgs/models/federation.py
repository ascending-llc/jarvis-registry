from datetime import UTC, datetime
from typing import Any

from beanie import Document, Insert, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from registry_pkgs.models.enums import (
    AgentCoreJwtTokenSource,
    AgentCoreRuntimeAccessMode,
    FederationJobType,
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
)


class AgentCoreRuntimeIamConfig(BaseModel):
    """Reserved container for future IAM runtime access settings."""

    model_config = ConfigDict(populate_by_name=True)


class AgentCoreRuntimeJwtConfig(BaseModel):
    """Federation-local configuration used to acquire JWTs for AgentCore runtime access."""

    tokenSource: AgentCoreJwtTokenSource = Field(
        default=AgentCoreJwtTokenSource.CLIENT_CREDENTIALS,
        description="JWT acquisition strategy for AgentCore runtime access",
    )
    discoveryUrl: str | None = Field(default=None, description="OIDC discovery URL override")
    audience: str | None = Field(default=None, description="Requested OAuth audience override")
    clientId: str | None = Field(default=None, description="OAuth client identifier")
    scope: str | None = Field(default=None, description="Optional OAuth scope string")
    clientSecretRef: str | None = Field(default=None, description="Reference to encrypted client secret storage")

    model_config = ConfigDict(populate_by_name=True)


class AgentCoreRuntimeAccessConfig(BaseModel):
    """Federation-local runtime access mode for AgentCore data-plane enrichment."""

    mode: AgentCoreRuntimeAccessMode = Field(
        default=AgentCoreRuntimeAccessMode.IAM,
        description="Explicit data-plane auth mode used for runtime enrichment",
    )
    iam: AgentCoreRuntimeIamConfig = Field(default_factory=AgentCoreRuntimeIamConfig)
    jwt: AgentCoreRuntimeJwtConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class AwsAgentCoreProviderConfig(BaseModel):
    """
    AWS AgentCore federation-level provider configuration.

    These fields describe how the federation connects to the AgentCore
    control plane. They are not child-resource attributes and therefore
    must not be stored on MCP servers or A2A agents.
    """

    region: str | None = Field(default=None, description="AWS region, e.g. us-east-1")
    assumeRoleArn: str | None = Field(
        default=None,
        description=(
            "Federation-level IAM role ARN used for AgentCore control plane operations. "
            "This federation assumes the role during discovery and sync."
        ),
    )
    resourceTagsFilter: dict[str, str] = Field(
        default_factory=dict,
        description="Tag filters used during discovery",
    )
    runtimeAccess: AgentCoreRuntimeAccessConfig = Field(
        default_factory=AgentCoreRuntimeAccessConfig,
        description="Federation-local runtime auth mode and JWT acquisition settings",
    )

    model_config = ConfigDict(populate_by_name=True)


class AzureAiFoundryProviderConfig(BaseModel):
    """
    Azure AI Foundry federation-level provider configuration.
    """

    projectEndpoint: str | None = Field(
        default=None,
        description="Azure AI Foundry project endpoint used to create AIProjectClient",
    )
    metadataFilter: dict[str, str] = Field(
        default_factory=dict,
        description="Agent metadata key/value filters applied during discovery",
    )

    model_config = ConfigDict(populate_by_name=True)


class FederationStats(BaseModel):
    """
    Federation statistics, used directly for list/detail display
    """

    mcpServerCount: int = 0
    agentCount: int = 0
    toolCount: int = 0
    importedTotal: int = 0

    model_config = ConfigDict(populate_by_name=True)


class FederationLastSyncSummary(BaseModel):
    """Summary counters for the most recent federation sync."""

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

    model_config = ConfigDict(populate_by_name=True)


class FederationLastSync(BaseModel):
    """Denormalized snapshot of the most recent federation sync."""

    jobId: Any | None = None
    jobType: FederationJobType | None = None
    status: FederationSyncStatus | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    summary: FederationLastSyncSummary | None = None

    model_config = ConfigDict(populate_by_name=True)


class Federation(Document):
    providerType: FederationProviderType = Field(..., description="Provider type")
    displayName: str = Field(..., description="Display name for GUI")
    description: str | None = Field(default=None, description="Optional description")

    tags: list[str] = Field(default_factory=list)

    status: FederationStatus = Field(
        default=FederationStatus.ACTIVE,
        description="Federation lifecycle status",
    )
    syncStatus: FederationSyncStatus = Field(
        default=FederationSyncStatus.IDLE,
        description="Latest sync status",
    )
    syncMessage: str | None = Field(
        default=None,
        description="Latest sync message or error summary",
    )

    providerConfig: dict[str, Any] = Field(
        default_factory=dict,
        description="Federation-level provider connection config payload",
    )

    stats: FederationStats = Field(default_factory=FederationStats)
    lastSync: FederationLastSync | None = Field(default=None)

    version: int = Field(default=1, description="Optimistic lock version")

    createdBy: str | None = None
    updatedBy: str | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deletedAt: datetime | None = None

    class Settings:
        name = "federations"
        keep_nulls = False
        use_state_management = True
        indexes = [
            IndexModel([("providerType", 1), ("status", 1), ("updatedAt", -1)]),
            IndexModel([("syncStatus", 1), ("updatedAt", -1)]),
            IndexModel([("displayName", "text"), ("description", "text")]),
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
