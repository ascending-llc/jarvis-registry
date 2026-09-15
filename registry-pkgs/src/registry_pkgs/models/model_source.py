from datetime import UTC, datetime
from typing import Annotated, Literal

from beanie import Document, Insert, Replace, Save, before_event
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from .enums import ModelSourceMode, ModelSourceProviderType


class AwsBedrockModelConfig(BaseModel):
    providerType: Literal[ModelSourceProviderType.AWS_BEDROCK] = ModelSourceProviderType.AWS_BEDROCK
    awsRegion: str = Field(min_length=1, description="AWS region, e.g. us-east-1")
    modelIdOrArn: str = Field(
        min_length=1,
        description="Bedrock foundation model id, or a full Application Inference Profile ARN. "
        "litellm accepts either as the model id. No AWS credential fields — the pod's IRSA is "
        "assumed to already have the right IAM permissions.",
    )
    baseModelId: str = Field(
        min_length=1,
        description="Canonical Bedrock foundation-model id used ONLY to look up metadata via "
        "litellm.get_model_info() — an AIP ARN is not a key litellm's model-cost map recognizes. "
        "When modelIdOrArn is already a plain foundation model id, set this to the same value.",
    )

    model_config = ConfigDict(populate_by_name=True)


class AzureOpenAIModelConfig(BaseModel):
    providerType: Literal[ModelSourceProviderType.AZURE_OPENAI] = ModelSourceProviderType.AZURE_OPENAI
    endpoint: str = Field(
        min_length=1,
        description="Azure OpenAI resource endpoint, e.g. https://{resource}.openai.azure.com",
    )
    deploymentName: str = Field(
        min_length=1,
        description="The customer-chosen Azure OpenAI deployment name, used for the actual API calls",
    )
    baseModelId: str = Field(
        min_length=1,
        description="Canonical OpenAI model id (e.g. 'gpt-4o') used ONLY for litellm.get_model_info() "
        "lookups — a deployment name is arbitrary and will not resolve.",
    )
    apiVersion: str = Field(min_length=1, description="Azure OpenAI api-version, e.g. 2024-10-21")
    apiKeyEncrypted: str | None = Field(
        default=None,
        description="Fallback credential, encrypted at rest, used only when Azure AD Workload "
        "Identity is not configured on this cluster. The primary credential path (Workload "
        "Identity / DefaultAzureCredential) needs no field here — it is resolved ambiently from "
        "the pod's federated identity by the model-instantiation code added in AS-1852/AS-1853.",
    )

    model_config = ConfigDict(populate_by_name=True)


ModelSourceProviderConfig = Annotated[
    AwsBedrockModelConfig | AzureOpenAIModelConfig,
    Field(discriminator="providerType"),
]


class ModelSource(Document):
    displayName: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    mode: ModelSourceMode
    providerConfig: ModelSourceProviderConfig
    createdBy: str | None = None
    updatedBy: str | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deletedAt: datetime | None = None

    class Settings:
        name = "model_sources"
        keep_nulls = False
        use_state_management = True
        indexes = [
            IndexModel([("providerConfig.providerType", 1), ("mode", 1), ("updatedAt", -1)]),
            IndexModel([("mode", 1), ("deletedAt", 1)]),
            IndexModel([("displayName", "text"), ("description", "text")]),
        ]

    @before_event(Insert, Replace, Save)
    async def update_timestamps(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
