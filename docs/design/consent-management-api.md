# Consent Management API

List apps for which the signed-in user has an app approval or a resource approval.

## Table of Contents

- [Concepts](#concepts)
- [API Route Prefix](#api-route-prefix)
- [API Endpoints](#api-endpoints)
- [Access Control](#access-control)
- [Data Models](#data-models)
- [Error Response Format](#error-response-format)

## Concepts

- Each OAuth `client_id` appears once per user, across all devices. Users can approve the same registered app again after revocation.
- App approval is consent to the client. Resource approval grants that client access to one server or agent. Either level can exist on its own.
- Consent and dynamic client registration (DCR) each expire after 30 days of inactivity. Viewing the list leaves both expiry times unchanged.

## API Route Prefix

Use `/api/v1` relative to the Registry base URL. Under `/gateway`, request `/gateway/api/v1/consents/clients`.

## API Endpoints

### `GET /api/v1/consents/clients`

Takes no parameters or request body. Returns `200 OK` with the list below, or `{"clients":[]}` when empty.
Clients are sorted by `displayName` (case-insensitive), then `clientId`. Resources are sorted by `type`, then `path`.

```json
{
  "clients": [
    {
      "clientId": "mcp-client-atlas", "category": "mcp_dcr", "displayName": "Atlas",
      "appApproved": true, "registrationExpired": false, "sharedAcrossDevices": false, "revokesTokens": true,
      "clientUri": "https://atlas.example", "redirectUris": ["https://atlas.example/oauth/callback"],
      "registeredAt": 1795000000, "registrationIp": "192.0.2.10",
      "softwareName": "Atlas Desktop", "softwareVersion": "2.1.0",
      "resources": [
        {"type": "a2a_agent", "path": "travel", "name": "Travel assistant"},
        {"type": "mcp_server", "path": "/time", "name": "Time"}
      ]
    },
    {
      "clientId": "jarvis-registry-claude-desktop", "category": "claude_desktop", "displayName": "Claude Desktop",
      "appApproved": true, "registrationExpired": false, "sharedAcrossDevices": true, "revokesTokens": true,
      "clientUri": null, "redirectUris": [], "registeredAt": null, "registrationIp": null,
      "softwareName": null, "softwareVersion": null, "resources": []
    },
    {
      "clientId": "user-generated", "category": "user_generated", "displayName": "Interactive tokens",
      "appApproved": false, "registrationExpired": false, "sharedAcrossDevices": false, "revokesTokens": false,
      "clientUri": null, "redirectUris": [], "registeredAt": null, "registrationIp": null,
      "softwareName": null, "softwareVersion": null,
      "resources": [{"type": "mcp_server", "path": "/archived", "name": null}]
    },
    {
      "clientId": "mcp-client-old", "category": "mcp_dcr", "displayName": "mcp-client-old",
      "appApproved": true, "registrationExpired": true, "sharedAcrossDevices": false, "revokesTokens": true,
      "clientUri": null, "redirectUris": [], "registeredAt": null, "registrationIp": null,
      "softwareName": null, "softwareVersion": null, "resources": []
    }
  ]
}
```

## Access Control

Requires the Registry session cookie and `user-read`. The session determines whose approvals are returned. A gateway Bearer token alone receives `401`.

## Data Models

`ConsentClientListResponse.clients` contains one `ConsentClientOutput` per app. All fields are present, including nullable ones.

| ConsentClientOutput field | Type | Meaning |
|---|---|---|
| `clientId` | string | OAuth client ID. Use as the row key. |
| `category` | enum | `mcp_dcr`, `a2a_dcr`, `claude_desktop`, `registry_cli`, `user_generated` (interactive tokens), or `unknown`. Internal Registry and headless clients are excluded. |
| `displayName` | string | App label. Falls back to `clientId` for unknown clients or missing registration names. |
| `appApproved` | boolean | App-level approval. Always false for interactive tokens. |
| `registrationExpired` | boolean | The DCR registration is gone. Show the remaining approvals. |
| `sharedAcrossDevices` | boolean | True for Claude Desktop and Registry CLI. Consent applies to all of this user's installations of that app. |
| `revokesTokens` | boolean | If true, whole-app revocation blocks further code and refresh token exchanges for that grant. Issued access tokens can last until expiry. If false, interactive tokens survive revocation. |
| `clientUri` | string or null | App website supplied at registration. Only absolute `http`/`https` URLs are returned; any other value is `null`. |
| `redirectUris` | string[] | Registered redirect URIs for DCR clients, otherwise `[]`. |
| `registeredAt` | integer or null | DCR registration time in Unix seconds. |
| `registrationIp` | string or null | IP recorded at dynamic registration. |
| `softwareName`, `softwareVersion` | string or null each | Software reported by an MCP DCR client. These can be missing even with a valid registration. |
| `resources` | ConsentResourceOutput[] | This app's resource approvals. |

DCR clients register themselves, so `displayName`, `clientUri` and `redirectUris` are whatever the app supplied and are not verified. Render them as plain text, not HTML. If you link `clientUri`, open it with `rel="noopener noreferrer"`. A DCR app can name itself after a first-party app (e.g. "Claude Desktop"), so identify first-party apps by `category` (`claude_desktop`, `registry_cli`), never by `displayName`.

| ConsentResourceOutput field | Type | Meaning |
|---|---|---|
| `type` | enum | `mcp_server`, `a2a_agent` or `unknown`. |
| `path` | string | Server path, agent slug or raw identifier for an unknown entry. |
| `name` | string or null | Display name. If null (deleted, renamed or unknown resource), show `path`. |

## Error Response Format

Errors return a string `detail`, for example `{"detail":"Session authentication required"}`.

| HTTP status | Meaning |
|---|---|
| `401` | A valid Registry session cookie identifying the user is required. |
| `403` | The signed-in user lacks the `user-read` scope. |
| `500` | The list could not be loaded. Offer a retry. |
