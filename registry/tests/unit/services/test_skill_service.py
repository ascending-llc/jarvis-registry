from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.skill_service import get_skill_with_files, list_skills


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
    return skill


class TestListSkills:
    @pytest.mark.asyncio
    @patch("registry.services.skill_service.Skill")
    async def test_returns_body_only_skills(self, mock_skill_cls):
        skill = _make_skill()

        skill_query = MagicMock()
        skill_query.sort.return_value = skill_query
        skill_query.to_list = AsyncMock(return_value=[skill])
        mock_skill_cls.find.return_value = skill_query

        result = await list_skills()

        assert result == [skill]
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
    async def test_returns_skill_and_files(self, mock_skill_cls, mock_file_cls):
        skill_id = PydanticObjectId()
        skill = _make_skill()
        skill.id = skill_id
        skill_file = MagicMock()
        skill_file.skillId = skill_id
        skill_file.relativePath = "references/guide.md"
        mock_skill_cls.get = AsyncMock(return_value=skill)
        mock_file_cls.skillId = "skillId"
        mock_file_cls.find.return_value.to_list = AsyncMock(return_value=[skill_file])

        result_skill, files = await get_skill_with_files(skill_id)

        assert result_skill is skill
        assert files == [skill_file]

    @pytest.mark.asyncio
    @patch("registry.services.skill_service.Skill")
    async def test_raises_on_not_found(self, mock_skill_cls):
        mock_skill_cls.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await get_skill_with_files(PydanticObjectId())
