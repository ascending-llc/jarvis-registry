"""Singleton the worker publishes so Registry knows which tools run and whether a result is outdated.

Same fixed-id singleton pattern as ModelGatewaySelection.
"""

from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import BaseModel

SKILL_SCAN_TOOLSET_ID = PydanticObjectId("000000000000000000000001")


class SkillScanToolDescriptor(BaseModel):
    tool: str
    toolVersion: str
    staticFingerprint: str  # tool name + pinned version + adapter-config hash (no LLM part)


class SkillScanToolset(Document):
    tools: list[SkillScanToolDescriptor]
    publishedBy: str  # worker lease owner id
    publishedAt: datetime

    class Settings:
        name = "skill_scan_toolsets"
