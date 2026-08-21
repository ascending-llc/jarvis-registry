from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    service = SkillSyncService(source_service)

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
