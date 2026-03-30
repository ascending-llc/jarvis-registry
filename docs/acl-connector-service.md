# ACL Service design

## Table of Contents
1. [Introduction](#introduction)
    - [Problem Statement](#problem-statement)
    - [Objectives](#objectives)
2. [Existing Role-Based Permission System](#existing-role-based-permission-system)
    - [Terminology](#terminology)
    - [Existing ACL Service](#existing-acl-service)
    - [Compatibility and Gaps with Current Requirements](#compatibility-and-gaps-with-current-requirements)
    - [Solution: Bridging the Gaps](#solution-bridging-the-gaps)
3. [ACL Service Design](#acl-service-design)
    - [High-Level Authentication & Authorization Sequence Flow](#high-level-authentication--authorization-sequence-flow)
    - [Data Models](#data-models)
    - [Service Design](#service-design)
4. [Jarvis Integration](#jarvis-integration)
    - [Authentication Middleware / JWT Forwarding](#authentication-middleware)
5. [Additional Considerations](#additional-considerations)
    - [Server Registration Form Updates](#server-registration-form-updates)
    - [ACL Service Cache](#acl-service-cache)
6. [Roadmap](#roadmap)
7. [Etc Notes](#etc-notes)


## Introduction

### Problem Statement

The MCP Gateway Registry requires fine-grained Access Control List (ACL) capabilities to support secure environments for MCP servers and A2A agents. Currently, all end users have access to the same set of connectors, which does not meet customer requirements for object-level permissions. To address this, we will introduce an ACL service in the MCP registry project that enables:

- Object-level permissions for servers and agents
- Control over visibility and access for individual users, user groups, and public (everyone)
- Integration with a MongoDB-backed persistence layer for scalable, transactional storage

This ACL service will be foundational for enforcing secure access and will be compatible with the shared data models and interfaces used in the Jarvis project.

### Objectives

- Design an ACL service that allows admins to share an MCP Server or Agent with:
  - Everyone (public)
  - Specific user groups
  - Specific users
- Ensure compatibility with existing data models and interfaces (as defined in jarvis-api and shared schemas)
- Leverage MongoDB as the single source of truth for ACL metadata and permissions
- Support role-based access control (RBAC) and object-level permissions for all resources

## Existing Role-Based Permission System

An ACLService is already implemented in the Jarvis project. Prior to defining the registry-specific approach, it is essential to review this existing design and assess its compatibility with the updated requirements for sharing connectors (MCP servers and agents) across user, group, and public scopes.

### Terminology
- **Principals**: Entities that can be granted permissions (individual users, groups, public, and roles)
- **Roles**: Predefined sets of permissions. Each role is associated with a resource type  and maps to permission bits (permBits)
- **Resources**: Items that require access control (mcp servers, agents), identified by resourceType and resourceId.
- **Permissions**: Numeric bitmasks that define allowed actions (view, edit).

### Existing ACL Service
The existing [ACLService](https://github.com/ascending-llc/jarvis-api/blob/deploy/packages/api/src/acl/accessControlService.ts) in Jarvis exposes several methods for managing object-level permissions. In the list below, methods that are misaligned with current requirements are shown with strikethroughs, while the remaining methods are candidates for refactoring to meet the updated needs:

- `grantPermission`: Grants permissions to a principal for specific resources using a permission set optionally defined in a role
- `findAccessibleResources`: Finds all resources of a specific type that a user has access to with specific permission bits
- ~~`findPubliclyAccessibleResources`: Find all publicly accessible resources of a specific type~~
- `getResourcePermissionsMap`: Get effective permissions for multiple resources in a batch operation
- `removeAllPermissions`: Removes all permissions for a resource
- `checkPermission`: Checks if a specific user has permissions on a resource
- ~~`validateResourceType`: Validates a resource types and manages permission schemas.~~

**Compatibility with Requirements:**
1. Supports permissions for users, groups, and public.
2. Enables fine-grained control via permission bits and roles.

**Misalignment with Requirements:**
1. Some functions (e.g., `findAccessibleResources`) are user-only and omit groups; others (e.g., `grantPermission`) need refactoring for broader principal support.
2. No automated sync of enums/constants (roles, permission bits) between Jarvis and registry schemas.
3. No mechanism for passing authenticated user context from Jarvis to the registry, blocking accurate permission checks.

**Proposed Solutions**
1. Design the registry ACL service with a minimal, focused set of functions that directly satisfy the current requirements for sharing resources with users, groups, and public, while allowing for future extensibility as additional use cases emerge.

2. Implement automated synchronization of enums and constants (such as roles and permission bits) between Jarvis and the registry project to maintain schema consistency and prevent drift.

3. Use session cookie authentication (`mcp-gateway-session`) for browser/UI users.

## ACL Service Design

### High-Level Authentication & Authorization Sequence Flow

**Note:** Jarvis users authenticate to the registry using a session cookie named `mcp-gateway-session`. This cookie is set after successful login and is included in all subsequent requests to the registry for authentication and permission checks.

```mermaid
sequenceDiagram
    participant U as User Browser
    participant R as Registry Backend (FastAPI)
    participant A as Auth Server (OAuth2)
    participant DB as MongoDB

    U->>R: Request login page
    R->>A: Fetch available OAuth2 providers
    R-->>U: Render login page with providers
    U->>R: Select provider, request /redirect/{provider}
    R->>A: Redirect to Auth Server for OAuth2 login
    U->>A: Authenticate (OAuth2 flow)
    A->>U: Redirect with signed user info (JWT or payload)
    U->>R: Callback to /redirect with signed user info
    R->>DB: Lookup or create user in MongoDB
    DB-->>R: User record
    R->>U: Set session cookie `mcp-gateway-session`, redirect to dashboard
    U->>R: Subsequent API requests with `mcp-gateway-session` cookie
    R->>DB: (If needed) Load user/ACL data for permissions
    DB-->>R: User/ACL data
    R-->>U: Serve protected resources based on permissions
```

### ACL Implementation

#### Field Definitions

Required Fields:
- `principalType`: String - The type of principal (user, group, or public)
- `principalId?`: Mixed - The ID of the principal (objectId for user/group, null for "public")
- `resourceType`: String - The type of resource (MCP Server, Agent)
- `resourceId`: ObjectId - The ID of the resource
- `permBits`: Number - The permission bits

Optional Fields:
- `principalModel?`: String - The MongoDB model, null for "public". Can be used to support bulk updates
- `roleId?:` ObjectId - The ID of the role whose permissions are being inherited
- `inheritedFrom?`: ObjectId - ID of the resource this permission is inherited from
- `grantedBy?`: ObjectId - ID of the user who granted this permission
- `grantedAt?`: String (ISO 8601) -  When this permission was granted

#### MongoDB Schema Model
MongoDB `ACLEntry`

```bson
{
  _id: ObjectId("..."),
  principalType: "user" | "group" | "public",
  principalId: "..." | null,
  principalModel: "..." | null,
  resourceType: "mcpServer" | "agent"
  resourceId: ObjectId("..."),
  permBits: NumberLong(1),
  roleId: ObjectId("...") | null,
  inheritedFrom: ObjectId("...") | null,
  grantedBy: ObjectId("...") | null,
  grantedAt: ISODate("..."),
  createdAt: ISODate("...")
  updatedAt: ISODate("...")
}
```
**Supporting Enums / Constants**
The `ACLEntry` relies on the following enums/constants exported by `librechat-data-provider`:

- **principalType**
- **principalModel**
- **ResourceType**
- **PermBits**
- **AccessRoleIds**

These enums are not currently imported via `import-schema`. Updates to the `import-schema` tool or an additional import tool will be needed to keep the supporting models in-line with jarvis-api.


### Service Design

The ACL service provides the following core operations:

1. Grant or update permissions for a principal (user, group, or public) on a resource: `grant_permission`
2. Delete all ACL entries for a resource, optionally filtered by permission bits: `delete_acl_entries_for_resource`
3. Delete a single ACL entry for a resource and principal: `delete_permission`
4. Get a permissions map for a user (across all resources): `get_permissions_map_for_user_id`
5. Search for principals (users, groups) by query string: `search_principals`
6. Get all ACL permissions for a specific resource: `get_resource_permissions`

Example method signatures:

```python
class ACLService:
    async def grant_permission(principal_type: str, principal_id: Optional[Union[PydanticObjectId, str]], resource_type: str, resource_id: PydanticObjectId, role_id: Optional[PydanticObjectId] = None, perm_bits: Optional[int] = None) -> IAclEntry: ...
    async def delete_acl_entries_for_resource(self, resource_type: str, resource_id: PydanticObjectId, perm_bits_to_delete: Optional[int] = None) -> int: ...
    async def delete_permission(self, resource_type: str, resource_id: PydanticObjectId, principal_type: str, principal_id: Optional[Union[PydanticObjectId, str]]) -> int: ...
    async def get_permissions_map_for_user_id(self, principal_type: str, principal_id: PydanticObjectId) -> dict: ...
    async def search_principals(self, query: str, limit: int = 30, principal_types: Optional[List[str]] = None) -> List[PermissionPrincipalOut]: ...
    async def get_resource_permissions(self, resource_type: str, resource_id: PydanticObjectId) -> Dict[str, Any]: ...
```

These methods are implemented in `registry/services/access_control_service.py` and are used by the API routes in `registry/api/v1/acl_routes.py`.

### API Endpoints

The following REST API endpoints are exposed for ACL management. All endpoints use **camelCase** for request and response field names.

#### 1. Search for Principals

**GET `/permissions/search-principals`**

Search for principals (users, groups) by query string. Used in the ACL sharing UI to find users/groups for permission assignment.

**Query Parameters:**
- `query` (string, required): Search string for principal name, email, or username
- `limit` (int, optional): Maximum number of results to return (default: 30)
- `principalTypes` (list of string, optional): Filter by principal type (e.g., `user`, `group`)

**Response:** `200 OK`
```json
[
    {
        "principalType": "user",
        "principalId": "68277281e5d4dd8bbeb261d4",
        "name": "Ryo",
        "email": "ryo.h@ascendingdc.com",
        "accessRoleId": "mcpServer_owner"
    }
]
```

---

#### 2. Get Available Roles for Resource Type (NEW)

**GET `/permissions/{resource_type}/roles`**

Get all available access roles for a specific resource type (e.g., mcpServer, agent). Used by the frontend to populate role selection dropdowns.

**Path Parameters:**
- `resource_type` (string, required): Type of resource (e.g., `mcpServer`, `agent`)

**Response:** `200 OK`
```json
[
    {
        "accessRoleId": "mcpServer_viewer",
        "name": "com_ui_mcp_server_role_viewer",
        "description": "com_ui_mcp_server_role_viewer_desc",
        "permBits": 1
    },
    {
        "accessRoleId": "mcpServer_editor",
        "name": "com_ui_mcp_server_role_editor",
        "description": "com_ui_mcp_server_role_editor_desc",
        "permBits": 3
    },
    {
        "accessRoleId": "mcpServer_owner",
        "name": "com_ui_mcp_server_role_owner",
        "description": "com_ui_mcp_server_role_owner_desc",
        "permBits": 15
    }
]
```

---

#### 3. Get Resource Permissions

**GET `/permissions/{resource_type}/{resource_id}`**

Get all ACL permissions for a specific resource with full principal details. Returns which users have access, their roles, and whether the resource is public.

**Path Parameters:**
- `resource_type` (string, required): Type of resource (e.g., `mcpServer`, `agent`)
- `resource_id` (string, required): ID of the resource

**Response:** `200 OK`
```json
{
    "resourceType": "mcpServer",
    "resourceId": "69c5440b74d340f5198fd1a4",
    "principals": [
        {
            "type": "user",
            "id": "68277281e5d4dd8bbeb261d4",
            "name": "Ryo",
            "email": "ryo.h@ascendingdc.com",
            "avatar": "avatar-1774274914691.png?manual=true",
            "source": "local",
            "idOnTheSource": "06910f4c-ed4f-4697-89c6-2c3d5d53bb9e",
            "accessRoleId": null
        },
        {
            "type": "user",
            "id": "68277223e5d4dd8bbeb26177",
            "name": "Daoqi Zhang",
            "email": "daoqi.zhang@ascendingdc.com",
            "source": "local",
            "idOnTheSource": "0e4dbb6e-05ad-4eab-b6c0-8b55bf42edfc",
            "accessRoleId": "mcpServer_owner"
        },
        {
            "type": "user",
            "id": "684b224c0b065c7d6ccc66d6",
            "name": "Kaiqi Yu",
            "email": "kaiqi.yu@ascendingdc.com",
            "source": "local",
            "idOnTheSource": "1c00bd92-b7f3-41a5-85a6-3a9ad70d1fb7",
            "accessRoleId": "mcpServer_viewer"
        }
    ],
    "public": false
}
```

---

#### 4. Update Resource Permissions

**PUT `/permissions/{resource_type}/{resource_id}`**

Update ACL permissions for a specific resource. Supports adding/updating/removing principals and setting public access.

**Path Parameters:**
- `resource_type` (string, required): Type of resource (e.g., `mcpServer`, `agent`)
- `resource_id` (string, required): ID of the resource

**Request Body:**
```json
{
    "updated": [
        {
            "principalType": "user",
            "principalId": "684b224c0b065c7d6ccc66d6",
            "accessRoleId": "mcpServer_editor",
            "permBits": 3
        }
    ],
    "removed": [
        {
            "principalType": "user",
            "principalId": "68277281e5d4dd8bbeb261d4"
        }
    ],
    "public": false
}
```

**Notes:**
- `accessRoleId` is preferred and will automatically map to the corresponding `permBits`
- If only `permBits` is provided, the system will automatically find and associate the corresponding role
- The system validates that at least one owner remains after the update

**Response:** `200 OK`
```json
{
    "message": "Updated 1 and deleted 1 permissions",
    "results": {
        "resourceId": "69c5440b74d340f5198fd1a4"
    }
}
```

**Error Response:** `400 Bad Request`
```json
{
    "error": "validation_error",
    "message": "At least one owner must remain for the resource"
}
```

## Additional Considerations

### ACL Service Cache
TBD after evaulating performance of initial service implementation
