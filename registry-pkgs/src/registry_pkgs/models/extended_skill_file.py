from pydantic import Field

from ._generated import SkillFile

# SkillFile.source for files whose bytes Registry stores inline in `body` (created in Registry or by GitHub
# skill sync). Any other source was written by Jarvis Chat, whose file storage Registry cannot read.
REGISTRY_SKILL_FILE_SOURCE = "registry-inline"


class ExtendedSkillFile(SkillFile):
    body: bytes | None = Field(default=None, description="Raw bytes for registry-inline files")

    class Settings:
        name = "skillfiles"
        keep_nulls = False
        use_state_management = True
