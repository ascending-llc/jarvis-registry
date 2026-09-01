from enum import StrEnum
from typing import NotRequired, TypedDict


class ClientBranding(StrEnum):
    VSCODE = "vscode"
    CLAUDE = "claude"
    CURSOR = "cursor"
    UNRECOGNIZED = "unrecognized"


class StateMetadata(TypedDict):
    # The brand of the AI agent connecting to our MCP server. In case it's VS Code, Claude Desktop or Cursor,
    # we use browser deep-link to redirect user back to the AI app window from our OAuth callback page.
    client_branding: ClientBranding

    # Whether we should send an `elicitation/complete` notification to the MCP client. If client connects to our
    # MCP gateway at `/proxy/mcpgw/mcp`, yes. If client connects to a specific proxied downstream MCP
    # via the dynamic catch-call route `/proxy/server/{full_path:path}`, no.
    notify_elicitation_complete: bool

    # Unique UUID4 for the elicitation.
    elicitation_id: NotRequired[str]


class OAuthFlowState(TypedDict):
    flow_id: str
    security_token: str
    meta: NotRequired[StateMetadata]
