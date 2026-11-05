"""spike server: runs on mcp v2; one server speaks both specs.

Two tools: hello (plain result), authorize (URL elicitation). authorize picks one
of THREE paths, mirroring the real Registry's _build_url_elicitation_result:
  - client has no URL-elicitation capability -> plain CallToolResult with the URL
    as text (the fallback, works with any client).
  - 2026-07-28 client: return InputRequiredResult; the client answers by retrying
    with request_state.
  - 2025-11-25 client: call ctx.elicit_url() over the back-channel (calling it on a
    2026-07-28 connection raises NoBackChannelError).
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestURLParams,
    InputRequiredResult,
    TextContent,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

# Fake auth URL. In the real Registry this is the consent/OAuth URL (confused-deputy fix).
AUTH_URL = "https://auth.example.com/authorize?state=abc123"

_ELICIT_KEY = "authorize"  # key in inputRequests on modern (server-chosen)
_ELICITATION_ID = "elicit-001"  # legacy only; stripped from the modern wire

mcp = MCPServer("as-1962-url-elicitation")


@mcp.tool()
async def hello(name: str = "world") -> str:
    """Plain result, no elicitation."""
    return f"hello {name}"


def _supports_url_elicitation(ctx: Context) -> bool:
    """Whether the client declared the URL-elicitation capability."""
    caps = ctx.client_capabilities
    return bool(caps and caps.elicitation and caps.elicitation.url)


@mcp.tool()
async def authorize(ctx: Context):
    # Return type left unannotated on purpose: InputRequiredResult is rejected as a
    # declared return type at registration, but returning it at runtime is fine.
    print(
        f"[server] authorize: protocol={ctx.protocol_version} url_elicitation={_supports_url_elicitation(ctx)}",
        file=sys.stderr,
    )

    # Fallback: a client without URL-elicitation support just gets the URL as text.
    if not _supports_url_elicitation(ctx):
        return CallToolResult(
            content=[TextContent(text=f"Open this URL to authorize, then retry: {AUTH_URL}")],
            is_error=True,
        )

    if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
        # 2026-07-28: no back-channel; deliver the elicitation via the return value.
        if ctx.input_responses:  # 2nd round: the client answered
            answer = ctx.input_responses.get(_ELICIT_KEY)
            action = answer.action if answer else "cancel"  # missing key -> treat as cancel
            if action != "accept":
                return f"Authorization {action}; no work performed."
            return f"Authorization {action}; proceeding with the real work."
        # 1st round: request_state is the opaque continuation token the client
        # echoes back on retry (the SDK encrypts it).
        return InputRequiredResult(
            input_requests={
                _ELICIT_KEY: ElicitRequest(
                    params=ElicitRequestURLParams(
                        message="Open the URL to authorize, then retry this tool.",
                        url=AUTH_URL,
                        elicitation_id=_ELICITATION_ID,  # stripped from the modern wire
                    )
                )
            },
            request_state="as1962-authorize-v1",
        )

    # 2025-11-25: server-initiated elicitation over the back-channel.
    result = await ctx.elicit_url(
        message="Open the URL to authorize.",
        url=AUTH_URL,
        elicitation_id=_ELICITATION_ID,
    )
    if result.action != "accept":
        return f"Authorization {result.action}; no work performed."
    return f"Authorization {result.action}; proceeding with the real work."


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
        json_response=True,
        stateless_http=True,
    )
