from ...core.config import settings


def get_default_redirect_uri(path: str) -> str:
    """
    Get default redirect URI for OAuth callback.

    Constructs redirect URI using registry_url from settings and the provided MCP server path.
    Automatically prepends /api/v1/mcp to the path.

    Args:
        path: The MCP server path (e.g., "/notion", "/brave")

    Returns:
        Redirect URI string with /oauth/callback appended

    Examples:
        get_default_redirect_uri("/notion") → http://localhost:7860/api/v1/mcp/notion/oauth/callback
        get_default_redirect_uri("brave")   → http://localhost:7860/api/v1/mcp/brave/oauth/callback
    """
    base_url = settings.registry_client_url
    # Ensure path starts with / and doesn't end with /
    normalized_path = path.strip("/")
    return f"{base_url}/api/v1/mcp/{normalized_path}/oauth/callback"


def parse_scope(scope: str | list[str] | None, default: list[str] = None) -> list[str]:
    """
    Parse OAuth scope field into a list of scopes.

    Args:
        scope: Can be a string (space or comma separated), a list of strings, or None
        default: Default value to return if scope is None

    Returns:
        List of scope strings
    """
    if scope is None:
        return default if default is not None else []

    if isinstance(scope, list):
        return scope

    if isinstance(scope, str):
        if "," in scope:
            return [s.strip() for s in scope.split(",") if s.strip()]
        else:
            return [s.strip() for s in scope.split() if s.strip()]

    return []


def scope_to_string(scope: str | list[str] | None) -> str:
    """
    Convert scope to a space-separated string.

    Args:
        scope: Can be a string, a list of strings, or None

    Returns:
        Space-separated string of scopes
    """
    if scope is None:
        return ""

    if isinstance(scope, list):
        return " ".join(scope)

    if isinstance(scope, str):
        return scope

    return ""
