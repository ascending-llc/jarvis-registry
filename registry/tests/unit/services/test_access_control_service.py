from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from registry.schemas.acl_schema import ResourcePermissions
from registry.services.access_control_service import ACLService
from registry_pkgs.models import ResourceType
from registry_pkgs.models.enums import PermissionBits, RoleBits


class TestACLService:
    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.get_current_session")
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_grant_permission_new_entry(self, mock_acl_entry, mock_get_session):
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_get_session.return_value = AsyncMock()  # Mock session
        mock_acl_entry.find_one = AsyncMock(return_value=None)

        # ExtendedAclEntry() returns an AsyncMock, whose insert is also an AsyncMock
        new_entry = AsyncMock()
        new_entry.insert = AsyncMock()
        mock_acl_entry.return_value = new_entry
        with (
            patch("registry.services.access_control_service.ExtendedAclEntry", mock_acl_entry),
            patch(
                "registry.services.access_control_service.IAccessRole.find_one",
                AsyncMock(return_value=MagicMock(permBits=PermissionBits.EDIT)),
            ),
        ):
            role_id = PydanticObjectId()
            await service.grant_permission(
                principal_type="user",
                principal_id={"id": "user1"},
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                role_id=role_id,
                perm_bits=PermissionBits.EDIT,
            )
            new_entry.insert.assert_awaited()
            assert mock_acl_entry.call_args.kwargs["roleId"] == role_id

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.get_current_session")
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_grant_permission_update_existing(self, mock_acl_entry, mock_get_session):
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_get_session.return_value = AsyncMock()  # Mock session
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
            )
            existing_entry.save.assert_awaited()

    @pytest.mark.asyncio
    async def test_grant_permission_missing_principal_id(self):
        service = ACLService(user_service=Mock(), group_service=Mock())
        with pytest.raises(ValueError):
            await service.grant_permission(
                principal_type="user",
                principal_id=None,
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
                perm_bits=PermissionBits.EDIT,
            )

    @pytest.mark.asyncio
    async def test_grant_permission_missing_perm_bits_and_role(self):
        service = ACLService(user_service=Mock(), group_service=Mock())
        with pytest.raises(ValueError):
            await service.grant_permission(
                principal_type="user",
                principal_id={"id": "user1"},
                resource_type=ResourceType.MCPSERVER.value,
                resource_id=PydanticObjectId(),
            )

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.get_current_session")
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_delete_acl_entries_for_resource(self, mock_acl_entry, mock_get_session):
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_get_session.return_value = AsyncMock()  # Mock session
        mock_result = MagicMock()
        mock_result.deleted_count = 2
        mock_acl_entry.find.return_value.delete = AsyncMock(return_value=mock_result)
        deleted = await service.delete_acl_entries_for_resource(
            resource_type=ResourceType.MCPSERVER.value, resource_id=PydanticObjectId()
        )
        assert deleted == 2

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_delete_acl_entries_for_resource_exception(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_acl_entry.find.return_value.delete = AsyncMock(side_effect=Exception("fail"))
        deleted = await service.delete_acl_entries_for_resource(
            resource_type=ResourceType.MCPSERVER.value, resource_id=PydanticObjectId()
        )
        assert deleted == 0

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_get_user_permissions_for_resource_edit_only(self, mock_acl_entry):
        """EDIT bit (2) should only grant EDIT, not VIEW."""
        service = ACLService(user_service=Mock(), group_service=Mock())
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
    @patch("registry.services.access_control_service.get_current_session")
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_delete_permission(self, mock_acl_entry, mock_get_session):
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_get_session.return_value = AsyncMock()  # Mock session
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_acl_entry.find.return_value.delete = AsyncMock(return_value=mock_result)
        deleted_count = await service.delete_permission(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            principal_type="user",
            principal_id=PydanticObjectId(),
        )
        assert deleted_count == 1

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_delete_permission_exception(self, mock_acl_entry):
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_acl_entry.find.return_value.delete = AsyncMock(side_effect=Exception("fail"))
        deleted = await service.delete_permission(
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
            principal_type="user",
            principal_id="user1",
        )
        assert deleted == 0

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_get_user_permissions_for_resource_owner(self, mock_acl_entry):
        """User with OWNER bits should resolve all permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock())
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
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_get_user_permissions_for_resource_no_match(self, mock_acl_entry):
        """No ACL entry should return all-False permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock())

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
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_get_user_permissions_for_resource_exception(self, mock_acl_entry):
        """Exception should return all-False permissions."""
        service = ACLService(user_service=Mock(), group_service=Mock())

        # Mock find() to raise exception
        mock_acl_entry.find = MagicMock(side_effect=Exception("db error"))

        perms = await service.get_user_permissions_for_resource(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER.value,
            resource_id=PydanticObjectId(),
        )
        assert perms == ResourcePermissions()

    @pytest.mark.asyncio
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_check_user_permission_allowed(self, mock_acl_entry):
        """User with VIEW should pass the VIEW check."""
        service = ACLService(user_service=Mock(), group_service=Mock())
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
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_check_user_permission_denied(self, mock_acl_entry):
        """User with VIEW-only should be denied EDIT."""
        service = ACLService(user_service=Mock(), group_service=Mock())
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
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_check_user_permission_no_entry(self, mock_acl_entry):
        """No ACL entry should raise 403."""
        service = ACLService(user_service=Mock(), group_service=Mock())

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
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_get_accessible_resource_ids(self, mock_acl_entry):
        """Should return deduplicated resource IDs with VIEW bit set."""
        service = ACLService(user_service=Mock(), group_service=Mock())
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
    @patch("registry.services.access_control_service.ExtendedAclEntry")
    async def test_get_accessible_resource_ids_exception(self, mock_acl_entry):
        """Exception should return empty list."""
        service = ACLService(user_service=Mock(), group_service=Mock())
        mock_acl_entry.find.return_value.to_list = AsyncMock(side_effect=Exception("fail"))
        result = await service.get_accessible_resource_ids(
            user_id=PydanticObjectId(),
            resource_type=ResourceType.MCPSERVER,
        )
        assert result == []
