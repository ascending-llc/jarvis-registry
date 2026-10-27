# Validate MCP access with two users and synthetic data

Before connecting a business system, check whether your Jarvis Registry deployment stops an unauthorized user from calling a registered MCP server, even when that user knows its server ID and tool name. This walkthrough uses two identities, one harmless tool, and a permission change.

The outcomes below are **acceptance criteria to validate on your deployment**, not a report of a completed tenant test. The included local probe checks the sample MCP server; it does not test Registry authentication or authorization.

Use the [MCP Gateway guide](../features/mcp-gateway-registry.md) for registration and sharing, and [Registry Endpoint](../features/registry-endpoint.md) for client connection setup. This walkthrough adds a repeatable access check to those workflows.

## Understand the access boundaries

**Roles define application capabilities; ACLs define access to specific resources.** To call a registered MCP server, the user's role must allow the action and the server's ACL must grant access. Both are required.

| Control | What it checks | Limit of this exercise |
|---|---|---|
| Application role | Allows gateway calls (`mcp-proxy-ops`) or server metadata reads (`servers-read`). | These capabilities do not grant access to a particular server. |
| Server access-control list (ACL) | Grants access to the particular server; `execute_tool` checks it on every invocation. | Knowing the server ID or discovering it earlier does not grant access. |
| Disabled tools | A server owner can disable tools for all consumers through normal discovery and `execute_tool`. | This is not a per-user permission, and the transparent proxy does not enforce this list. |
| Downstream authorization | The downstream service and the credentials used for the request determine access to business operations and records. | A synthetic server-access test cannot prove record-level permissions in a business system. |

The transparent proxy and the Registry gateway are different execution paths. The proxy checks server ACLs, but does not apply `disabledTools`. Repeat the server-access checks in the connection mode your clients use; do not infer one mode's tool restrictions from another. See the [tool-enablement contract](../design/mcp-tool-enablement-api.md).

## Before you start

Use an isolated, non-production Registry deployment and synthetic data only. If you need a deployment first, follow the [Deployment Guide](../deployment-guide.md).

- **Alice** has the built-in power-user role and owns the test server. Her role allows the setup actions; ownership grants her access to this server.
- **Bob** has the built-in user role, including the `mcp-proxy-ops` and `servers-read` scopes, but no ACL grant on the test server. Do not use the read-only role: it lacks the gateway capability.
- Use separate browser profiles and client sessions with separate credentials. Verify each authenticated identity before testing.
- Keep the test server private: turn off **Everyone** and avoid any group grant that includes Bob. Retain Alice as owner throughout.
- Use a client that lets you inspect discovery results and invoke explicit tools. Record the deployed Registry version, client version, connection mode, and test server ID.

Verify each session or token includes the required scopes; a down-scoped token can have fewer capabilities than its user's role. See [RBAC and scopes](../design/scopes.md). Entra deployments should also follow [Configure Entra scope groups](../runbooks/entra-scope-groups.md); a same-named group is not a substitute for the configured group Object ID.

## 1. Check the synthetic MCP server locally

The included [synthetic server](examples/mcp/synthetic_mcp_server.py) exposes one tool, `lookup_synthetic_ticket`. It returns a fabricated ticket and makes no external requests. It has no credentials, customer records, write tools, or authentication.

From the repository root, with the repository dependencies available, start it in one terminal:

```sh
uv run python docs/walkthroughs/examples/mcp/synthetic_mcp_server.py --port 8879
```

From the repository root in another terminal, run the [protocol probe](examples/mcp/probe_synthetic_mcp.py):

```sh
uv run python docs/walkthroughs/examples/mcp/probe_synthetic_mcp.py --port 8879
```

The probe connects to `http://127.0.0.1:8879/mcp`, initializes an MCP session, lists tools, and makes three calls. Its acceptance criteria are:

- `tools/list` contains `lookup_synthetic_ticket` with the required `ticket_id` input.
- Calling it with `{"ticket_id":"SYNTHETIC-1"}` succeeds and returns this structured content:

```json
{"ticket_id": "SYNTHETIC-1", "status": "open", "synthetic": true}
```

- Calling it with an unknown ticket ID or without the required argument returns a tool error.

Resolve any probe failure before diagnosing Registry permissions. A successful probe establishes only that the local sample works.

### Keep the network boundary intact

The sample binds only to `127.0.0.1` and has no authentication. **Never expose it publicly.** A Registry process can use the URL above only if it shares that host's network context. A container's loopback address refers to the container itself; a remote Registry cannot reach your laptop's loopback address. Adding `host.docker.internal` alone does not make a loopback-only listener reachable.

For the Registry portion, run the sample in the same isolated network context as Registry, or use an approved private synthetic endpoint reachable from Registry. Establish that arrangement before registering the URL. Do not change the sample to listen on a public interface to make the test work.

## 2. Register and enable a private test server

As Alice:

1. Follow the [MCP server registration workflow](../features/mcp-gateway-registry.md#registering-an-mcp-server). Use Streamable HTTP and the synthetic endpoint that Registry can actually reach. Select **No Auth** only for the isolated sample described above.
2. Keep **Everyone** off and confirm Bob has no direct or group grant. Verify Alice is the owner.
3. Enable the server and complete any required authorization. Enabling fetches its tools and schedules search indexing. If tools are missing, use the deployment's capabilities-refresh flow and inspect the resulting status before continuing.
4. Read the saved server details and tool catalog. Record the actual server ID, downstream tool name, and input schema. `GET /api/v1/servers/{server_id}/tools` reads the cached catalog; it does not itself fetch tools from the downstream server.
5. Wait until Alice can discover the tool through the Registry endpoint. If she cannot, resolve reachability, enablement, capabilities, or indexing first.

**Acceptance criteria:** Alice owns a private, enabled server; its saved catalog contains the synthetic tool; Registry discovery returns that tool for Alice. Registration success alone is insufficient.

## 3. Compare discovery and explicit execution

Connect Alice and Bob to the Registry endpoint in separate sessions. In each, call `discover_servers` with the same query, for example these tool arguments:

```json
{"query": "lookup synthetic ticket", "top_n": 20, "type_list": ["tool"]}
```

From Alice's matching result, copy the exact `server_id` and `tool_name` from the same entity. Inspect `input_schema`; if it is absent, use the saved tool catalog. Do not derive the downstream name from a display label or wrapper name.

Invoke the Registry tool named `execute_tool` in both sessions using the same arguments:

```json
{
  "server_id": "REPLACE_WITH_THE_ACTUAL_SERVER_ID",
  "tool_name": "lookup_synthetic_ticket",
  "arguments": {"ticket_id": "SYNTHETIC-1"}
}
```

Use the actual discovered tool name if testing another approved synthetic endpoint. Give Bob the known test identifiers even if his search returns nothing. This deliberately checks whether execution is denied independently of discovery.

**Acceptance criteria to validate:**

| Check | Alice, owner | Bob, no server grant |
|---|---|---|
| Discover the synthetic tool | The matching tool appears. | The test server's tool does not appear. |
| Call the known tool through `execute_tool` | The synthetic result returns after any required consent. | The result has `isError: true` and an access-denied message; the tool invocation is not forwarded downstream. |
| Read server tools or metadata | The saved details are accessible. | The details are denied. |

Complete any client consent or downstream authorization flow before counting an allowed call as successful, then retry. A consent prompt is not a completed tool call. An empty discovery result alone does not prove an ACL denial: indexing or an ACL-service failure can also prevent results.

Inspect the response body as well as HTTP status. MCP tool errors can arrive in an HTTP 200 response. Use the deployment's available request logs or traces to correlate denied and allowed calls with downstream activity; a UI message alone does not demonstrate that no tool invocation was forwarded.

If clients connect directly through the transparent MCP proxy, repeat the allowed and denied tool calls through each user's configured proxy connection. Use the connection URL supplied by the deployment. Validate server-ACL denial separately from the `execute_tool` result, and record the actual proxy response.

## 4. Grant Bob access, then remove it

1. As Alice, open the server's sharing panel and grant Bob **VIEW**. Save and read back the permissions.
2. In Bob's existing client session, repeat the same `execute_tool` call. Complete any required consent and retry.
3. Remove Bob's direct grant and save. Verify that no group grant or **Everyone** setting still grants him access.
4. Repeat the call in Bob's existing session, then after a fresh sign-in. Record both results and timestamps. If transparent proxy connections are in scope, repeat those calls too.
5. Repeat Alice's call to check that her owner access still works.

**Acceptance criteria:** Bob's call succeeds after the grant and is denied after all applicable grants are removed, without forwarding the denied tool invocation. Alice continues to succeed. Validate and record the actual behavior and timing for both existing and fresh sessions.

`execute_tool` checks the server ACL on every invocation. This does not promise immediate global token or session revocation. The resolver considers direct, group, and public grants using Registry's stored membership information; removing an IdP group membership is a separate operation from removing a server ACL. The [Entra runbook](../runbooks/entra-scope-groups.md) also states that existing tokens are not automatically revoked by configuration changes. An access change cannot recall an already completed call or previously returned data.

## 5. Test server-wide tool disabling separately

Grant Bob VIEW again so both users can access the server. Confirm both can call the harmless tool before testing its disabled state.

As Alice, use the server's tool controls to disable `lookup_synthetic_ticket`. The equivalent API is:

```text
PATCH /api/v1/servers/{server_id}/tools
```

For a dedicated test server whose disabled list was initially empty, the request body is:

```json
{"disabledTools": ["lookup_synthetic_ticket"]}
```

This replaces the **entire** disabled list. Read and retain the previous list first, preserve its entries when adding the test tool, and read back the saved response. Names are checked against the cached tool catalog; unknown names are silently dropped. Updating the list requires `servers-write` scope and the server's owner-only `SHARE` permission.

**Acceptance criteria to validate:**

- `execute_tool` rejects the disabled tool for both users with `isError: true`, before forwarding a tool invocation.
- Normal `discover_servers` results omit it once the search index reflects the change. Index updates are best-effort, so a result may briefly remain visible; execution reads the server document and should still reject it.
- After restoring the original disabled list, both users can call the tool again while their server grants remain in place.

The transparent proxy does **not** enforce `disabledTools`. Do not treat hiding or disabling a tool through the Registry gateway as a capability boundary for every client mode. A requirement such as “Alice can call tool A but Bob cannot, while both access this server” needs a separate authorization design; this server-wide switch does not provide it. See [MCP Tool Enablement API](../design/mcp-tool-enablement-api.md).

## 6. Record results and restore the test setup

For each acceptance check, retain the user alias, deployed version, client and connection mode, server ID, tool name, timestamp, sanitized result, and whether the downstream tool invocation was observed. Include a request or trace ID when available. Observability availability depends on deployment configuration; see [Observability](../features/observability.md).

| Observation | What to check next |
|---|---|
| Bob still succeeds after his direct grant is removed | Confirm the authenticated identity, remaining group/public grants, stored group membership, and deployed version. |
| Search hides the tool but execution succeeds | Identify the connection mode and read back the saved disabled-tool names. |
| `execute_tool` returns “Service temporarily unavailable” | Investigate ACL lookup availability; this is an operational failure, not proof of a policy denial. |
| An allowed call produces consent or authentication prompts | Complete the approved authorization flow and repeat before assessing the result. |
| HTTP status is successful but the tool result is an error | Inspect `isError`, content, and any JSON-RPC error. |

Use synthetic inputs throughout: the gateway implementation logs the MCP request body, including tool arguments. Remove tokens, cookies, personal identifiers, and sensitive payloads from any retained evidence.

Restore the original sharing and disabled-tool settings, disconnect the test clients, and stop the local sample with Ctrl+C when finished. Keep only the sanitized acceptance record. Passing these checks establishes behavior for the tested version, identities, and connection modes; it does not establish downstream record-level authorization or a compliance certification.

## Implementation references

- [Gateway execution and per-call ACL checks](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry/src/registry/mcpgw/tools/server.py#L368-L470)
- [Server ACL resolution](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry/src/registry/services/access_control_service.py#L577-L632) and [shared user/group/public principal resolution](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry-pkgs/src/registry_pkgs/access_control.py)
- [Discovery ACL and enabled-tool filters](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry/src/registry/services/search/service.py#L132-L211)
- [Transparent proxy access checks](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry/src/registry/api/proxy_routes.py#L1024-L1035)
- [Scope definitions and built-in group mappings](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry-pkgs/src/registry_pkgs/scopes.yml)
- [Server enablement, capabilities, and disabled-tool updates](https://github.com/ascending-llc/jarvis-registry/blob/01372e3c80baa1b8833d6851acabb41497b89d2d/registry/src/registry/services/server_service.py#L835-L985)
