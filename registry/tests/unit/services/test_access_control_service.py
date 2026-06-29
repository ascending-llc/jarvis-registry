from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.acl_schema import ResourcePermissions
from registry.services.access_control_service import ACLService
from registry.services.group_service import GroupService
from registry.services.user_service import UserService
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import PermissionBits, RoleBits


class TestACLService:
    def _mock_sorted_acl_query(self, mock_acl_entry, entries):
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=entries)
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        return mock_find_result

    def _assert_group_clause_present(self, query, group_id):
        assert {
            "principalType": PrincipalType.GROUP.value,
            "principalId": {"$in": [group_id]},
        } in query["$or"]

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_grant_permission_new_entry(self, mock_acl_entry):
        role_id = PydanticObjectId()
        mock_session = AsyncMock()
        service = ACLService(
            user_service=Mock(),
            group_service=Mock(),
            role_cache={(ResourceType.MCPSERVER.value, PermissionBits.EDIT): role_id},
        )
        mock_acl_entry.find_one = AsyncMock(return_value=None)

        # RegistryAclEntry() returns an AsyncMock, whose insert is also an AsyncMock
        new_entry = AsyncMock()
        new_entry.insert = AsyncMock()
        mock_acl_entry.return_value = new_entry
        with patch("registry.services.access_control_service.RegistryAclEntry", mock_acl_entry):
            await service.grant_permission(
                principal_type="user",
                principal_id={"id": "user1"},
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=PermissionBits.EDIT,
                session=mock_session,
            )
            new_entry.insert.assert_awaited()
            assert mock_acl_entry.call_args.kwargs["roleId"] == role_id

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_grant_permission_update_existing(self, mock_acl_entry):
        mock_session = AsyncMock()
        service = ACLService(
            user_service=Mock(),
            group_service=Mock(),
            role_cache={(ResourceType.MCPSERVER.value, PermissionBits.EDIT): PydanticObjectId()},
        )
        existing_entry = MagicMock()
        existing_entry.save = AsyncMock()
        mock_acl_entry.find_one = AsyncMock(return_value=existing_entry)
        with patch("registry.services.access_control_service.datetime") as mock_datetime:
            mock_datetime.now.return_value = MagicMock()
            await service.grant_permission(
                principal_type="user",
                principal_id={"id": "user1"},
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=PermissionBits.EDIT,
                session=mock_session,
            )
            existing_entry.save.assert_awaited()

    @pytest.mark.asyncio
    async def test_grant_permission_missing_principal_id(self):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        with pytest.raises(ValueError):
            await service.grant_permission(
                principal_type="user",
                principal_id=None,
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=PermissionBits.EDIT,
            )

    @pytest.mark.asyncio
    async def test_grant_permission_no_matching_role_raises(self):
        """Null-guard: perm_bits with no matching role in the cache raises ValueError."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        with pytest.raises(ValueError, match="No role found"):
            await service.grant_permission(
                principal_type="user",
                principal_id={"id": "user1"},
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=PermissionBits.EDIT,
            )

    @pytest.mark.asyncio
    async def test_grant_permission_public_non_view_rejected(self):
        """Hard invariant: a PUBLIC principal may only be granted VIEW.

        Structural backstop for the route guard — a public OWNER/EDITOR grant is
        rejected at the write boundary regardless of how it arrived.
        """
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        with pytest.raises(ValueError, match="VIEW"):
            await service.grant_permission(
                principal_type=PrincipalType.PUBLIC.value,
                principal_id=None,
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=RoleBits.OWNER,
            )

    @pytest.mark.asyncio
    async def test_grant_permission_public_view_passes_invariant(self):
        """PUBLIC + VIEW clears the invariant (then fails later only on empty role_cache)."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        # With an empty cache, a VIEW grant gets past the public clamp and fails on the
        # role lookup instead — proving the clamp does not block the legitimate VIEW path.
        with pytest.raises(ValueError, match="No role found"):
            await service.grant_permission(
                principal_type=PrincipalType.PUBLIC.value,
                principal_id=None,
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=PermissionBits.VIEW,
            )

    def test_resolve_perm_bits_for_role(self):
        """Resolves perm_bits only for a role belonging to the given resource type."""
        role_id = PydanticObjectId()
        service = ACLService(
            user_service=Mock(),
            group_service=Mock(),
            role_cache={(ResourceType.MCPSERVER.value, PermissionBits.EDIT): role_id},
        )
        assert service.resolve_perm_bits_for_role(ResourceType.MCPSERVER.value, role_id) == PermissionBits.EDIT
        assert service.resolve_perm_bits_for_role(ResourceType.MCPSERVER.value, PydanticObjectId()) is None
        assert service.resolve_perm_bits_for_role(ResourceType.AGENT.value, role_id) is None

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.Group")
    @patch("registry.services.access_control_service.User")
    async def test_resolve_group_ids_user_with_entra_id(self, mock_user, mock_group):
        user_id = PydanticObjectId()
        group_a_id = PydanticObjectId()
        group_b_id = PydanticObjectId()
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})

        mock_user.get = AsyncMock(return_value=MagicMock(idOnTheSource="entra-uuid-123"))
        mock_group.find.return_value.to_list = AsyncMock(
            return_value=[
                MagicMock(id=group_a_id),
                MagicMock(id=group_b_id),
            ]
        )

        result = await service._resolve_group_ids_for_user(user_id)

        assert result == [group_a_id, group_b_id]
        mock_group.find.assert_called_once_with({"memberIds": "entra-uuid-123"}, session=None)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.Group")
    @patch("registry.services.access_control_service.User")
    async def test_resolve_group_ids_local_user_returns_empty(self, mock_user, mock_group):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_user.get = AsyncMock(return_value=MagicMock(idOnTheSource=None))

        result = await service._resolve_group_ids_for_user(PydanticObjectId())

        assert result == []
        mock_group.find.assert_not_called()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.Group")
    @patch("registry.services.access_control_service.User")
    async def test_resolve_group_ids_user_not_found_returns_empty(self, mock_user, mock_group):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_user.get = AsyncMock(return_value=None)

        result = await service._resolve_group_ids_for_user(PydanticObjectId())

        assert result == []
        mock_group.find.assert_not_called()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_delete_acl_entries_for_resource(self, mock_acl_entry):
        mock_session = AsyncMock()
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_result = MagicMock()
        mock_result.deleted_count = 2
        mock_acl_entry.find.return_value.delete = AsyncMock(return_value=mock_result)
        deleted = await service.delete_acl_entries_for_resource(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            session=mock_session,
        )
        assert deleted == 2

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_delete_acl_entries_for_resource_exception(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_acl_entry.find.return_value.delete = AsyncMock(side_effect=Exception("fail"))
        deleted = await service.delete_acl_entries_for_resource(
            resource_type=ResourceType.MCPSERVER.value, resource_id=PydanticObjectId()
        )
        assert deleted == 0

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resource_edit_only(self, mock_acl_entry):
        """EDIT bit (2) should only grant EDIT, not VIEW."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        entry = MagicMock()
        entry.permBits = PermissionBits.EDIT

        # Mock the chained methods: find().sort().to_list()
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[entry])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )
        assert isinstance(perms, ResourcePermissions)
        assert perms.VIEW is False
        assert perms.EDIT is True
        assert perms.DELETE is False
        assert perms.SHARE is False

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_group_grant_only(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        group_id = PydanticObjectId()
        entry = MagicMock(
            principalType=PrincipalType.GROUP.value,
            principalId=group_id,
            permBits=PermissionBits.VIEW,
        )
        self._mock_sorted_acl_query(mock_acl_entry, [entry])
        service._resolve_group_ids_for_user = AsyncMock(return_value=[group_id])

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )

        assert perms.VIEW is True
        assert perms.EDIT is False
        query = mock_acl_entry.find.call_args.args[0]
        self._assert_group_clause_present(query, group_id)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_group_grant_higher_than_user_grant(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        group_id = PydanticObjectId()
        user_entry = MagicMock(
            principalType=PrincipalType.USER.value,
            principalId=PydanticObjectId(),
            permBits=PermissionBits.VIEW,
        )
        group_entry = MagicMock(
            principalType=PrincipalType.GROUP.value,
            principalId=group_id,
            permBits=PermissionBits.EDIT,
        )
        self._mock_sorted_acl_query(mock_acl_entry, [group_entry, user_entry])
        service._resolve_group_ids_for_user = AsyncMock(return_value=[group_id])

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )

        assert perms.EDIT is True
        query = mock_acl_entry.find.call_args.args[0]
        self._assert_group_clause_present(query, group_id)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_no_group_membership_skips_group_clause(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        self._mock_sorted_acl_query(mock_acl_entry, [])
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )

        query = mock_acl_entry.find.call_args.args[0]
        assert len(query["$or"]) == 2
        assert all(clause["principalType"] != PrincipalType.GROUP.value for clause in query["$or"])

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_delete_permission(self, mock_acl_entry):
        mock_session = AsyncMock()
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_acl_entry.find.return_value.delete = AsyncMock(return_value=mock_result)
        deleted_count = await service.delete_permission(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            principal_type="user",
            principal_id=PydanticObjectId(),
            session=mock_session,
        )
        assert deleted_count == 1

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_delete_permission_exception(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_acl_entry.find.return_value.delete = AsyncMock(side_effect=Exception("fail"))
        deleted = await service.delete_permission(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            principal_type="user",
            principal_id="user1",
        )
        assert deleted == 0

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resource_owner(self, mock_acl_entry):
        """User with OWNER bits should resolve all permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        entry = MagicMock()
        entry.permBits = RoleBits.OWNER  # 15

        # Mock the chained methods: find().sort().to_list()
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[entry])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )
        assert isinstance(perms, ResourcePermissions)
        assert perms.VIEW is True
        assert perms.EDIT is True
        assert perms.DELETE is True
        assert perms.SHARE is True

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resource_no_match(self, mock_acl_entry):
        """No ACL entry should return all-False permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})

        # Mock the chained methods: find().sort().to_list() returning empty list
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )
        assert perms.VIEW is False
        assert perms.EDIT is False
        assert perms.DELETE is False
        assert perms.SHARE is False

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resource_exception(self, mock_acl_entry):
        """Exception should return all-False permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})

        # Mock find() to raise exception
        mock_acl_entry.find = MagicMock(side_effect=Exception("db error"))
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )
        assert perms == ResourcePermissions()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_check_user_permission_allowed(self, mock_acl_entry):
        """User with VIEW should pass the VIEW check."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        entry = MagicMock()
        entry.permBits = RoleBits.VIEWER  # 1

        # Mock the chained methods: find().sort().to_list()
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[entry])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        perms = await service.check_user_permission(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            required_permission="VIEW",
        )
        assert perms.VIEW is True

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_check_user_permission_denied(self, mock_acl_entry):
        """User with VIEW-only should be denied EDIT."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        entry = MagicMock()
        entry.permBits = RoleBits.VIEWER  # 1 = VIEW only

        # Mock the chained methods: find().sort().to_list()
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[entry])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await service.check_user_permission(
                user_id=PydanticObjectId(),
                resource_type=ResourceType.MCPSERVER,
                resource_id=PydanticObjectId(),
                required_permission="EDIT",
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_check_user_permission_no_entry(self, mock_acl_entry):
        """No ACL entry should raise 403."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})

        # Mock the chained methods: find().sort().to_list() returning empty list
        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        with pytest.raises(HTTPException) as exc_info:
            await service.check_user_permission(
                user_id=PydanticObjectId(),
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                required_permission="VIEW",
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_accessible_resource_ids(self, mock_acl_entry):
        """Should return deduplicated resource IDs with VIEW bit set."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        rid1 = PydanticObjectId()
        rid2 = PydanticObjectId()

        entry_view = MagicMock()
        entry_view.permBits = RoleBits.VIEWER  # 1 — has VIEW
        entry_view.resourceId = rid1

        entry_edit_only = MagicMock()
        entry_edit_only.permBits = PermissionBits.EDIT  # 2 — no VIEW bit
        entry_edit_only.resourceId = rid2

        entry_owner = MagicMock()
        entry_owner.permBits = RoleBits.OWNER  # 15 — has VIEW
        entry_owner.resourceId = rid1  # duplicate of rid1

        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry_view, entry_edit_only, entry_owner])
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        result = await service.get_accessible_resource_ids(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER,
        )
        # rid1 appears twice but should be deduplicated; rid2 has no VIEW bit
        assert result == [str(rid1)]

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_accessible_ids_includes_group_only_resource(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        group_id = PydanticObjectId()
        resource_id = PydanticObjectId()
        entry = MagicMock(
            principalType=PrincipalType.GROUP.value,
            principalId=group_id,
            resourceId=resource_id,
            permBits=PermissionBits.VIEW,
        )
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry])
        service._resolve_group_ids_for_user = AsyncMock(return_value=[group_id])

        result = await service.get_accessible_resource_ids(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
        )

        assert str(resource_id) in result
        query = mock_acl_entry.find.call_args.args[0]
        self._assert_group_clause_present(query, group_id)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_accessible_ids_deduplicates_user_and_group_grant(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        group_id = PydanticObjectId()
        resource_id = PydanticObjectId()
        entries = [
            MagicMock(
                principalType=PrincipalType.USER.value,
                principalId=PydanticObjectId(),
                resourceId=resource_id,
                permBits=PermissionBits.VIEW,
            ),
            MagicMock(
                principalType=PrincipalType.GROUP.value,
                principalId=group_id,
                resourceId=resource_id,
                permBits=PermissionBits.VIEW,
            ),
        ]
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=entries)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[group_id])

        result = await service.get_accessible_resource_ids(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
        )

        assert result.count(str(resource_id)) == 1
        query = mock_acl_entry.find.call_args.args[0]
        self._assert_group_clause_present(query, group_id)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_accessible_resource_ids_exception(self, mock_acl_entry):
        """Exception should return empty list."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_acl_entry.find.return_value.to_list = AsyncMock(side_effect=Exception("fail"))
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])
        result = await service.get_accessible_resource_ids(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER,
        )
        assert result == []

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resources_batches_single_query(self, mock_acl_entry):
        """Batch resolution uses a single $in query and resolves per-resource permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        rid_a, rid_b, rid_missing = PydanticObjectId(), PydanticObjectId(), PydanticObjectId()

        entry_a = MagicMock(resourceId=rid_a, permBits=PermissionBits.VIEW | PermissionBits.EDIT)
        entry_b = MagicMock(resourceId=rid_b, permBits=PermissionBits.VIEW)

        mock_find_result = MagicMock()
        mock_sort_result = MagicMock()
        mock_sort_result.to_list = AsyncMock(return_value=[entry_a, entry_b])
        mock_find_result.sort = MagicMock(return_value=mock_sort_result)
        mock_acl_entry.find = MagicMock(return_value=mock_find_result)
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        result = await service.get_user_permissions_for_resources(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_ids=[rid_a, rid_b, rid_missing],
        )

        # Exactly one DB query covering all ids via $in.
        mock_acl_entry.find.assert_called_once()
        query = mock_acl_entry.find.call_args.args[0]
        assert query["resourceId"] == {"$in": [rid_a, rid_b, rid_missing]}

        assert result[rid_a].VIEW and result[rid_a].EDIT
        assert result[rid_b].VIEW and not result[rid_b].EDIT
        # A resource with no ACL entry falls back to empty permissions (no KeyError).
        assert result[rid_missing] == ResourcePermissions()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_batch_permissions_group_grant_populates_result(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        group_id = PydanticObjectId()
        resource_id_a = PydanticObjectId()
        resource_id_b = PydanticObjectId()
        entry = MagicMock(
            principalType=PrincipalType.GROUP.value,
            principalId=group_id,
            resourceId=resource_id_a,
            permBits=PermissionBits.VIEW,
        )
        self._mock_sorted_acl_query(mock_acl_entry, [entry])
        service._resolve_group_ids_for_user = AsyncMock(return_value=[group_id])

        result = await service.get_user_permissions_for_resources(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_ids=[resource_id_a, resource_id_b],
        )

        assert result[resource_id_a].VIEW is True
        assert result[resource_id_b] == ResourcePermissions()
        query = mock_acl_entry.find.call_args.args[0]
        self._assert_group_clause_present(query, group_id)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.Group")
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_resource_permissions_returns_group_principal(self, mock_acl_entry, mock_group):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        group_id = PydanticObjectId()
        role_id = PydanticObjectId()
        entry = MagicMock(
            principalType=PrincipalType.GROUP.value,
            principalId=group_id,
            roleId=role_id,
        )
        group = SimpleNamespace(
            id=group_id,
            name="Engineering",
            email="engineering@example.com",
            avatar=None,
            source="entra",
            idOnTheSource="entra-group-1",
        )
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry])
        mock_group.find.return_value.to_list = AsyncMock(return_value=[group])

        result = await service.get_resource_permissions(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )

        assert result["principals"][0]["type"] == "group"
        assert result["principals"][0]["name"] == "Engineering"
        assert result["principals"][0]["email"] == "engineering@example.com"
        assert result["principals"][0]["roleId"] == role_id

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.User")
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_resource_permissions_batch_fetch_not_n_plus_1(self, mock_acl_entry, mock_user):
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        user_ids = [PydanticObjectId(), PydanticObjectId(), PydanticObjectId()]
        role_id = PydanticObjectId()
        entries = [
            MagicMock(principalType=PrincipalType.USER.value, principalId=user_id, roleId=role_id)
            for user_id in user_ids
        ]
        users = []
        for idx, user_id in enumerate(user_ids):
            user = SimpleNamespace(
                id=user_id,
                name=f"User {idx}",
                email=f"user{idx}@example.com",
                avatar=None,
                source=None,
                idOnTheSource=f"entra-user-{idx}",
            )
            users.append(user)
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=entries)
        mock_user.find.return_value.to_list = AsyncMock(return_value=users)
        mock_user.get = AsyncMock(side_effect=AssertionError("User.get must not be called"))

        result = await service.get_resource_permissions(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )

        mock_user.find.assert_called_once()
        mock_user.get.assert_not_called()
        assert len(result["principals"]) == 3

    @pytest.mark.asyncio
    @patch("registry.services.group_service.Group")
    async def test_search_groups_applies_limit(self, mock_group):
        mock_group.find.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        result = await GroupService().search_groups("alpha", limit=5)

        assert result == []
        mock_group.find.return_value.limit.assert_called_once_with(5)

    @pytest.mark.asyncio
    @patch("registry.services.user_service.User")
    async def test_search_users_applies_limit(self, mock_user):
        mock_user.find.return_value.limit.return_value.to_list = AsyncMock(return_value=[])

        result = await UserService().search_users("alpha", limit=5)

        assert result == []
        mock_user.find.return_value.limit.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_search_principals_groups_appear_when_users_fill_limit(self):
        users = []
        for idx in range(30):
            user = SimpleNamespace(id=PydanticObjectId(), name=f"User {idx}", email=f"user{idx}@example.com")
            users.append(user)
        groups = [
            SimpleNamespace(id=PydanticObjectId(), name="Alpha Group", email="alpha@example.com"),
            SimpleNamespace(id=PydanticObjectId(), name="Beta Group", email="beta@example.com"),
        ]
        user_service = Mock()
        user_service.search_users = AsyncMock(return_value=users)
        group_service = Mock()
        group_service.search_groups = AsyncMock(return_value=groups)
        service = ACLService(user_service=user_service, group_service=group_service, role_cache={})

        result = await service.search_principals(query="al", limit=30)

        assert any(principal.principalType == PrincipalType.GROUP for principal in result)
        user_service.search_users.assert_awaited_once_with("al", limit=30)
        group_service.search_groups.assert_awaited_once_with("al", limit=30)

    @pytest.mark.asyncio
    async def test_search_principals_none_limit_defaults_to_thirty(self):
        user_service = Mock()
        user_service.search_users = AsyncMock(return_value=[])
        group_service = Mock()
        group_service.search_groups = AsyncMock(return_value=[])
        service = ACLService(user_service=user_service, group_service=group_service, role_cache={})

        result = await service.search_principals(query="al", limit=None)

        assert result == []
        user_service.search_users.assert_awaited_once_with("al", limit=30)
        group_service.search_groups.assert_awaited_once_with("al", limit=30)

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resources_empty_input_skips_query(self, mock_acl_entry):
        """Empty resource_ids short-circuits without touching the database."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_acl_entry.find = MagicMock(side_effect=AssertionError("find must not be called"))

        result = await service.get_user_permissions_for_resources(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_ids=[],
        )

        assert result == {}
        mock_acl_entry.find.assert_not_called()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_user_permissions_for_resources_exception_returns_empty_perms(self, mock_acl_entry):
        """A DB failure degrades to empty permissions for every requested resource."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        rid = PydanticObjectId()
        mock_acl_entry.find = MagicMock(side_effect=Exception("db error"))
        service._resolve_group_ids_for_user = AsyncMock(return_value=[])

        result = await service.get_user_permissions_for_resources(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_ids=[rid],
        )

        assert result == {rid: ResourcePermissions()}

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_validate_owner_remains_resolves_perm_bits_from_cache(self, mock_acl_entry):
        """An updated principal whose roleId maps to OWNER bits keeps an owner present (no DB query, no raise)."""
        owner_role_id = PydanticObjectId()
        service = ACLService(
            user_service=Mock(),
            group_service=Mock(),
            role_cache={(ResourceType.MCPSERVER.value, RoleBits.OWNER): owner_role_id},
        )
        principal_id = PydanticObjectId()
        entry = MagicMock(
            principalType=PrincipalType.USER.value, principalId=principal_id, permBits=PermissionBits.VIEW
        )
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry])

        updated = MagicMock(principalType=PrincipalType.USER.value, principalId=principal_id, roleId=owner_role_id)

        # Should not raise: the cache resolves the updated principal to OWNER bits.
        await service.validate_at_least_one_owner_remains(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            updated_principals=[updated],
            removed_principals=[],
        )
        # No per-principal role catalog lookups should occur (cache only).
        mock_acl_entry.find.assert_called_once()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_validate_owner_remains_unknown_role_not_counted_as_owner(self, mock_acl_entry):
        """A roleId absent from the cache is NOT counted as owner (closes client-permBits bypass)."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        principal_id = PydanticObjectId()
        entry = MagicMock(principalType=PrincipalType.USER.value, principalId=principal_id, permBits=RoleBits.OWNER)
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry])

        # Updated principal downgrades the only owner; its roleId is unknown to the cache.
        updated = MagicMock(principalType=PrincipalType.USER.value, principalId=principal_id, roleId=PydanticObjectId())

        with pytest.raises(HTTPException) as exc_info:
            await service.validate_at_least_one_owner_remains(
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                updated_principals=[updated],
                removed_principals=[],
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "owner_required"

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_validate_owner_remains_new_public_owner_not_counted(self, mock_acl_entry):
        """A newly added PUBLIC principal must NOT satisfy the owner constraint."""
        owner_role_id = PydanticObjectId()
        service = ACLService(
            user_service=Mock(),
            group_service=Mock(),
            role_cache={(ResourceType.MCPSERVER.value, RoleBits.OWNER): owner_role_id},
        )
        owner_id = PydanticObjectId()
        entry = MagicMock(principalType=PrincipalType.USER.value, principalId=owner_id, permBits=RoleBits.OWNER)
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry])

        # Remove the only real owner and add PUBLIC as a new owner-roled principal.
        removed = MagicMock(principalType=PrincipalType.USER.value, principalId=owner_id)
        new_public = MagicMock(principalType=PrincipalType.PUBLIC.value, principalId=None, roleId=owner_role_id)

        with pytest.raises(HTTPException) as exc_info:
            await service.validate_at_least_one_owner_remains(
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                updated_principals=[new_public],
                removed_principals=[removed],
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "owner_required"

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_validate_owner_remains_cross_type_role_not_counted_as_owner(self, mock_acl_entry):
        """An OWNER roleId belonging to a different resourceType must NOT count as an owner."""
        # Cache holds an OWNER role for "workflow" only; the resource is an MCP server.
        workflow_owner_id = PydanticObjectId()
        service = ACLService(
            user_service=Mock(),
            group_service=Mock(),
            role_cache={("workflow", RoleBits.OWNER): workflow_owner_id},
        )
        principal_id = PydanticObjectId()
        entry = MagicMock(principalType=PrincipalType.USER.value, principalId=principal_id, permBits=RoleBits.OWNER)
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[entry])

        # The only owner is "updated" to a workflow OWNER roleId on an mcpServer resource.
        updated = MagicMock(principalType=PrincipalType.USER.value, principalId=principal_id, roleId=workflow_owner_id)

        with pytest.raises(HTTPException) as exc_info:
            await service.validate_at_least_one_owner_remains(
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                updated_principals=[updated],
                removed_principals=[],
            )
        assert exc_info.value.status_code == 409
