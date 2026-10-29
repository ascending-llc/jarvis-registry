from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from registry_pkgs.migrations.versions import m0003_snake_case_mcp_server_apikey_fields as migration


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
    )
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db, collection


def _ops(collection: MagicMock) -> list:
    """All UpdateOne ops across every bulk_write call, in order."""
    return [op for call in collection.bulk_write.await_args_list for op in call.args[0]]


@pytest.mark.asyncio
async def test_find_filter_only_matches_camel_case_documents() -> None:
    db, collection = _db([])

    await migration.up(db)

    db.__getitem__.assert_called_once_with("mcpservers")
    match, _projection = collection.find.call_args.args
    assert match == {
        "$or": [
            {"config.apiKey.authorizationType": {"$exists": True}},
            {"config.apiKey.customHeader": {"$exists": True}},
        ]
    }
    collection.bulk_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_is_compare_and_swap_with_set_and_unset_only() -> None:
    _id = ObjectId()
    db, collection = _db(
        [{"_id": _id, "config": {"apiKey": {"authorizationType": "custom", "customHeader": "Authorization"}}}]
    )

    await migration.up(db)

    assert collection.bulk_write.await_args.kwargs["ordered"] is False
    (op,) = _ops(collection)
    # CAS filter: _id plus the observed camelCase values.
    assert op._filter == {
        "_id": _id,
        "config.apiKey.authorizationType": "custom",
        "config.apiKey.customHeader": "Authorization",
    }
    # Only idempotent operators.
    assert set(op._doc) == {"$set", "$unset"}
    assert op._doc["$set"] == {
        "config.apiKey.authorization_type": "custom",
        "config.apiKey.custom_header": "Authorization",
    }
    assert op._doc["$unset"] == {
        "config.apiKey.authorizationType": "",
        "config.apiKey.customHeader": "",
    }


@pytest.mark.asyncio
async def test_mixed_document_camel_case_wins_and_key_source_untouched() -> None:
    _id = ObjectId()
    db, collection = _db(
        [
            {
                "_id": _id,
                "config": {
                    "apiKey": {
                        "source": "admin",
                        "authorization_type": "bearer",  # stale snake_case
                        "authorizationType": "custom",  # camelCase wins
                        "key": "enc-key",
                    }
                },
            }
        ]
    )

    await migration.up(db)

    (op,) = _ops(collection)
    # camelCase value is the one $set.
    assert op._doc["$set"] == {"config.apiKey.authorization_type": "custom"}
    # customHeader absent in the doc → not touched.
    assert op._doc["$unset"] == {"config.apiKey.authorizationType": ""}
    # key and source never appear in filter or update.
    flat = str(op._filter) + str(op._doc)
    assert "key" not in op._doc["$set"] and "enc-key" not in flat
    assert "source" not in flat


@pytest.mark.asyncio
async def test_writes_are_flushed_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migration, "_BATCH_SIZE", 2)
    ids = [ObjectId() for _ in range(3)]
    db, collection = _db([{"_id": _id, "config": {"apiKey": {"authorizationType": "bearer"}}} for _id in ids])

    await migration.up(db)

    # One full batch mid-stream, then the remainder: no op dropped or sent twice.
    assert [len(call.args[0]) for call in collection.bulk_write.await_args_list] == [2, 1]
    assert [op._filter["_id"] for op in _ops(collection)] == ids


@pytest.mark.asyncio
async def test_document_without_camel_case_fields_is_skipped() -> None:
    db, collection = _db([{"_id": ObjectId(), "config": {"apiKey": {"authorization_type": "bearer"}}}])

    await migration.up(db)

    collection.bulk_write.assert_not_awaited()
