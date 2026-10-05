"""Tests for the `registry_migrations` record model and its collection I/O."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

from registry_pkgs.migrations.records import MIGRATIONS_COLLECTION, MigrationRecord, insert_record, read_records

_APPLIED_AT = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)


def _record(**overrides: object) -> MigrationRecord:
    fields: dict[str, object] = {
        "id": "0001",
        "name": "seed_access_roles",
        "checksum": "abc",
        "applied_at": _APPLIED_AT,
        "duration_ms": 12,
        "build_version": "v1",
    }
    fields.update(overrides)
    return MigrationRecord(**fields)


def _db(collection: MagicMock) -> MagicMock:
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db


def test_dumps_version_as_mongo_id() -> None:
    assert _record().model_dump(by_alias=True) == {
        "_id": "0001",
        "name": "seed_access_roles",
        "checksum": "abc",
        "applied_at": _APPLIED_AT,
        "duration_ms": 12,
        "build_version": "v1",
    }


def test_parses_mongo_document_and_assumes_utc_for_naive_datetimes() -> None:
    record = MigrationRecord.model_validate(
        {
            "_id": "0002",
            "name": "x",
            "checksum": "c",
            "applied_at": datetime(2026, 10, 1, 12, 30),  # pymongo returns naive UTC by default
            "duration_ms": 5,
            "build_version": "b",
        }
    )

    assert record.id == "0002"
    assert record.applied_at == _APPLIED_AT
    assert record.applied_at.tzinfo is UTC


def test_keeps_aware_datetimes() -> None:
    assert _record().applied_at == _APPLIED_AT


@pytest.mark.asyncio
async def test_read_records_reads_tracking_collection_sorted() -> None:
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[_record(id="0001").model_dump(by_alias=True)])
    collection = MagicMock(find=MagicMock(return_value=cursor))
    db = _db(collection)

    records = await read_records(db)

    db.__getitem__.assert_called_once_with(MIGRATIONS_COLLECTION)
    collection.find.assert_called_once_with({})
    cursor.sort.assert_called_once_with("_id", 1)
    assert records == [_record(id="0001")]


@pytest.mark.asyncio
async def test_insert_record_writes_by_alias() -> None:
    collection = MagicMock(insert_one=AsyncMock())
    db = _db(collection)

    await insert_record(db, _record())

    db.__getitem__.assert_called_once_with(MIGRATIONS_COLLECTION)
    collection.insert_one.assert_awaited_once_with(_record().model_dump(by_alias=True))


@pytest.mark.asyncio
async def test_insert_record_propagates_duplicate_key() -> None:
    collection = MagicMock(insert_one=AsyncMock(side_effect=DuplicateKeyError("dup")))

    with pytest.raises(DuplicateKeyError):
        await insert_record(_db(collection), _record())
