from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from registry_pkgs.models.enums import EmbeddingReindexJobStatus, ModelSourceMode, ModelSourceProviderType

from .server_api_schemas import PaginationMetadata


class AwsBedrockModelConfigInput(BaseModel):
    providerType: Literal[ModelSourceProviderType.AWS_BEDROCK] = ModelSourceProviderType.AWS_BEDROCK
    awsRegion: str = Field(min_length=1)
    modelIdOrArn: str = Field(min_length=1)
    baseModelId: str = Field(min_length=1)


class AzureOpenAIModelConfigInput(BaseModel):
    providerType: Literal[ModelSourceProviderType.AZURE_OPENAI] = ModelSourceProviderType.AZURE_OPENAI
    endpoint: str = Field(min_length=1)
    deploymentName: str = Field(min_length=1)
    baseModelId: str = Field(min_length=1)
    apiVersion: str = Field(min_length=1)
    apiKey: str | None = Field(
        default=None,
        min_length=1,
        description="Plaintext fallback credential; encrypted by the service before storage.",
    )


class _FirstPartyModelConfigInput(BaseModel):
    modelId: str = Field(min_length=1)
    baseUrl: str | None = Field(default=None, min_length=1)
    apiKey: str | None = Field(
        default=None,
        min_length=1,
        description="Required on create (first-party has no ambient identity); may be omitted on "
        "update to keep the stored key. Encrypted by the service before storage.",
    )

    @field_validator("baseUrl")
    @classmethod
    def _require_https(cls, value: str | None) -> str | None:
        if value is not None and not value.lower().startswith("https://"):
            raise ValueError("baseUrl must start with https://")
        return value


class AnthropicModelConfigInput(_FirstPartyModelConfigInput):
    providerType: Literal[ModelSourceProviderType.ANTHROPIC] = ModelSourceProviderType.ANTHROPIC


class OpenAIModelConfigInput(_FirstPartyModelConfigInput):
    providerType: Literal[ModelSourceProviderType.OPENAI] = ModelSourceProviderType.OPENAI


ModelSourceProviderConfigInput = Annotated[
    AwsBedrockModelConfigInput | AzureOpenAIModelConfigInput | AnthropicModelConfigInput | OpenAIModelConfigInput,
    Field(discriminator="providerType"),
]


class AwsBedrockModelConfigResponse(BaseModel):
    providerType: Literal[ModelSourceProviderType.AWS_BEDROCK] = ModelSourceProviderType.AWS_BEDROCK
    awsRegion: str
    modelIdOrArn: str
    baseModelId: str
    model_config = ConfigDict(use_enum_values=True)


class AzureOpenAIModelConfigResponse(BaseModel):
    providerType: Literal[ModelSourceProviderType.AZURE_OPENAI] = ModelSourceProviderType.AZURE_OPENAI
    endpoint: str
    deploymentName: str
    baseModelId: str
    apiVersion: str
    hasApiKey: bool = False
    model_config = ConfigDict(use_enum_values=True)


class _FirstPartyModelConfigResponse(BaseModel):
    modelId: str
    baseUrl: str | None = None
    hasApiKey: bool = False
    model_config = ConfigDict(use_enum_values=True)


class AnthropicModelConfigResponse(_FirstPartyModelConfigResponse):
    providerType: Literal[ModelSourceProviderType.ANTHROPIC] = ModelSourceProviderType.ANTHROPIC


class OpenAIModelConfigResponse(_FirstPartyModelConfigResponse):
    providerType: Literal[ModelSourceProviderType.OPENAI] = ModelSourceProviderType.OPENAI


ModelSourceProviderConfigResponse = (
    AwsBedrockModelConfigResponse
    | AzureOpenAIModelConfigResponse
    | AnthropicModelConfigResponse
    | OpenAIModelConfigResponse
)


class ModelSourceCreateRequest(BaseModel):
    displayName: str = Field(min_length=1, max_length=128)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    mode: ModelSourceMode
    providerConfig: ModelSourceProviderConfigInput


class ModelSourceUpdateRequest(BaseModel):
    displayName: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    tags: list[str] | None = None
    mode: ModelSourceMode | None = None
    providerConfig: ModelSourceProviderConfigInput | None = None

    @field_validator("displayName", "tags", "mode", "providerConfig")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        # These map to non-nullable ModelSource fields. Omitting them means "no change"
        # (they never enter model_fields_set), but an explicit JSON null would otherwise
        # persist an invalid document — reject it. Only `description` is nullable (clear).
        if value is None:
            raise ValueError("field may not be null; omit it to leave unchanged")
        return value


class ModelSourceMetadataResponse(BaseModel):
    maxInputTokens: int | None = None
    maxOutputTokens: int | None = None
    inputCostPerToken: float | None = None
    outputCostPerToken: float | None = None
    supportsPromptCaching: bool | None = None
    unavailableReason: str | None = None


class ModelSourceListItemResponse(BaseModel):
    id: str
    displayName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    mode: ModelSourceMode
    providerType: ModelSourceProviderType
    createdAt: datetime
    updatedAt: datetime
    model_config = ConfigDict(use_enum_values=True)


class ModelSourceDetailResponse(ModelSourceListItemResponse):
    providerConfig: ModelSourceProviderConfigResponse
    metadata: ModelSourceMetadataResponse | None = None
    createdBy: str | None = None
    updatedBy: str | None = None


class ModelSourcePagedResponse(BaseModel):
    modelSources: list[ModelSourceListItemResponse]
    pagination: PaginationMetadata


class ModelSourceDeleteResponse(BaseModel):
    id: str
    deletedAt: datetime


class ModelGatewaySelectionResponse(BaseModel):
    defaultWorkflowModelSourceId: str | None = None
    embeddingModelSourceId: str | None = None


class EmbeddingReindexJobResponse(BaseModel):
    id: str
    status: EmbeddingReindexJobStatus
    targetEmbeddingModelSourceId: str
    previousEmbeddingModelSourceId: str | None = None
    previousCollectionGeneration: str | None = None
    requestedBy: str | None = None
    startedAt: datetime
    switchedAt: datetime | None = None
    finishedAt: datetime | None = None
    attempts: int
    error: str | None = None
    lastError: str | None = None
    leaseOwner: str | None = None
    leaseExpiresAt: datetime | None = None
    heartbeatAt: datetime | None = None
    model_config = ConfigDict(use_enum_values=True)


class EmbeddingReindexJobListResponse(BaseModel):
    jobs: list[EmbeddingReindexJobResponse]


class SetDefaultWorkflowModelRequest(BaseModel):
    modelSourceId: str


class SetEmbeddingModelRequest(BaseModel):
    modelSourceId: str
