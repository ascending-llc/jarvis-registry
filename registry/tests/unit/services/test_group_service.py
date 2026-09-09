"""Unit tests for GroupService per-provider sync methods."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.group_directory_client import IdPGroupDirectoryClient
from registry.services.group_service import GroupService
from registry_pkgs.models import ExtendedGroupSource
from registry_pkgs.models._generated.user import User


def _make_user(oid: str = "user-oid-1", provider: str = "openid") -> MagicMock:
    user = MagicMock(spec=User)
    user.idOnTheSource = oid
    user.id = PydanticObjectId()
    user.provider = provider
    return user


def _make_group(source: ExtendedGroupSource = ExtendedGroupSource.ENTRA, id_on_source: str = "g1") -> MagicMock:
    group = MagicMock()
    group.id = PydanticObjectId()
    group.source = source
    group.idOnTheSource = id_on_source
    group.memberIds = []
    group.set = AsyncMock()
    return group


def _make_client(
    group_ids: list | None = None,
    members: list | None = None,
    details: list | None = None,
) -> MagicMock:
    client = MagicMock(spec=IdPGroupDirectoryClient)
    client.get_user_group_ids = AsyncMock(return_value=group_ids or [])
    client.get_group_members = AsyncMock(return_value=members or [])
    client.get_group_details_batch = AsyncMock(return_value=details or [])
    return client


def _make_service(
    *,
    entra_client: MagicMock | None = None,
    google_client: MagicMock | None = None,
    entra_enabled: bool = True,
    google_enabled: bool = True,
) -> GroupService:
    clients: dict[ExtendedGroupSource, MagicMock] = {}
    if entra_enabled:
        clients[ExtendedGroupSource.ENTRA] = entra_client or _make_client()
    if google_enabled:
        clients[ExtendedGroupSource.GOOGLE] = google_client or _make_client()
    return GroupService(directory_clients=clients)


def _entra_client(service: GroupService) -> MagicMock:
    return service._clients[ExtendedGroupSource.ENTRA]


def _google_client(service: GroupService) -> MagicMock:
    return service._clients[ExtendedGroupSource.GOOGLE]


async def test_sync_skips_when_source_disabled():
    entra = _make_client(group_ids=["g1"])
    service = _make_service(entra_client=entra, entra_enabled=False)
    user = _make_user()
    await service.sync_user_group_memberships(user)
    entra.get_user_group_ids.assert_not_called()


async def test_sync_skips_when_idOnTheSource_is_none():
    service = _make_service()
    user = _make_user()
    user.idOnTheSource = None
    await service.sync_user_group_memberships(user)
    _entra_client(service).get_user_group_ids.assert_not_called()


async def test_sync_skips_when_idOnTheSource_is_empty_string():
    service = _make_service()
    user = _make_user()
    user.idOnTheSource = ""
    await service.sync_user_group_memberships(user)
    _entra_client(service).get_user_group_ids.assert_not_called()


async def test_sync_skips_db_write_when_directory_returns_empty_list():
    """Empty list from the directory must not touch DB (protects against transient failures)."""
    service = _make_service(entra_client=_make_client(group_ids=[]))
    user = _make_user()

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        await service.sync_user_group_memberships(user)
        mock_group_cls.find.assert_not_called()


async def test_sync_adds_user_to_existing_entra_group():
    """User is bulk-added to groups that already exist in DB."""
    service = _make_service(entra_client=_make_client(group_ids=["g1"]))
    user = _make_user(oid="oid-user")

    existing_group = MagicMock()
    existing_group.idOnTheSource = "g1"

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[existing_group])

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user)

    assert mock_group_cls.find.call_count >= 2


async def test_sync_google_user_uses_google_client_not_entra():
    """A provider='google' user must sync against the Google directory client only."""
    entra = _make_client(group_ids=["entra-g"])
    google = _make_client(group_ids=[])  # empty -> no DB writes, short-circuits after fetch
    service = _make_service(entra_client=entra, google_client=google)
    user = _make_user(provider="google")

    await service.sync_user_group_memberships(user)

    google.get_user_group_ids.assert_called_once_with(user.idOnTheSource)
    entra.get_user_group_ids.assert_not_called()


async def test_sync_google_upserts_with_google_source():
    """New Google groups are upserted with source=GOOGLE."""
    google = _make_client(
        group_ids=["groups/1"],
        details=[{"id": "groups/1", "name": "G", "email": "g@corp.com", "description": "d"}],
    )
    service = _make_service(google_client=google)
    user = _make_user(provider="google")

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[])  # nothing in DB

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user)

    google.get_group_details_batch.assert_called_once_with(["groups/1"])
    # every find() query must be scoped to the GOOGLE source
    assert all(ExtendedGroupSource.GOOGLE in str(c) for c in mock_group_cls.find.call_args_list)


async def test_sync_fetches_details_for_missing_groups():
    """Groups not in DB trigger a get_group_details_batch call."""
    service = _make_service(
        entra_client=_make_client(
            group_ids=["g-new"],
            details=[{"id": "g-new", "name": "New Group", "email": "ng@example.com", "description": "desc"}],
        )
    )
    user = _make_user()

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[])

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user)

    _entra_client(service).get_group_details_batch.assert_called_once_with(["g-new"])


async def test_sync_partial_batch_logs_warning_and_skips_unresolved():
    """Partial batch result: resolved groups get $addToSet; unresolved ones are skipped with a warning."""
    service = _make_service(
        entra_client=_make_client(
            group_ids=["g-new-1", "g-new-2"],
            details=[{"id": "g-new-1", "name": "G1", "email": None, "description": None}],
        )
    )
    user = _make_user()

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[])

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        with patch("registry.services.group_service.logger") as mock_logger:
            await service.sync_user_group_memberships(user)
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "get_group_details_batch" in warning_msg

    addtoset_calls = [c for c in find_mock.update_many.call_args_list if "$addToSet" in str(c)]
    for call in addtoset_calls:
        query_str = str(call)
        assert "g-new-2" not in query_str or "$nin" in query_str


async def test_sync_does_not_call_details_when_all_groups_exist():
    """No details call needed when all directory groups already exist in DB."""
    service = _make_service(entra_client=_make_client(group_ids=["g1"]))
    user = _make_user()

    existing_group = MagicMock()
    existing_group.idOnTheSource = "g1"

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[existing_group])

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user)

    _entra_client(service).get_group_details_batch.assert_not_called()


async def test_sync_removes_user_from_stale_groups():
    """stale removal: user must be $pullAll'd from groups no longer in the directory response."""
    service = _make_service(entra_client=_make_client(group_ids=["g1"]))
    user = _make_user(oid="oid-user")

    existing_group = MagicMock()
    existing_group.idOnTheSource = "g1"

    find_mock = MagicMock()
    find_mock.update_many = AsyncMock()
    find_mock.to_list = AsyncMock(return_value=[existing_group])

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.find.return_value = find_mock
        await service.sync_user_group_memberships(user)

    stale_removal_calls = [c for c in mock_group_cls.find.call_args_list if "$nin" in str(c)]
    assert len(stale_removal_calls) == 1


# ---------------------------------------------------------------------------
# ensure_group_principal_exists
# ---------------------------------------------------------------------------


async def test_ensure_skips_when_group_not_found():
    service = _make_service()
    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=None)
        await service.ensure_group_principal_exists(str(PydanticObjectId()))
    _entra_client(service).get_group_members.assert_not_called()


async def test_ensure_skips_for_local_source_group():
    service = _make_service()
    local_group = _make_group(source=ExtendedGroupSource.LOCAL)
    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=local_group)
        await service.ensure_group_principal_exists(str(local_group.id))
    _entra_client(service).get_group_members.assert_not_called()
    _google_client(service).get_group_members.assert_not_called()


async def test_ensure_skips_when_source_disabled():
    entra = _make_client(members=["u1"])
    service = _make_service(entra_client=entra, entra_enabled=False)
    group = _make_group(source=ExtendedGroupSource.ENTRA)
    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        await service.ensure_group_principal_exists(str(group.id))
    entra.get_group_members.assert_not_called()


async def test_ensure_skips_when_idOnTheSource_is_none():
    service = _make_service()
    group = _make_group(source=ExtendedGroupSource.ENTRA)
    group.idOnTheSource = None
    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        await service.ensure_group_principal_exists(str(group.id))
    _entra_client(service).get_group_members.assert_not_called()


async def test_ensure_replaces_member_ids_with_full_snapshot_entra():
    entra = _make_client(members=["u1", "u2", "u3"])
    service = _make_service(entra_client=entra)
    group = _make_group(source=ExtendedGroupSource.ENTRA, id_on_source="g-entra")

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        await service.ensure_group_principal_exists(str(group.id))

    group.set.assert_called_once_with({"memberIds": ["u1", "u2", "u3"]})


async def test_ensure_replaces_member_ids_with_full_snapshot_google():
    google = _make_client(members=["a@corp.com", "b@corp.com"])
    service = _make_service(google_client=google)
    group = _make_group(source=ExtendedGroupSource.GOOGLE, id_on_source="groups/42")

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        await service.ensure_group_principal_exists(str(group.id))

    google.get_group_members.assert_called_once_with("groups/42")
    group.set.assert_called_once_with({"memberIds": ["a@corp.com", "b@corp.com"]})


async def test_ensure_propagates_directory_client_error():
    entra = _make_client()
    entra.get_group_members = AsyncMock(side_effect=ValueError("graph error"))
    service = _make_service(entra_client=entra)
    group = _make_group(source=ExtendedGroupSource.ENTRA, id_on_source="g-entra")

    with patch("registry.services.group_service.ExtendedGroup") as mock_group_cls:
        mock_group_cls.get = AsyncMock(return_value=group)
        with patch("registry.services.group_service.logger") as mock_logger:
            with pytest.raises(ValueError, match="graph error"):
                await service.ensure_group_principal_exists(str(group.id))
            mock_logger.error.assert_called_once()
