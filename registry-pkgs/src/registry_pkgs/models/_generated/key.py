from datetime import datetime

from beanie import Document, PydanticObjectId


class Key(Document):
    userId: PydanticObjectId
    name: str
    value: str
    expiresAt: datetime | None = None
    tenantId: str | None = None

    class Settings:
        name = "keys"
        keep_nulls = False
        use_state_management = True
