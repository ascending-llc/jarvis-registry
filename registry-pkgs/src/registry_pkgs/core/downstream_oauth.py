"""Shared identifiers for the downstream MCP OAuth flow (Layer B: registry-as-AS).

The downstream issuer string is an exact-match security contract: the registry mints and verifies
it, and the auth-server advertises it via RFC 9728 / RFC 8414. It must stay byte-identical across
both workspaces, so the single source of truth lives here in ``registry-pkgs`` where both
``registry`` and ``auth-server`` can import it.
"""

DOWNSTREAM_OAUTH_NAMESPACE = "proxy/server/oauth"

# Redis key prefix for a Layer B authorization code's stashed PKCE / binding context.
DOWNSTREAM_OAUTH_CODE_PREFIX = "downstream_mcp_code:"


def downstream_mcp_issuer(jwt_issuer: str, user_id: str, server_path: str) -> str:
    """Build the issuer identifier for a downstream confirmation token.

    ``jwt_issuer`` is origin-only (no path); the result is
    ``{jwt_issuer}/proxy/server/oauth/{user_id}/{server_path}``.
    """
    return f"{jwt_issuer}/{DOWNSTREAM_OAUTH_NAMESPACE}/{user_id}/{server_path}"


def downstream_mcp_code_key(code: str) -> str:
    """Redis key under which a Layer B authorization code's context is stashed between the
    OAuth callback and the ``/token`` exchange."""
    return f"{DOWNSTREAM_OAUTH_CODE_PREFIX}{code}"


def oauth_error_payload(error: str, error_description: str | None = None) -> dict[str, str]:
    """Build the body of an RFC 6749 §5.2 OAuth error response.

    ``error_description`` is included only when truthy, matching RFC 6749 (the field is OPTIONAL).
    Kept framework-free so auth-server and registry can wrap it in their own response types.
    """
    payload = {"error": error}
    if error_description:
        payload["error_description"] = error_description
    return payload
