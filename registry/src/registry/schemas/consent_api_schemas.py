from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from registry_pkgs.core.client_categories import ClientCategory
from registry_pkgs.core.consent_store import ConsentEntryKind

from ..constants import MAX_CONSENT_CLIENT_ID_LENGTH, MAX_CONSENT_RESOURCE_PATH_LENGTH, MAX_CONSENT_REVOKE_BATCH
from .case_conversion import APIBaseModel

ConsentClientId = Annotated[str, Field(min_length=1, max_length=MAX_CONSENT_CLIENT_ID_LENGTH)]


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


class RevokeSelectedClients(APIBaseModel):
    model_config = ConfigDict(extra="forbid")

    clientIds: list[ConsentClientId] = Field(min_length=1, max_length=MAX_CONSENT_REVOKE_BATCH)


class RevokeAllClients(APIBaseModel):
    model_config = ConfigDict(extra="forbid")

    all: Literal[True]

    @field_validator("all", mode="before")
    @classmethod
    def require_true(cls, value: object) -> object:
        # Literal[True] also accepts the JSON number 1, which must never trigger a revoke-all.
        if value is not True:
            raise ValueError("all must be true")
        return value


class ClientRevokeResult(APIBaseModel):
    clientId: str
    status: Literal["revoked", "not_found", "rejected"]


class ClientRevokeResponse(APIBaseModel):
    results: list[ClientRevokeResult]


class ConsentResourceInput(APIBaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[ConsentEntryKind.MCP_SERVER, ConsentEntryKind.A2A_AGENT]
    path: str = Field(min_length=1, max_length=MAX_CONSENT_RESOURCE_PATH_LENGTH)


class RevokeResourcesRequest(APIBaseModel):
    model_config = ConfigDict(extra="forbid")

    resources: list[ConsentResourceInput] = Field(min_length=1, max_length=MAX_CONSENT_REVOKE_BATCH)


class ResourceRevokeResult(ConsentResourceInput):
    status: Literal["revoked", "not_found"]


class ResourceRevokeResponse(APIBaseModel):
    results: list[ResourceRevokeResult]
