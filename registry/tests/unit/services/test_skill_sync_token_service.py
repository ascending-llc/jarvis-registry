from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.schemas.oauth_schema import OAuthTokens
from registry.services.skill_sync_token_service import SkillSyncTokenService, build_skill_sync_token_identifier
from registry_pkgs.models.token_type import TokenType


def _make_token(token: str = "encrypted-value", expires_in_seconds: int = 3600):
    return SimpleNamespace(
        token=token,
        expiresAt=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        save=AsyncMock(),
    )


def _make_naive_token(token: str = "encrypted-value", expires_in_seconds: int = 3600):
    return SimpleNamespace(
        token=token,
        expiresAt=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expires_in_seconds),
        save=AsyncMock(),
    )


@pytest.fixture
def service():
    return SkillSyncTokenService(http_client=MagicMock())


@pytest.mark.asyncio
async def test_resolve_access_token_returns_decrypted_value_when_valid(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    access_token = _make_token()
    find_one = AsyncMock(return_value=access_token)
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.decrypt_value",
        lambda value: f"decrypted-{value}",
    )

    result = await service.resolve_access_token(
        user_id=user_id,
        source_id=source_id,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert result == "decrypted-encrypted-value"
    find_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_access_token_handles_naive_mongo_expiry(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    access_token = _make_naive_token()
    find_one = AsyncMock(return_value=access_token)
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.decrypt_value",
        lambda value: f"decrypted-{value}",
    )

    result = await service.resolve_access_token(
        user_id=user_id,
        source_id=source_id,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert result == "decrypted-encrypted-value"


@pytest.mark.asyncio
async def test_resolve_access_token_refreshes_when_access_expired(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    expired_access = _make_token(expires_in_seconds=-60)
    refresh_token = _make_token(token="encrypted-refresh")

    find_one = AsyncMock(side_effect=[expired_access, refresh_token])
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.decrypt_value",
        lambda value: f"decrypted-{value}",
    )
    new_tokens = OAuthTokens(access_token="new-access", refresh_token="new-refresh", expires_in=3600)
    monkeypatch.setattr(service, "refresh_tokens", AsyncMock(return_value=new_tokens))
    monkeypatch.setattr(service, "store_tokens", AsyncMock())

    result = await service.resolve_access_token(
        user_id=user_id,
        source_id=source_id,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert result == "new-access"
    service.refresh_tokens.assert_awaited_once_with(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="decrypted-encrypted-refresh",
    )
    service.store_tokens.assert_awaited_once_with(user_id=user_id, source_id=source_id, tokens=new_tokens)


@pytest.mark.asyncio
async def test_resolve_access_token_returns_none_when_no_valid_tokens(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    find_one = AsyncMock(side_effect=[None, None])
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find_one", find_one)

    result = await service.resolve_access_token(
        user_id=user_id,
        source_id=source_id,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert result is None


@pytest.mark.asyncio
async def test_resolve_access_token_returns_none_when_refresh_fails(monkeypatch, service):
    import httpx

    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    expired_access = _make_token(expires_in_seconds=-60)
    refresh_token = _make_token(token="encrypted-refresh")
    find_one = AsyncMock(side_effect=[expired_access, refresh_token])
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.decrypt_value",
        lambda value: f"decrypted-{value}",
    )
    monkeypatch.setattr(service, "refresh_tokens", AsyncMock(side_effect=httpx.HTTPError("boom")))

    result = await service.resolve_access_token(
        user_id=user_id,
        source_id=source_id,
        client_id="client-id",
        client_secret="client-secret",
    )

    assert result is None


class _FakeToken:
    """Stand-in for the Beanie ``Token`` document that skips collection init."""

    find_one = None  # patched per-test
    created: list["_FakeToken"] = []

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.insert = AsyncMock()
        _FakeToken.created.append(self)


@pytest.mark.asyncio
async def test_store_tokens_upserts_access_and_refresh(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    find_one = AsyncMock(return_value=None)
    _FakeToken.created = []
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token", _FakeToken)
    monkeypatch.setattr(_FakeToken, "find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.encrypt_value",
        lambda value: f"encrypted-{value}",
    )
    tokens = OAuthTokens(access_token="access-1", refresh_token="refresh-1", expires_in=3600)

    await service.store_tokens(user_id=user_id, source_id=source_id, tokens=tokens)

    assert find_one.await_count == 2
    assert len(_FakeToken.created) == 2
    for created_token in _FakeToken.created:
        created_token.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_tokens_updates_existing_token(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    existing_token = _make_token()
    find_one = AsyncMock(return_value=existing_token)
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.encrypt_value",
        lambda value: f"encrypted-{value}",
    )
    tokens = OAuthTokens(access_token="access-1", expires_in=3600)

    await service.store_tokens(user_id=user_id, source_id=source_id, tokens=tokens)

    assert existing_token.token == "encrypted-access-1"
    existing_token.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_store_tokens_skips_missing_refresh_token(monkeypatch, service):
    user_id = str(PydanticObjectId())
    source_id = PydanticObjectId()
    find_one = AsyncMock(return_value=None)
    _FakeToken.created = []
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token", _FakeToken)
    monkeypatch.setattr(_FakeToken, "find_one", find_one)
    monkeypatch.setattr(
        "registry.services.skill_sync_token_service.encrypt_value",
        lambda value: f"encrypted-{value}",
    )
    tokens = OAuthTokens(access_token="access-only", expires_in=3600)

    await service.store_tokens(user_id=user_id, source_id=source_id, tokens=tokens)

    assert find_one.await_count == 1
    assert len(_FakeToken.created) == 1


@pytest.mark.asyncio
async def test_delete_source_tokens_deletes_matching_tokens(monkeypatch, service):
    source_id = PydanticObjectId()
    delete_mock = AsyncMock()
    find_result = SimpleNamespace(delete=delete_mock)
    find_mock = MagicMock(return_value=find_result)
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find", find_mock)

    await service.delete_source_tokens(source_id)

    find_mock.assert_called_once_with(
        {
            "identifier": build_skill_sync_token_identifier(source_id),
            "type": {
                "$in": [
                    TokenType.SKILL_SYNC_GITHUB_ACCESS.value,
                    TokenType.SKILL_SYNC_GITHUB_REFRESH.value,
                ]
            },
        }
    )
    delete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_user_access_token_scopes_to_user_and_access_type(monkeypatch, service):
    source_id = PydanticObjectId()
    user_id = str(PydanticObjectId())
    delete_mock = AsyncMock()
    find_result = SimpleNamespace(delete=delete_mock)
    find_mock = MagicMock(return_value=find_result)
    monkeypatch.setattr("registry.services.skill_sync_token_service.Token.find", find_mock)

    await service.delete_user_access_token(user_id=user_id, source_id=source_id)

    find_mock.assert_called_once_with(
        {
            "userId": PydanticObjectId(user_id),
            "type": TokenType.SKILL_SYNC_GITHUB_ACCESS.value,
            "identifier": build_skill_sync_token_identifier(source_id),
        }
    )
    delete_mock.assert_awaited_once()
