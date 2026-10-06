"""Shared auth-provider mock for auth-server route tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

_DEFAULT_USER_INFO: dict[str, Any] = {
    "username": "testuser",
    "email": "test@example.com",
    "name": "Test User",
    "id": "user-123",
    "groups": [],
}


def _mock_entra_provider(user_info: dict[str, Any] | None = None) -> MagicMock:
    """Return an Entra-like provider whose ``get_user_info`` resolves to ``user_info``.

    Install it with a zero-argument lambda (``dependency_overrides[get_auth_provider] = lambda:
    _mock_entra_provider(...)``); assigning the function itself would make FastAPI treat
    ``user_info`` as a request-body dependency.
    """
    provider = MagicMock()
    provider.get_user_info = AsyncMock(return_value=dict(_DEFAULT_USER_INFO) if user_info is None else user_info)
    return provider
