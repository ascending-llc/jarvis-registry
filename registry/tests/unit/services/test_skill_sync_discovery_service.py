from pathlib import Path

import pytest

from registry.services.skill_sync_discovery_service import (
    SkillSyncDiscoveryService,
    _parse_frontmatter,
)
from registry.services.skill_sync_github_service import ExtractedAuxFile, ExtractedSkillFolder, ExtractionResult
from registry_pkgs.models.enums import SkillSyncSkillErrorCode


def _md(name: str, description: str = "A skill", body: str = "Hello world", **extra) -> str:
    lines = [f"name: {name}", f"description: {description}"]
    for k, v in extra.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    fm = "\n".join(lines)
    return f"---\n{fm}\n---\n{body}"


def _skill_folder(
    tmp_path: Path,
    path: str,
    skill_md_content: str | bytes,
    aux_files: dict[str, bytes] | None = None,
) -> ExtractedSkillFolder:
    folder_dir = tmp_path / path
    folder_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = folder_dir / "SKILL.md"
    if isinstance(skill_md_content, str):
        skill_md_path.write_text(skill_md_content)
    else:
        skill_md_path.write_bytes(skill_md_content)

    extracted_aux: list[ExtractedAuxFile] = []
    if aux_files:
        for name, content in aux_files.items():
            file_path = folder_dir / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
            extracted_aux.append(
                ExtractedAuxFile(
                    relative_path=f"{path}/{name}",
                    absolute_path=file_path,
                    size=len(content),
                )
            )

    return ExtractedSkillFolder(
        root_relative_path=path,
        skill_md_path=skill_md_path,
        aux_files=extracted_aux,
    )


def _extraction(
    skill_folders: list[ExtractedSkillFolder] | None = None,
    skipped_paths: list[str] | None = None,
    oversized_skill_paths: list[str] | None = None,
) -> ExtractionResult:
    return ExtractionResult(
        skill_folders=skill_folders or [],
        skipped_paths=skipped_paths or [],
        oversized_skill_paths=oversized_skill_paths or [],
    )


# ── discover_skills ───────────────────────────────────────────


def test_discover_single_skill(tmp_path):
    folder = _skill_folder(tmp_path, "skills/hello", _md("hello", "A hello skill", "Say hello"))
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))

    assert len(result.skills) == 1
    assert result.skills[0].name == "hello"
    assert result.skills[0].description == "A hello skill"
    assert result.skills[0].body == "Say hello"
    assert result.skills[0].upstream_id == "skills/hello"
    assert result.summary.discoveredSkillCount == 1


def test_discover_multiple_skills(tmp_path):
    folders = [
        _skill_folder(tmp_path, "skills/a", _md("alpha", "Skill A")),
        _skill_folder(tmp_path, "skills/b", _md("beta", "Skill B")),
    ]
    result = SkillSyncDiscoveryService().discover_skills(_extraction(folders))
    assert len(result.skills) == 2
    names = {s.name for s in result.skills}
    assert names == {"alpha", "beta"}


def test_discover_with_auxiliary_files(tmp_path):
    folder = _skill_folder(
        tmp_path,
        "skills/deploy",
        _md("deploy", "Deploy skill"),
        aux_files={"helper.py": b"print('hi')", "config.json": b'{"key": "val"}'},
    )
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 1
    assert len(result.skills[0].files) == 2


def test_discover_frontmatter_fields(tmp_path):
    content = _md(
        "test-skill",
        "Test desc",
        "Body here",
        category="devops",
        alwaysApply=True,
        userInvocable=False,
        disableModelInvocation=True,
        tags=["tag1", "tag2"],
        displayTitle="Test Skill Title",
    )
    folder = _skill_folder(tmp_path, "skills/test", content)
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    skill = result.skills[0]
    assert skill.category == "devops"
    assert skill.always_apply is True
    assert skill.user_invocable is False
    assert skill.disable_model_invocation is True
    assert skill.tags == ["tag1", "tag2"]
    assert skill.display_title == "Test Skill Title"


def test_discover_defaults(tmp_path):
    folder = _skill_folder(tmp_path, "skills/minimal", _md("minimal", "Minimal skill"))
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    skill = result.skills[0]
    assert skill.category == "general"
    assert skill.always_apply is False
    assert skill.user_invocable is True
    assert skill.disable_model_invocation is False
    assert skill.allowed_tools is None
    assert skill.tags == []


# ── error cases ───────────────────────────────────────────────


def test_missing_name(tmp_path):
    folder = _skill_folder(tmp_path, "skills/bad", "---\ndescription: no name\n---\nbody")
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_NAME_MISSING


def test_missing_description(tmp_path):
    folder = _skill_folder(tmp_path, "skills/bad", "---\nname: no-desc\n---\nbody")
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_duplicate_name(tmp_path):
    folders = [
        _skill_folder(tmp_path, "skills/a", _md("dup", "First")),
        _skill_folder(tmp_path, "skills/b", _md("dup", "Second")),
    ]
    result = SkillSyncDiscoveryService().discover_skills(_extraction(folders))
    assert len(result.skills) == 1
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.DUPLICATE_SKILL_NAME


def test_too_many_files(tmp_path, monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_discovery_service.MAX_FILES_PER_SKILL", 2)
    folder = _skill_folder(
        tmp_path,
        "skills/deploy",
        _md("deploy", "Deploy"),
        aux_files={"a.py": b"a", "b.py": b"b", "c.py": b"c"},
    )
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.TOO_MANY_FILES


def test_no_frontmatter_error(tmp_path):
    folder = _skill_folder(tmp_path, "skills/plain", "# Just a readme\nNo frontmatter.")
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_invalid_yaml_error(tmp_path):
    folder = _skill_folder(tmp_path, "skills/bad", "---\n[invalid yaml: {{{\n---\nbody")
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_binary_content_error(tmp_path):
    folder = _skill_folder(tmp_path, "skills/binary", b"\x80\x81\x82\x83")
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_empty_frontmatter_error(tmp_path):
    folder = _skill_folder(tmp_path, "skills/empty", "---\n\n---\nbody")
    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_quoted_false_boolean_is_rejected(tmp_path):
    folder = _skill_folder(
        tmp_path,
        "skills/bad-bool",
        '---\nname: bad-bool\ndescription: A skill\nalwaysApply: "false"\n---\nBody',
    )

    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))

    assert result.skills == []
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tags", "not-a-list"),
        ("allowedTools", "not-a-list"),
        ("description", 123),
    ],
)
def test_invalid_frontmatter_field_type_is_rejected(tmp_path, field, value):
    content = _md("invalid-type")
    content = content.replace("---\nHello world", f"{field}: {value}\n---\nHello world")
    folder = _skill_folder(tmp_path, f"skills/{field}", content)

    result = SkillSyncDiscoveryService().discover_skills(_extraction([folder]))

    assert result.skills == []
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_oversized_skill_paths_become_errors():
    result = SkillSyncDiscoveryService().discover_skills(_extraction(oversized_skill_paths=["skills/big"]))
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.FILE_TOO_LARGE
    assert result.errors[0].skillPath == "skills/big"


def test_skipped_paths_in_summary():
    result = SkillSyncDiscoveryService().discover_skills(
        _extraction(skipped_paths=["skills/bare-file.md", "skills/no-skill-folder"])
    )
    assert result.summary.skippedPaths == ["skills/bare-file.md", "skills/no-skill-folder"]


# ── _parse_frontmatter ────────────────────────────────────────


def test_parse_frontmatter_valid():
    content = "---\nname: test\ndescription: hello\n---\nBody text"
    result = _parse_frontmatter(content)
    assert result is not None
    fm, body = result
    assert fm["name"] == "test"
    assert body == "Body text"


def test_parse_frontmatter_no_fence():
    assert _parse_frontmatter("No frontmatter here") is None


def test_parse_frontmatter_unclosed():
    assert _parse_frontmatter("---\nname: test\nno closing fence") is None


def test_parse_frontmatter_not_dict():
    assert _parse_frontmatter("---\n- item1\n- item2\n---\nbody") is None


def test_parse_frontmatter_leading_whitespace():
    content = "  \n---\nname: test\n---\nbody"
    result = _parse_frontmatter(content)
    assert result is not None
    assert result[0]["name"] == "test"


def test_discovery_summary_file_count(tmp_path):
    folders = [
        _skill_folder(tmp_path, "a", _md("alpha", "A"), aux_files={"helper.py": b"x"}),
        _skill_folder(tmp_path, "b", _md("beta", "B")),
    ]
    result = SkillSyncDiscoveryService().discover_skills(_extraction(folders))
    assert result.summary.discoveredSkillCount == 2
    assert result.summary.discoveredFileCount == 3
