from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from beanie import Document
from pydantic import BaseModel, Field, StringConstraints

EmailValue = Annotated[str, StringConstraints(pattern=r"\S+@\S+\.\S+", to_lower=True)]
LowercaseStr = Annotated[str, StringConstraints(to_lower=True)]


class SystemRoles(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"


class Personalization(BaseModel):
    memories: bool = True


class Favorite(BaseModel):
    agentId: str | None = None
    model: str | None = None
    endpoint: str | None = None
    spec: str | None = None


class User(Document):
    name: str | None = None
    username: LowercaseStr = ""
    email: EmailValue
    emailVerified: bool = False
    avatar: str | None = None
    provider: str = "local"
    role: SystemRoles = SystemRoles.USER
    googleId: str | None = None
    facebookId: str | None = None
    openidId: str | None = None
    samlId: str | None = None
    ldapId: str | None = None
    githubId: str | None = None
    discordId: str | None = None
    appleId: str | None = None
    plugins: list[Any] = Field(default_factory=list)
    twoFactorEnabled: bool = False
    expiresAt: datetime | None = None
    termsAccepted: bool = False
    personalization: Personalization = Field(default_factory=Personalization)
    favorites: list[Favorite] = Field(default_factory=list)
    idOnTheSource: str | None = None
    tenantId: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Settings:
        name = "users"
        keep_nulls = False
        use_state_management = True
