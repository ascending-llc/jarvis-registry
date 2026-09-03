"""Unit tests for Claude Code SKILL.md frontmatter models."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from registry.models.skill_frontmatter import (
    ClaudeCodeSkillFrontmatter,
    dump_claude_code_frontmatter,
    parse_claude_code_frontmatter,
)


def test_accepts_canonical_kebab_case_and_dumps_camel_case() -> None:
    frontmatter = parse_claude_code_frontmatter(
        {
            "name": "review-skill",
            "description": "  Review pull requests  ",
            "allowed-tools": ["Read", "Grep"],
            "argument-hint": "[pull-request]",
            "disallowed-tools": "Write",
            "arguments": ["subcommand"],
            "disable-model-invocation": True,
            "user-invocable": False,
        }
    )

    assert frontmatter.description == "Review pull requests"
    assert frontmatter.allowedTools == ["Read", "Grep"]
    assert frontmatter.argumentHint == "[pull-request]"
    assert frontmatter.disallowedTools == "Write"
    assert frontmatter.arguments == ["subcommand"]
    assert frontmatter.disableModelInvocation is True
    assert frontmatter.userInvocable is False
    assert "allowedTools" in frontmatter.model_dump()
    assert "allowed-tools" not in frontmatter.model_dump()


def test_accepts_legacy_camel_case_keys() -> None:
    frontmatter = parse_claude_code_frontmatter(
        {
            "name": "legacy-skill",
            "description": "Legacy",
            "allowedTools": ["Read"],
            "argumentHint": "[path]",
            "disableModelInvocation": True,
            "userInvocable": False,
        }
    )

    assert frontmatter.allowedTools == ["Read"]
    assert frontmatter.argumentHint == "[path]"
    assert frontmatter.disableModelInvocation is True
    assert frontmatter.userInvocable is False


def test_preserves_unknown_keys_at_top_level_without_mutating_input() -> None:
    raw = {
        "name": "custom-skill",
        "description": "Custom",
        "foo": "top-level",
        "metadata": {"foo": "explicit", "owner": "platform"},
    }
    original = deepcopy(raw)

    frontmatter = parse_claude_code_frontmatter(raw)

    assert frontmatter.foo == "top-level"
    assert frontmatter.metadata == {"foo": "explicit", "owner": "platform"}
    assert frontmatter.model_dump()["foo"] == "top-level"
    assert raw == original


def test_dump_excludes_registry_bookkeeping_and_preserves_open_fields() -> None:
    frontmatter = parse_claude_code_frontmatter(
        {
            "name": "portable-skill",
            "description": "Portable",
            "displayTitle": "Display title",
            "category": "Code",
            "alwaysApply": True,
            "tags": ["review"],
            "arguments": ["subcommand"],
            "future-field": {"enabled": True},
        }
    )

    assert dump_claude_code_frontmatter(frontmatter) == {
        "disableModelInvocation": False,
        "userInvocable": True,
        "arguments": ["subcommand"],
        "future-field": {"enabled": True},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metadata", "opaque-metadata"),
        ("hooks", []),
        ("arguments", {"subcommand": "review"}),
    ],
)
def test_preserves_opaque_extra_field_values(field: str, value: object) -> None:
    frontmatter = parse_claude_code_frontmatter(
        {
            "name": "opaque-skill",
            "description": "Opaque",
            field: value,
        }
    )

    assert frontmatter.model_dump()[field] == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["Read", "Grep"], ["Read", "Grep"]),
        ("Read Grep", ["Read", "Grep"]),
        ("Read, Grep", ["Read", "Grep"]),
        ("Read,  Grep Write", ["Read", "Grep", "Write"]),
    ],
)
def test_accepts_allowed_tools_as_list_or_delimited_string(value: object, expected: list[str]) -> None:
    frontmatter = parse_claude_code_frontmatter(
        {
            "name": "tools-skill",
            "description": "Tools",
            "allowed-tools": value,
        }
    )

    assert frontmatter.allowedTools == expected


def test_tokenizes_allowed_tools_without_splitting_spaces_inside_parentheses() -> None:
    frontmatter = parse_claude_code_frontmatter(
        {
            "name": "git-skill",
            "description": "Git",
            "allowed-tools": "Bash(git add *) Bash(git commit *) Bash(git status *)",
        }
    )

    assert frontmatter.allowedTools == [
        "Bash(git add *)",
        "Bash(git commit *)",
        "Bash(git status *)",
    ]


@pytest.mark.parametrize(
    "name",
    ["has/slash", "has\\backslash", "..", "UPPER", "has space", "leading-", "-trailing"],
)
def test_rejects_non_slug_names(name: str) -> None:
    with pytest.raises(ValidationError):
        parse_claude_code_frontmatter({"name": name, "description": "Invalid"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowed-tools", 123),
        ("allowed-tools", {"tool": "Read"}),
        ("allowed-tools", ["Read", 123]),
        ("disable-model-invocation", "false"),
        ("user-invocable", 1),
    ],
)
def test_rejects_invalid_strict_field_types(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        parse_claude_code_frontmatter(
            {
                "name": "strict-skill",
                "description": "Strict",
                field: value,
            }
        )


def test_defaults_match_claude_code_behavior() -> None:
    frontmatter = ClaudeCodeSkillFrontmatter.model_validate(
        {
            "name": "minimal-skill",
            "description": "Minimal",
        }
    )

    assert frontmatter.allowedTools is None
    assert frontmatter.disableModelInvocation is False
    assert frontmatter.userInvocable is True
    assert frontmatter.model_extra == {}
