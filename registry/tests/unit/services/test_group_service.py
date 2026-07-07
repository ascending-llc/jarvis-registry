"""Unit tests for GroupService sync methods."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.group_directory_client import IdPGroupDirectoryClient
from registry.services.group_service import GroupService
from registry_pkgs.models._generated.group import Group, GroupSource
from registry_pkgs.models._generated.user import User


def _make_user(oid: str = "user-oid-1") -> MagicMock:
    user = MagicMock(spec=User)
    user.idOnTheSource = oid
    user.id = PydanticObjectId()
    return user


def _make_group(source: GroupSource = GroupSource.ENTRA, id_on_source: str = "g1") -> MagicMock:
    group = MagicMock(spec=Group)
    group.id = PydanticObjectId()
    group.source = source
    group.idOnTheSource = id_on_source
    group.memberIds = []
    group.set = AsyncMock()
    return group


def _make_service(
    client_group_ids: list | None = None,
    client_members: list | None = None,
    client_details: list | None = None,
) -> GroupService:
    mock_client = MagicMock(spec=IdPGroupDirectoryClient)
    mock_client.get_user_group_ids = AsyncMock(return_value=client_group_ids or [])
    mock_client.get_group_members = AsyncMock(return_value=client_members or [])
    mock_client.get_group_details_batch = AsyncMock(return_value=client_details or [])
    return GroupService(group_directory_client=mock_client)


async def test_sync_skips_when_disabled():
    service = _make_service()
    user = _make_user()
    await service.sync_user_group_memberships(user, enabled=False)
    service._directory_client.get_user_group_ids.assert_not_called()


async def test_sync_skips_when_idOnTheSource_is_none():
    service = _make_service()
    user = _make_user()
    user.idOnTheSource = None
    await service.sync_user_group_memberships(user, enabled=True)
    service._directory_client.get_user_group_ids.assert_not_called()


async def test_sync_skips_when_idOnTheSource_is_empty_string():
    service = _make_service()
    user = _make_user()
    user.idOnTheSource = ""
    await service.sync_user_group_memberships(user, enabled=True)
    service._directory_client.get_user_group_ids.assert_not_called()


async def test_sync_skips_db_write_when_graph_returns_empty_list():
    """Empty list from Graph must not touch DB (protects against transient failures)."""
    service = _make_service(client_group_ids=[])
    user = _make_user()

    with patch("registry.services.group_service.Group") as mock_group_cls:
        await service.sync_user_group_memberships(user, enabled=True)
        mock_group_cls.find.assert_not_called()


async def test_sync_adds_user_to_existing_entra_group():
    """User is bulk-added to Entra groups that already exist in DB."""
    service = _make_service(client_group_ids=["g1"])
    user = _make_user(oid="oid-user")

    existing_group = MagicMock()
    existing_group.idOnTheSource = "g1"

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[existing_group])

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user, enabled=True)

    # Group.find must have been called at least twice:
    # once for $addToSet on existing groups, once to fetch existing list
    assert mock_group_cls.find.call_count >= 2


async def test_sync_fetches_details_for_missing_groups():
    """Groups not in DB trigger a get_group_details_batch call."""
    service = _make_service(
        client_group_ids=["g-new"],
        client_details=[{"id": "g-new", "name": "New Group", "email": "ng@example.com", "description": "desc"}],
    )
    user = _make_user()

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[])  # nothing in DB

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user, enabled=True)

    service._directory_client.get_group_details_batch.assert_called_once_with(["g-new"])


async def test_sync_partial_batch_logs_warning_and_skips_unresolved():
    """Partial $batch result: resolved groups get $addToSet; unresolved ones are skipped with a warning."""
    service = _make_service(
        client_group_ids=["g-new-1", "g-new-2"],
        client_details=[{"id": "g-new-1", "name": "G1", "email": None, "description": None}],
    )
    user = _make_user()

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[])  # both groups missing from DB

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        with patch("registry.services.group_service.logger") as mock_logger:
            await service.sync_user_group_memberships(user, enabled=True)
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "get_group_details_batch" in warning_msg

    # $addToSet must only reference the resolved group (g-new-1), not g-new-2
    addtoset_calls = [c for c in find_mock.update_many.call_args_list if "$addToSet" in str(c)]
    for call in addtoset_calls:
        query_str = str(call)
        assert "g-new-2" not in query_str or "$nin" in query_str  # stale removal may mention it


async def test_sync_does_not_call_details_when_all_groups_exist():
    """No details call needed when all Graph groups already exist in DB."""
    service = _make_service(client_group_ids=["g1"])
    user = _make_user()

    existing_group = MagicMock()
    existing_group.idOnTheSource = "g1"

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[existing_group])

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user, enabled=True)

    service._directory_client.get_group_details_batch.assert_not_called()


async def test_sync_removes_user_from_stale_groups():
    """stale removal: user must be $pullAll'd from groups no longer in Graph response."""
    service = _make_service(client_group_ids=["g1"])
    user = _make_user(oid="oid-user")

    existing_group = MagicMock()
    existing_group.idOnTheSource = "g1"

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[existing_group])

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user, enabled=True)

    # The stale removal query uses {"$nin": group_ids} — verify it was called
    stale_removal_calls = [c for c in mock_group_cls.find.call_args_list if "$nin" in str(c)]
    assert len(stale_removal_calls) == 1


async def test_ensure_skips_when_disabled():
    service = _make_service()
    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.get = AsyncMock()
        await service.ensure_group_principal_exists("some-id", enabled=False)
        mock_group_cls.get.assert_not_called()


async def test_ensure_skips_when_group_not_found():
    service = _make_service()
    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=None)
        await service.ensure_group_principal_exists(str(PydanticObjectId()), enabled=True)
    service._directory_client.get_group_members.assert_not_called()


async def test_ensure_skips_for_local_source_group():
    service = _make_service()
    local_group = _make_group(source=GroupSource.LOCAL)
    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=local_group)
        await service.ensure_group_principal_exists(str(local_group.id), enabled=True)
    service._directory_client.get_group_members.assert_not_called()


async def test_ensure_skips_when_idOnTheSource_is_none():
    service = _make_service()
    group = _make_group(source=GroupSource.ENTRA)
    group.idOnTheSource = None
    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        await service.ensure_group_principal_exists(str(group.id), enabled=True)
    service._directory_client.get_group_members.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_group_principal_exists — happy path + error
# ---------------------------------------------------------------------------


async def test_ensure_replaces_member_ids_with_full_snapshot():
    service = _make_service(client_members=["u1", "u2", "u3"])
    group = _make_group(source=GroupSource.ENTRA, id_on_source="g-entra")

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        await service.ensure_group_principal_exists(str(group.id), enabled=True)

    group.set.assert_called_once_with({"memberIds": ["u1", "u2", "u3"]})


async def test_ensure_propagates_directory_client_error():
    service = _make_service()
    service._directory_client.get_group_members = AsyncMock(side_effect=ValueError("graph error"))
    group = _make_group(source=GroupSource.ENTRA, id_on_source="g-entra")

    with patch("registry.services.group_service.Group") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        with patch("registry.services.group_service.logger") as mock_logger:
            with pytest.raises(ValueError, match="graph error"):
                await service.ensure_group_principal_exists(str(group.id), enabled=True)
            mock_logger.error.assert_called_once()
