"""
Extended Skill Model for Registry-Specific Fields
"""

from datetime import datetime

from pydantic import Field

from ._generated import Skill


class ExtendedSkill(Skill):
    """Extended Skill Document with Registry-Specific Fields"""

    category: str = ""
    deletedAt: datetime | None = Field(default=None, description="Soft-delete timestamp for delta sync")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    path: str | None = Field(default=None, description="Slug path for CLI local directory reconstruction")

    class Settings:
        name = "skills"
        keep_nulls = False
        use_state_management = True
