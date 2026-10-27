from enum import StrEnum
from typing import NotRequired, TypedDict


class ClientBranding(StrEnum):
    VSCODE = "vscode"
    CURSOR = "cursor"
    UNRECOGNIZED = "unrecognized"


class StateMetadata(TypedDict):
    # The brand of the AI agent connecting to our MCP server. In case it's VS Code or Cursor,
    # we use browser deep-link to redirect user back to the AI app window from our OAuth callback page.
    client_branding: ClientBranding

    # Unique UUID4 for the elicitation.
    elicitation_id: NotRequired[str]


class OAuthFlowState(TypedDict):
    flow_id: str
    security_token: str
    meta: NotRequired[StateMetadata]
