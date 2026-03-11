# In-session Re-authorization Design

## Objective

As of 2026-02-18, if both the access token and refresh token of an MCP server managed by our MCP registry expire,
all tool calls to this server via our MCP gateway will fail with 401. Users have to manually login to MCP registry in a browser and re-authorize.
We want to make this flow more user friendly:
- When the MCP gateway receives a 401 from its downstream MCP, it returns a specific response to the MCP client.
- The client then knows to show some "Please re-authorize" button in its "native" UI.
- The user clicks the button, causing an auth URL to open in the browser.
- The user logins, or if session cookies are in place, user is logged in automatically.
- Identity provider redirects to the callback route of our `registry`. Tokens for the downstream MCP server is refreshed.
- Ideally, the focus should _automatically_ go back to the user's IDE app from the browser.
  The IDE then _automatically_ retries the last failed tool call, which succeeds this time.
- If the last point cannot be done, bottom line is that the user, after seeing that re-auth succeeds,
  should be able to manually go back to his/her IDE, click some "refresh/retry" button to retry the last tool call.

## Key Decisions

The following are two key decisions in how we choose to implement this in-session-re-auth workflow.

- On detecting token for downstream MCP server being invalid, `mcpgw` should raise an `UrlElicitationRequiredError`
  to end the current tool call (to `mcpgw`) and to signal to the client that "you should re-auth and then retry".

- When `registry` successfully processes the OAuth callback, it should signal `mcpgw` to send
  the `notifications/elicitation/complete` SSE to the MCP client.

## The 2025-11-25 MCP spec

The 2025-11-25 MCP spec, a.k.a "Anniversary Release", introduces several important changes to the MCP spec.
On the other hand, `FastMCP` v2.14.5 tries its best to be compatible to the 2025-11-25 spec,
but the ways of doing many things are still cumbersome. **We must migrate to FastMCP v3 for this change**.
Reasons will be explained below.

The 2025-11-25 MCP spec introduced the following important changes.

- Unified Session Model

  If an MCP server is stateful, we must use `session_id` to communicate with any client.
  Because we decide to use SSE, our MCP gateway server is stateful – FastMCP needs to manage the state of
  which SSEs have been sent to which client and which events haven't been sent.
  **Therefore, we can no longer use `stateless_http=True` for our MCP server after the change.**

- URL Mode Elicitation

  This is an MCP client capability that is specifically designed to solve exactly our in-session re-auth problem.
  This is the future-proof approach that we should implement, but we should (optionally?) provide a fallback for old
  clients that do not have the Elicitation capability.

  Reference: [MCP SEP-1036](https://modelcontextprotocol.io/community/seps/1036-url-mode-elicitation-for-secure-out-of-band-intera#url-elicitation-required-error)

- SSE Polling via Server-side Disconnect

  When our `registry` completes the re-auth, it should notify `mcpgw` that "I'm done for this `elicitation_id`".
  `mcpgw` then sends a `notifications/elicitation/complete` SSE to the client,
  this is how the MCP client knows that the re-auth is complete and it can automatically retry the last tool call.
  **Implementing this SSE notification requires us to use FastMCP v3 (specifically, `fastmcp[tasks]`), which has Redis as a hard requirement.**

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
  **In fact this task feature is less about freeing agent from waiting but more about fixing the "distributed system problem" of MCP.**
  More details in the next section.

## The "distributed system problem" of MCP

Before the 2025-11-25 MCP spec and `fastmcp<=2.14.4`, MCP servers have a "distributed system problem" –
they either have to be stateless or have to be a single server instance (i.e. not LBC) if stateful. Consider the following scenario.

1. A stateful (supporting mid-request elicitation/sampling or SSE) MCP server is deployed as multiple pods in EKS behind an ALB.
2. Client makes a tool call, and this POST request reaches Pod A.
3. Client opens an SSE connection, and this GET request reaches Pod B.
4. Midway in the initial tool call request, Pod A decides, "I need to make a mid-request elicitation/sampling to get more inputs",
   so it needs to send an SSE to the client. Before the elicitation/sampling is answered by client, this tool call on Pod A hangs in the middle indefinitely.
   However, it's Pod B that holds the SSE GET connection.
5. The combination of FastMCP v3 and Redis is actually capable of making Pod B send the SSE for Pod A.
6. Client answers the elicitation/sampling with another POST request, which reaches Pod C.
   Even though elicitation/sampling is answered, there is no way for Pod C to tell Pod A,
   "Hey, the elicitation/sampling result is here. Continue with your tool call execution and respond to client".
   **To solve this problem, not only are FastMCP v3 and Redis required, but all tool calls must also be marked as tasks.**

**Note**: The problem above can NOT be solved by turning on "sticky session" on the ALB.
See [here](https://gofastmcp.com/deployment/http#without-stateless-mode) for the reason.

In summary, with a **stateful MCP server behind an LBC**, there is a full solution, but it requires:
1. FastMCP v3.
2. Redis.
3. All tools must be marked with `@mcp.tool(task=True)`.

1 and 2 alone can solve the "Pod B sends SSE for Pod A" problem. 3 is also needed to solve the mid-request elicitation/sampling problem.

**However, our MCP gateway doesn't have the mid-request elicitation/sampling problem,** because our elicitation is not mid-request –
it ends the current request and asks the client to re-auth and then retry. Therefore, we don't have to mark all of our tools as task right now,
but we do need FastMCP v3 and Redis.

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
  Note that the `redirect_uri` portion of the auth URL should have a `state` parameter that at least contains
  both the `elicitation_id` and the `sesssion_id` of the client,
  so that our `registry` service, upon receiving such a callback, knows these two parameters too.
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

- Once our `registry` successfully responds to the OAuth callback, it should notify `mcpgw` that this `elicitation_id` is done.
  Then `mcpgw` sends the `notifications/elicitation/complete` SSE to the client. This way, any MCP client properly
  implemented according to the 2025-11-25 MCP spec will automatically retry the last tool call without needing the user
  to click any "Retry" button. FastMCP v3 provides an existing solution to this across-service signaling problem,
  but `registry` must now rely on `fastmcp[tasks]>=3.0.0`.

  The key of this "registry-to-mcpgw" signaling is in the [`fastmcp.server.tasks.notifications` package](https://gofastmcp.com/python-sdk/fastmcp-server-tasks-notifications#notifications).

  On the `mcpgw` side, we must make sure, whenever a client session starts, we subscribe to a Redis List for all SSEs
  that should be but haven't been sent to this client.

  ```python
  from fastmcp import FastMCP
  from fastmcp.server.tasks.docket import Docket
  from fastmcp.server.tasks.notifications import ensure_subscriber_running
  from key_value.aio.stores.redis import RedisStore

  REDIS_URL = "redis://redis:6379/0"

  # Initialize Docket with your Redis pod
  docket = Docket(redis_url=REDIS_URL)

  # Use the same store for both state AND the event bus
  redis_storage = RedisStore(uri=REDIS_URL)

  mcp = FastMCP(
      "JarvisRegistry",
      # Use Redis as centralized state storage for all pods
      session_state_store=redis_storage,
      # This ensures Pod A can signal Pod B to send SSE via Redis
      event_bus="redis"
      # This ensures `registry` can publish notifications to `mcpgw` via Redis List
      docket=docket
  )

  @mcp.on_session_start
  async def on_start(session_id, session):
      # This starts a background asyncio task on THIS pod
      # that listens to Redis for notifications for THIS session.
      # ensure_subscriber_running is idempotent, so it's safe even if this `on_start` hook fires
      # multiple times for the same client session on multiple pods.
      ensure_subscriber_running(session_id, session, mcp.docket, mcp)
  ```

  On the `registry` side, just add the following logic to the callback route handler.
  Note that `session_id` and `elicitation_id` both come from the OAuth `state` parameter.

  ```python
  from fastmcp.server.tasks.notifications import push_notification
  from fastmcp.server.tasks.docket import Docket

  # Must connect to the same Redis instance as `mcpgw`
  docket = Docket(redis_url="redis://redis:6379/0")

  # Call the following function in the callback route (`/gateway/redirect) handler
  async def publish_sse_notification(session_id: str, elicitation_id: str):
      # Build the MCP-compliant notification
      notification = {
          "method": "notifications/elicitation/complete",
          "params": {
              "elicitationId": elicitation_id,
              "status": "success"
          }
      }

      # Push to the session's specific queue in Redis
      push_notification(session_id, notification, docket)
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

## Background Task

Background Task is a recent SEP to the MCP spec, and also a new feature in FastMCP v3.

In terms of MCP spec, task is in fact an augmentation to existing resource, prompt and tool calls.
From [here](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks#creating-tasks)
we can see that background task it about calling resource, prompt and tool "as task" –
if the `params.task` field exist in the JSON-RPC POST request, it is a task request.
Otherwise it's an old-school resource/prompt/tool call.

In terms of FastMCP v3, it provides the `@mcp.tool(task=True)` decorator.
If a function is decorated with this, the tool can be called in two ways by the client.
If client uses `params.task` in the JSON-RPC request body, FastMCP executes the function as a background task via Docket.
If the client doesn't use `params.task`, FastMCP executes the function synchronously as before,
i.e. this function execution is not done via Docket.

Background task is introduced mainly to solve the "distributed system problem" of MCP –
if an MCP server sits behind an LBC, **only tool calls that are background task can handle mid-request elicitation and sampling**.
If a synchronous tool can try to do mid-request elicitation/sampling, it will be broken by the LBC because the client's
answer to elicitation/sampling is sent to the server via a separate POST request, which may not reach the same pod
that is processing the original request because of the LBC. Background tasks are executed by Docket using
a "kill-retry-skip-over" strategy, and that's why it's not broken by LBC.

**Summary**:

If we want to proxy background tasks that perform mid-request elicitation and/or sampling,
we have to make all tools of `mcpgw` task too, because we run behind an LBC.

If we only proxy non-task tool calls to downstream MCPs, `mcpgw`'s tools should be non-task too,
because it is way easier to implement this way.

## Elicitation and SSE

There are two ways of implementing elicitation.

The first one is our "stateless" URL mode elicitation, where we simply raise an `UrlElicitationRequiredError`,
which completes the current tool call, and ask client to retry after re-auth.
In this case, the URL mode elicitation is in the JSON-RPC response itself.

The second is stateful mid-request elicitation, for example [here](https://gofastmcp.com/servers/elicitation#overview).
In this example, the order of execution is:
- Client makes initial tool call (a POST request), and `collect_user_info` starts executing.
- In the middle of the function, the line ` result = await ctx.elicit(...)` causes the server to send the elicitation
  to the client as an SSE message over the SSE GET connection between them.
- The client is supposed to answer the elicitation with another POST request to the server.
  Before this other POST request comes in, the `collect_user_info` function simply hangs at the `await ctx.elicit(...)` statement.
- When the other POST is received by the server, `collect_user_info` resumes execution and finally responds to the initial POST request.

This is why mid-request elicitation will be broken by the LBC – when the 2nd request lands on a different pod from the 1st.
The only way to solve this right now is to use background task for all tool calls that need mid-request elicitation
(side note: we don't).

## Sampling and SSE

Sampling is similar to elicitation. The only difference is that sampling must be mid-request.
Otherwise it's not sampling, but just an ordinary tool call response that tells LLM to use another tool (e.g. our fallback plan).

## What SSE features do we want to implement for `mcpgw`?

The current SSE we are planning to add happens to be simple – whenever `registry` finishes processing an OAuth callback,
it notifies (via Redis) the `mcpgw` pod that holds the current client session to send the SSE notification.
The design above should work.

If we want to forward SSE messages from downstream MCP servers to clients of `mcpgw`, this is more complex.
For example, at any moment, with every "client and downstream MCP" combination,
only one `registry` pod (because it's `registry` that actually calls downstream MCP) can hold the SSE GET connection.
Guaranteeing this "exactly one pod" is possible by using Redis, but not straightforward.

In addition, currently `registry` uses `httpx` to call downstream MCPs. When forwarding SSE messages,
`registry` should use the high-level FastMCP client code to call downstream.
Otherwise, because SSE messages come as different chunks of the same response to the SSE GET request,
we have to implement low-level SSE message parsing by ourselves unless we use FastMCP code.
Additionally, FastMCP v3 provides helpful utility functions such as `fastmcp.server.tasks.notifications.push_notification`.
