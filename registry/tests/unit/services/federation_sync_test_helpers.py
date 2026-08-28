"""Shared factories and fakes for federation sync service tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from beanie import PydanticObjectId

from registry.services.federation_sync_service import FederationSyncService
from registry_pkgs.models.enums import FederationProviderType, FederationStatus, FederationSyncStatus

_DEFAULT_USER_OBJECT_ID = PydanticObjectId()


def _make_federation_sync_service() -> FederationSyncService:
    user_service = MagicMock()
    user_service.get_user_by_user_id = AsyncMock(
        return_value=SimpleNamespace(id=_DEFAULT_USER_OBJECT_ID, user_id="user-1")
    )
    mcp_server_repo = MagicMock()
    mcp_server_repo.ensure_collection = AsyncMock()
    a2a_agent_repo = MagicMock()
    a2a_agent_repo.ensure_collection = AsyncMock()
    return FederationSyncService(
        federation_crud_service=MagicMock(),
        federation_job_service=MagicMock(),
        mcp_server_repo=mcp_server_repo,
        a2a_agent_repo=a2a_agent_repo,
        acl_service=MagicMock(),
        user_service=user_service,
    )


def _make_federation(provider_type: FederationProviderType, provider_config: dict):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerType=provider_type,
        providerConfig=provider_config,
        status=FederationStatus.ACTIVE,
        syncStatus=FederationSyncStatus.IDLE,
        createdAt=now,
        updatedAt=now,
    )


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    async def to_list(self):
        return list(self._items)


class _FakeTxnCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _FakeTxnSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def start_transaction(self):
        return _FakeTxnCtx()


class _FakeTxnClient:
    def __init__(self, session: _FakeTxnSession):
        self.session = session

    def start_session(self):
        return self.session


def _patch_mongo_session(monkeypatch) -> _FakeTxnSession:
    session = _FakeTxnSession()
    monkeypatch.setattr(
        "registry.services.federation_sync_service.MongoDB.get_client",
        lambda: _FakeTxnClient(session),
    )
    return session
