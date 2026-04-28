# IDP Integration

Jarvis Registry ships with a built-in **Auth Server** — a standards-compliant OpenID Connect (OIDC) and OAuth 2.0 authorization server. It handles token issuance, validation, and refresh for all users and AI agents connecting to the gateway.

Rather than building its own identity store, the auth server is designed to federate with your existing enterprise Identity Provider (IdP). Configure your IdP once, and every user, group, and service principal flows through automatically.

---

## Standards-Based by Design

The auth server implements standard OIDC and OAuth 2.0 flows:

- **Authorization Code Flow** — for browser-based users logging into the Jarvis Registry UI
- **Device Authorization Flow** — for CLI tools and headless AI agents
- **Client Credentials Flow(Roadmap)** — for machine-to-machine (M2M) service accounts and AI copilots
- **Token Refresh** — automatic access token renewal via refresh tokens, without re-authentication

All tokens are signed JWTs (RS256). Scopes and group claims from your IdP are preserved and mapped directly to Jarvis Registry [RBAC roles](../design/scopes.md).

---

## Supported Identity Providers

Jarvis Registry ships with first-party support for the two most common enterprise IdPs:

| Provider | Use Case |
|---|---|
| **Microsoft Entra ID** | Azure AD tenants, Microsoft 365 organizations |
| **Keycloak** | Self-hosted or cloud-managed open-source IdP |

See the setup guides for each:

- [Microsoft Entra ID Setup](../entra-id-setup.md)
- [Keycloak Integration](../keycloak-integration.md)

---

## Extending to Other Providers

The auth server's provider interface is open. Any OIDC-compliant IdP can be integrated by implementing the `AuthProvider` abstract base class in `auth-server/src/auth_server/providers/`. Community contributions for additional providers (Okta, Auth0, Ping Identity, AWS IAM Identity Center) are welcome — see [Contributing](../CONTRIBUTING.md).

!!! info "RBAC & Scopes"
    Once your IdP is configured, group membership from your IdP is automatically mapped to Jarvis Registry roles. See [RBAC & Scopes](../design/scopes.md) for the full role and permission model.
