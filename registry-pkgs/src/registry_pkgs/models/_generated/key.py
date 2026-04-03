from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field


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
        """
        Index definitions are intentionally left out to avoid conflicts.
        Consult Mongoose schema definitions in the jarvis-api project for index information.
        Note that you cannot know whether a WRITE operation **is possible** to violate
        a unique index constraint without knowing the index information.
        """

        name = "keys"
        keep_nulls = False
