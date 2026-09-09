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


async def test_returns_empty_when_user_missing():
    with patch.object(access_control.User, "get", AsyncMock(return_value=None)):
        result = await access_control.resolve_group_ids_for_user(PydanticObjectId())
    assert result == []


async def test_returns_empty_when_user_has_no_source_id():
    user = MagicMock()
    user.idOnTheSource = None
    with patch.object(access_control.User, "get", AsyncMock(return_value=user)):
        result = await access_control.resolve_group_ids_for_user(PydanticObjectId())
    assert result == []


async def test_resolves_group_ids_including_google_source():
    user = MagicMock()
    user.id = PydanticObjectId()
    user.idOnTheSource = "ada@example.com"
    g1, g2 = PydanticObjectId(), PydanticObjectId()
    groups = [_group(g1, "google"), _group(g2, "entra")]

    query = MagicMock()
    query.to_list = AsyncMock(return_value=groups)
    with (
        patch.object(access_control.User, "get", AsyncMock(return_value=user)),
        patch.object(access_control.ExtendedGroup, "find", MagicMock(return_value=query)) as find,
    ):
        result = await access_control.resolve_group_ids_for_user(user.id)

    assert result == [g1, g2]
    find.assert_called_once()
    assert find.call_args.args[0] == {"memberIds": "ada@example.com"}
