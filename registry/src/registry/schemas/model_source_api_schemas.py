from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from registry_pkgs.models.enums import ModelSourceMode, ModelSourceProviderType

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


ModelSourceProviderConfigInput = Annotated[
    AwsBedrockModelConfigInput | AzureOpenAIModelConfigInput,
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


ModelSourceProviderConfigResponse = AwsBedrockModelConfigResponse | AzureOpenAIModelConfigResponse


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
