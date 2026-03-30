from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.api.v1.acl_routes import (
    get_resource_permissions,
    search_principals,
    update_resource_permissions,
)
from registry.schemas.acl_schema import PermissionPrincipalIn, UpdateResourcePermissionsRequest
from registry_pkgs.models._generated import PrincipalType, ResourceType
from registry_pkgs.models.enums import PermissionBits

TEST_PRINCIPAL_ID = "000000000000000000000001"


@pytest.fixture
def sample_user_context():
    return {
        "user_id": TEST_PRINCIPAL_ID,
        "username": "testuser",
        "acl_permission_map": {},
    }


@pytest.mark.asyncio
async def test_search_principals_uses_injected_acl_service():
    from registry.schemas.acl_schema import PermissionPrincipalOut

    acl_service = MagicMock()
    acl_service.search_principals = AsyncMock(
        return_value=[
            PermissionPrincipalOut(
                principalType=PrincipalType.USER,
                principalId=TEST_PRINCIPAL_ID,
                name="Test User",
                email="test@example.com",
                accessRoleId="viewer",
            )
        ]
    )

    result = await search_principals(
        query="test",
        limit=5,
        principalTypes=[PrincipalType.USER.value],
        acl_service=acl_service,
    )

    acl_service.search_principals.assert_awaited_once_with(
        query="test",
        limit=5,
        principal_types=[PrincipalType.USER.value],
    )
    assert result[0].principalId == TEST_PRINCIPAL_ID


@pytest.mark.asyncio
async def test_update_resource_permissions_uses_injected_acl_service(sample_user_context):
    resource_id = str(PydanticObjectId())
    principal_id = str(PydanticObjectId())
    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock()
    acl_service.validate_at_least_one_owner_remains = AsyncMock()
    acl_service.delete_permission = AsyncMock(return_value=1)
    acl_service.grant_permission = AsyncMock(return_value=MagicMock(id=PydanticObjectId()))

    request = UpdateResourcePermissionsRequest(
        public=False,
        updated=[
            PermissionPrincipalIn(
                principalType=PrincipalType.USER,
                principalId=principal_id,
                permBits=PermissionBits.VIEW,
            )
        ],
        removed=[
            PermissionPrincipalIn(
                principalType=PrincipalType.USER,
                principalId=principal_id,
                permBits=PermissionBits.VIEW,
            )
        ],
    )

    with patch("registry_pkgs.database.decorators.MongoDB.get_client") as mock_get_client:
        mock_session = AsyncMock()
        mock_client = MagicMock()
        mock_client.start_session.return_value.__aenter__.return_value = mock_session
        mock_session.start_transaction.return_value.__aenter__.return_value = None
        mock_get_client.return_value = mock_client

        result = await update_resource_permissions(
            resource_id=resource_id,
            resource_type=ResourceType.MCPSERVER.value,
            data=request,
            user_context=sample_user_context,
            acl_service=acl_service,
        )

    acl_service.check_user_permission.assert_awaited_once()
    acl_service.validate_at_least_one_owner_remains.assert_awaited_once()
    assert acl_service.delete_permission.await_count == 2
    acl_service.grant_permission.assert_awaited_once()
    assert result.results["resourceId"] == resource_id


@pytest.mark.asyncio
async def test_get_resource_permissions_uses_injected_acl_service(sample_user_context):
    resource_id = str(PydanticObjectId())
    acl_service = MagicMock()
    acl_service.check_user_permission = AsyncMock()
    acl_service.get_resource_permissions = AsyncMock(
        return_value={
            "resourceType": ResourceType.MCPSERVER.value,
            "resourceId": resource_id,
            "principals": [],
            "public": False,
        }
    )

    result = await get_resource_permissions(
        resource_type=ResourceType.MCPSERVER.value,
        resource_id=resource_id,
        user_context=sample_user_context,
        acl_service=acl_service,
    )

    acl_service.check_user_permission.assert_awaited_once()
    acl_service.get_resource_permissions.assert_awaited_once()
    assert result.resourceType == ResourceType.MCPSERVER.value
    assert result.resourceId == resource_id
    assert result.principals == []
    assert result.public is False
