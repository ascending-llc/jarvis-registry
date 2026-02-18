# In-session Re-authorization Design

## Objective

As of 2026-02-18, if both the access token and refresh token of an MCP server managed by our MCP registry expire,
all tool calls to this server via our MCP gateway will fail with 401. Users have to manually login to MCP registry in a browser and re-authorize.
We want to make this flow more user friendly:
- When the MCP gateway receives a 401 from its downstream MCP, it returns a specific response to the MCP client.
- The client then knows to show some "Please re-authorize" button in its "native" UI.
- The user clicks the button, causing an auth URL to open in the browser.
- The user logins, or if session cookies are in place, user is logged in automatically.
- Identity provider redirects to the callback route of our `auth-server`. Tokens for the downstream MCP server is refreshed.
- Ideally, the focus should _automatically_ go back to the user's IDE app from the browser.
  The IDE then _automatically_ retries the last failed tool call, which succeeds this time.
- If the last point cannot be done, bottom line is that the user, after seeing that re-auth succeeds,
  should be able to manually go back to his/her IDE, click some "refresh/retry" button to retry the last tool call.

## The 2025-11-25 MCP spec

The 2025-11-25 MCP spec, a.k.a "Anniversary Release", introduces several important changes to the MCP spec.
On the other hand, `FastMCP` v2.14.5 tries its best to be compatible to the 2025-11-25 spec,
but the ways of doing many things are still cumbersome. Eventually we need to migrate to `FastMCP` v2,
which makes many things much easier to code.

The 2025-11-25 MCP spec introduced the following important changes.

- Unified Session Model

  We need to use `session_id` when communicating with any client.

- URL Mode Elicitation

  This is an MCP client capability that is specifically designed to solve exactly our in-session re-auth problem.
  This is the future-proof approach that we should implement, but we should (optionally?) provide a fallback for old
  clients that do not have the Elicitation capability.

  Reference: [MCP SEP-1036](https://modelcontextprotocol.io/community/seps/1036-url-mode-elicitation-for-secure-out-of-band-intera#url-elicitation-required-error)

- SSE Polling via Server-side Disconnect

  When our `auth-server` completes the re-auth, it should notify `mcpgw` (not `registry`) that
  "I'm done for this `elicitation_id`". `mcpgw` then sends a `notifications/elicitation/complete` SSE to the client,
  this is how the MCP client knows that the re-auth is complete and it can automatically retry the last tool call.
  However, this is an optional feature for now because there are easier ways to notify mainstream agents (Claude, Cursor, VS Code),
  and users of other agents can manually re-focus on their IDE and click some "Retry" button.

- Upgrade Sampling Requests

  This is a MCP server feature that feels like our initial idea of "exposing re-auth as a tool of `mcpgw` and tell LLM to use it",
  but this is not a good fallback, because if a client doesn't support Elicitation, it probably doesn't support
  upgraded sampling requests either. The best fallback approach is to make `mcpgw` return a tool call result like below directly,
  without implementing and asking LLM to use another tool.

  ```python
  from mcp.types import CallToolResult, TextContent

  # Inside your tool...
  if not client_supports_url_elicitation:
      return CallToolResult(
          content=[
              # 1. The Human instruction (Primary)
              TextContent(
                  type="text",
                  text=(
                      "🔑 **Authorization Required**\n"
                      f"To proceed, please log in here: {auth_url}\n\n"
                      "Once you've finished, tell me 'I'm logged in' to retry."
                  )
              ),
              # 2. The Machine hint (Programmatic fallback)
              # This is "StructuredContent" but formatted as a 'meta' hint
              # that many 2026 IDEs use to render native buttons.
          ],
          isError=True,
          _meta={
              "auth_required": {
                  "url": auth_url,
                  "elicitation_id": elicitation_id,
                  "type": "oauth2"
              }
          }
      )
  ```

- Background Task

  This is not relevant to this re-auth problem, but is likely something that we must migrate our codebase about
  once our downstream MCP servers start to make their tool calls asynchronous (i.e. tasks).

## High-level flow

- In `mcpgw`, once the `call_registry_api` call returns a 401 _due to invalid token_, we start the following flow.

- Check the `request_context` to see if the MCP client supports URL mode elicitation.

  ```python
  from fastmcp import Context

  def supports_url_elicition(ctx: Context) -> bool:
      # 1. Access the underlying request context
      # This contains the 'initialize' result from the start of the session
      req_ctx = ctx.request_context

      # 2. Safely traverse the capabilities dictionary
      capabilities = getattr(req_ctx.session, "client_capabilities", {})

      # 3. Check for elicitation -> url support
      elicitation_caps = capabilities.get("elicitation", {})
      return "url" in elicitation_caps
  ```

- If elicitation is supported, simply raise an `UrlElicitationRequiredError`.
  Note that the auth URL should have a `state` parameter that at least contains the `elicitation_id`,
  which is needed in the success page returned by the callback route of our `auth-server`.
  The `elicitation_id` is simply a unique ID (e.g. UUID) that identifies the elicitation.
  Our `mcpgw` stores the mapping from `elicitation_id` to `session_id` in our Redis, so that we know which client the `elicitation_id` is for.
  The MCP client maps `elicitation_id` to the last failed tool call, so it knows what to retry when re-auth completes.

  ```python
  from mcp.shared.exceptions import UrlElicitationRequiredError
  from mcp.types import ElicitRequestURLParams

  # In your tool:
  raise UrlElicitationRequiredError([
      ElicitRequestURLParams(
          mode="url",
          message="Please authorize with the downstream service.",
          url=f"https://auth.downstream.com/oauth?state={elicitation_id}",
          elicitation_id=elicitation_id
      )
  ])
  ```

- The MCP client and Agentic IDE now know to ask user to open the auth URL in browser.

- Once our `auth-server` succeeds with the OAuth callback, it should return a "Universal Success Page" with the following simple JS script embedded.
  The script uses some specific protocols to communicate with Claude, Cursor and VS Code, letting them know that the elicitation is done
  and they are free to retry. For other less mainstream agent, the user might have to manually refocus on the app and click some "Retry" button.

  ```javascript
  <script>
    // The `auth-server` needs `elicitation_id` to fill in the placeholder `{{id}}`.
    // That's why we need `elicitation_id` in the `state` parameter.
    const eid = "{{id}}";
    // 1. Signal any IDE that opened this as a popup/iframe (VS Code/Cursor)
    if (window.opener) window.opener.postMessage({ type: "mcp:elicitation_complete", elicitation_id: eid }, "*");

    // 2. Trigger Deep Links for Desktop Apps (Claude/Cursor/VS Code)
    const protocols = ["cursor", "claude-desktop", "vscode"];
    protocols.forEach(p => {
      const iframe = document.createElement("iframe");
      iframe.style.display = "none";
      iframe.src = `${p}://mcp-auth-complete?elicitation_id=${eid}`;
      document.body.appendChild(iframe);
    });
  </script>

  <div style="text-align: center; font-family: sans-serif; margin-top: 50px;">
    <h2>Authorization Successful</h2>
    <p>You can now close this window and return to your AI Agent.</p>
    <button onclick="window.close()" style="padding: 10px 20px; cursor: pointer;">Close Tab</button>
  </div>
  ```

- Later, we can implement the `notifications/elicitation/complete` SSE properly, so that all elicitation-capable clients
  can automatically refocus and retry. Maybe we should do this after migrating to FastMCP v3.
  This SSE implementation requires our using the pub-sub feature of Redis, so that `auth-server` can tell `mcpgw`
  that a certain elicitation is done. If also requires saving the `elicitation_id` to `session_id` mapping in Redis,
  so that `mcpgw` can look up the `session_id` for the `elicitation_id` before sending the SSE.
  This also requires starting the MCP server in `mcpgw` via `asyncio` and `mcp.run_async`, because before the MCP server starts up,
  we need to create an `asyncio` task that listens to a Redis channel for "elicitation-complete" events from the `auth-server`.

  ```python
  import asyncio
  from fastmcp import FastMCP

  mcp = FastMCP("mcpgw")

  async def redis_listener():
      """Listens for 'elicitation-complete' signals from the auth-server."""
      print("Post-auth listener started...")
      # ... your Redis Pub/Sub logic ...
      # When a message arrives, use mcp.notify_elicitation_complete(id)

  async def main():
      # 1. Start the listener in the background
      # This 'backgrounds' the task within the current event loop
      asyncio.create_task(redis_listener())

      # 2. Run the MCP server using Streamable-HTTP
      # This will now run alongside the redis_listener
      print("Starting MCP Gateway on port 8000...")
      await mcp.run_async(transport="http", host="0.0.0.0", port=8000)

  if __name__ == "__main__":
      # In 2026, we always use the high-level asyncio.run for the entry point
      asyncio.run(main())
  ```

- If the user is not elicitation-capable, the tool call to `mcpgw` just returns the following response.
  This basically tells the LLM that "hey, the last tool call was not successful. Re-auth using the URL and retry".
  This is pretty much the same as what the `mcp-google_workspace` project does, except that we use `CallTooResult`
  to provide more metadata for our result.

  ```python
  from mcp.types import CallToolResult, TextContent

  # Inside your tool...
  if not client_supports_url_elicitation:
      return CallToolResult(
          content=[
              # 1. The Human instruction (Primary)
              TextContent(
                  type="text",
                  text=(
                      "🔑 **Authorization Required**\n"
                      f"To proceed, please log in here: {auth_url}\n\n"
                      "Once you've finished, tell me 'I'm logged in' to retry."
                  )
              ),
              # 2. The Machine hint (Programmatic fallback)
              # This is "StructuredContent" but formatted as a 'meta' hint
              # that many 2026 IDEs use to render native buttons.
          ],
          isError=True,
          _meta={
              "auth_required": {
                  "url": auth_url,
                  "elicitation_id": elicitation_id,
                  "type": "oauth2"
              }
          }
      )
  ```

## Summary

We use a phased approach.

- First use "URL mode elicitation" and "Universal Success Page" so that at least mainstream agents such as Claude, Cursor and VS Code all work well.

- Then add the `notifications/elicitation/complete` SSE implementation later, which is the recommended approach by the 2025-11-25 MCP spec,
  and we have a plan for SSE anyway.
