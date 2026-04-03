from datetime import UTC, datetime
from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import Field


class Token(Document):
    """
    Token Model

    Generated from: token.ts
    Schema version: asc0.5.1
    Generated at: 2026-04-02T17:28:14.234664Z
    """

    userId: PydanticObjectId = Field(...)  # references IUser collection
    email: str | None = Field(default=None)
    type: str | None = Field(default=None)
    identifier: str | None = Field(default=None)
    token: str = Field(...)
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expiresAt: datetime = Field(...)
    metadata: dict[str, Any] | None = Field(default=None)
    tenantId: str | None = Field(default=None)

    class Settings:
        """
        Index definitions are intentionally left out to avoid conflicts.
        Consult Mongoose schema definitions in the jarvis-api project for index information.
        Note that you cannot know whether a WRITE operation **is possible** to violate
        a unique index constraint without knowing the index information.
        """

        name = "tokens"
        keep_nulls = False
