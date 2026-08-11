import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.core.client_categories import (
    ClientCategory,
    get_builtin_max_scopes,
    resolve_client_category,
    resolve_granted_scopes,
)
from registry_pkgs.core.config import JwtTokenConfig

_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_KEY = _RSA_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")
_PUBLIC_KEY = (
    _RSA_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)

_REGISTRY_CLIENT_ID = "jarvis-registry-client"
_HEADLESS_AGENT_CLIENT_ID = "jarvis-headless-agent"
_ALL_SCOPES = frozenset(
    {
        "servers-read",
        "servers-write",
        "agents-read",
        "agents-write",
        "mcp-proxy-ops",
        "a2a-proxy-ops",
        "skills-read",
        "skills-write",
        "user-read",
        "system-ops",
    }
)


@pytest.fixture
def cfg() -> JwtTokenConfig:
    return JwtTokenConfig(
        jwt_private_key=_PRIVATE_KEY,
        jwt_public_key=_PUBLIC_KEY,
        jwt_issuer="https://jarvis.test",
        jwt_self_signed_kid="self-signed-key-v1",
        managed_agents_audience="jarvis-managed-agents",
        crud_services_audience="jarvis-crud-services",
        registry_client_id=_REGISTRY_CLIENT_ID,
        headless_agent_client_id=_HEADLESS_AGENT_CLIENT_ID,
        all_scopes=_ALL_SCOPES,
    )


@pytest.mark.parametrize(
    "client_id, expected",
    [
        (_REGISTRY_CLIENT_ID, ClientCategory.REGISTRY_APP),
        (_HEADLESS_AGENT_CLIENT_ID, ClientCategory.HEADLESS_AGENT),
        ("user-generated", ClientCategory.USER_GENERATED),
        ("jarvis-registry-cli", ClientCategory.REGISTRY_CLI),
        ("mcp-client-abc", ClientCategory.MCP_DCR),
        ("mcp-client-", ClientCategory.MCP_DCR),
        ("a2a-client-xyz", ClientCategory.A2A_DCR),
        ("a2a-client-", ClientCategory.A2A_DCR),
        ("totally-unknown", ClientCategory.UNKNOWN),
        ("", ClientCategory.UNKNOWN),
    ],
)
def test_resolve_client_category(cfg, client_id, expected):
    assert resolve_client_category(client_id, cfg) == expected


# ---------------------------------------------------------------------------
# get_builtin_max_scopes
# ---------------------------------------------------------------------------


def test_unknown_ceiling_is_empty():
    assert get_builtin_max_scopes(ClientCategory.UNKNOWN, _ALL_SCOPES) == frozenset()


def test_registry_app_ceiling_excludes_proxy_ops():
    result = get_builtin_max_scopes(ClientCategory.REGISTRY_APP, _ALL_SCOPES)
    assert result == _ALL_SCOPES - {"mcp-proxy-ops", "a2a-proxy-ops", "skills-read"}


def test_user_generated_ceiling_equals_all():
    assert get_builtin_max_scopes(ClientCategory.USER_GENERATED, _ALL_SCOPES) == _ALL_SCOPES


@pytest.mark.parametrize(
    "category, expected",
    [
        (ClientCategory.MCP_DCR, frozenset({"mcp-proxy-ops"})),
        (ClientCategory.A2A_DCR, frozenset({"a2a-proxy-ops"})),
        (ClientCategory.HEADLESS_AGENT, frozenset({"mcp-proxy-ops", "a2a-proxy-ops"})),
        (ClientCategory.REGISTRY_CLI, frozenset({"skills-read"})),
    ],
)
def test_fixed_category_ceilings(category, expected):
    assert get_builtin_max_scopes(category, _ALL_SCOPES) == expected


def test_resolve_granted_scopes_filters_by_ceiling(cfg):
    result = resolve_granted_scopes(
        "mcp-client-x",
        ["mcp-proxy-ops", "servers-read"],
        cfg,
    )
    assert result == ["mcp-proxy-ops"]


def test_resolve_granted_scopes_preserves_order(cfg):
    result = resolve_granted_scopes(
        "user-generated",
        ["agents-write", "servers-read", "mcp-proxy-ops"],
        cfg,
    )
    assert result == ["agents-write", "servers-read", "mcp-proxy-ops"]


def test_resolve_granted_scopes_accepts_string(cfg):
    result = resolve_granted_scopes(
        "mcp-client-x",
        "mcp-proxy-ops servers-read agents-write",
        cfg,
    )
    assert result == ["mcp-proxy-ops"]


def test_resolve_granted_scopes_unknown_returns_empty(cfg):
    result = resolve_granted_scopes(
        "totally-unknown",
        ["mcp-proxy-ops", "servers-read"],
        cfg,
    )
    assert result == []


def test_resolve_granted_scopes_empty_input(cfg):
    assert resolve_granted_scopes("mcp-client-x", [], cfg) == []
    assert resolve_granted_scopes("mcp-client-x", "", cfg) == []
