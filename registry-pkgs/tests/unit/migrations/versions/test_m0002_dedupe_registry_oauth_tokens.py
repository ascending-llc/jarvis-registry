"""Tests for migration 0002: dedupe Registry MCP OAuth token records."""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from registry_pkgs.migrations.versions import m0002_dedupe_registry_oauth_tokens as migration
from registry_pkgs.models import TokenType
from registry_pkgs.oauth.token_service import registry_access_token_identifier


class _Cursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = iter(docs)

    def __aiter__(self) -> "_Cursor":
        return self

    async def __anext__(self) -> dict:
        try:
            return next(self._docs)
        except StopIteration:
            raise StopAsyncIteration from None


def _db(groups: list[dict]) -> tuple[MagicMock, MagicMock]:
    collection = MagicMock(
        aggregate=AsyncMock(return_value=_Cursor(groups)),
        delete_many=AsyncMock(side_effect=lambda filter_: MagicMock(deleted_count=len(filter_["_id"]["$in"]))),
    )
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db, collection


def _key(user_id: ObjectId) -> dict:
    return {"userId": user_id, "type": "mcp_oauth", "identifier": "registry:mcp:atlassian"}


@pytest.mark.asyncio
async def test_groups_only_registry_oauth_records_with_duplicates() -> None:
    db, collection = _db([])

    await migration.up(db)

    db.__getitem__.assert_called_once_with("tokens")
    match, group, having = collection.aggregate.await_args.args[0]
    pattern = match["$match"]["identifier"]["$regex"]
    assert re.match(pattern, registry_access_token_identifier("atlassian"))
    assert not re.match(pattern, "mcp:atlassian")  # Jarvis Chat's records
    assert sorted(match["$match"]["type"]["$in"]) == sorted(
        [TokenType.MCP_OAUTH_ACCESS.value, TokenType.MCP_OAUTH_REFRESH.value]
    )
    assert set(group["$group"]["_id"]) == {"userId", "type", "identifier"}
    assert having == {"$match": {"count": {"$gt": 1}}}
    collection.delete_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_keeps_minimum_id_and_deletes_only_seen_duplicates() -> None:
    user = ObjectId()
    oldest, middle, newest = sorted(ObjectId() for _ in range(3))
    db, collection = _db([{"_id": _key(user), "ids": [newest, oldest, middle], "count": 3}])

    await migration.up(db)

    (filter_,) = collection.delete_many.await_args.args
    # A fixed list of _ids (not "$ne survivor"), scoped to the group's key.
    assert sorted(filter_["_id"]["$in"]) == [middle, newest]
    assert {k: v for k, v in filter_.items() if k != "_id"} == _key(user)


@pytest.mark.asyncio
async def test_each_group_is_deduplicated_independently() -> None:
    a, b = ObjectId(), ObjectId()
    ids_a = sorted(ObjectId() for _ in range(2))
    ids_b = sorted(ObjectId() for _ in range(2))
    db, collection = _db(
        [
            {"_id": _key(a), "ids": ids_a, "count": 2},
            {"_id": _key(b), "ids": ids_b, "count": 2},
        ]
    )

    await migration.up(db)

    deleted = [call.args[0]["_id"]["$in"] for call in collection.delete_many.await_args_list]
    assert deleted == [[ids_a[1]], [ids_b[1]]]
