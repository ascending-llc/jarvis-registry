import pytest
from pydantic import ValidationError

from registry_pkgs.models import ExtendedSkill, ExtendedSkillFile, SkillSource


class TestExtendedSkill:
    def test_registry_fields_have_backward_compatible_defaults(self):
        skill = ExtendedSkill.model_construct(authorName="Test User")

        assert skill.source == SkillSource.INLINE
        assert skill.enabled is True
        assert skill.createdByRegistry is False

    def test_shared_skill_fields_are_inherited_from_generated_model(self):
        skill = ExtendedSkill.model_construct(
            displayTitle="Test Skill",
            authorName="Test User",
            sourceMetadata={"repository": "example/repo"},
        )

        assert skill.displayTitle == "Test Skill"
        assert skill.authorName == "Test User"
        assert skill.sourceMetadata == {"repository": "example/repo"}

    def test_skill_file_requires_shared_source(self):
        with pytest.raises(ValidationError):
            ExtendedSkillFile(
                skillId="000000000000000000000001",
                relativePath="SKILL.md",
                mimeType="text/markdown",
                bytes=1,
            )
