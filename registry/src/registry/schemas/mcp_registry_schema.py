from typing import Any

from pydantic import BaseModel, Field

DEFAULT_SERVER_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-09-29/server.schema.json"

# Official server.json constraints (openapi.yaml component schemas).
NAME_PATTERN = r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$"
DESCRIPTION_MAX_LENGTH = 100


class Input(BaseModel):
    """A single templated variable a client prompts the user to fill (e.g. ``userId``)."""

    description: str | None = None
    isRequired: bool | None = None
    default: str | None = None


class Transport(BaseModel):
    """A remote connection endpoint for a server (``remotes[]`` entry)."""

    type: str
    url: str | None = None
    variables: dict[str, Input] | None = None


class Repository(BaseModel):
    url: str
    source: str


class ServerJSON(BaseModel):
    model_config = {"populate_by_name": True}

    schema_: str = Field(alias="$schema", default=DEFAULT_SERVER_SCHEMA)
    name: str = Field(pattern=NAME_PATTERN, min_length=3, max_length=200)
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    version: str = Field(min_length=1, max_length=255)
    title: str | None = None
    repository: Repository | None = None
    remotes: list[Transport] | None = None
    meta: dict[str, Any] | None = Field(default=None, serialization_alias="_meta")


class ServerResponse(BaseModel):
    model_config = {"populate_by_name": True}
    server: ServerJSON
    meta: dict[str, Any] | None = Field(default=None, serialization_alias="_meta")


class PaginationMetadata(BaseModel):
    count: int
    nextCursor: str | None = None


class ServerListResponse(BaseModel):
    servers: list[ServerResponse]
    metadata: PaginationMetadata


class ErrorModel(BaseModel):
    status: int
    title: str
    detail: str
    instance: str | None = None
