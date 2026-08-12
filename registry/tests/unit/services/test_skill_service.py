"""Unit tests for SkillService business rules."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.skill_api_schemas import SkillCreateRequest
from registry.services.skill_service import SkillService
from registry_pkgs.models import SkillSource

_USER_ID = "000000000000000000000001"


def _make_skill(*, created_by_registry: bool = True) -> MagicMock:
    skill = MagicMock()
    skill.id = PydanticObjectId()
    skill.name = "test-skill"
    skill.author = PydanticObjectId(_USER_ID)
    skill.source = SkillSource.INLINE
    skill.createdByRegistry = created_by_registry
    skill.enabled = True
    skill.updatedAt = None
    skill.save = AsyncMock()
    return skill


def _async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.fixture
def acl_service() -> MagicMock:
    acl = MagicMock()
    acl.get_accessible_resource_ids = AsyncMock()
    acl.get_user_permissions_for_resources = AsyncMock()
    acl.get_user_permissions_for_resource = AsyncMock()
    acl.check_user_permission = AsyncMock(return_value=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True))
    acl.grant_permission = AsyncMock()
    acl.delete_acl_entries_for_resource = AsyncMock(return_value=1)
    return acl


@pytest.fixture
def user_service() -> MagicMock:
    service = MagicMock()
    service.get_user_by_user_id = AsyncMock(
        return_value=SimpleNamespace(name="Database User", username="database-user")
    )
    return service


@pytest.mark.asyncio
@patch("registry.services.skill_service.Skill")
async def test_list_skills_filters_by_acl_status_and_file_count(mock_skill_cls, acl_service, user_service):
    skill = _make_skill()
    query = MagicMock()
    query.sort.return_value = query
    query.to_list = AsyncMock(return_value=[skill])
    mock_skill_cls.find.return_value = query
    acl_service.get_accessible_resource_ids.return_value = [str(skill.id)]
    permissions = ResourcePermissions(VIEW=True)
    acl_service.get_user_permissions_for_resources.return_value = {skill.id: permissions}

    result = await SkillService(acl_service, user_service).list_skills(_USER_ID, enabled=True, file_count=0)

    assert result == [(skill, permissions)]
    mock_skill_cls.find.assert_called_once_with(
        {"_id": {"$in": [skill.id]}, "deletedAt": None, "enabled": True, "fileCount": 0}
    )


@pytest.mark.asyncio
async def test_list_skills_short_circuits_when_acl_has_no_resources(acl_service, user_service):
    acl_service.get_accessible_resource_ids.return_value = []

    assert await SkillService(acl_service, user_service).list_skills(_USER_ID) == []


@pytest.mark.asyncio
@patch("registry.services.skill_service.SkillFile")
async def test_chat_file_content_returns_available_false(mock_file_cls, acl_service, user_service):
    skill = _make_skill()
    skill_file = MagicMock(
        relativePath="references/guide.md",
        mimeType="text/markdown",
        isBinary=False,
        source="s3",
    )
    mock_file_cls.find_one = AsyncMock(return_value=skill_file)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)

    result = await service.get_skill_file_content(skill.id, skill_file.relativePath, _USER_ID)

    assert result.available is False
    assert result.content is None
    assert "Jarvis Chat" in result.unavailableReason


@pytest.mark.asyncio
@patch("registry.services.skill_service.SkillFile")
async def test_registry_binary_file_content_is_base64_encoded(mock_file_cls, acl_service, user_service):
    skill = _make_skill()
    skill_file = MagicMock(
        relativePath="assets/data.bin",
        mimeType="application/octet-stream",
        isBinary=True,
        source="registry-inline",
        body=b"\x00\xff",
    )
    mock_file_cls.find_one = AsyncMock(return_value=skill_file)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)

    result = await service.get_skill_file_content(skill.id, skill_file.relativePath, _USER_ID)

    assert result.available is True
    assert result.body == "AP8="
    assert result.content is None


@pytest.mark.asyncio
@patch("registry.services.skill_service.SkillFile")
async def test_unknown_binary_state_falls_back_to_base64_for_invalid_utf8(mock_file_cls, acl_service, user_service):
    skill = _make_skill()
    skill_file = MagicMock(
        relativePath="assets/data.bin",
        mimeType="application/octet-stream",
        isBinary=None,
        source="registry-inline",
        body=b"\xff",
    )
    mock_file_cls.find_one = AsyncMock(return_value=skill_file)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)

    result = await service.get_skill_file_content(skill.id, skill_file.relativePath, _USER_ID)

    assert result.body == "/w=="
    assert result.content is None


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
async def test_delete_rejects_chat_created_skill(mock_mongodb, acl_service, user_service):
    skill = _make_skill(created_by_registry=False)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)
    session = MagicMock()
    session.start_transaction = AsyncMock(return_value=_async_context(None))
    client = MagicMock()
    client.start_session.return_value = _async_context(session)
    mock_mongodb.get_client.return_value = client

    with pytest.raises(HTTPException) as exc_info:
        await service.delete_skill(skill.id, _USER_ID)

    assert exc_info.value.status_code == 409
    acl_service.check_user_permission.assert_awaited_once()


@pytest.mark.asyncio
async def test_toggle_requires_edit_and_saves(acl_service, user_service):
    skill = _make_skill()
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)

    result = await service.toggle_skill(skill.id, False, _USER_ID)

    assert result.enabled is False
    acl_service.check_user_permission.assert_awaited_once()
    skill.save.assert_awaited_once_with()


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
@patch("registry.services.skill_service.Skill")
async def test_create_inserts_skill_and_grants_owner(mock_skill_cls, mock_mongodb, acl_service, user_service):
    skill = _make_skill()
    skill.insert = AsyncMock()
    mock_skill_cls.return_value = skill
    mock_skill_cls.find_one = AsyncMock(return_value=None)
    session = MagicMock()
    session.start_transaction = AsyncMock(return_value=_async_context(None))
    client = MagicMock()
    client.start_session.return_value = _async_context(session)
    mock_mongodb.get_client.return_value = client
    data = SkillCreateRequest(name="test-skill", description="description", body="# Test")

    result, permissions = await SkillService(acl_service, user_service).create_skill(data, _USER_ID, "Token User")

    assert result is skill
    assert permissions.SHARE is True
    skill.insert.assert_awaited_once_with(session=session)
    acl_service.grant_permission.assert_awaited_once()
    assert mock_skill_cls.call_args.kwargs["source"] == SkillSource.INLINE
    assert mock_skill_cls.call_args.kwargs["createdByRegistry"] is True
    assert mock_skill_cls.call_args.kwargs["authorName"] == "Database User"
    assert mock_skill_cls.call_args.kwargs["fileCount"] == 0
    mock_skill_cls.find_one.assert_awaited_once_with(
        {"name": "test-skill", "author": PydanticObjectId(_USER_ID), "deletedAt": None}
    )
