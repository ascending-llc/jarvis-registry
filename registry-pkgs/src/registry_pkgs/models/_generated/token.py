from datetime import UTC, datetime
from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import Field


class Token(Document):
    userId: PydanticObjectId
    email: str | None = None
    type: str | None = None
    identifier: str | None = None
    token: str
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expiresAt: datetime
    metadata: dict[str, Any] | None = None
    tenantId: str | None = None

    class Settings:
        name = "tokens"
        keep_nulls = False
        use_state_management = True
