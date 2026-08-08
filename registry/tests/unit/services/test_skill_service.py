from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.skill_service import (
    compute_skill_content_hash,
    get_skill_with_files,
    list_skills,
)


def _make_skill(name: str = "test-skill", body: str = "body") -> MagicMock:
    skill = MagicMock()
    skill.id = PydanticObjectId()
    skill.name = name
    skill.description = "description"
    skill.alwaysApply = False
    skill.disableModelInvocation = False
    skill.userInvocable = True
    skill.allowedTools = None
    skill.category = "testing"
    skill.frontmatter = {"name": name, "description": "description"}
    skill.body = body
    skill.contentHash = None
    return skill


class TestComputeSkillContentHash:
    def test_is_deterministic_and_uses_versioned_sha256(self):
        skill = _make_skill()

        first = compute_skill_content_hash(skill)
        second = compute_skill_content_hash(skill)

        assert first == second
        assert first.startswith("sha256:")
        assert len(first) == 71

    def test_body_change_changes_hash(self):
        original = _make_skill(body="original body")
        changed = _make_skill(body="changed body")

        assert compute_skill_content_hash(original) != compute_skill_content_hash(changed)

    def test_non_body_fields_do_not_affect_hash(self):
        skill_a = _make_skill()
        skill_b = _make_skill()
        skill_b.name = "different-name"
        skill_b.description = "different description"
        skill_b.category = "other"
        skill_b.frontmatter = {"different": True}
        skill_b.alwaysApply = True
        skill_b.disableModelInvocation = True
        skill_b.userInvocable = False
        skill_b.allowedTools = ["bash"]

        assert compute_skill_content_hash(skill_a) == compute_skill_content_hash(skill_b)

    def test_manifest_contains_format_and_body(self):
        from registry.services.skill_service import _canonical_manifest

        skill = _make_skill(body="test body")
        manifest = _canonical_manifest(skill)

        assert manifest == {
            "format": "jarvis-skill-content-v1",
            "skill": {"body": "test body"},
        }


class TestListSkills:
    @pytest.mark.asyncio
    @patch("registry.services.skill_service.Skill")
    async def test_returns_body_only_skills_with_hashes(self, mock_skill_cls):
        skill = _make_skill()
        skill.save = AsyncMock()

        skill_query = MagicMock()
        skill_query.sort.return_value = skill_query
        skill_query.to_list = AsyncMock(return_value=[skill])
        mock_skill_cls.find.return_value = skill_query

        result = await list_skills()

        assert result == [skill]
        assert skill.contentHash.startswith("sha256:")
        skill.save.assert_not_awaited()
        mock_skill_cls.find.assert_called_once_with({"fileCount": 0})

    @pytest.mark.asyncio
    @patch("registry.services.skill_service.Skill")
    async def test_returns_empty_when_no_body_only_skills(self, mock_skill_cls):
        query = MagicMock()
        query.sort.return_value = query
        query.to_list = AsyncMock(return_value=[])
        mock_skill_cls.find.return_value = query

        result = await list_skills()

        assert result == []
        mock_skill_cls.find.assert_called_once_with({"fileCount": 0})


class TestGetSkillWithFiles:
    @pytest.mark.asyncio
    @patch("registry.services.skill_service.SkillFile")
    @patch("registry.services.skill_service.Skill")
    async def test_returns_files_and_computes_hash_without_saving(self, mock_skill_cls, mock_file_cls):
        skill_id = PydanticObjectId()
        skill = _make_skill()
        skill.id = skill_id
        skill.save = AsyncMock()
        skill_file = MagicMock()
        skill_file.skillId = skill_id
        skill_file.relativePath = "references/guide.md"
        mock_skill_cls.get = AsyncMock(return_value=skill)
        mock_file_cls.skillId = "skillId"
        mock_file_cls.find.return_value.to_list = AsyncMock(return_value=[skill_file])

        result_skill, files = await get_skill_with_files(skill_id)

        assert result_skill is skill
        assert files == [skill_file]
        assert skill.contentHash.startswith("sha256:")
        skill.save.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("registry.services.skill_service.Skill")
    async def test_raises_on_not_found(self, mock_skill_cls):
        mock_skill_cls.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await get_skill_with_files(PydanticObjectId())
