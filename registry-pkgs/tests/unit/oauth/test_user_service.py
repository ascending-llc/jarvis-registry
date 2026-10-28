from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from registry_pkgs.models import User
from registry_pkgs.oauth.user_service import UserService

pytestmark = pytest.mark.asyncio


async def test_get_user_by_user_id_preserves_session(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = MagicMock()
    session = MagicMock()
    find = AsyncMock(return_value=expected)
    monkeypatch.setattr(User, "get", find)
    user_id = ObjectId()
    assert await UserService().get_user_by_user_id(str(user_id), session) is expected
    find.assert_awaited_once_with(user_id, session=session)


async def test_invalid_user_id_does_not_query_database(monkeypatch: pytest.MonkeyPatch) -> None:
    find = AsyncMock()
    monkeypatch.setattr(User, "get", find)
    assert await UserService().get_user_by_user_id("invalid") is None
    find.assert_not_awaited()


async def test_search_users_preserves_filter_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    result = [MagicMock()]
    cursor = MagicMock()
    cursor.limit.return_value.to_list = AsyncMock(return_value=result)
    find = MagicMock(return_value=cursor)
    monkeypatch.setattr(User, "find", find)
    assert await UserService().search_users("Ada", limit=5) == result
    find.assert_called_once_with(
        {"$or": [{field: {"$regex": "Ada", "$options": "i"}} for field in ("email", "name", "username")]}
    )
    cursor.limit.assert_called_once_with(5)
