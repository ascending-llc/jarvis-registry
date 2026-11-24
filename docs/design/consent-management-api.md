# Consent Management API

Use the consent management page to review or revoke existing approvals. Use the resource consent pages to approve pending requests for a server or agent.

## API Overview

**1. App Consent Management**

| # | Method | Path | What it does | Success |
|---|---|---|---|---|
| [1.1](#11-list-app-consents) | GET | `/api/v1/consents/clients` | Lists the user's apps and approved resources. | `200` |
| [1.2](#12-revoke-one-app) | DELETE | `/api/v1/consents/clients/{client_id}` | Removes one app's approvals across the user's devices. | `204` |
| [1.3](#13-revoke-selected-or-all-apps) | POST | `/api/v1/consents/clients/revoke` | Revokes selected apps or all eligible apps and reports each outcome. | `200` |
| [1.4](#14-revoke-selected-resources) | POST | `/api/v1/consents/clients/{client_id}/resources/revoke` | Removes selected server or agent approvals for one app. | `200` |

**2. Resource Approval**

| # | Method | Path | What it does | Success |
|---|---|---|---|---|
| [2.1](#21-approve-a-server) | POST | `/api/v1/mcp/consent/server` | Approves an MCP server from a pending consent page. | `200` |
| [2.2](#22-approve-an-agent) | POST | `/api/v1/mcp/consent/agent` | Approves an A2A agent from a pending consent page. | `200` |

## Table of Contents

- [Concepts](#concepts)
- [API Route Prefix](#api-route-prefix)
- [Access Control](#access-control)
- [API Endpoints](#api-endpoints)
  - [1. App Consent Management](#1-app-consent-management)
  - [2. Resource Approval](#2-resource-approval)
- [Data Models](#data-models)
- [Error Response Format](#error-response-format)

## Concepts

- Each OAuth `client_id` appears once per user, across all devices. Users can approve the same registered app again after revocation.
- Revoking an app removes its app approval and all its resource approvals for the signed-in user. For Claude Desktop and Registry CLI, this affects all of that user's devices. The client registration and upstream OAuth connections remain available.
- The app must reconnect and obtain fresh consent on its next sign-in or refresh. Old authorization codes, device codes and refresh tokens for the revoked grant cannot be exchanged, even after the user reconnects. Issued access tokens expire within one hour.
- Calls that check resource consent, including gateway `execute_tool` and direct-connect `tools/call`, ask for approval again immediately. Direct-connect handshake and discovery/list methods and the separate `/proxy/a2a/*` routes can continue with an unexpired access token.
- Revoking selected resources preserves the app approval and all other resources. A new server or agent approval for a registered app requires its app approval. Historical entries may have only a resource approval. Interactive tokens (`user-generated`) can obtain resource approvals independently and remain valid after revocation.
- Consent and dynamic client registration (DCR) each expire after 30 days of inactivity. Viewing the list leaves both expiry times unchanged.

## API Route Prefix

Use `/api/v1` relative to the Registry base URL. Under `/gateway`, request `/gateway/api/v1/consents/clients`.

## Access Control

Use a Registry session with the `user-read` scope for all endpoints in the overview. The frontend's shared HTTP client sends these credentials on same-origin requests.

| Parameter | In | Required for | Value |
|---|---|---|---|
| `jarvis_registry_session` | Cookie | Every request | The signed-in user's session. A gateway Bearer token alone receives `401`. |
| `X-Jarvis-CSRF` | Header | Every POST and DELETE | The value of the `jarvis_registry_csrf` cookie. A missing or invalid header returns `403`. |

Names shown are the defaults. Send request bodies as `application/json`.

## API Endpoints

### 1. App Consent Management

#### 1.1 List app consents

**Request parameters**

No path or query parameters. No request body. The session cookie identifies the user.

**Example request**

```http
GET /api/v1/consents/clients HTTP/1.1
```

**Example response · `200 OK`**

`clients` is sorted by `displayName` (case-insensitive), then `clientId`. Each app's resources are sorted by `type`, then `path`. An empty list returns `{"clients":[]}`.

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

#### 1.2 Revoke one app

**Request parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `client_id` | Path | string | Yes | The app's `clientId` from the list. |

No query parameters or request body.

**Example request**

```http
DELETE /api/v1/consents/clients/jarvis-registry-claude-desktop HTTP/1.1
```

**Example response · `204 No Content`**

```http
HTTP/1.1 204 No Content
```

The response body is empty, including on repeat requests. The frontend can remove the row without parsing JSON. Internal Registry and headless clients return `400`.

#### 1.3 Revoke selected or all apps

**Request parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `clientIds` | JSON body | string[] | For selected apps | 1–100 client IDs from the list. |
| `all` | JSON body | boolean | For all apps | Must be the literal `true`. Selects every revocable app for this user. |

Send exactly one of the two fields. No path or query parameters.

**Example request · `POST /api/v1/consents/clients/revoke`**

```json
{"clientIds": ["mcp-client-atlas", "mcp-client-missing", "jarvis-headless-agent"]}
```

To revoke all eligible apps, send this body to the same endpoint.

```json
{"all": true}
```

**Example response · `200 OK` for selected apps**

```json
{
  "results": [
    {"clientId":"mcp-client-atlas","status":"revoked"},
    {"clientId":"mcp-client-missing","status":"not_found"},
    {"clientId":"jarvis-headless-agent","status":"rejected"}
  ]
}
```

`revoked` means a consent record existed and was removed. `not_found` means the user had no stored consent for the app. `rejected` identifies an explicitly requested internal Registry or headless client. Revoke-all skips these categories and returns `{"results":[]}` when there are no eligible records.

#### 1.4 Revoke selected resources

**Request parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `client_id` | Path | string | Yes | The app's `clientId` from the list. |
| `resources` | JSON body | array | Yes | 1–100 resources belonging to this app. |
| `resources[].type` | JSON body | enum | Yes | `mcp_server` or `a2a_agent`. |
| `resources[].path` | JSON body | string | Yes | Exact path from the list. The type and path together identify the approval. |

No query parameters. An `unknown` resource can be removed with the whole app.

**Example request · `POST /api/v1/consents/clients/mcp-client-atlas/resources/revoke`**

```json
{
  "resources": [
    {"type": "mcp_server", "path": "/time"},
    {"type": "a2a_agent", "path": "travel"}
  ]
}
```

**Example response · `200 OK` when only the server had an approval**

```json
{
  "results": [
    {"type":"mcp_server","path":"/time","status":"revoked"},
    {"type":"a2a_agent","path":"travel","status":"not_found"}
  ]
}
```

Both explicit batch endpoints return one result per input item in request order. Repeated items count toward the 100-item limit, are removed once and receive the same status.

### 2. Resource Approval

These two existing endpoints submit a one-time approval from the browser. On success, `client_branding` identifies the AI app to return to when that information is available. It can be null for either endpoint.

#### 2.1 Approve a server

**Request parameters** — `nonce` (JSON body, required string) is the one-time value from the server consent URL. No path or query parameters.

**Example request · `POST /api/v1/mcp/consent/server`**

```json
{"nonce": "example-server-consent-nonce"}
```

**Example response · `200 OK`**

```json
{"status": "ok", "client_branding": null}
```

#### 2.2 Approve an agent

**Request parameters** — `nonce` (JSON body, required string) is the one-time value from the agent consent URL. No path or query parameters.

**Example request · `POST /api/v1/mcp/consent/agent`**

```json
{"nonce": "example-agent-consent-nonce"}
```

**Example response · `200 OK`**

```json
{"status": "ok", "client_branding": "vscode"}
```

#### When approval requires reconnecting

Both approval endpoints return `409 Conflict` if the app requires app approval and that approval is missing when the user submits the page. This also applies to a request opened before whole-app revocation.

```json
{"detail":"This app's access was revoked. Reconnect it from your AI app, then try again."}
```

Show the reconnect instruction and have the user start a new connection from their AI app. The link has already been consumed. Retrying it returns `404`.

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
| `400` | Single-app or resource revoke targets a non-revocable category, or a resource path collides with a reserved consent field. |
| `401` | A valid Registry session cookie identifying the user is required. |
| `403` | The session lacks `user-read`, or a write request has a missing or invalid CSRF header. |
| `404` | The submitted consent page link has expired or was already consumed. |
| `409` | A server or agent consent page requires the user to reconnect the app. |
| `422` | Invalid request body, including an unsupported resource type, conflicting batch options or a list outside the 1–100 item range. |
| `500` | The operation failed. Reload the list to check the current approvals before retrying. |
