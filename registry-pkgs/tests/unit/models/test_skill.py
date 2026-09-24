import pytest
from pydantic import ValidationError

from registry_pkgs.models import ExtendedSkill, ExtendedSkillFile, SkillSource


class TestExtendedSkill:
    def test_registry_fields_have_backward_compatible_defaults(self):
        skill = ExtendedSkill.model_construct(authorName="Test User")

        assert skill.source == SkillSource.INLINE
        assert skill.enabled is True
        assert skill.createdByRegistry is False

    def test_path_field_is_not_defined(self):
        # Removed: it duplicated `name` (API-created skills) or `sourceMetadata.skillPath` (synced skills).
        assert "path" not in ExtendedSkill.model_fields

    def test_unknown_fields_are_ignored_so_legacy_documents_with_path_still_load(self):
        assert ExtendedSkill.model_config.get("extra", "ignore") == "ignore"

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
