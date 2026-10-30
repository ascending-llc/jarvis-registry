from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry_pkgs import access_control

pytestmark = pytest.mark.asyncio


def _group(gid, source):
    g = MagicMock()
    g.id = gid
    g.source = source
    return g


def _user(provider: str, *, email: str | None = None, idOnTheSource: str | None = None) -> MagicMock:
    user = MagicMock()
    user.id = PydanticObjectId()
    user.provider = provider
    user.email = email
    user.idOnTheSource = idOnTheSource
    return user


def test_group_member_id_prefers_email_for_google():
    # email wins regardless of idOnTheSource being missing, equal, or a stale different value.
    assert access_control.group_member_id_for_user(_user("google", email="ada@example.com")) == "ada@example.com"
    assert (
        access_control.group_member_id_for_user(
            _user("google", email="ada@example.com", idOnTheSource="old@example.com")
        )
        == "ada@example.com"
    )


def test_group_member_id_uses_idonthesource_for_other_providers():
    assert access_control.group_member_id_for_user(_user("openid", idOnTheSource="object-1")) == "object-1"


def test_group_member_id_none_when_selected_field_empty():
    assert access_control.group_member_id_for_user(_user("google", email="")) is None
    assert access_control.group_member_id_for_user(_user("openid", idOnTheSource=None)) is None


async def test_returns_empty_when_user_missing():
    with patch.object(access_control.User, "get", AsyncMock(return_value=None)):
        result = await access_control.resolve_group_ids_for_user(PydanticObjectId())
    assert result == []


async def test_returns_empty_when_user_has_no_source_id():
    user = _user("openid", idOnTheSource=None)
    with patch.object(access_control.User, "get", AsyncMock(return_value=user)):
        result = await access_control.resolve_group_ids_for_user(PydanticObjectId())
    assert result == []


async def _resolve(user: MagicMock) -> tuple[list, MagicMock]:
    g1, g2 = PydanticObjectId(), PydanticObjectId()
    query = MagicMock()
    query.to_list = AsyncMock(return_value=[_group(g1, "google"), _group(g2, "entra")])
    with (
        patch.object(access_control.User, "get", AsyncMock(return_value=user)),
        patch.object(access_control.ExtendedGroup, "find", MagicMock(return_value=query)) as find,
    ):
        result = await access_control.resolve_group_ids_for_user(user.id)
    return [g1, g2] == result, find


async def test_resolves_group_ids_including_google_source():
    # A Google user with no idOnTheSource resolves by email.
    user = _user("google", email="ada@example.com")
    ok, find = await _resolve(user)
    assert ok
    find.assert_called_once()
    assert find.call_args.args[0] == {"memberIds": "ada@example.com"}


async def test_google_user_resolves_by_email_not_stale_source_id():
    user = _user("google", email="ada@example.com", idOnTheSource="old@example.com")
    ok, find = await _resolve(user)
    assert ok
    assert find.call_args.args[0] == {"memberIds": "ada@example.com"}


async def test_entra_user_resolves_by_idonthesource():
    user = _user("openid", idOnTheSource="object-1")
    ok, find = await _resolve(user)
    assert ok
    assert find.call_args.args[0] == {"memberIds": "object-1"}


async def test_entra_user_without_source_id_returns_empty():
    user = _user("openid", idOnTheSource=None)
    with patch.object(access_control.User, "get", AsyncMock(return_value=user)):
        result = await access_control.resolve_group_ids_for_user(user.id)
    assert result == []
