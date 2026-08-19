from registry.services.skill_sync_discovery_service import (
    SkillSyncDiscoveryService,
    _parse_frontmatter,
)
from registry.services.skill_sync_github_service import DiscoveredFile
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


def _file(path: str, content: str | bytes = "") -> DiscoveredFile:
    if isinstance(content, str):
        content = content.encode()
    return DiscoveredFile(relative_path=path, content=content, size=len(content))


# ── discover_skills ───────────────────────────────────────────


def test_discover_single_skill():
    files = [_file("skills/hello.md", _md("hello", "A hello skill", "Say hello"))]
    result = SkillSyncDiscoveryService().discover_skills(files)

    assert len(result.skills) == 1
    assert result.skills[0].name == "hello"
    assert result.skills[0].description == "A hello skill"
    assert result.skills[0].body == "Say hello"
    assert result.skills[0].upstream_id == "skills/hello.md"
    assert result.summary.discoveredSkillCount == 1


def test_discover_multiple_skills():
    files = [
        _file("skills/a.md", _md("alpha", "Skill A")),
        _file("skills/b.md", _md("beta", "Skill B")),
    ]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 2
    names = {s.name for s in result.skills}
    assert names == {"alpha", "beta"}


def test_discover_with_auxiliary_files():
    files = [
        _file("skills/deploy.md", _md("deploy", "Deploy skill")),
        _file("skills/helper.py", "print('hi')"),
        _file("skills/config.json", '{"key": "val"}'),
    ]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 1
    assert len(result.skills[0].files) == 2


def test_discover_frontmatter_fields():
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
    files = [_file("skills/test.md", content)]
    result = SkillSyncDiscoveryService().discover_skills(files)
    skill = result.skills[0]
    assert skill.category == "devops"
    assert skill.always_apply is True
    assert skill.user_invocable is False
    assert skill.disable_model_invocation is True
    assert skill.tags == ["tag1", "tag2"]
    assert skill.display_title == "Test Skill Title"


def test_discover_defaults():
    files = [_file("skills/minimal.md", _md("minimal", "Minimal skill"))]
    result = SkillSyncDiscoveryService().discover_skills(files)
    skill = result.skills[0]
    assert skill.category == "general"
    assert skill.always_apply is False
    assert skill.user_invocable is True
    assert skill.disable_model_invocation is False
    assert skill.allowed_tools is None
    assert skill.tags == []


# ── error cases ───────────────────────────────────────────────


def test_missing_name():
    content = "---\ndescription: no name\n---\nbody"
    files = [_file("skills/bad.md", content)]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_NAME_MISSING


def test_missing_description():
    content = "---\nname: no-desc\n---\nbody"
    files = [_file("skills/bad.md", content)]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.SKILL_PARSE_FAILED


def test_duplicate_name():
    files = [
        _file("skills/a.md", _md("dup", "First")),
        _file("skills/b.md", _md("dup", "Second")),
    ]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 1
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.DUPLICATE_SKILL_NAME


def test_too_many_files(monkeypatch):
    monkeypatch.setattr("registry.services.skill_sync_discovery_service.MAX_FILES_PER_SKILL", 2)
    files = [
        _file("skills/deploy.md", _md("deploy", "Deploy")),
        _file("skills/a.py", "a"),
        _file("skills/b.py", "b"),
        _file("skills/c.py", "c"),
    ]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert len(result.errors) == 1
    assert result.errors[0].errorCode == SkillSyncSkillErrorCode.TOO_MANY_FILES


def test_no_frontmatter_skipped():
    files = [_file("skills/plain.md", "# Just a readme\nNo frontmatter.")]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert len(result.errors) == 0
    assert "skills/plain.md" in result.summary.skippedPaths


def test_non_md_files_not_skills():
    files = [
        _file("skills/script.py", "print('hello')"),
        _file("skills/data.json", "{}"),
    ]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert len(result.errors) == 0


def test_invalid_yaml_skipped():
    content = "---\n[invalid yaml: {{{\n---\nbody"
    files = [_file("skills/bad.md", content)]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert "skills/bad.md" in result.summary.skippedPaths


def test_binary_content_skipped():
    files = [_file("skills/binary.md", b"\x80\x81\x82\x83")]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert "skills/binary.md" in result.summary.skippedPaths


def test_empty_frontmatter_skipped():
    content = "---\n\n---\nbody"
    files = [_file("skills/empty.md", content)]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert len(result.skills) == 0
    assert "skills/empty.md" in result.summary.skippedPaths


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


def test_discovery_summary_file_count():
    files = [
        _file("a/skill.md", _md("alpha", "A")),
        _file("a/helper.py", "x"),
        _file("b/skill.md", _md("beta", "B")),
    ]
    result = SkillSyncDiscoveryService().discover_skills(files)
    assert result.summary.discoveredSkillCount == 2
    assert result.summary.discoveredFileCount == 3
