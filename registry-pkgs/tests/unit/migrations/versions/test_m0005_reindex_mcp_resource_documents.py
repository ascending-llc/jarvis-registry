from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from registry_pkgs.migrations.versions import m0005_reindex_mcp_resource_documents as migration
from registry_pkgs.models.enums import McpAuthMode
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer


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


def _db(docs: list[dict]) -> tuple[MagicMock, MagicMock]:
    collection = MagicMock(
        find=MagicMock(return_value=_Cursor(docs)),
        bulk_write=AsyncMock(return_value=MagicMock(modified_count=len(docs))),
        update_many=AsyncMock(return_value=MagicMock(matched_count=0, modified_count=0)),
    )
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db, collection


def _ops(collection: MagicMock) -> list:
    return [op for call in collection.bulk_write.await_args_list for op in call.args[0]]


@pytest.mark.asyncio
async def test_strip_step_only_reads_servers_with_resources() -> None:
    db, collection = _db([])

    await migration.up(db)

    db.__getitem__.assert_called_once_with("mcpservers")
    match, projection = collection.find.call_args.args
    assert match == {"config.resources.0": {"$exists": True}}
    assert set(projection) == {"config.requiresOAuth", "config.oauth", "config.runtimeAccess"}
    collection.bulk_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_strip_step_clears_only_oauth_servers_with_set_only() -> None:
    oauth_id, api_key_id = ObjectId(), ObjectId()
    db, collection = _db(
        [
            {"_id": oauth_id, "config": {"requiresOAuth": True}},
            {"_id": api_key_id, "config": {}},
        ]
    )

    await migration.up(db)

    ops = _ops(collection)
    assert len(ops) == 1
    assert ops[0]._filter == {
        "_id": oauth_id,
        "config.resources.0": {"$exists": True},
        "config.requiresOAuth": {"$exists": True, "$eq": True},
        "config.oauth": {"$exists": False},
        "config.runtimeAccess": {"$exists": False},
    }
    assert set(ops[0]._doc) == {"$set"}
    assert ops[0]._doc["$set"]["config.resources"] == []
    assert ops[0]._doc["$set"]["vectorSyncAttempts"] == 0
    assert ops[0]._doc["$set"]["vectorSyncPendingAt"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "expected_auth_filter"),
    [
        (
            {"oauth": {"client_id": "x"}},
            {
                "config.requiresOAuth": {"$exists": False},
                "config.oauth": {"$exists": True, "$eq": {"client_id": "x"}},
                "config.runtimeAccess": {"$exists": False},
            },
        ),
        (
            {"requiresOAuth": False, "oauth": {}, "runtimeAccess": {}},
            {
                "config.requiresOAuth": {"$exists": True, "$eq": False},
                "config.oauth": {"$exists": True, "$eq": {}},
                "config.runtimeAccess": {"$exists": True, "$eq": {}},
            },
        ),
        (
            {"requiresOAuth": None, "oauth": None, "runtimeAccess": None},
            {
                "config.requiresOAuth": {"$exists": True, "$eq": None},
                "config.oauth": {"$exists": True, "$eq": None},
                "config.runtimeAccess": {"$exists": True, "$eq": None},
            },
        ),
    ],
)
async def test_strip_step_guards_all_authentication_fields(config: dict, expected_auth_filter: dict) -> None:
    server_id = ObjectId()
    db, collection = _db([{"_id": server_id, "config": config}])

    await migration.up(db)

    # Mocks do not evaluate Mongo filters; assert the CAS preconditions themselves.
    assert _ops(collection)[0]._filter == {
        "_id": server_id,
        "config.resources.0": {"$exists": True},
        **expected_auth_filter,
    }


@pytest.mark.asyncio
async def test_rebuild_step_skips_servers_already_pending() -> None:
    db, collection = _db([])

    await migration.up(db)

    match, update = collection.update_many.await_args.args
    assert match == {"config.resources.0": {"$exists": True}, "vectorSyncPendingAt": None}
    assert set(update) == {"$set"}
    assert update["$set"]["vectorSyncAttempts"] == 0


@pytest.mark.parametrize(
    "config",
    [
        {"requiresOAuth": True},
        {"oauth": {"client_id": "x"}},
        {"requiresOAuth": False, "oauth": {}},
        {"runtimeAccess": {"mode": "jwt"}, "oauth": {"client_id": "x"}},
        {"apiKey": {"key": "k"}},
        {},
    ],
)
def test_oauth_classifier_matches_model(config: dict) -> None:
    server = ExtendedMCPServer.model_construct(config=config)
    assert migration._is_oauth(config) == (server.mcp_auth_mode is McpAuthMode.OAUTH)
