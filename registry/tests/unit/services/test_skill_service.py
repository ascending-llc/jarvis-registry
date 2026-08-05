from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.skill_service import (
    SkillContentUnavailableError,
    compute_skill_content_hash,
    get_skill_with_files,
    list_skills_delta,
)


def _make_skill(name: str = "test-skill", body: str = "body") -> MagicMock:
    skill = MagicMock()
    skill.id = PydanticObjectId()
    skill.name = name
    skill.description = "description"
    skill.alwaysApply = False
    skill.category = "testing"
    skill.frontmatter = {"name": name, "description": "description"}
    skill.body = body
    skill.contentHash = None
    return skill


def _make_file(relative_path: str, content: str | None, is_binary: bool | None = False) -> MagicMock:
    skill_file = MagicMock()
    skill_file.skillId = PydanticObjectId()
    skill_file.relativePath = relative_path
    skill_file.content = content
    skill_file.isBinary = is_binary
    return skill_file


class TestComputeSkillContentHash:
    def test_is_deterministic_and_uses_versioned_sha256(self):
        skill = _make_skill()

        first = compute_skill_content_hash(skill, [])
        second = compute_skill_content_hash(skill, [])

        assert first == second
        assert first.startswith("sha256:")
        assert len(first) == 71

    @pytest.mark.parametrize("field", ["name", "description", "alwaysApply", "category", "body", "frontmatter"])
    def test_skill_definition_change_changes_hash(self, field):
        original = _make_skill()
        changed = _make_skill()
        if field == "frontmatter":
            value = {"changed": True}
        elif field == "alwaysApply":
            value = True
        else:
            value = "changed"
        setattr(changed, field, value)

        assert compute_skill_content_hash(original, []) != compute_skill_content_hash(changed, [])

    def test_file_order_is_irrelevant(self):
        skill = _make_skill()
        first = _make_file("scripts/run.sh", "run")
        second = _make_file("references/guide.md", "guide")

        assert compute_skill_content_hash(skill, [first, second]) == compute_skill_content_hash(skill, [second, first])

    def test_file_content_change_changes_hash(self):
        skill = _make_skill()

        assert compute_skill_content_hash(skill, [_make_file("a.txt", "one")]) != compute_skill_content_hash(
            skill, [_make_file("a.txt", "two")]
        )

    @pytest.mark.parametrize(
        ("skill_file", "message"),
        [
            (_make_file("assets/logo.png", None, True), "binary"),
            (_make_file("references/large.md", None, False), "no cached content"),
            (_make_file("../escape.txt", "bad"), "unsafe relative path"),
        ],
    )
    def test_unreconstructable_file_fails_closed(self, skill_file, message):
        with pytest.raises(SkillContentUnavailableError, match=message):
            compute_skill_content_hash(_make_skill(), [skill_file])

    def test_duplicate_path_fails_closed(self):
        files = [_make_file("a.txt", "one"), _make_file("a.txt", "two")]

        with pytest.raises(SkillContentUnavailableError, match="duplicate"):
            compute_skill_content_hash(_make_skill(), files)

    def test_non_json_frontmatter_fails_closed(self):
        skill = _make_skill()
        skill.frontmatter = {"invalid": object()}

        with pytest.raises(SkillContentUnavailableError, match="canonical JSON"):
            compute_skill_content_hash(skill, [])


class TestListSkillsDelta:
    @pytest.mark.asyncio
    @patch("registry.services.skill_service.SkillFile")
    @patch("registry.services.skill_service.Skill")
    async def test_batches_files_and_computes_fresh_hash_without_saving(self, mock_skill_cls, mock_file_cls):
        skill = _make_skill()
        skill.save = AsyncMock()
        skill_file = _make_file("scripts/run.sh", "run")
        skill_file.skillId = skill.id

        skill_query = MagicMock()
        skill_query.sort.return_value = skill_query
        skill_query.to_list = AsyncMock(return_value=[skill])
        mock_skill_cls.find.return_value = skill_query
        mock_file_cls.find.return_value.to_list = AsyncMock(return_value=[skill_file])

        result = await list_skills_delta()

        assert result == [skill]
        assert skill.contentHash.startswith("sha256:")
        skill.save.assert_not_awaited()
        mock_file_cls.find.assert_called_once_with({"skillId": {"$in": [skill.id]}})

    @pytest.mark.asyncio
    @patch("registry.services.skill_service.SkillFile")
    @patch("registry.services.skill_service.Skill")
    async def test_filters_by_inclusive_cursor(self, mock_skill_cls, mock_file_cls):
        since = datetime(2026, 8, 1, tzinfo=UTC)
        query = MagicMock()
        query.sort.return_value = query
        query.to_list = AsyncMock(return_value=[])
        mock_skill_cls.find.return_value = query

        result = await list_skills_delta(since)

        assert result == []
        mock_skill_cls.find.assert_called_once_with({"updatedAt": {"$gte": since}})
        mock_file_cls.find.assert_not_called()


class TestGetSkillWithFiles:
    @pytest.mark.asyncio
    @patch("registry.services.skill_service.SkillFile")
    @patch("registry.services.skill_service.Skill")
    async def test_returns_files_and_computes_hash_without_saving(self, mock_skill_cls, mock_file_cls):
        skill_id = PydanticObjectId()
        skill = _make_skill()
        skill.id = skill_id
        skill.save = AsyncMock()
        skill_file = _make_file("references/guide.md", "guide")
        skill_file.skillId = skill_id
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
