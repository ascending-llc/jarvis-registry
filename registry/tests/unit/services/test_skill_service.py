"""Unit tests for SkillService business rules."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.skill_api_schemas import SkillCreateRequest, SkillUpdateRequest
from registry.services.skill_service import SkillService
from registry_pkgs.models import SkillSource

_USER_ID = "000000000000000000000001"
_OTHER_USER_ID = "000000000000000000000002"
_THIRD_USER_ID = "000000000000000000000003"


def _make_skill(*, created_by_registry: bool = True) -> MagicMock:
    skill = MagicMock()
    skill.id = PydanticObjectId()
    skill.name = "test-skill"
    skill.displayTitle = "Test Skill"
    skill.description = "description"
    skill.body = "# Test"
    skill.frontmatter = {"license": "Apache-2.0"}
    skill.disableModelInvocation = False
    skill.userInvocable = True
    skill.allowedTools = None
    skill.path = "test-skill"
    skill.version = 1
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


def _configure_transaction(mock_mongodb: MagicMock) -> MagicMock:
    session = MagicMock()
    session.start_transaction = AsyncMock(return_value=_async_context(None))
    client = MagicMock()
    client.start_session.return_value = _async_context(session)
    mock_mongodb.get_client.return_value = client
    return session


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
    mock_skill_cls.find.assert_called_once_with({"_id": {"$in": [skill.id]}, "enabled": True, "fileCount": 0})


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
    session = _configure_transaction(mock_mongodb)
    data = SkillCreateRequest(
        name="test-skill",
        description="description",
        body="# Test",
        frontmatter={
            "allowed-tools": ["Read"],
            "disable-model-invocation": True,
            "user-invocable": False,
            "license": "MIT",
            "custom": "value",
        },
    )

    result, permissions = await SkillService(acl_service, user_service).create_skill(data, _USER_ID, "Token User")

    assert result is skill
    assert permissions.SHARE is True
    skill.insert.assert_awaited_once_with(session=session)
    acl_service.grant_permission.assert_awaited_once()
    assert mock_skill_cls.call_args.kwargs["source"] == SkillSource.INLINE
    assert mock_skill_cls.call_args.kwargs["createdByRegistry"] is True
    assert mock_skill_cls.call_args.kwargs["authorName"] == "Database User"
    assert mock_skill_cls.call_args.kwargs["fileCount"] == 0
    assert mock_skill_cls.call_args.kwargs["frontmatter"] == {
        "allowedTools": ["Read"],
        "license": "MIT",
        "custom": "value",
        "disableModelInvocation": True,
        "userInvocable": False,
    }
    assert mock_skill_cls.call_args.kwargs["disableModelInvocation"] is True
    assert mock_skill_cls.call_args.kwargs["userInvocable"] is False
    assert mock_skill_cls.call_args.kwargs["allowedTools"] == ["Read"]
    mock_skill_cls.find_one.assert_awaited_once_with({"name": "test-skill", "author": PydanticObjectId(_USER_ID)})


@pytest.mark.asyncio
@patch("registry.services.skill_service.Skill")
async def test_create_invalid_frontmatter_returns_422_without_inserting(
    mock_skill_cls,
    acl_service,
    user_service,
):
    mock_skill_cls.find_one = AsyncMock(return_value=None)
    data = SkillCreateRequest(
        name="test-skill",
        description="description",
        frontmatter={"allowed-tools": {"tool": "Read"}},
    )

    with pytest.raises(HTTPException) as exc_info:
        await SkillService(acl_service, user_service).create_skill(data, _USER_ID, "Token User")

    assert exc_info.value.status_code == 422
    assert "Invalid frontmatter" in exc_info.value.detail
    mock_skill_cls.assert_not_called()


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
async def test_update_accepts_frontmatter_only_and_replaces_existing_value(
    mock_mongodb,
    acl_service,
    user_service,
):
    skill = _make_skill()
    session = _configure_transaction(mock_mongodb)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)
    service._list_skill_files = AsyncMock(return_value=[])
    data = SkillUpdateRequest(
        frontmatter={
            "allowed-tools": "Read, Grep",
            "foo": "bar",
        }
    )

    result, files, _ = await service.update_skill(skill.id, data, _USER_ID)

    assert result is skill
    assert files == []
    assert skill.frontmatter == {
        "allowedTools": ["Read", "Grep"],
        "foo": "bar",
        "disableModelInvocation": False,
        "userInvocable": True,
    }
    assert skill.allowedTools == ["Read", "Grep"]
    assert skill.version == 2
    skill.save.assert_awaited_once_with(session=session)


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
async def test_update_invalid_frontmatter_returns_422_without_saving(
    mock_mongodb,
    acl_service,
    user_service,
):
    skill = _make_skill()
    _configure_transaction(mock_mongodb)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)
    data = SkillUpdateRequest(frontmatter={"allowedTools": 123})

    with pytest.raises(HTTPException) as exc_info:
        await service.update_skill(skill.id, data, _USER_ID)

    assert exc_info.value.status_code == 422
    assert "Invalid frontmatter" in exc_info.value.detail
    skill.save.assert_not_awaited()


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
async def test_update_without_frontmatter_preserves_existing_frontmatter(
    mock_mongodb,
    acl_service,
    user_service,
):
    skill = _make_skill()
    session = _configure_transaction(mock_mongodb)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)
    service._list_skill_files = AsyncMock(return_value=[])

    await service.update_skill(skill.id, SkillUpdateRequest(description="Updated"), _USER_ID)

    assert skill.description == "Updated"
    assert skill.frontmatter == {"license": "Apache-2.0"}
    skill.save.assert_awaited_once_with(session=session)


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
@patch("registry.services.skill_service.Skill")
async def test_update_name_and_frontmatter_uses_new_identity_and_preserves_uniqueness_check(
    mock_skill_cls,
    mock_mongodb,
    acl_service,
    user_service,
):
    skill = _make_skill()
    session = _configure_transaction(mock_mongodb)
    mock_skill_cls.find_one = AsyncMock(return_value=None)
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)
    service._list_skill_files = AsyncMock(return_value=[])
    data = SkillUpdateRequest(
        name="renamed-skill",
        description="Renamed description",
        frontmatter={
            "name": "stale-name",
            "description": "Stale description",
            "license": "MIT",
        },
    )

    await service.update_skill(skill.id, data, _USER_ID)

    assert skill.name == "renamed-skill"
    assert skill.path == "renamed-skill"
    assert skill.description == "Renamed description"
    assert skill.frontmatter == {
        "license": "MIT",
        "disableModelInvocation": False,
        "userInvocable": True,
    }
    mock_skill_cls.find_one.assert_awaited_once_with(
        {
            "_id": {"$ne": skill.id},
            "name": "renamed-skill",
            "author": skill.author,
        },
        session=session,
    )


@pytest.mark.asyncio
@patch("registry.services.skill_service.MongoDB")
@patch("registry.services.skill_service.Skill")
async def test_update_duplicate_name_returns_409_without_saving(
    mock_skill_cls,
    mock_mongodb,
    acl_service,
    user_service,
):
    skill = _make_skill()
    _configure_transaction(mock_mongodb)
    mock_skill_cls.find_one = AsyncMock(return_value=_make_named_skill("existing-skill"))
    service = SkillService(acl_service, user_service)
    service._get_existing_skill = AsyncMock(return_value=skill)

    with pytest.raises(HTTPException) as exc_info:
        await service.update_skill(skill.id, SkillUpdateRequest(name="existing-skill"), _USER_ID)

    assert exc_info.value.status_code == 409
    skill.save.assert_not_awaited()


def _make_named_skill(name: str, author_id: str = _USER_ID) -> MagicMock:
    skill = MagicMock()
    skill.id = PydanticObjectId()
    skill.name = name
    skill.author = PydanticObjectId(author_id)
    return skill


_VIEW = ResourcePermissions(VIEW=True)


def test_dedup_no_duplicates():
    s1 = _make_named_skill("alpha")
    s2 = _make_named_skill("beta")
    items = [(s1, _VIEW), (s2, _VIEW)]

    result = SkillService._deduplicate_by_name(items, PydanticObjectId(_USER_ID))

    assert len(result) == 2
    assert result[0][0] is s1
    assert result[1][0] is s2


def test_dedup_author_match_wins(caplog):
    owned = _make_named_skill("dup", author_id=_USER_ID)
    other = _make_named_skill("dup", author_id=_OTHER_USER_ID)
    items = [(other, _VIEW), (owned, _VIEW)]

    import logging

    with caplog.at_level(logging.WARNING, logger="registry.services.skill_service"):
        result = SkillService._deduplicate_by_name(items, PydanticObjectId(_USER_ID))

    assert len(result) == 1
    assert result[0][0] is owned
    assert "WARNING" in caplog.text or any(r.levelno == logging.WARNING for r in caplog.records)


def test_dedup_no_author_match_excludes_all(caplog):
    s1 = _make_named_skill("dup", author_id=_OTHER_USER_ID)
    s2 = _make_named_skill("dup", author_id=_THIRD_USER_ID)
    items = [(s1, _VIEW), (s2, _VIEW)]

    import logging

    with caplog.at_level(logging.ERROR, logger="registry.services.skill_service"):
        result = SkillService._deduplicate_by_name(items, PydanticObjectId(_USER_ID))

    assert len(result) == 0
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_dedup_mixed_unique_and_duplicate(caplog):
    unique_a = _make_named_skill("alpha")
    owned_dup = _make_named_skill("beta", author_id=_USER_ID)
    other_dup = _make_named_skill("beta", author_id=_OTHER_USER_ID)
    unique_c = _make_named_skill("gamma")
    items = [(unique_a, _VIEW), (other_dup, _VIEW), (owned_dup, _VIEW), (unique_c, _VIEW)]

    import logging

    with caplog.at_level(logging.WARNING, logger="registry.services.skill_service"):
        result = SkillService._deduplicate_by_name(items, PydanticObjectId(_USER_ID))

    assert len(result) == 3
    assert result[0][0] is unique_a
    assert result[1][0] is owned_dup
    assert result[2][0] is unique_c


def test_dedup_preserves_order():
    s1 = _make_named_skill("alpha", author_id=_USER_ID)
    s2 = _make_named_skill("beta", author_id=_USER_ID)
    s3 = _make_named_skill("gamma", author_id=_USER_ID)
    items = [(s1, _VIEW), (s2, _VIEW), (s3, _VIEW)]

    result = SkillService._deduplicate_by_name(items, PydanticObjectId(_USER_ID))

    assert [r[0].name for r in result] == ["alpha", "beta", "gamma"]


def test_dedup_three_same_name_one_owned(caplog):
    owned = _make_named_skill("dup", author_id=_USER_ID)
    other1 = _make_named_skill("dup", author_id=_OTHER_USER_ID)
    other2 = _make_named_skill("dup", author_id=_THIRD_USER_ID)
    items = [(other1, _VIEW), (owned, _VIEW), (other2, _VIEW)]

    import logging

    with caplog.at_level(logging.WARNING, logger="registry.services.skill_service"):
        result = SkillService._deduplicate_by_name(items, PydanticObjectId(_USER_ID))

    assert len(result) == 1
    assert result[0][0] is owned
    assert any(r.levelno == logging.WARNING for r in caplog.records)
