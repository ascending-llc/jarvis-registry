# Configure Entra scope groups

Registry grants OAuth scopes through four internal group names in the bundled `scopes.yml`.
Each deployment binds those names to specific Entra **group Object IDs**. A different group with
the same display name grants nothing. Login and workflow resume use the same bindings.

## Create and protect the groups

In the client's Entra admin center, create these four groups:

- `jarvis-registry-admin`
- `jarvis-registry-power-user`
- `jarvis-registry-user`
- `jarvis-registry-read-only`

Use group type **Security** and membership type **Assigned**. Limit owners to the client's IT
administrators, who control membership and therefore Registry privileges. Names are for people;
Object IDs determine authorization. Reserve these groups for Registry and do not rename or reuse
them for other purposes. Recreating a group produces a new ID and requires updating its binding.

Do not enable hidden membership. Graph can omit groups the caller cannot evaluate. Nested groups
are supported: membership is evaluated transitively.

For defense in depth, set **Users can create security groups in Azure portals, API or PowerShell**
to **No** under Entra admin center → Groups → General. Object-ID binding remains the authorization
boundary regardless of this tenant setting.

## Configure the deployment before updating images

Collect each group's **Object ID** under Groups → group → Overview. Do not use the application ID,
tenant ID, or a group display name.

| Environment variable | Value |
|---|---|
| `JARVIS_REGISTRY_ADMIN_GROUP_OBJECT_ID` | Object ID of `jarvis-registry-admin` |
| `JARVIS_REGISTRY_POWER_USER_GROUP_OBJECT_ID` | Object ID of `jarvis-registry-power-user` |
| `JARVIS_REGISTRY_USER_GROUP_OBJECT_ID` | Object ID of `jarvis-registry-user` |
| `JARVIS_REGISTRY_READ_ONLY_GROUP_OBJECT_ID` | Object ID of `jarvis-registry-read-only` |

Set all four on the **registry**, **auth-server**, and **workflow-worker** pods. Replace the example
GUIDs in `.env.example`; they do not identify real authorization groups. Docker Compose passes the
root `.env` to all three services.

When `AUTH_PROVIDER=entra`, missing, invalid or duplicate bindings fail startup. Errors name every
bad variable, for example `JARVIS_REGISTRY_ADMIN_GROUP_OBJECT_ID is not set`. UUIDs are normalized
before duplicate detection. A syntactically valid but incorrect ID passes startup validation and
grants no membership for that binding, so verify IDs and real sign-ins before production rollout.

auth-server also requires the bindings when `ENTRA_ENABLED=true` (the default), even if
`AUTH_PROVIDER=google`. For Google-only deployments set `AUTH_PROVIDER=google` and
`ENTRA_ENABLED=false`; configure `GOOGLE_ALLOWED_HD` as described in
[Google SSO onboarding](google-sso-onboarding.md).

No new Graph permission is required. Login calls `POST /v1.0/me/checkMemberGroups` with the four IDs
using the existing delegated `User.Read` permission. The endpoint checks transitive membership in
one request. See [Microsoft Graph's checkMemberGroups documentation](https://learn.microsoft.com/en-us/graph/api/directoryobject-checkmembergroups?view=graph-rest-1.0).

No MongoDB migration or historical user cleanup is required for this change. Keep environment-specific
user cleanup separate from this rollout.

## Verify on DEMO, then production

1. Confirm all three services start with the new configuration. A validation error identifies the
   environment variable to correct; fix configuration before retrying the rollout.
2. Start a fresh login as a member of the configured admin group and verify admin features work.
3. Start a fresh browser login as a user outside all four configured groups. Expect the page saying
   no permissions are currently assigned. Membership only in a same-named, unconfigured group must
   not grant admin scopes.
4. Verify nested membership where the deployment uses nested groups.
5. Resume a workflow through the path that rebuilds the triggering user's authorization from current
   database memberships. Only groups with configured IDs may grant scopes; local groups grant none.
6. Verify an ordinary CLI/MCP login and a requested-scope flow. Client protocols and the abstract
   `groups` claim remain unchanged.

A Graph lookup failure produces a `Group resolution failed for provider=entra` log entry and empty
groups, never a fallback to display-name authorization. Fix Graph availability or configuration and
sign in again. Logs report the failure type without user identifiers, tokens or response bodies.

Existing tokens are not automatically revoked by this deployment. Use fresh sign-ins for verification;
token/session revocation, if required operationally, follows the existing procedures.
