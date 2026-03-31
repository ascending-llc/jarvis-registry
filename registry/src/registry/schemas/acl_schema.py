"""
Pydantic Schemas for ACL Management API v1

These schemas define the request and response models for the
ACL Management endpoints based on the API documentation.

All schemas use camelCase for API input/output and for MongoDB storage.
"""

from pydantic import BaseModel, Field

from registry_pkgs.models._generated import PrincipalType

from .case_conversion import APIBaseModel


class ResourcePermissions(BaseModel):
    """Permission flags for a resource (used internally)"""

    VIEW: bool = False
    EDIT: bool = False
    DELETE: bool = False
    SHARE: bool = False


class PermissionPrincipalIn(APIBaseModel):
    """Request schema for a principal in permission update"""

    principalId: str = Field(..., description="Principal ID")
    principalType: PrincipalType = Field(..., description="Principal type (user, group, etc.)")
    permBits: int | None = Field(default=0, description="Permission bits")
    accessRoleId: str | None = Field(default=None, description="Access role ID")


class UpdateResourcePermissionsRequest(APIBaseModel):
    """Request schema for updating resource permissions"""

    updated: list[PermissionPrincipalIn] = Field(default_factory=list, description="Principals to update or add")
    removed: list[PermissionPrincipalIn] = Field(default_factory=list, description="Principals to remove")
    public: bool = Field(default=False, description="Whether the resource is public")


class PermissionPrincipalOut(APIBaseModel):
    """Response schema for a principal in search results"""

    principalType: PrincipalType = Field(..., description="Principal type")
    principalId: str = Field(..., description="Principal ID")
    name: str | None = Field(default=None, description="Principal name")
    email: str | None = Field(default=None, description="Principal email")
    accessRoleId: str = Field(..., description="Access role ID")


class PrincipalDetailOut(APIBaseModel):
    """Response schema for principal details with full information"""

    type: str = Field(..., description="Principal type: user, group, etc.")
    id: str = Field(..., description="Principal ID")
    name: str | None = Field(default=None, description="User/group name")
    email: str | None = Field(default=None, description="Email address")
    avatar: str | None = Field(default=None, description="Avatar URL")
    source: str | None = Field(default=None, description="Authentication source")
    idOnTheSource: str | None = Field(default=None, description="ID on the authentication source")
    accessRoleId: str | None = Field(default=None, description="Access role ID if assigned")


class GetResourcePermissionsResponse(APIBaseModel):
    """Response schema for getting resource permissions"""

    resourceType: str = Field(..., description="Type of the resource")
    resourceId: str = Field(..., description="ID of the resource")
    principals: list[PrincipalDetailOut] = Field(default_factory=list, description="List of principals with access")
    public: bool = Field(default=False, description="Whether the resource has public access")


class RoleOut(APIBaseModel):
    """Response schema for an access role"""

    accessRoleId: str = Field(..., description="Access role ID")
    name: str = Field(..., description="Role name (i18n key)")
    description: str = Field(..., description="Role description (i18n key)")
    permBits: int = Field(..., description="Permission bits value")


class UpdateResourcePermissionsResponse(APIBaseModel):
    """Response schema for updating resource permissions"""

    message: str = Field(..., description="Success message")
    results: dict = Field(..., description="Operation results")
