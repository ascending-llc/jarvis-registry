# Security Control Design

Security in the registry is layered. Each layer has a distinct responsibility, and they compose in a fixed order on every request.

---

## The Four Layers

```
Incoming Request
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Authentication (UnifiedAuthMiddleware)                │
│    Who are you? Validates JWT or session cookie.         │
│    Sets request.state.user (UserContextDict).            │
│    Rejects unauthenticated requests to protected paths.  │
└────────────────────────┬────────────────────────────────┘
                         │ authenticated
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Authorization / RBAC (ScopePermissionMiddleware)      │
│    What can you do? Checks scopes against scopes.yml.    │
│    Returns 403 if the user's token lacks the required    │
│    scope for the requested endpoint + HTTP method.       │
└────────────────────────┬────────────────────────────────┘
                         │ scope allowed
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Resource ACL (ACLService in service layer)            │
│    Can you access this specific resource?                │
│    Checks ExtendedAclEntry records in MongoDB.           │
│    Filters or rejects based on VIEW/EDIT/DELETE bits.    │
└────────────────────────┬────────────────────────────────┘
                         │ ACL permits
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Business Logic                                        │
│    Executes; returns data or mutates state.              │
└─────────────────────────────────────────────────────────┘
```

---

## 1. Authentication

`UnifiedAuthMiddleware` runs on every request. It accepts two credential forms:

- **JWT Bearer token** — signed HS256 or RS256 token issued by the auth server or a trusted IdP (Keycloak, Cognito, Entra ID). Verified via `verify_access_token()` / `decode_jwt()`.
- **Session cookies** (`jarvis_registry_session` and `jarvis_registry_refresh` by default) — for browser users who went through the OAuth2 login flow. These cookie names are configurable via application settings, so deployed environments may use different names.

On success, identity and group membership are stored in `request.state.user` as a `UserContextDict`. Public paths (health check, OAuth callbacks, login) are exempted and never reach the layers below.

Identity provider setup: [Entra ID Implementation](entra-id-implementation.md) | [`docs/cognito.md`](../cognito.md)

---

## 2. Authorization — RBAC via Scopes

`ScopePermissionMiddleware` runs immediately after authentication. It answers: **does this user's role allow calling this endpoint?**

The scope system is configured entirely in `registry-pkgs/src/registry_pkgs/scopes.yml` with no code changes required. Four roles are built in:

| Role | Description |
|---|---|
| `jarvis-registry-admin` | Full access including system ops |
| `jarvis-registry-power-user` | Full CRUD + ACL sharing; no system ops |
| `jarvis-registry-user` | CRUD on resources; no sharing |
| `jarvis-registry-read-only` | Read-only |

Scopes are resolved from the JWT: explicit `scope` claims take precedence; otherwise IdP group membership is expanded via `group_mappings`. This prevents down-scoped tokens from being silently upgraded.

Full scope catalog and configuration guide: [RBAC & Scope System](scopes.md)

---

## 3. Resource-Level ACL

Scopes control endpoint access. ACL controls **which specific resource instances** a user can see or modify.

Every protected resource (MCP server, A2A agent, federation) has ACL entries in MongoDB (`ExtendedAclEntry`). Each entry records:

- **Principal**: a user, a group, or `public`
- **Resource**: type (`mcpServer`, `agent`, `federation`, …) + ObjectId
- **Permission bits**: `VIEW (1)`, `EDIT (3)`, `DELETE / OWNER (15)`

The `ACLService` in `registry/src/registry/services/access_control_service.py` handles all ACL reads and writes. Service layer code calls it before returning or mutating any resource data.

When a resource is created, the creator is automatically granted `OWNER` permission. Resources default to **private** — not visible to anyone else until explicitly shared.

ACL data model and API: [ACL Service Design](acl-connector-service.md)

---

## How They Work Together — An Example

A user with `jarvis-registry-user` role calls `GET /api/v1/servers`:

1. **Auth**: JWT validated → `request.state.user` populated with user identity + groups.
2. **RBAC**: `servers-read` scope covers `GET /servers` → user's group maps to that scope → allowed.
3. **ACL**: `ACLService.find_accessible_resources()` returns only the server documents where the user has at least `VIEW (1)` permission.
4. **Response**: filtered list of servers the user is entitled to see.

If the same user calls `PUT /api/v1/permissions/mcpServer/{id}` (sharing), step 2 fails immediately — `servers-share` scope is not in their role — and a `403` is returned before the service layer is ever reached.
