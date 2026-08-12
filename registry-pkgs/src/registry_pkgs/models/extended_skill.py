"""
Extended Skill Model for Registry-Specific Fields
"""

from datetime import datetime

from pydantic import Field

from ._generated import Skill


class ExtendedSkill(Skill):
    """Extended Skill Document with Registry-Specific Fields"""

    deletedAt: datetime | None = Field(default=None, description="Soft-delete timestamp for delta sync")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    path: str | None = Field(default=None, description="Slug path for CLI local directory reconstruction")
    enabled: bool = Field(default=True, description="Whether the skill is enabled")
    createdByRegistry: bool = Field(default=False, description="Whether Registry created and owns the skill")

    class Settings:
        name = "skills"
        keep_nulls = False
        use_state_management = True
