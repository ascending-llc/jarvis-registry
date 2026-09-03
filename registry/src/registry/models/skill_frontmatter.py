"""Pydantic domain models for SKILL.md frontmatter.

Only fields Registry interprets or elevates to a top-level Skill column are explicitly
validated. Every other field passes through under its own top-level key via ``extra="allow"``.
Claude Code's field set is open-ended, and relocating an unfamiliar behavioral field into
``metadata`` can break a valid skill because Claude Code does not act on metadata contents.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

_MERGE_ALIASES = {
    "allowed-tools": "allowedTools",
    "disallowed-tools": "disallowedTools",
    "argument-hint": "argumentHint",
    "disable-model-invocation": "disableModelInvocation",
    "user-invocable": "userInvocable",
}

_REGISTRY_BOOKKEEPING_FIELDS = {
    "name",
    "description",
    "displayTitle",
    "category",
    "alwaysApply",
    "tags",
}


def _tokenize_allowed_tools(value: str) -> list[str]:
    """Split allowed-tools on separators outside parenthesized tool specifiers."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for character in value.strip():
        if character == "(":
            depth += 1
            current.append(character)
            continue
        if character == ")":
            depth = max(0, depth - 1)
            current.append(character)
            continue
        if depth == 0 and (character == "," or character.isspace()):
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(character)
    if current:
        tokens.append("".join(current))
    return tokens


class SkillFrontmatter(BaseModel):
    name: StrictStr = Field(min_length=1, max_length=64, pattern=SKILL_NAME_PATTERN)
    description: StrictStr = Field(min_length=1, max_length=1024)
    allowedTools: list[StrictStr] | None = Field(default=None, alias="allowed-tools")

    @field_validator("allowedTools", mode="before")
    @classmethod
    def _split_allowed_tools(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _tokenize_allowed_tools(value)
        return value

    @field_validator("description")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    model_config = ConfigDict(extra="allow", strict=True, populate_by_name=True)


class ClaudeCodeSkillFrontmatter(SkillFrontmatter):
    disableModelInvocation: StrictBool = Field(default=False, alias="disable-model-invocation")
    userInvocable: StrictBool = Field(default=True, alias="user-invocable")


def parse_claude_code_frontmatter(raw: dict[str, Any]) -> ClaudeCodeSkillFrontmatter:
    """Normalize merge-ambiguous aliases, validate known fields, and pass extras through."""
    normalized = {_MERGE_ALIASES.get(key, key): value for key, value in raw.items()}
    return ClaudeCodeSkillFrontmatter.model_validate(normalized)


def dump_claude_code_frontmatter(
    frontmatter: ClaudeCodeSkillFrontmatter,
    *,
    exclude_unset: bool = False,
) -> dict[str, Any]:
    """Serialize portable frontmatter without Registry-only bookkeeping fields."""
    return frontmatter.model_dump(
        exclude=_REGISTRY_BOOKKEEPING_FIELDS,
        exclude_unset=exclude_unset,
        exclude_none=True,
    )
