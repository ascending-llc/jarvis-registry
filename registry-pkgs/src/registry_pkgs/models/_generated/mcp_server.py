from datetime import datetime
from typing import Any

from beanie import Document, PydanticObjectId


class MCPServer(Document):
    serverName: str
    config: dict[str, Any]
    author: PydanticObjectId
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "mcpservers"
        keep_nulls = False
        use_state_management = True
