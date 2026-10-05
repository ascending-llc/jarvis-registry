"""Tests for migration 0001: idempotent seeding of Registry-owned access roles."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry_pkgs.migrations.versions import m0001_seed_access_roles as migration
from registry_pkgs.models.extended_access_role import RegistryResourceType

_EXPECTED_ROLE_COUNT = 21


def _db(upserted_ids: list[object | None]) -> tuple[MagicMock, MagicMock]:
    collection = MagicMock(update_one=AsyncMock(side_effect=[MagicMock(upserted_id=i) for i in upserted_ids]))
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db, collection


@pytest.mark.asyncio
async def test_upserts_each_role_with_set_on_insert_only() -> None:
    db, collection = _db([None] * _EXPECTED_ROLE_COUNT)

    await migration.up(db)

    db.__getitem__.assert_called_once_with("accessroles")
    calls = collection.update_one.await_args_list
    assert len(calls) == _EXPECTED_ROLE_COUNT
    role_ids = [call.args[0]["accessRoleId"] for call in calls]
    assert len(set(role_ids)) == _EXPECTED_ROLE_COUNT
    for call in calls:
        filter_, update = call.args
        assert call.kwargs == {"upsert": True}
        assert list(filter_) == ["accessRoleId"]
        # $setOnInsert alone is what makes a re-run unable to modify an existing role.
        assert list(update) == ["$setOnInsert"]
        assert update["$setOnInsert"]["accessRoleId"] == filter_["accessRoleId"]


@pytest.mark.asyncio
async def test_all_roles_share_one_timestamp() -> None:
    db, collection = _db([None] * _EXPECTED_ROLE_COUNT)

    await migration.up(db)

    roles = [call.args[1]["$setOnInsert"] for call in collection.update_one.await_args_list]
    timestamps = {role["createdAt"] for role in roles} | {role["updatedAt"] for role in roles}
    assert len(timestamps) == 1
    assert next(iter(timestamps)).tzinfo is not None


@pytest.mark.asyncio
async def test_seeds_viewer_editor_owner_for_every_registry_resource_type() -> None:
    db, collection = _db([None] * _EXPECTED_ROLE_COUNT)

    await migration.up(db)

    roles = [call.args[1]["$setOnInsert"] for call in collection.update_one.await_args_list]
    by_type: dict[str, list[int]] = {}
    for role in roles:
        by_type.setdefault(role["resourceType"], []).append(role["permBits"])
    assert set(by_type) == {member.value for member in RegistryResourceType}
    assert all(sorted(bits) == [1, 3, 15] for bits in by_type.values())


@pytest.mark.asyncio
async def test_logs_inserted_count(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=migration.__name__)
    db, _ = _db(["new-id", "new-id"] + [None] * (_EXPECTED_ROLE_COUNT - 2))

    await migration.up(db)

    assert "Access roles: 2 inserted, 19 already present" in caplog.text
