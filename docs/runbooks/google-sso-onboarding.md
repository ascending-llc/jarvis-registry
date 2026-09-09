# Google SSO — Operational Runbook

This document covers the external, human-driven setup steps the code cannot perform itself: registering a client's GCP/Workspace
resources, and standing up our own internal dual-provider dogfooding environments (DEMO, PROD).

## Two Google admin surfaces — keep these straight

| | Google Cloud Console (`console.cloud.google.com`) | Google Admin Console (`admin.google.com`) |
|---|---|---|
| Manages | GCP project, service accounts, OAuth client registration | Workspace/Cloud Identity: users, **Groups**, admin roles |
| Privilege model | GCP IAM roles | Workspace admin roles (Super Admin, Groups Reader, etc.) |

GCP project-level access does **not** grant any rights over Workspace Groups — that's a separate
privilege system, granted only by the Workspace Super Admin (or someone they delegate to) on the Admin
Console side. Confirm who actually holds Super Admin before starting a client engagement; it may not be
the same person who gave you GCP project access.

---

## Part 1 — Client Onboarding (real deployment)

### 1.1 Prerequisite check

Confirm the client's GCP project is attached to **their own** Workspace/Cloud Identity org (not a
personal-Gmail-owned project). This determines the audience setting in step 1.3 — if it isn't attached to
their org, Internal audience (below) isn't available at all, and you're into External + Google's
verification process instead (multi-day lead time on top of the Super Admin role-grant lead time below —
flag this early).

### 1.2 Enable the Cloud Identity API

`console.cloud.google.com/apis/library/cloudidentity.googleapis.com?project=<PROJECT_ID>` → Enable.
Required before the service account (step 1.4) can call `cloudidentity.googleapis.com` at all — this is
easy to skip since nothing else in the setup flow prompts for it, and it's exactly what we missed on our
own first pass at the dogfood project.

### 1.3 Register the OAuth client (Google Auth Platform)

1. **Branding**: App name, user support email, developer contact email (required before Google lets you
   create a Client ID at all).
2. **Audience**: set to **Internal**. The client's users are all within their own Workspace org, and
   `GOOGLE_ALLOWED_HD` restricts by domain anyway — Internal gets you there with zero verification and no
   user cap. Only fall back to External if a client specifically needs non-Workspace-domain users to log
   in (rare; treat as an exception, not the default).
3. **Clients**: create an OAuth Client ID, type Web application. Redirect URI:
   `{AUTH_SERVER_EXTERNAL_URL}/oauth2/callback/google` (same shape as the existing Entra/Cognito/Keycloak
   registration). Confirm the exact scheme/host/trailing-slash match — OAuth redirect URIs are matched
   byte-for-byte.

### 1.4 Create the service account (Cloud Identity Groups API access)

1. Same GCP project, IAM & Admin → Service Accounts → Create.
2. Generate a JSON key. **If key creation is blocked by an org policy**
   (`iam.disableServiceAccountKeyCreation` is common in security-conscious orgs), escalate to the client's
   GCP org admin for a one-off exception on this specific service account — don't design around it, just
   flag it as a known possible blocker and resolve it live if it happens.
3. From the downloaded JSON, extract only the `client_email` field. **Never forward the JSON file
   itself** to the Workspace Super Admin for the next step — they don't need the private key to assign a
   role to an identifier, and the private key is a long-lived credential that shouldn't travel over chat.

### 1.5 Client action required — Workspace role grant (start this early; external lead time)

This is the one step the client's Super Admin must do; it cannot be done from the GCP side, and its
timing is outside our control. Send them **only the service account's `client_email`**, and ask them to,
in `admin.google.com`:

1. Account → Admin roles → **Groups Reader** → Assign admin → Assign service accounts → paste the
   `client_email` → Assign role.
2. **Also assign a Users:Read-equivalent privilege.** Why: our code calls
   `groups.memberships:searchTransitiveGroups` (`registry_pkgs/google/cloud_identity_client.py:101`) to
   resolve a member's transitive groups. Google's own reference for that method
   (https://docs.cloud.google.com/identity/docs/reference/rest/v1/groups.memberships/searchTransitiveGroups)
   states the caller only needs "groups read permissions" — but that's not sufficient in practice: an
   independent writeup of the same API
   (https://blog.salrashid.dev/articles/2022/search_group_membership) documents needing a custom admin role
   with **both** "user read" and "group read" before the call stops returning `PERMISSION_DENIED` for a
   service account, and this matches exactly what we hit in our own dogfood testing — Groups Reader alone
   was not sufficient, and lookups only started working once a Users:Read-style privilege was added
   (verified directly by the engineer who implemented this code). This is an undocumented quirk of the API
   (resolving a member's transitive groups apparently requires read access to the member's own Directory
   user record, not just to the groups themselves), not something we chose. The exact Admin Console role or
   custom-role privilege set used internally wasn't precisely recorded (shorthanded as "Users:Read" in
   Slack chat) — confirm the precise bundle with Daoqi Zhang / Kelvin Yu before repeating this instruction
   verbatim to a client, since granting more than Groups Reader is a bigger ask of a client's Super Admin
   than the original least-privilege design intended, and worth being precise about.
3. Role changes can take up to ~24h to propagate — don't debug a 403 as a config error in the first few
   hours after granting.

### 1.6 Group naming convention

Each Google Workspace group the client creates must be named `<group-name>@<client-domain>`, where
`<group-name>` is exactly one of the four keys under `group_mappings` in
`registry-pkgs/src/registry_pkgs/scopes.yml` (lines 16–83): `jarvis-registry-admin`,
`jarvis-registry-power-user`, `jarvis-registry-user`, `jarvis-registry-read-only`. No client-specific
config file — `GoogleProvider` strips the domain off every Cloud Identity group email before scope
mapping, so `jarvis-registry-admin@clientdomain.com` resolves to the `jarvis-registry-admin` key
automatically, regardless of which domain it was created under.

**Double-check the name before creating the group.** We hit this ourselves during dogfooding — a test
group was created as `jarvis-demo` and had to be renamed to `jarvis-registry-user` after the fact once the
fixed naming convention was noticed.

### 1.7 Provision secrets and deploy

All Google env vars go wherever this client's other provider secrets already live — see
`docs/deployment-guide.md` Phase 3/4 (AWS: single JSON blob in the Secrets Manager "Registry Secret",
synced by External Secrets Operator; Azure: individual Key Vault secrets). Add a row to that Phase 4 table:

| Category | Keys | Notes |
|---|---|---|
| **Identity Provider (Google)** | `GOOGLE_ENABLED`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_ALLOWED_HD`, `GOOGLE_SERVICE_ACCOUNT_KEY_JSON`, `GOOGLE_GROUP_SYNC_ENABLED` | `GOOGLE_ALLOWED_HD` = client's domain. `GOOGLE_SERVICE_ACCOUNT_KEY_JSON` = the full JSON key pasted verbatim as downloaded — do not trim unused fields (`project_id`, `client_id`, etc. are harmless; `private_key_id` is useful for tracking key rotation later). |

Secrets must be in place **before** the Helm chart deploys (Phase 4 precedes Phase 5).

### 1.8 Post-deploy verification (client, single provider)

- [ ] `GET /oauth2/providers` returns `google`.
- [ ] Login button appears and redirects correctly.
- [ ] A fresh Google login creates a `users` document with `provider="google"`, `googleId` set,
      `idOnTheSource` equal to the account's email.
- [ ] A login from an account outside `GOOGLE_ALLOWED_HD` is rejected with a clear error, not a generic
      failure page.
- [ ] A Workspace group, shared to a resource via the ShareModal UI, correctly shares that resource with a
      member of that group.
- [ ] **Revoking the service account's Workspace role does not cleanly degrade — test both login paths
      separately, and expect different outcomes.** There's no cached/synced group data this path falls
      back to: `GoogleProvider.get_user_info` calls Cloud Identity live on every login, with no internal
      error handling around that call. A failure there is caught only because `oauth_flow.py`'s OAuth
      callback has a broad `except Exception:` (meant for unrelated token-parsing edge cases) that falls
      through to a generic OIDC userinfo call (logged as `"Falling back to userInfo on token parsing
      error"`), which returns `groups: []`.
      - **Plain browser login (no requested scope)**: a session is created, but resolves to **zero
        scopes** — the account can do nothing until the role is restored and the user logs in again. Not
        "reduced" access; no access.
      - **Any MCP/device flow that requests specific scopes** (including this runbook's Part 3 flip-test):
        scope negotiation fails outright and the login is rejected with `invalid_scope`/`scope_denied` —
        it does **not** succeed.
      This is an accidental byproduct of a catch-all written for a different failure mode, not a designed
      fallback — see "Open follow-ups" below.

---

## Part 2 — Internal Dogfooding Setup (DEMO + PROD)

### 2.1 Environment facts

- Throwaway domain: **famulei.us** (already registered; a separate business owned by the same people as
  ascendingdc.com — not related to our production Workspace directory).
- The dogfood **GCP project is parented under ascendingdc.com's Workspace org**, not famulei.us's. This
  matters directly for the audience decision below.
- Workspace/Admin Console access for famulei.us: **Kelvin Yu**.
- GCP project / service account creation: **Daoqi Zhang**.
- Why famulei.us and not ascendingdc.com: our real Workspace already uses Entra as the upstream IdP
  (Google as Service Provider, redirecting to Entra) — an `@ascendingdc.com` login would silently redirect
  through Entra again and never exercise the Google-as-terminal-IdP code path. A domain also can't be
  claimed by two Workspace/Cloud Identity orgs at once, so `ascendingdc.com` couldn't be reused even if the
  federation issue didn't exist.

### 2.2 Audience: External + Testing (not Internal)

Because the GCP project lives under **ascendingdc.com's** org, "Internal" audience would only ever admit
`@ascendingdc.com` accounts — exactly the accounts that are federated to Entra and can't test the Google
path. External is therefore required here, not just a fallback. Consequences:

- Publishing status stays **Testing** (never verify/publish this app) — 100-user cap, rejects anyone not
  explicitly listed.
- Under Google Auth Platform → Audience → Test users, add every team member who needs to log in, e.g.
  `kent.xue@famulei.us`, `yulin.deng@famulei.us`, before they attempt to log in.
- Redirect URIs — register **both**, since the same OAuth client is used for local iteration and the
  shared DEMO deployment:
  - Local: `http://localhost:8888/oauth2/callback/google`
  - DEMO: `https://jarvis-demo.ascendingdc.com/oauth2/callback/google`
  - PROD: confirm the actual external URL with Daoqi Zhang before registering — not yet confirmed here.

### 2.3 Service account

Same as client onboarding (1.4–1.5), but internal: Daoqi Zhang creates the service account and JSON key;
share only `client_email` with Kelvin Yu; Kelvin assigns Groups Reader **and** the Users:Read-equivalent
privilege (2.2/1.5) via `admin.google.com`.

### 2.4 Test users and groups

1. Kelvin Yu creates a `@famulei.us` account for each team member who needs to test Google SSO.
2. Create test groups in the Admin Console Directory as `<group-name>@famulei.us`, per the format in §1.6
   — not an improvised name. (We got this wrong once already; see §1.6.) **Only `jarvis-registry-user@famulei.us`
   exists today** — the other three roles (`jarvis-registry-admin`, `jarvis-registry-power-user`,
   `jarvis-registry-read-only`) have no test group yet, so dogfooding so far has not exercised those roles'
   scope mappings at all. Create the remaining three before relying on the checklist below to cover all
   four roles.
3. Add the relevant test users to the appropriate group(s).

### 2.5 Enable dual-provider on DEMO and PROD

In each environment's AWS Secrets Manager "Registry Secret" JSON blob (`docs/deployment-guide.md` Phase
3/4):

```
ENTRA_ENABLED=true
GOOGLE_ENABLED=true
GOOGLE_CLIENT_ID=<dogfood OAuth client id>
GOOGLE_CLIENT_SECRET=<dogfood OAuth client secret>
GOOGLE_ALLOWED_HD=famulei.us
GOOGLE_SERVICE_ACCOUNT_KEY_JSON=<full JSON key, pasted verbatim>
GOOGLE_GROUP_SYNC_ENABLED=true
```

`AUTH_PROVIDER` stays at each environment's normal default — it only affects the single-provider MCP/DCR
metadata endpoint (§3), not the dual-provider browser login. Redeploy per your normal process for that
environment (confirm the exact command with Daoqi Zhang — whether a plain External-Secrets sync is enough
or a Helm upgrade / pod restart is needed to pick up the change).

### 2.6 Post-deploy verification (DEMO + PROD, dual provider)

- [ ] `GET /oauth2/providers` returns both `entra` and `google`.
- [ ] Login page shows two buttons; each redirects through the correct provider.
- [ ] A Google login (famulei.us test account) creates a distinct `users` document
      (`provider="google"`) from an Entra login by the same person — expected, not a bug.
- [ ] A login attempt from outside `famulei.us` is rejected cleanly.
- [ ] A famulei.us test group, shared to a resource via ShareModal, correctly shares with a member of that
      group.
- [ ] Revoking the service account's role — test both the browser-login and MCP/device-flow paths
      separately; see §1.8's last item for why they behave differently (zero-scope session vs. outright
      rejection).
- [ ] Google Auth Platform → Audience → Test users includes every team member currently testing (Testing
      mode hard-rejects anyone missing).

---

## Part 3 — MCP/DCR flip-test ("test MCP invocation")

Any MCP client (Claude Code, an IDE extension) doing its own OAuth/DCR flow is routed to a single
hardcoded provider via `auth-server/src/auth_server/routes/well_known.py`'s RFC 8414
`authorization_endpoint`, driven by the single-value `AUTH_PROVIDER` setting — independent of
`GOOGLE_ENABLED`/`ENTRA_ENABLED`. There is no way for one deployment to expose both providers to MCP/DCR
clients simultaneously, so this has to be a temporary flip.

**Do this locally, iteratively — not on DEMO/PROD.** `AUTH_PROVIDER` is a single shared value; flipping it
on DEMO breaks Entra-based MCP login for anyone else using DEMO for as long as the flip is live.

### 3.1 Local (repeat as needed)

1. In the repo-root `.env`, set `AUTH_PROVIDER=google`.
2. `docker compose restart auth-server registry` (registry also reads `AUTH_PROVIDER` in places; restart
   both to be safe).
3. Verify: `curl http://localhost:8888/.well-known/oauth-authorization-server | jq .authorization_endpoint`
   — should end in `/oauth2/login/google`.

   Note the docker-compose interpolation trap (`docker-compose.yml:186`,
   `AUTH_PROVIDER=${AUTH_PROVIDER:-cognito}`): that `${VAR}` syntax only resolves from the real shell
   environment or a file literally named `.env` next to the compose file — never from `env_file:`. If
   your local setup sources `AUTH_PROVIDER` from a differently-named env file, this line silently
   overwrites it back to `cognito`. Trust the curl output above, not the `.env` file alone.
4. Point the MCP client at the local registry endpoint and run its OAuth/DCR flow to completion — confirm
   it redirects to Google, completes, and a token is minted.
5. Revert: set `AUTH_PROVIDER` back to its original value, restart the same containers, and re-run the
   curl check to confirm it reverted.

### 3.2 One final DEMO pass (once, announced, before sign-off)

Local-only testing never actually exercises the real deployed environment — do this once to close that
gap, not routinely:

1. Announce in the team channel that DEMO's `AUTH_PROVIDER` will be `google` for a short window, and that
   Entra-based MCP login on DEMO won't work during it.
2. Set `AUTH_PROVIDER=google` in DEMO's Secrets Manager "Registry Secret" and redeploy (confirm the exact
   redeploy command with Daoqi Zhang — see §2.5).
3. Verify via `curl https://jarvis-demo.ascendingdc.com/.well-known/oauth-authorization-server`, then run
   the MCP client's OAuth/DCR flow against DEMO end-to-end.
4. Revert `AUTH_PROVIDER` to its original value, redeploy again, and re-verify via curl.
5. Announce completion.

PROD dogfooding is covered by Part 2's browser-login checklist only — this MCP/DCR flip-test is scoped to
DEMO.

---

## Open follow-ups

- [ ] Confirm the precise Admin Console role/privilege bundle behind "Groups Reader + Users:Read" (§1.5)
      with Daoqi Zhang / Kelvin Yu before repeating it verbatim in a client engagement. The *why* is now
      documented (searchTransitiveGroups needs read access to the member's own Directory user record, not
      just to groups — see §1.5's citations) — what's still unconfirmed is the exact Admin Console role
      name or custom-role privilege set that satisfies it.
- [ ] Confirm PROD's actual external URL for OAuth redirect URI registration (§2.2).
      more than once).
- [ ] Tear down the famulei.us dogfooding environment once Google SSO testing is fully validated.
