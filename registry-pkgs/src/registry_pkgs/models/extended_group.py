from enum import StrEnum

from pydantic import Field

from ._generated import Group


class ExtendedGroupSource(StrEnum):
    LOCAL = "local"
    ENTRA = "entra"
    GOOGLE = "google"


class ExtendedGroup(Group):
    source: ExtendedGroupSource = Field(default=ExtendedGroupSource.LOCAL)
