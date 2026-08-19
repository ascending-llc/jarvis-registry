from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_service import SkillSyncService


class _FakeTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    def __init__(self):
        self.transaction = _FakeTransaction()

    async def start_transaction(self):
        return self.transaction


class _FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_create_source_with_owner_acl_uses_one_transaction(monkeypatch):
    session = _FakeSession()
    client = MagicMock()
    client.start_session.return_value = _FakeSessionContext(session)
    monkeypatch.setattr("registry.services.skill_sync_service.MongoDB.get_client", lambda: client)

    source = SimpleNamespace(id=PydanticObjectId())
    source_service = MagicMock()
    source_service.create_source = AsyncMock(return_value=source)
    acl_service = MagicMock()
    acl_service.grant_permission = AsyncMock()
    service = SkillSyncService(
        source_crud_service=source_service,
        job_service=MagicMock(),
        token_service=MagicMock(),
        github_service=MagicMock(),
        discovery_service=MagicMock(),
        acl_service=MagicMock(),
    )

    result = await service.create_source_with_owner_acl(
        display_name="Skills",
        description=None,
        tags=["official"],
        owner="octocat",
        repo="skills",
        ref="main",
        paths=["skills"],
        skill_discovery_depth=2,
        github_app_client_id="client",
        github_app_client_secret="secret",
        created_by="user-1",
        principal_id=PydanticObjectId(),
        acl_service=acl_service,
    )

    assert result is source
    source_session = source_service.create_source.await_args.kwargs["session"]
    acl_session = acl_service.grant_permission.await_args.kwargs["session"]
    assert source_session is session
    assert acl_session is session


def _make_service() -> SkillSyncService:
    return SkillSyncService(
        source_crud_service=MagicMock(),
        job_service=MagicMock(),
        token_service=MagicMock(),
        github_service=MagicMock(),
        discovery_service=MagicMock(),
        acl_service=MagicMock(),
    )


def _sync_source(**overrides):
    defaults = {
        "id": PydanticObjectId(),
        "githubAppClientSecretEncrypted": "encrypted",
        "githubAppClientId": "client",
        "owner": "octocat",
        "repo": "skills",
        "ref": "main",
        "paths": ["skills"],
        "skillDiscoveryDepth": 2,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _acl_entry(principal_id, resource_id, perm_bits=7, principal_type="user"):
    return SimpleNamespace(
        principalType=principal_type,
        principalId=principal_id,
        resourceType="skillSyncSource",
        resourceId=resource_id,
        permBits=perm_bits,
    )


@pytest.mark.asyncio
async def test_trigger_sync_marks_source_and_creates_job_in_one_transaction(monkeypatch):
    session = _FakeSession()
    client = MagicMock()
    client.start_session.return_value = _FakeSessionContext(session)
    monkeypatch.setattr("registry.services.skill_sync_service.MongoDB.get_client", lambda: client)
    monkeypatch.setattr("registry.services.skill_sync_service.decrypt_value", lambda value: value)
    monkeypatch.setattr("registry.services.skill_sync_service._fire_background", MagicMock())

    source = _sync_source()
    job = SimpleNamespace(id=PydanticObjectId())
    source_service = MagicMock()
    source_service.mark_sync_pending = AsyncMock(return_value=source)
    job_service = MagicMock()
    job_service.create_job = AsyncMock(return_value=job)
    token_service = MagicMock()
    token_service.resolve_access_token = AsyncMock(return_value="access-token")
    service = SkillSyncService(
        source_crud_service=source_service,
        job_service=job_service,
        token_service=token_service,
        github_service=MagicMock(),
        discovery_service=MagicMock(),
        acl_service=MagicMock(),
    )
    service._run_sync = MagicMock(return_value=object())

    result, needs_auth = await service.trigger_sync(
        source=source,
        user_id=str(PydanticObjectId()),
        trigger_type=MagicMock(),
    )

    assert result is job
    assert needs_auth is False
    assert source_service.mark_sync_pending.await_args.kwargs["session"] is session
    assert job_service.create_job.await_args.kwargs["session"] is session


@pytest.mark.asyncio
async def test_delete_source_marks_source_and_creates_job_in_one_transaction(monkeypatch):
    session = _FakeSession()
    client = MagicMock()
    client.start_session.return_value = _FakeSessionContext(session)
    monkeypatch.setattr("registry.services.skill_sync_service.MongoDB.get_client", lambda: client)
    monkeypatch.setattr("registry.services.skill_sync_service._fire_background", MagicMock())

    source = _sync_source()
    job = SimpleNamespace(id=PydanticObjectId())
    source_service = MagicMock()
    source_service.mark_deleting = AsyncMock(return_value=source)
    job_service = MagicMock()
    job_service.create_job = AsyncMock(return_value=job)
    service = SkillSyncService(
        source_crud_service=source_service,
        job_service=job_service,
        token_service=MagicMock(),
        github_service=MagicMock(),
        discovery_service=MagicMock(),
        acl_service=MagicMock(),
    )
    service._run_delete = MagicMock(return_value=object())

    result = await service.delete_source_with_skills(source=source, user_id=str(PydanticObjectId()))

    assert result is job
    assert source_service.mark_deleting.await_args.kwargs["session"] is session
    assert job_service.create_job.await_args.kwargs["session"] is session


@pytest.mark.asyncio
async def test_build_source_stats_counts_live_skill_files():
    skill_ids = [PydanticObjectId(), PydanticObjectId()]
    skills = [SimpleNamespace(id=skill_id) for skill_id in skill_ids]
    finder = MagicMock()
    finder.count = AsyncMock(return_value=3)

    with patch("registry.services.skill_sync_service.SkillFile") as mock_skill_file:
        mock_skill_file.find.return_value = finder
        stats = await SkillSyncService._build_source_stats(skills)

    assert stats.skillCount == 2
    assert stats.fileCount == 3
    mock_skill_file.find.assert_called_once_with({"skillId": {"$in": skill_ids}})


@pytest.mark.asyncio
async def test_inherit_source_acl_no_skills():
    service = _make_service()
    with patch("registry.services.skill_sync_service.RegistryAclEntry") as mock_acl:
        await service._inherit_source_acl_to_skills(SimpleNamespace(id=PydanticObjectId()), [])
        mock_acl.find.assert_not_called()


@pytest.mark.asyncio
async def test_inherit_source_acl_no_source_entries():
    service = _make_service()
    source = SimpleNamespace(id=PydanticObjectId())
    skill_ids = [PydanticObjectId()]

    mock_find = MagicMock()
    mock_find.to_list = AsyncMock(return_value=[])

    with patch("registry.services.skill_sync_service.RegistryAclEntry") as mock_acl:
        mock_acl.find.return_value = mock_find
        await service._inherit_source_acl_to_skills(source, skill_ids)
        mock_acl.insert_many.assert_not_called()


@pytest.mark.asyncio
async def test_inherit_source_acl_inserts_missing():
    service = _make_service()
    source_id = PydanticObjectId()
    source = SimpleNamespace(id=source_id)
    skill_id = PydanticObjectId()
    principal_id = PydanticObjectId()
    role_id = PydanticObjectId()

    source_entry = _acl_entry(principal_id, source_id, perm_bits=7)

    source_find = MagicMock()
    source_find.to_list = AsyncMock(return_value=[source_entry])
    existing_find = MagicMock()
    existing_find.to_list = AsyncMock(return_value=[])
    call_count = [0]

    def _find_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return source_find
        return existing_find

    with (
        patch("registry.services.skill_sync_service.RegistryAclEntry") as mock_acl,
        patch("registry.services.skill_sync_service.RegistryAccessRole") as mock_role,
    ):
        mock_acl.find.side_effect = _find_side_effect
        mock_acl.insert_many = AsyncMock()

        role_find = MagicMock()
        role_find.to_list = AsyncMock(return_value=[SimpleNamespace(permBits=7, id=role_id)])
        mock_role.find.return_value = role_find

        await service._inherit_source_acl_to_skills(source, [skill_id])

        mock_acl.insert_many.assert_awaited_once()
        inserted = mock_acl.insert_many.await_args[0][0]
        assert len(inserted) == 1
        call_kwargs = mock_acl.call_args_list[0].kwargs
        assert call_kwargs["resourceId"] == skill_id
        assert call_kwargs["principalId"] == principal_id
        assert call_kwargs["permBits"] == 7
        assert call_kwargs["roleId"] == role_id


@pytest.mark.asyncio
async def test_inherit_source_acl_skips_existing():
    service = _make_service()
    source_id = PydanticObjectId()
    source = SimpleNamespace(id=source_id)
    skill_id = PydanticObjectId()
    principal_id = PydanticObjectId()

    source_entry = _acl_entry(principal_id, source_id, perm_bits=7)
    existing_entry = SimpleNamespace(resourceId=skill_id, principalId=principal_id)

    source_find = MagicMock()
    source_find.to_list = AsyncMock(return_value=[source_entry])
    existing_find = MagicMock()
    existing_find.to_list = AsyncMock(return_value=[existing_entry])
    call_count = [0]

    def _find_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return source_find
        return existing_find

    with (
        patch("registry.services.skill_sync_service.RegistryAclEntry") as mock_acl,
        patch("registry.services.skill_sync_service.RegistryAccessRole") as mock_role,
    ):
        mock_acl.find.side_effect = _find_side_effect
        mock_acl.insert_many = AsyncMock()

        role_find = MagicMock()
        role_find.to_list = AsyncMock(return_value=[SimpleNamespace(permBits=7, id=PydanticObjectId())])
        mock_role.find.return_value = role_find

        await service._inherit_source_acl_to_skills(source, [skill_id])

        mock_acl.insert_many.assert_not_awaited()
