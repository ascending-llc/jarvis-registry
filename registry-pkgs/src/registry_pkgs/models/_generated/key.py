from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel


class Key(Document):
    """
    Key Model

    Generated from: key.ts
    Schema version: asc0.5.1
    Generated at: 2026-04-02T17:28:14.216595Z
    """

    userId: PydanticObjectId = Field(...)  # references IUser collection
    name: str = Field(...)
    value: str = Field(...)
    expiresAt: datetime | None = Field(default=None)
    tenantId: str | None = Field(default=None)

    class Settings:
        name = "keys"
        keep_nulls = False

        indexes = [
            [("tenantId", 1)],
            IndexModel([("expiresAt", 1)], expireAfterSeconds=0),
        ]
