"""
Pydantic Schemas for ACL Management API v1

These schemas define the request and response models for the
ACL Management endpoints based on the API documentation.
"""

from pydantic import BaseModel, Field

from registry_pkgs.models._generated import PrincipalType


class ResourcePermissions(BaseModel):
    VIEW: bool = False
    EDIT: bool = False
    DELETE: bool = False
    SHARE: bool = False


class PermissionPrincipalIn(BaseModel):
    principalId: str = Field(alias="principal_id")
    principalType: PrincipalType = Field(alias="principal_type")
    permBits: int | None = Field(default=0, alias="perm_bits")
    accessRoleId: str | None = None

    class Config:
        populate_by_name = True


class UpdateResourcePermissionsRequest(BaseModel):
    updated: list[PermissionPrincipalIn] = Field(default_factory=list)
    removed: list[PermissionPrincipalIn] = Field(default_factory=list)
    public: bool = False


class PermissionPrincipalOut(BaseModel):
    principalType: PrincipalType = Field(serialization_alias="principalType")
    principalId: str = Field(serialization_alias="principalId")
    name: str | None = None
    email: str | None = None
    accessRoleId: str

    class Config:
        populate_by_name = True


class PrincipalDetailOut(BaseModel):
    type: str = Field(description="Principal type: user, group, etc.")
    id: str = Field(description="Principal ID")
    name: str | None = None
    email: str | None = None
    avatar: str | None = None
    source: str | None = None
    idOnTheSource: str | None = None
    accessRoleId: str | None = Field(default=None, description="Access role ID if assigned")


class GetResourcePermissionsResponse(BaseModel):
    resourceType: str
    resourceId: str
    principals: list[PrincipalDetailOut]
    public: bool = Field(default=False, description="Whether the resource has public access")


class RoleOut(BaseModel):
    accessRoleId: str
    name: str
    description: str
    permBits: int


class UpdateResourcePermissionsResponse(BaseModel):
    message: str
    results: dict
