from unittest.mock import AsyncMock, MagicMock

import pytest

from registry_pkgs.migrations.versions import m0006_rebuild_resource_capture_metadata as migration


@pytest.mark.asyncio
async def test_rebuild_is_marked_once_without_overwriting_concurrent_capture() -> None:
    collection = MagicMock(update_many=AsyncMock(return_value=MagicMock(matched_count=2, modified_count=2)))
    db = MagicMock()
    db.__getitem__.return_value = collection
    await migration.up(db)
    db.__getitem__.assert_called_once_with("mcpservers")
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
