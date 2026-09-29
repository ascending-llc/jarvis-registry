from unittest.mock import patch

import pytest
from beanie.odm.settings.document import DocumentSettings
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

    def test_legacy_document_with_path_still_loads(self):
        legacy_document = {
            "_id": "65f000000000000000000001",
            "name": "demo",
            "description": "Demo skill",
            "author": "65f000000000000000000002",
            "authorName": "Test User",
            "path": "skills/demo",
        }

        # Beanie validation needs collection settings; supply defaults instead of initializing a database.
        with patch.object(ExtendedSkill, "get_settings", return_value=DocumentSettings()):
            skill = ExtendedSkill.model_validate(legacy_document)

        assert skill.name == "demo"
        assert not hasattr(skill, "path")
        assert "path" not in skill.model_dump()

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
