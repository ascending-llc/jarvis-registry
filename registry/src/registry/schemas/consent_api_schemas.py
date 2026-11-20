from typing import Literal

from pydantic import Field

from registry_pkgs.core.client_categories import ClientCategory
from registry_pkgs.core.consent_store import ConsentEntryKind

from .case_conversion import APIBaseModel


class ConsentResourceOutput(APIBaseModel):
    type: Literal[ConsentEntryKind.MCP_SERVER, ConsentEntryKind.A2A_AGENT, ConsentEntryKind.UNKNOWN]
    path: str
    name: str | None


class ConsentClientOutput(APIBaseModel):
    clientId: str
    category: Literal[
        ClientCategory.MCP_DCR,
        ClientCategory.A2A_DCR,
        ClientCategory.CLAUDE_DESKTOP,
        ClientCategory.REGISTRY_CLI,
        ClientCategory.USER_GENERATED,
        ClientCategory.UNKNOWN,
    ]
    displayName: str
    appApproved: bool
    registrationExpired: bool = False
    sharedAcrossDevices: bool = False
    revokesTokens: bool = True
    clientUri: str | None = None
    redirectUris: list[str] = Field(default_factory=list)
    registeredAt: int | None = None
    registrationIp: str | None = None
    softwareName: str | None = None
    softwareVersion: str | None = None
    resources: list[ConsentResourceOutput]


class ConsentClientListResponse(APIBaseModel):
    clients: list[ConsentClientOutput]


class ConsentErrorResponse(APIBaseModel):
    detail: str
