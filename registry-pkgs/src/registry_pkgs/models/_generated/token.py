from datetime import UTC, datetime
from typing import Any

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel


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
        name = "tokens"
        keep_nulls = False

        indexes = [
            [("tenantId", 1)],
            IndexModel([("expiresAt", 1)], expireAfterSeconds=0),
        ]
