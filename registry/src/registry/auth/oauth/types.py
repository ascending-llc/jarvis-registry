from enum import StrEnum
from typing import NotRequired, TypedDict


class ClientBranding(StrEnum):
    VSCODE = "vscode"
    CLAUDE = "claude"
    CURSOR = "cursor"
    UNRECOGNIZED = "unrecognized"


class StateMetadata(TypedDict, total=False):
    client_branding: ClientBranding
    elicitation_id: str


class OAuthFlowState(TypedDict):
    flow_id: str
    security_token: str
    meta: NotRequired[StateMetadata]
