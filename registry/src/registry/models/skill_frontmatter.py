"""Strict domain model for imported SKILL.md frontmatter."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator


class SkillFrontmatter(BaseModel):
    name: StrictStr = Field(min_length=1)
    description: StrictStr = Field(min_length=1)
    displayTitle: StrictStr | None = None
    category: StrictStr = "general"
    alwaysApply: StrictBool = False
    userInvocable: StrictBool = True
    disableModelInvocation: StrictBool = False
    allowedTools: list[StrictStr] | None = None
    tags: list[StrictStr] = Field(default_factory=list)

    @field_validator("name", "description")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    model_config = ConfigDict(extra="ignore", strict=True)
