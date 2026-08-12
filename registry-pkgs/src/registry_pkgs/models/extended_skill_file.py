from pydantic import Field

from ._generated import SkillFile


class ExtendedSkillFile(SkillFile):
    body: bytes | None = Field(default=None, description="Raw bytes for registry-inline files")

    class Settings:
        name = "skillfiles"
        keep_nulls = False
        use_state_management = True
