from unittest.mock import AsyncMock, MagicMock

import pytest

from registry_pkgs.migrations.versions import m0006_rebuild_resource_capture_metadata as migration
from registry_pkgs.models.mcp_resource_capture import uses_shared_credentials


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
        bulk_write=AsyncMock(return_value=MagicMock(modified_count=1)),
        update_many=AsyncMock(return_value=MagicMock(matched_count=2, modified_count=2)),
    )
    db = MagicMock()
    db.__getitem__.return_value = collection
    return db, collection


@pytest.mark.asyncio
async def test_queues_recapture_only_for_unsigned_shared_lists() -> None:
    shared = {"_id": 1, "config": {"requiresOAuth": False}}
    oauth = {"_id": 2, "config": {"oauth": {"client_id": "x"}}}
    agentcore = {"_id": 3, "config": {"oauth": {}, "runtimeAccess": {"roleArn": "r"}}}
    db, collection = _db([shared, oauth, agentcore])

    await migration.up(db)

    query, projection = collection.find.call_args.args
    assert query == {
        "config.resources.0": {"$exists": True},
        "config.resourceCapture": {"$exists": False},
        "resourceRecapturePendingAt": {"$exists": False},
    }
    assert set(projection) == {"config.requiresOAuth", "config.oauth", "config.runtimeAccess"}
    ops = collection.bulk_write.await_args.args[0]
    assert [op._filter["_id"] for op in ops] == [1, 3]
    first = ops[0]
    assert first._filter["config.requiresOAuth"] == {"$exists": True, "$eq": False}
    assert first._filter["config.oauth"] == {"$exists": False}
    assert first._filter["resourceRecapturePendingAt"] == {"$exists": False}
    assert set(first._doc) == {"$set"}
    assert set(first._doc["$set"]) == {"resourceRecapturePendingAt", "resourceRecaptureAttempts"}


@pytest.mark.asyncio
async def test_rebuild_is_marked_once_without_overwriting_concurrent_capture() -> None:
    db, collection = _db([])
    await migration.up(db)
    db.__getitem__.assert_called_with("mcpservers")
    collection.bulk_write.assert_not_awaited()
    query, update = collection.update_many.await_args.args
    assert query == {
        "config.resourceVisibilityVersion": {"$ne": 1},
        "$or": [
            {"config.resources.0": {"$exists": True}},
            {"config.resourceTemplates.0": {"$exists": True}},
        ],
    }
    assert set(update) == {"$set"}
    assert set(update["$set"]) == {"config.resourceVisibilityVersion", "vectorSyncPendingAt", "vectorSyncAttempts"}
    assert update["$set"]["config.resourceVisibilityVersion"] == 1
    assert update["$set"]["vectorSyncAttempts"] == 0


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"requiresOAuth": True},
        {"requiresOAuth": False},
        {"oauth": None},
        {"oauth": {}, "runtimeAccess": {"roleArn": "r"}},
        {"requiresOAuth": True, "runtimeAccess": None},
    ],
)
def test_copied_classifier_matches_app(config: dict) -> None:
    assert migration._uses_shared_credentials(config) is uses_shared_credentials(config)
