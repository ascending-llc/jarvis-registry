"""Client-ID categorization: maps every client_id to a ClientCategory and a builtin max scope set.

Two kinds of category exist:
- Prefixed collections (MCP_DCR, A2A_DCR): dynamically DCR-registered, client_id stored in Redis
  under a fixed prefix (see auth-server's oauth_flow.py).
- Single-instance client_ids (REGISTRY_APP, HEADLESS_AGENT, USER_GENERATED, REGISTRY_CLI): always
  special-cased, never stored in or queried from Redis.

Resolution order in ``resolve_client_category`` matters: exact-match single-instance ids are checked
before prefix matches, and an unrecognized client_id resolves to UNKNOWN with an *empty* ceiling
(fail closed) rather than falling back to any other category's scopes.
"""

from enum import StrEnum

from .config import INTERACTIVE_TOKEN_CLIENT_ID, JwtTokenConfig

MCP_CLIENT_ID_PREFIX = "mcp-client-"
A2A_CLIENT_ID_PREFIX = "a2a-client-"
REGISTRY_CLI_CLIENT_ID = "jarvis-registry-cli"

_PROXY_OPS_SCOPES_EXCLUDED_FROM_REGISTRY_APP = frozenset({"mcp-proxy-ops", "a2a-proxy-ops", "skills-read"})

_FIXED_CATEGORY_CEILINGS: dict["ClientCategory", frozenset[str]] = {}


class ClientCategory(StrEnum):
    MCP_DCR = "mcp_dcr"
    A2A_DCR = "a2a_dcr"
    REGISTRY_APP = "registry_app"
    HEADLESS_AGENT = "headless_agent"
    USER_GENERATED = "user_generated"
    REGISTRY_CLI = "registry_cli"
    UNKNOWN = "unknown"


_FIXED_CATEGORY_CEILINGS.update(
    {
        ClientCategory.MCP_DCR: frozenset({"mcp-proxy-ops"}),
        ClientCategory.A2A_DCR: frozenset({"a2a-proxy-ops"}),
        ClientCategory.HEADLESS_AGENT: frozenset({"mcp-proxy-ops", "a2a-proxy-ops"}),
        ClientCategory.REGISTRY_CLI: frozenset({"skills-read"}),
        ClientCategory.UNKNOWN: frozenset(),
    }
)


def resolve_client_category(client_id: str, config: JwtTokenConfig) -> ClientCategory:
    """Resolve a client_id to its category. Exact-match single-instance ids first, then prefixes."""
    if client_id == config.registry_client_id:
        return ClientCategory.REGISTRY_APP
    if client_id == config.headless_agent_client_id:
        return ClientCategory.HEADLESS_AGENT
    if client_id == INTERACTIVE_TOKEN_CLIENT_ID:
        return ClientCategory.USER_GENERATED
    if client_id == REGISTRY_CLI_CLIENT_ID:
        return ClientCategory.REGISTRY_CLI
    if client_id.startswith(MCP_CLIENT_ID_PREFIX):
        return ClientCategory.MCP_DCR
    if client_id.startswith(A2A_CLIENT_ID_PREFIX):
        return ClientCategory.A2A_DCR
    return ClientCategory.UNKNOWN


def get_builtin_max_scopes(category: ClientCategory, all_scopes: frozenset[str]) -> frozenset[str]:
    """Return the builtin max scope set for a category.

    REGISTRY_APP and USER_GENERATED are defined against ``all_scopes`` (every scope in scopes.yml)
    rather than an enumerated allowlist, so a new scope added to scopes.yml is automatically
    available to them without a code change here.
    """
    if category is ClientCategory.REGISTRY_APP:
        return all_scopes - _PROXY_OPS_SCOPES_EXCLUDED_FROM_REGISTRY_APP
    if category is ClientCategory.USER_GENERATED:
        return all_scopes
    return _FIXED_CATEGORY_CEILINGS.get(category, frozenset())


def resolve_granted_scopes(
    client_id: str,
    requested_scopes: str | list[str],
    config: JwtTokenConfig,
) -> list[str]:
    """Intersect requested scopes against client_id's category ceiling. Order-preserving."""
    scopes = requested_scopes.split() if isinstance(requested_scopes, str) else requested_scopes
    ceiling = get_builtin_max_scopes(resolve_client_category(client_id, config), config.all_scopes)
    return [scope for scope in scopes if scope in ceiling]
