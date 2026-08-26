"""Shared, framework-agnostic types used across ``registry``, ``auth-server``, and ``registry_pkgs``."""

from __future__ import annotations

from typing import TypedDict


class UserContextDict(TypedDict):
    """
    UserContextDict is the type of the dictionary set as the Request.state.user attribute for each incoming request
    **to a non-public route** by the UnifiedAuthMiddlware.
    If a FastAPI path operation function needs to access this UserContextDict information, it can retrieve this dictionary
    by asking for the `user_context: CurrentUser` dependency injection (``registry.auth.dependencies.CurrentUser``).
    If an MCP tool/resource/prompt handler function needs it, it can retrieve this dictionary by asking for the
    `ctx: Context` dependency injection (`mcp.server.fastmcp.Context`) and then accessing
    `ctx.request_context.request.state.user`.
    """

    # From the "user_id" field of the JWT claim.
    user_id: str | None

    # From the "client_id" claim of the JWT.
    client_id: str

    # From the "sub" field of the JWT claim.
    username: str | None

    # From the "groups" field of the JWT claim.
    groups: list[str]

    # Converted from the "scope" field of the JWT claim, or mapped from the "groups" field if "scope" doesn't exist.
    scopes: list[str]

    # "jwt" if the JWT token comes from the Authorization header. "traditional" if from browser cookie.
    auth_method: str

    # "jwt" if the JWT token comes from the Authorization header. "local" if from browser cookie.
    provider: str

    # "jwt_auth" if the JWT token comes from the Authorization header. "jwt_session_auth" if from browser cookie.
    auth_source: str
