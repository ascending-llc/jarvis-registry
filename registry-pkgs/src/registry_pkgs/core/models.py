from typing import TypedDict


class UserContextDict(TypedDict, total=False):
    user_id: str | None
    username: str
    groups: list[str]
    scopes: list[str]
    auth_method: str
    provider: str
    auth_source: str
