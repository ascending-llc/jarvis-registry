from datetime import UTC, datetime

from beanie import Document, Insert, PydanticObjectId, Replace, Save, before_event
from pydantic import ConfigDict, Field

MODEL_GATEWAY_SELECTION_ID = PydanticObjectId("000000000000000000000001")


class ModelGatewaySelection(Document):
    defaultWorkflowModelSourceId: PydanticObjectId | None = None
    embeddingModelSourceId: PydanticObjectId | None = None
    updatedBy: str | None = None
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "model_gateway_selection"

    @before_event(Insert, Replace, Save)
    async def update_timestamp(self) -> None:
        self.updatedAt = datetime.now(UTC)

    model_config = ConfigDict(populate_by_name=True)
