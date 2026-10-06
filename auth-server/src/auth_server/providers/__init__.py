"""Authentication provider package for MCP Gateway Registry."""

from .base import AuthProvider
from .entra import EntraIdProvider
from .factory import get_auth_provider

__all__ = [
    "AuthProvider",
    "EntraIdProvider",
    "get_auth_provider",
]
