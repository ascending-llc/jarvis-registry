"""Client-ID categorization: maps every client_id to a ClientCategory and a builtin max scope set.

Two kinds of category exist:
- Prefixed collections (MCP_DCR, A2A_DCR): dynamically DCR-registered, client_id stored in Redis
  under a fixed prefix (see auth-server's oauth_flow.py).
- Single-instance client_ids (REGISTRY_APP, HEADLESS_AGENT, USER_GENERATED, REGISTRY_CLI,
  CLAUDE_DESKTOP): always special-cased, never stored in or queried from Redis.

Resolution order in ``resolve_client_category`` matters: exact-match single-instance ids are checked
before prefix matches, and an unrecognized client_id resolves to UNKNOWN with an *empty* ceiling
(fail closed) rather than falling back to any other category's scopes.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .config import INTERACTIVE_TOKEN_CLIENT_ID, JwtTokenConfig
from .downstream_oauth import DEVICE_CODE_GRANT_TYPE

MCP_CLIENT_ID_PREFIX = "mcp-client-"
A2A_CLIENT_ID_PREFIX = "a2a-client-"
REGISTRY_CLI_CLIENT_ID = "jarvis-registry-cli"
# Permanent identifier embedded in enterprise device policies.
CLAUDE_DESKTOP_CLIENT_ID = "jarvis-registry-claude-desktop"
AUTHORIZATION_CODE_GRANT_TYPE = "authorization_code"
REFRESH_TOKEN_GRANT_TYPE = "refresh_token"

PUBLIC_CLIENT_CREDENTIAL = None
CLAUDE_DESKTOP_REDIRECT_URI = "http://127.0.0.1:47280/callback"

_PROXY_OPS_SCOPES_EXCLUDED_FROM_REGISTRY_APP = frozenset({"mcp-proxy-ops", "a2a-proxy-ops"})


class ClientCategory(StrEnum):
    MCP_DCR = "mcp_dcr"
    A2A_DCR = "a2a_dcr"
    REGISTRY_APP = "registry_app"
    HEADLESS_AGENT = "headless_agent"
    USER_GENERATED = "user_generated"
    REGISTRY_CLI = "registry_cli"
    CLAUDE_DESKTOP = "claude_desktop"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClientPolicy:
    """Security policy shared by discovery, registration, and token issuance for this category."""

    category: ClientCategory
    allowed_grant_types: tuple[str, ...]
    max_scopes: frozenset[str]
    token_endpoint_auth_method: str | None = None
    client_id_prefix: str | None = None
    default_scope: str | None = None


_DCR_GRANT_TYPES = (
    AUTHORIZATION_CODE_GRANT_TYPE,
    REFRESH_TOKEN_GRANT_TYPE,
    DEVICE_CODE_GRANT_TYPE,
)

_CLIENT_POLICIES: dict[ClientCategory, ClientPolicy] = {
    ClientCategory.MCP_DCR: ClientPolicy(
        category=ClientCategory.MCP_DCR,
        allowed_grant_types=_DCR_GRANT_TYPES,
        max_scopes=frozenset({"mcp-proxy-ops"}),
        client_id_prefix=MCP_CLIENT_ID_PREFIX,
        default_scope="mcp-proxy-ops",
    ),
    ClientCategory.A2A_DCR: ClientPolicy(
        category=ClientCategory.A2A_DCR,
        allowed_grant_types=_DCR_GRANT_TYPES,
        max_scopes=frozenset({"a2a-proxy-ops"}),
        client_id_prefix=A2A_CLIENT_ID_PREFIX,
        default_scope="a2a-proxy-ops",
    ),
    ClientCategory.REGISTRY_CLI: ClientPolicy(
        category=ClientCategory.REGISTRY_CLI,
        allowed_grant_types=(DEVICE_CODE_GRANT_TYPE, REFRESH_TOKEN_GRANT_TYPE),
        max_scopes=frozenset({"skills-read"}),
        token_endpoint_auth_method="none",
        default_scope="skills-read",
    ),
    # Valid consent is shared across devices for this public client. Accept the native-app
    # trust model: a local process can reuse that consent with a live IdP session, but tokens
    # remain capped at mcp-proxy-ops. Do not force consent on every authorization.
    ClientCategory.CLAUDE_DESKTOP: ClientPolicy(
        category=ClientCategory.CLAUDE_DESKTOP,
        allowed_grant_types=(AUTHORIZATION_CODE_GRANT_TYPE, REFRESH_TOKEN_GRANT_TYPE),
        max_scopes=frozenset({"mcp-proxy-ops"}),
        token_endpoint_auth_method="none",
        default_scope="mcp-proxy-ops",
    ),
    ClientCategory.HEADLESS_AGENT: ClientPolicy(
        category=ClientCategory.HEADLESS_AGENT,
        allowed_grant_types=(),
        max_scopes=frozenset({"mcp-proxy-ops", "a2a-proxy-ops"}),
    ),
    ClientCategory.UNKNOWN: ClientPolicy(
        category=ClientCategory.UNKNOWN,
        allowed_grant_types=(),
        max_scopes=frozenset(),
    ),
}


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
    if client_id == CLAUDE_DESKTOP_CLIENT_ID:
        return ClientCategory.CLAUDE_DESKTOP
    if client_id.startswith(MCP_CLIENT_ID_PREFIX):
        return ClientCategory.MCP_DCR
    if client_id.startswith(A2A_CLIENT_ID_PREFIX):
        return ClientCategory.A2A_DCR
    return ClientCategory.UNKNOWN


def is_consent_revocable(category: ClientCategory) -> bool:
    return category not in (ClientCategory.REGISTRY_APP, ClientCategory.HEADLESS_AGENT)


def requires_app_consent(category: ClientCategory) -> bool:
    """Resource approvals require an app grant except for Registry and token-page clients."""
    return category not in (ClientCategory.REGISTRY_APP, ClientCategory.HEADLESS_AGENT, ClientCategory.USER_GENERATED)


def get_client_policy(category: ClientCategory) -> ClientPolicy | None:
    """Return the explicit protocol policy for a client category, if it has one."""
    return _CLIENT_POLICIES.get(category)


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
    policy = get_client_policy(category)
    return policy.max_scopes if policy is not None else frozenset()


def get_client_max_scopes(client_id: str, config: JwtTokenConfig) -> frozenset[str]:
    """Return the builtin max scope set for client_id's category."""
    return get_builtin_max_scopes(resolve_client_category(client_id, config), config.all_scopes)


def resolve_granted_scopes(
    client_id: str,
    requested_scopes: str | list[str],
    config: JwtTokenConfig,
) -> list[str]:
    """Intersect requested scopes against client_id's category ceiling. Order-preserving."""
    scopes = requested_scopes.split() if isinstance(requested_scopes, str) else requested_scopes
    ceiling = get_client_max_scopes(client_id, config)
    return [scope for scope in scopes if scope in ceiling]


def _static_entry(
    *,
    client_id: str,
    category: ClientCategory,
    client_name: str,
    redirect_uris: list[str],
    response_types: list[str],
) -> dict[str, Any]:
    policy = get_client_policy(category)
    if policy is None or policy.token_endpoint_auth_method is None:
        raise RuntimeError(f"{client_name} client policy is not configured")

    return {
        "client_id": client_id,
        "client_name": client_name,
        "client_secret": PUBLIC_CLIENT_CREDENTIAL,
        "redirect_uris": redirect_uris,
        "grant_types": list(policy.allowed_grant_types),
        "response_types": response_types,
        "scope": policy.default_scope,
        "token_endpoint_auth_method": policy.token_endpoint_auth_method,
    }


def _build_static_client_metadata() -> dict[str, dict[str, Any]]:
    return {
        REGISTRY_CLI_CLIENT_ID: _static_entry(
            client_id=REGISTRY_CLI_CLIENT_ID,
            category=ClientCategory.REGISTRY_CLI,
            client_name="Jarvis Registry CLI",
            redirect_uris=[],
            response_types=[],
        ),
        CLAUDE_DESKTOP_CLIENT_ID: _static_entry(
            client_id=CLAUDE_DESKTOP_CLIENT_ID,
            category=ClientCategory.CLAUDE_DESKTOP,
            client_name="Claude Desktop",
            redirect_uris=[CLAUDE_DESKTOP_REDIRECT_URI],
            response_types=["code"],
        ),
    }


STATIC_CLIENT_METADATA = _build_static_client_metadata()
