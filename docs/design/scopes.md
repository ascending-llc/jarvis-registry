# RBAC & Scope System Design

The registry uses a two-layer authorization model: **scopes** control which API endpoints a user can call, and **ACL** controls which specific resources they can see or modify within those endpoints. This model is grounded in [enterprise AI governance principles](https://exploreagentic.ai/ai-governance/) and implemented through the [Jarvis Governed AI Layer](https://ascendingdc.com/jarvis-ai/governed-ai/).

<iframe width="100%" height="450" src="https://www.youtube.com/embed/CK1wRMjuFdE" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>



The canonical source of truth for scope definitions is [`registry-pkgs/src/registry_pkgs/scopes.yml`](../../registry-pkgs/src/registry_pkgs/scopes.yml). The enforcement logic lives in [`registry/src/registry/middleware/rbac.py`](../../registry/src/registry/middleware/rbac.py).

---

## Two-Layer Authorization

Scopes are coarse-grained (endpoint access). ACL is fine-grained (per-resource access). Both must pass for a request to succeed on protected resources.

**Layer 1 — ScopePermissionMiddleware**: loaded from `scopes.yml` at startup, checks whether the user's token includes the scope required for the requested endpoint/method. Returns `403` immediately if not.

**Layer 2 — ACL** (resource-level): enforced inside the service layer via `access_control_service.py`, checks whether the user has `VIEW`, `EDIT`, or `DELETE` permission on the specific resource being accessed.

---

## Roles and Their Scopes

Four built-in roles are defined via IdP group mappings in `scopes.yml` under `group_mappings`.

| Role | Group Name | Description |
|---|---|---|
| Admin | `jarvis-registry-admin` | Full access including system ops and ACL management |
| Power User | `jarvis-registry-power-user` | Full CRUD on all resources + share; no system ops |
| User | `jarvis-registry-user` | CRUD on servers/agents/federations; no sharing or ACL write |
| Read-Only | `jarvis-registry-read-only` | Read-only access to servers, agents, federations |

Role-to-scope mapping is defined entirely in `scopes.yml` — no code changes needed to adjust which scopes a role carries.

---

## Scope Catalog

Each scope maps to one or more API endpoints. `ScopePermissionMiddleware` compiles these at startup and enforces them on every authenticated request.

| Scope | What It Covers |
|---|---|
| `servers-read` | List/get MCP servers, check connections, get tools |
| `server-write` | Register, update, delete, toggle MCP servers |
| `agents-read` | List/get A2A agents, skills, health, well-known card |
| `agents-write` | Create, update, delete, toggle A2A agents, sync well-known |
| `federations-read` | List/get federations |
| `federations-write` | Create, update, delete, sync federations |
| `federations-share` | Manage ACL on federation resources |
| `servers-share` | Manage ACL on MCP server resources |
| `agents-share` | Manage ACL on A2A agent resources |
| `acl-read` | Search principals, read resource permissions |
| `acl-write` | Update resource permissions |
| `user-read` | Auth info, tokens, search, MCP connection/OAuth management |
| `system-ops` | Admin stats, token admin, AgentCore runtime sync |
| `mcp-proxy-ops` | MCP proxy and session endpoints |

---

## How Scopes Are Resolved

Scopes for an incoming request are resolved from the JWT token via `effective_scopes_from_context()`:

1. **Explicit scopes in token** — if the `scope` claim is present (e.g., a down-scoped token), those scopes are used as-is. No group mappings are applied, preventing unintended privilege escalation.
2. **Group-derived scopes** — if no explicit scopes exist, the user's IdP groups are looked up in `group_mappings` and expanded into their scope list.

This lets agents and services operate with minimally scoped tokens while human users get their full role-based scope set from their IdP group membership.

---

## Sharing and ACL Independence

Sharing scopes (`federations-share`, `servers-share`, `agents-share`) are intentionally decoupled:

- Sharing a federation does **not** cascade to the servers or agents it synced in. Each resource is shared independently.
- This means a user can have access to a federation definition without automatically gaining access to every resource it produced.

---

## Adding or Modifying Scopes

All changes are made in `scopes.yml` — no code changes required:

- **New endpoint**: add an entry under the appropriate scope block with `action`, `method`, and `endpoint`.
- **New role**: add a key under `group_mappings` and list the scopes it should carry.
- **New scope**: define a new top-level key with its endpoint rules, then reference it from roles in `group_mappings`.

`ScopePermissionMiddleware` loads rules at application startup, so a service restart is required after any change to `scopes.yml`.
