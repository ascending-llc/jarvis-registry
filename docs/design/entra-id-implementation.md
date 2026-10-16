# Entra ID identity and authorization

For deployment configuration, use the [Entra setup guide](../entra-id-setup.md). This document describes
the current implementation; the former access-token identity and Graph identity fallback paths have
been removed.

## Responsibilities

- `auth-server/providers/entra.py` verifies the upstream ID token and extracts identity claims. It
  separately uses the access token to query Graph for configured scope-group Object IDs.
- `registry-pkgs/oauth/identity.py` binds the verified identity to a shared MongoDB user and provisions
  first-time users with a trusted email.
- `auth-server/routes/oauth_flow.py` negotiates scopes, resolves the user, and checks client consent.
- `auth-server/services/token_grant_service.py` issues tokens using the stored `user_id` without
  resolving the identity again.
- Registry's callback loads that user and synchronizes group memberships. It does not create users.

## Verified identity

The provider requires a successfully verified ID token with nonempty `iss`, `sub`, and `oid` claims.
Existing issuer, audience, signature and expiry validation remains in effect. Missing or invalid
identity data fails login; it never falls back to another identity source.

The `email` claim is stripped and lowercased. A UPN is never substituted for email. Only boolean
`xms_edov: true` allows the email to link or provision an account. Preferred username and name are
display attributes. The provider returns `username`, `email`, `name`, `id`, `groups`, `issuer`,
`subject`, `object_id`, and `email_trusted`. `id` and `object_id` contain Entra's `oid`.

## Shared user resolution

Resolution proceeds through subject, Entra object ID, trusted email, then provisioning. Issuer-scoped
matches are preferred; missing-issuer legacy records can be repaired on login. Username is never an
identity lookup key. Email linking rejects conflicting provider, subject, issuer or object-ID bindings.

New Entra records use `provider="openid"`, `openidId=sub`, `openidIssuer=iss`, and `idOnTheSource=oid`.
Chat and Registry share the same Entra client ID in each environment so their pairwise subjects agree.
Existing identity bindings, usernames and roles are never overwritten to make a login match.
Conditional updates protect against concurrent changes; a duplicate insert retries lookup once.
No shared user indexes or generated models are changed.

## Authorization and token flow

Group membership resolves the configured scope groups using Graph's `checkMemberGroups`; display
names never grant scopes. A failed group lookup yields no scope groups. Browser and device flows
both reject zero scopes before provisioning a user.

Once a `user_id` exists, third-party clients require consent before authorization completes. Auth
codes, approved device codes, pending consent, and refresh records carry the resolved identity.
Older stored records without `user_id` require a new sign-in instead of a username/email lookup.

The upstream Entra `sub` used for identity resolution is distinct from the subject of Jarvis-issued
JWTs. Jarvis user tokens use the MongoDB user ID for both `sub` and the compatibility `user_id` claim;
`preferred_username` carries the display name. Registry readers reject older username subjects.
Deploy auth-server and Registry together: browser users must sign in again and user-generated tokens
must be regenerated. OAuth refresh records carrying `user_id` can issue the new token format.

## Operations and tests

Configure `xms_edov` and the shared app registration before deployment. Apply the auth-server and
Registry changes together, removing legacy identity-source settings at deployment time.

Tests mock the identity provider, MongoDB and Redis. They cover verified claim extraction, scope
denials, first-login consent, identity conflicts, concurrent provisioning and stored-user-ID grants.
For operator diagnosis and account cleanup, see the
[identity conflict runbook](../runbooks/identity-conflict-resolution.md).
