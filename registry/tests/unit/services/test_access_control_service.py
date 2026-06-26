from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.acl_schema import ResourcePermissions
from registry.services.access_control_service import ACLService
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import PermissionBits, RoleBits


class TestACLService:
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

        result = await service.get_accessible_resource_ids(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER,
        )
        # rid1 appears twice but should be deduplicated; rid2 has no VIEW bit
        assert result == [str(rid1)]

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_accessible_resource_ids_with_none_user_id_returns_public_only(self, mock_acl_entry):
        """When user_id is None, only PUBLIC ACL entries are matched."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})

        # Build mock ACL entries: one public and one user-specific
        public_entry = MagicMock()
        public_entry.permBits = RoleBits.VIEWER  # 1 = VIEW
        public_entry.resourceId = PydanticObjectId("507f1f77bcf86cd799439011")

        user_entry = MagicMock()
        user_entry.permBits = RoleBits.VIEWER
        user_entry.resourceId = PydanticObjectId("507f1f77bcf86cd799439012")

        # Mock to return only public_entry when user_id is None
        mock_acl_entry.find.return_value.to_list = AsyncMock(return_value=[public_entry])

        result = await service.get_accessible_resource_ids(user_id=None, resource_type="mcpServer")

        # Should only return the public resource ID
        assert result == ["507f1f77bcf86cd799439011"]
        # Verify that the query was called with only PUBLIC principalType filter
        mock_acl_entry.find.assert_called_once()
        call_args = mock_acl_entry.find.call_args
        query = call_args.args[0]
        assert query["resourceType"] == "mcpServer"
        assert "principalType" in query
        assert query["principalType"] == PrincipalType.PUBLIC.value
        assert query["principalId"] is None

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.RegistryAclEntry")
    async def test_get_accessible_resource_ids_exception(self, mock_acl_entry):
        """Exception should return empty list."""
        service = ACLService(user_service=Mock(), group_service=Mock(), role_cache={})
        mock_acl_entry.find.return_value.to_list = AsyncMock(side_effect=Exception("fail"))
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
