"""Typed metadata for resources discovered through federation providers."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import FederationProviderType


class FederationMetadataBase(BaseModel):
    """Fields shared by every federation metadata variant."""

    enrichmentError: str | None = Field(
        default=None,
        description="Message from the most recent failed enrichment attempt; cleared on success",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        extra="allow",
    )


class AgentCoreMcpFederationMetadata(FederationMetadataBase):
    """Metadata for an MCP server federated from AWS AgentCore."""

    providerType: Literal[FederationProviderType.AWS_AGENTCORE]
    runtimeArn: str
    runtimeId: str
    runtimeName: str
    runtimeVersion: str | int
    runtimeStatus: str
    serverProtocol: str
    lastUpdatedAt: datetime | None = None
    createdAt: datetime | None = None
    protocolConfiguration: dict[str, Any] | None = None
    authorizerConfiguration: dict[str, Any] | None = None
    runtimeTags: dict[str, str] = Field(default_factory=dict)
    enrichedAt: datetime | None = None


class AgentCoreA2AFederationMetadata(FederationMetadataBase):
    """Metadata for an A2A agent federated from AWS AgentCore."""

    providerType: Literal[FederationProviderType.AWS_AGENTCORE]
    runtimeArn: str
    runtimeId: str
    runtimeVersion: str | int
    runtimeStatus: str
    lastUpdatedAt: datetime | None = None
    createdAt: datetime | None = None
    failureReason: str | None = None
    workloadIdentityDetails: dict[str, Any] | None = None
    protocolConfiguration: dict[str, Any] | None = None
    authorizerConfiguration: dict[str, Any] | None = None
    runtimeTags: dict[str, str] = Field(default_factory=dict)
    enrichedAt: datetime | None = None


class AzureFoundryFederationMetadata(FederationMetadataBase):
    """Metadata for an A2A agent federated from Azure AI Foundry."""

    providerType: Literal[FederationProviderType.AZURE_AI_FOUNDRY]
    runtimeArn: str
    agentName: str
    agentVersion: str | int
    agentGuid: str | None = None
    versionId: str | None = None
    status: str | None = None
    createdAt: datetime | None = None
    modifiedAt: datetime | None = None
    projectEndpoint: str | None = None
    a2aBaseUrl: str | None = None
    agentCardPath: str | None = None
    authorizationSchemes: list[dict[str, Any]] = Field(default_factory=list)


A2AFederationMetadata = Annotated[
    AgentCoreA2AFederationMetadata | AzureFoundryFederationMetadata,
    Field(discriminator="providerType"),
]

AgentCoreFederationMetadata = AgentCoreMcpFederationMetadata | AgentCoreA2AFederationMetadata

FederationMetadata = AgentCoreMcpFederationMetadata | AgentCoreA2AFederationMetadata | AzureFoundryFederationMetadata


def extract_runtime_arn(metadata: FederationMetadata | None) -> str | None:
    """Return the stable remote identity shared by every provider variant."""
    return metadata.runtimeArn if metadata is not None else None


def extract_runtime_version(metadata: FederationMetadata | None) -> str | None:
    """Return the provider-specific runtime version as a string."""
    if metadata is None:
        return None
    if isinstance(metadata, AzureFoundryFederationMetadata):
        return str(metadata.agentVersion)
    return str(metadata.runtimeVersion)


def extract_enrichment_error(metadata: FederationMetadata | None) -> str | None:
    """Return the optional enrichment error from typed metadata."""
    error = metadata.enrichmentError if metadata is not None else None
    return str(error) if error else None


def detect_runtime_version_change(
    existing_metadata: FederationMetadata | None,
    new_metadata: FederationMetadata | None,
) -> list[str]:
    """Return the existing federation sync version-change message, if any."""
    old_version = extract_runtime_version(existing_metadata)
    new_version = extract_runtime_version(new_metadata)
    if old_version == new_version:
        return []

    version_label = (
        "agentVersion"
        if isinstance(existing_metadata, AzureFoundryFederationMetadata)
        or isinstance(new_metadata, AzureFoundryFederationMetadata)
        else "runtimeVersion"
    )
    return [f"{version_label}: {old_version} -> {new_version}"]
